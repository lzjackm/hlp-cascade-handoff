#!/usr/bin/env python3
"""Test 19: where the removed balances went.

A per-account USDC diff across every Hyperliquid account. For each account:

    residual = USDC at the end - (USDC at the start + fill cash - fees + funding + ledger flows)

where USDC = perp raw cash (accountValue minus signed position value) + spot USDC, so
perp/spot transfers cancel and no mark price is needed. Accounts credited by HL without
a ledger event (fee recipients, and whoever received the removed balances) show a
positive residual. Two windows:

    control  Artemis crawl Oct 10 ~01:56 UTC -> snapshot block 758750000 (no ADL)
    cascade  snapshot block 758750000 -> Artemis crawl Oct 11 ~01:54 UTC

The control window measures what share of protocol fees the Assistance Fund and the
HLP parent receive; the cascade window's residual beyond that share is compared with
the balances removed from accounts liquidated through auto-deleveraging.

Inputs (--raw-dir), all public:
    account_value_snapshot_758750000_1760126694218.json   ConejoCapital HyperReplay
    perp_positions_by_market_758750000_1760126694218.json   clearinghouse snapshot archive
    spot_balances__758750000_1760126694218.json
    artemis_20251010.jsonl, artemis_20251011.jsonl        s3://artemis-hyperliquid-data/raw/perp_and_spot_balances/
    fills_YYYYMMDD_H.lz4, misc_YYYYMMDD_H.lz4             s3://hl-mainnet-node-data/{node_fills,misc_events}_by_block/hourly/
        for 2025-10-10 hours 1-23 and 2025-10-11 hours 0-2 (requester pays)

`download` fetches the S3 objects with the AWS CLI (about 3.5 GB, requester pays).

Usage:
    python scripts/trace_destination.py download --raw-dir RAW [--profile P]
    python scripts/trace_destination.py run --raw-dir RAW --work WORK
"""
import argparse
import glob
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_self_liquidation import DATA, VAULTS, walk  # noqa: E402

SNAP_BLOCK, SNAP_MS = 758750000, 1760126694218
AF = "0xfefefefefefefefefefefefefefefefefefefefe"
HLP = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
HOURS = [("20251010", h) for h in range(1, 24)] + [("20251011", h) for h in range(0, 3)]
ARTEMIS = {"artemis_20251010.jsonl": "2025/10/10/perp_and_spot_balances_20251010_015001.jsonl",
           "artemis_20251011.jsonl": "2025/10/11/perp_and_spot_balances_20251011_015002.jsonl"}


def cmd_download(a):
    os.makedirs(a.raw_dir, exist_ok=True)
    flags = ["--request-payer", "requester", "--region", "ap-northeast-1", "--only-show-errors"]
    if a.profile:
        flags += ["--profile", a.profile]
    jobs = [(f"s3://artemis-hyperliquid-data/raw/perp_and_spot_balances/{k}", v) for v, k in ARTEMIS.items()]
    for day, h in HOURS:
        for src, name in (("node_fills_by_block", "fills"), ("misc_events_by_block", "misc")):
            jobs.append((f"s3://hl-mainnet-node-data/{src}/hourly/{day}/{h}.lz4", f"{name}_{day}_{h}.lz4"))
    procs = [subprocess.Popen(["aws", "s3", "cp", s, os.path.join(a.raw_dir, d)] + flags) for s, d in jobs
             if not os.path.exists(os.path.join(a.raw_dir, d))]
    for p in procs:
        p.wait()
    print(f"{len(procs)} objects downloaded to {a.raw_dir}")


def hour_file(raw, kind, day, h):
    """fills/misc file for one hour, compressed (.lz4) or decompressed (.json)."""
    for ext in (".lz4", ".json"):
        p = os.path.join(raw, f"{kind}_{day}_{h}{ext}")
        if os.path.exists(p):
            return p
    sys.exit(f"missing {kind}_{day}_{h}.lz4 in {raw}")


def lines(path):
    if path.endswith(".lz4"):
        return subprocess.Popen(["lz4", "-dc", path], stdout=subprocess.PIPE, text=True).stdout
    return open(path)


def spot_quotes(work):
    p = os.path.join(work, "spotMeta.json")
    if not os.path.exists(p):
        import requests
        json.dump(requests.post("https://api.hyperliquid.xyz/info", json={"type": "spotMeta"}, timeout=30).json(), open(p, "w"))
    meta = json.load(open(p))
    tok = {t["index"]: t["name"] for t in meta["tokens"]}
    q = {}
    for u in meta["universe"]:
        q[u["name"]] = q[f"@{u['index']}"] = tok[u["tokens"][1]]
    return q


def process_hour(raw, work, day, h, quote):
    out = os.path.join(work, f"h_{day}_{h:02d}")
    if os.path.exists(out + "_ledger.parquet"):
        return
    F = []
    for line in lines(hour_file(raw, "fills", day, h)):
        if not line.strip():
            continue
        b = json.loads(line)
        for user, f in b["events"]:
            s = 1 if f["side"] == "B" else -1
            px, sz = float(f["px"]), float(f["sz"])
            spot = f["coin"].startswith("@") or "/" in f["coin"]
            usdc_quote = spot and quote.get(f["coin"]) == "USDC"
            F.append((user, f["time"], b["block_number"], 0.0 if spot else -s * sz * px, -s * sz * px if usdc_quote else 0.0,
                      float(f["fee"]), f.get("feeToken") or "USDC", float(f.get("builderFee") or 0)))
    pd.DataFrame(F, columns=["user", "t", "block", "perp_cash", "spot_usdc", "fee", "fee_token", "builder_fee"]).to_parquet(out + "_fills.parquet")
    FU, LE = [], []
    for line in lines(hour_file(raw, "misc", day, h)):
        if not line.strip():
            continue
        b = json.loads(line)
        t = pd.Timestamp(b["block_time"]).value // 1_000_000
        for e in b["events"]:
            if "Funding" in e["inner"]:
                FU += [(d["user"], t, b["block_number"], float(d["funding_amount"])) for d in e["inner"]["Funding"]["deltas"]]
            elif "LedgerUpdate" in e["inner"]:
                lu = e["inner"]["LedgerUpdate"]
                LE.append((t, b["block_number"], lu["delta"].get("type"), json.dumps(lu["users"]), json.dumps(lu["delta"])))
    pd.DataFrame(FU, columns=["user", "t", "block", "amount"]).to_parquet(out + "_funding.parquet")
    pd.DataFrame(LE, columns=["t", "block", "type", "users", "delta"]).to_parquet(out + "_ledger.parquet")


def ledger_flows(L):
    """USDC moved by each ledger event, per account (perp and spot combined)."""
    C = []
    for r in L.itertuples():
        d, us, t, ty = json.loads(r.delta), json.loads(r.users), r.t, r.type
        g = lambda k: float(d.get(k) or 0)  # noqa: E731
        if ty == "deposit": C.append((us[0], t, r.block, g("usdc")))
        elif ty == "withdraw": C.append((us[0], t, r.block, -g("usdc") - g("fee")))
        elif ty == "internalTransfer": C += [(d["user"], t, r.block, -g("usdc") - g("fee")), (d["destination"], t, r.block, g("usdc"))]
        elif ty == "subAccountTransfer": C += [(d["user"], t, r.block, -g("usdc")), (d["destination"], t, r.block, g("usdc"))]
        elif ty == "spotTransfer" and d["token"] == "USDC":
            C += [(d["user"], t, r.block, -g("amount") - (g("fee") if d.get("feeToken") in ("USDC", "") else 0)), (d["destination"], t, r.block, g("amount"))]
        elif ty == "spotTransfer" and d.get("feeToken") == "USDC": C.append((d["user"], t, r.block, -g("fee")))
        elif ty == "vaultDeposit": C += [(us[0], t, r.block, -g("usdc")), (d["vault"], t, r.block, g("usdc"))]
        elif ty == "vaultWithdraw": C += [(d["user"], t, r.block, g("netWithdrawnUsd")), (d["vault"], t, r.block, -g("requestedUsd"))]
        elif ty == "vaultLeaderCommission": C.append((d["user"], t, r.block, g("usdc")))
        elif ty == "vaultDistribution": C.append((us[0], t, r.block, g("usdc")))
        elif ty == "vaultCreate": C += [(us[0], t, r.block, -g("usdc") - g("fee")), (d["vault"], t, r.block, g("usdc"))]
        elif ty == "rewardsClaim" and d.get("token") == "USDC": C.append((us[0], t, r.block, g("amount")))
        elif ty == "accountActivationGas" and d.get("token") == "USDC": C.append((us[0], t, r.block, -g("amount")))
    return pd.DataFrame(C, columns=["user", "t", "block", "usdc"])


def snapshot_state(raw):
    av = pd.DataFrame(json.load(open(os.path.join(raw, "account_value_snapshot_758750000_1760126694218.json"))))[["user", "account_value"]].set_index("user")
    pv, usdc = {}, {}
    for m in json.load(open(os.path.join(raw, "perp_positions_by_market_758750000_1760126694218.json"))):
        for p in m["positions"]:
            pv[p["user"]] = pv.get(p["user"], 0.0) + (1 if p["size"] > 0 else -1) * p["notional_size"]
    for tk in json.load(open(os.path.join(raw, "spot_balances__758750000_1760126694218.json"))):
        if tk["token_name"] == "USDC":
            for b in tk["balances"]:
                usdc[b["user"]] = usdc.get(b["user"], 0.0) + b["total"]
    st = av.join(pd.Series(pv, name="pv"), how="outer").join(pd.Series(usdc, name="usdc"), how="outer").fillna(0.0)
    return (st.account_value - st.pv + st.usdc).rename("usdc_snap")


def artemis_state(path):
    rows = []
    for line in open(path):
        r = json.loads(line)
        if r.get("_metadata"):
            continue
        p, s = r["response"]["perpetual"], r["response"]["spot"]
        pv = sum(float(x["position"]["positionValue"]) * (1 if float(x["position"]["szi"]) > 0 else -1) for x in p["assetPositions"])
        usdc = sum(float(b["total"]) for b in s.get("balances", []) if b["coin"] == "USDC")
        rows.append((r["address"], p["time"], float(p["marginSummary"]["accountValue"]) - pv + usdc))
    return pd.DataFrame(rows, columns=["user", "t", "usdc_artemis"]).set_index("user")


def window_flows(work, art, block_filter, after):
    """Σ flows per account between the snapshot and each account's Artemis query time."""
    cut = art.t
    keep = (lambda x: x.t > cut.reindex(x.user).values) if after else (lambda x: x.t <= cut.reindex(x.user).values)
    fl, fees = [], []
    for f in sorted(glob.glob(os.path.join(work, "h_*_fills.parquet"))):
        x = pd.read_parquet(f)
        x = x[block_filter(x.block)]
        fees.append(x[x.fee_token == "USDC"][["t", "fee", "builder_fee"]])  # every fill counts toward protocol fees
        x = x[x.user.isin(cut.index)]
        x = x[keep(x)]
        usd = x.fee_token == "USDC"
        x["cash"] = x.perp_cash + x.spot_usdc - np.where(usd, x.fee, 0.0)
        fl.append(x.groupby("user").cash.sum())
    fu = pd.concat([pd.read_parquet(f) for f in glob.glob(os.path.join(work, "h_*_funding.parquet"))])
    lc = ledger_flows(pd.concat([pd.read_parquet(f) for f in glob.glob(os.path.join(work, "h_*_ledger.parquet"))]))
    out = pd.concat(fl).groupby(level=0).sum()
    for z in (fu.rename(columns={"amount": "usdc"}), lc):
        z = z[block_filter(z.block) & z.user.isin(cut.index)]
        out = out.add(z[keep(z)].groupby("user").usdc.sum(), fill_value=0.0)
    return out, pd.concat(fees)



def cmd_run(a):
    os.makedirs(a.work, exist_ok=True)
    quote = spot_quotes(a.work)
    for day, h in HOURS:
        process_hour(a.raw_dir, a.work, day, h, quote)
    snap = snapshot_state(a.raw_dir)
    res = {}
    for name, art_file, after in (("control", "artemis_20251010.jsonl", True), ("cascade", "artemis_20251011.jsonl", False)):
        art = artemis_state(os.path.join(a.raw_dir, art_file))
        art = art[art.index.isin(snap.index)] if name == "control" else art
        bf = (lambda b: b <= SNAP_BLOCK) if name == "control" else (lambda b: b > SNAP_BLOCK)
        flows, fees = window_flows(a.work, art, bf, after)
        df = art.join(snap, how="left").fillna({"usdc_snap": 0.0}).join(flows.rename("flows")).fillna({"flows": 0.0})
        start, end = ("usdc_artemis", "usdc_snap") if name == "control" else ("usdc_snap", "usdc_artemis")
        df["resid"] = df[end] - df[start] - df.flows
        lo, hi = (int(art.t[AF]), SNAP_MS) if name == "control" else (SNAP_MS, int(art.t[AF]))
        fw = fees[(fees.t > lo) & (fees.t <= hi)]
        res[name] = dict(df=df, proto_fees=float((fw.fee - fw.builder_fee).sum()))
    ctl, cas = res["control"], res["cascade"]
    share_af = ctl["df"].resid[AF] / ctl["proto_fees"]
    share_hlp = ctl["df"].resid[HLP] / ctl["proto_fees"]
    # accounts liquidated through auto-deleveraging during the cascade
    adl = []
    for day, h in (("20251010", 20), ("20251010", 21)):
        tids, liq = set(), {}
        for line in lines(hour_file(a.raw_dir, "fills", day, h)):
            b = json.loads(line)
            for user, f in b["events"]:
                lq = f.get("liquidation")
                if lq and lq["method"] == "backstop":
                    if f["dir"] == "Auto-Deleveraging":
                        tids.add(f["tid"])
                    elif user == lq["liquidatedUser"]:
                        liq.setdefault(f["tid"], user)
        adl += [u for t, u in liq.items() if t in tids]
    adl = sorted(set(adl))
    r_adl = cas["df"].resid.reindex(adl)
    vaults = [v["address"] for v in VAULTS.values()]
    removed_vaults = sum(walk(v)[0].removed.sum() for v in VAULTS)  # Test 14
    removed_others = -r_adl.drop(vaults)[r_adl.drop(vaults) < 0].sum()
    removed = removed_vaults + removed_others
    exc_af = cas["df"].resid[AF] - share_af * cas["proto_fees"]
    exc_hlp = cas["df"].resid[HLP] - share_hlp * cas["proto_fees"]
    rows = [
        ("control_window_protocol_fees", ctl["proto_fees"]), ("control_af_residual", ctl["df"].resid[AF]),
        ("control_hlp_residual", ctl["df"].resid[HLP]), ("af_fee_share", share_af), ("hlp_fee_share", share_hlp),
        ("control_accounts", len(ctl["df"])), ("control_accounts_over_1usd", int((ctl["df"].resid.abs() > 1).sum())),
        ("cascade_window_protocol_fees", cas["proto_fees"]), ("cascade_af_residual", cas["df"].resid[AF]),
        ("cascade_hlp_residual", cas["df"].resid[HLP]), ("cascade_accounts", len(cas["df"])),
        ("cascade_accounts_over_1usd", int((cas["df"].resid.abs() > 1).sum())),
        ("af_excess_over_fee_share", exc_af), ("hlp_excess_over_fee_share", exc_hlp),
        ("adl_liquidated_accounts", len(adl)), ("adl_liquidated_accounts_negative_over_1usd", int((r_adl < -1).sum())),
        ("removed_from_vaults", removed_vaults), ("removed_from_other_adl_accounts", removed_others), ("removed_total", removed),
        ("af_plus_hlp_excess_minus_removed", exc_af + exc_hlp - removed),
        ("af_share_of_excess", exc_af / (exc_af + exc_hlp)), ("af_share_of_fee_split", share_af / (share_af + share_hlp)),
    ]
    # timing: Assistance Fund USDC spending vs its fee income at the control share
    t_af = int(artemis_state(os.path.join(a.raw_dir, "artemis_20251011.jsonl")).t[AF])
    spend, fee_t = [], []
    for f in sorted(glob.glob(os.path.join(a.work, "h_*_fills.parquet"))):
        x = pd.read_parquet(f)
        x = x[(x.block > SNAP_BLOCK) & (x.t <= t_af)]
        y = x[x.user == AF]
        spend.append(pd.DataFrame({"t": y.t, "v": -(y.perp_cash + y.spot_usdc - np.where(y.fee_token == "USDC", y.fee, 0.0))}))
        u = x[x.fee_token == "USDC"]
        fee_t.append(pd.DataFrame({"t": u.t, "v": -share_af * (u.fee - u.builder_fee)}))
    ev = pd.concat([pd.concat(spend), pd.concat(fee_t)]).sort_values("t", kind="stable")
    bal = snap.get(AF, 0.0) - ev.v.cumsum()
    sp = pd.concat(spend); ts = pd.to_datetime(sp.t, unit="ms")
    burst = sp.v[(ts >= "2025-10-10 21:16:04") & (ts < "2025-10-10 23:20")].sum()
    first_round = pd.Timestamp("2025-10-10 21:16:04.831").value // 1_000_000
    rows += [("af_balance_fees_only_min_before_first_round", float(bal[ev.t.values < first_round].min())),
             ("af_balance_fees_only_min_after_first_round", float(bal[ev.t.values >= first_round].min())),
             ("af_usdc_spent_2116_2320_utc", burst)]
    out = pd.DataFrame(rows, columns=["metric", "value"])
    out.to_csv(os.path.join(a.work, "destination_summary.csv"), index=False)
    pd.set_option("display.float_format", lambda v: f"{v:,.4f}" if abs(v) < 10 else f"{v:,.2f}")
    print(out.to_string(index=False))
    top = cas["df"].resid.sort_values(ascending=False).head(5)
    print("\nlargest positive cascade residuals:", ", ".join(f"{u[:10]}… {v:,.0f}" for u, v in top.items()))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["download", "run"])
    p.add_argument("--raw-dir", required=True)
    p.add_argument("--work", default="trace_work")
    p.add_argument("--profile", default=None)
    a = p.parse_args()
    {"download": cmd_download, "run": cmd_run}[a.command](a)


if __name__ == "__main__":
    main()
