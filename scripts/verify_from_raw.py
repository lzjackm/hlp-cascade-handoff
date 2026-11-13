#!/usr/bin/env python3
"""Rebuild the cascade extract and run the checks that need raw node data.

data/fills/cascade_L1_L2_20251010_h20_h21.parquet and data/funding_hourly_20251010.csv
are cut from Hyperliquid node data for 2025-10-10 20:00-22:00 UTC (node_fills_by_block
and misc_events_by_block, one block per JSON line). The same two hours, plus the
clearinghouse snapshot at block 758750000, are mirrored in
github.com/ConejoCapital/HyperReplay (data/raw and the clearinghouse snapshot archive).

Point --raw-dir at a directory holding:
    20_fills.json, 21_fills.json   node fills (or node_fills_20251010_20.lz4 / _21.lz4)
    20_misc.json,  21_misc.json    misc events (or misc_events_20251010_20.lz4 / _21.lz4)
    account_value_snapshot_758750000_1760126694218.json     counterparties only
    perp_positions_by_market_758750000_1760126694218.json   counterparties only

Commands:
    extract         rebuild the bundled extract into data/from_raw/ and diff it against data/
    incoming        Test 17: price at which the vaults take over liquidated users,
                    against each liquidated user's account value
    counterparties  Test 18: settlement of the traders auto-deleveraged against the
                    vaults' own liquidations. --live compares the current balances of
                    accounts untouched since then with what their fills imply.

Usage:
    python scripts/verify_from_raw.py extract --raw-dir RAW
    python scripts/verify_from_raw.py incoming --raw-dir RAW
    python scripts/verify_from_raw.py counterparties --raw-dir RAW [--live] [--limit 2000]
"""
import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_self_liquidation import DATA, EXTRACT, SNAP_BLOCK, VAULTS, walk  # noqa: E402

ADDR = {v["address"]: name for name, v in VAULTS.items()}
SNAP_AV = "account_value_snapshot_758750000_1760126694218.json"
SNAP_POS = "perp_positions_by_market_758750000_1760126694218.json"


def lines(raw_dir, kind, hour):
    names = {"fills": [f"{hour}_fills.json", f"node_fills_20251010_{hour}.lz4"],
             "misc": [f"{hour}_misc.json", f"misc_events_20251010_{hour}.lz4"]}[kind]
    for n in names:
        p = os.path.join(raw_dir, n)
        if not os.path.exists(p):
            continue
        if not p.endswith(".lz4"):
            return open(p)
        try:
            import lz4.frame
            return lz4.frame.open(p, mode="rt")
        except ImportError:
            return subprocess.Popen(["lz4", "-dc", p], stdout=subprocess.PIPE, text=True).stdout
    sys.exit(f"missing {kind} for hour {hour}: expected one of {names} in {raw_dir}")


def blocks(raw_dir, kind):
    for hour in (20, 21):
        for line in lines(raw_dir, kind, hour):
            if line.strip():
                yield json.loads(line)


def cmd_extract(a):
    rows = []
    for b in blocks(a.raw_dir, "fills"):
        for i, (user, f) in enumerate(b["events"]):
            if user not in ADDR:
                continue
            liq = f.get("liquidation")
            rows.append(dict(
                vault=ADDR[user], block=b["block_number"], idx=i, ts=f["time"], coin=f["coin"],
                side="buy" if f["side"] == "B" else "sell", px=float(f["px"]), sz=float(f["sz"]),
                start_position=float(f["startPosition"]), dir_raw=f["dir"], closed_pnl=float(f["closedPnl"]),
                fee=float(f["fee"]), tid=f["tid"], hash=f["hash"],
                liq_method=liq["method"] if liq else None,
                liq_mark_px=float(liq["markPx"]) if liq else np.nan,
                liq_role=None if not liq else ("self" if liq["liquidatedUser"] == user else "counterparty")))
    ex = pd.DataFrame(rows)
    ex["ts"] = pd.to_datetime(ex.ts, unit="ms", utc=True)
    ex["tid"] = ex.tid.astype("Int64")
    fund = []
    for b in blocks(a.raw_dir, "misc"):
        for e in b["events"]:
            for dl in e["inner"].get("Funding", {}).get("deltas", []):
                if dl["user"] in ADDR:
                    fund.append(dict(ts=e["time"] + "Z", block=b["block_number"], vault_name=ADDR[dl["user"]],
                                     coin=dl["coin"], delta_usd=float(dl["funding_amount"]),
                                     szi=float(dl["szi"]), funding_rate=float(dl["funding_rate"])))
    out = os.path.join(DATA, "from_raw")
    os.makedirs(os.path.join(out, "fills"), exist_ok=True)
    ex.to_parquet(os.path.join(out, "fills", EXTRACT), index=False)
    pd.DataFrame(fund).to_csv(os.path.join(out, "funding_hourly_20251010.csv"), index=False)
    print(f"wrote {len(ex):,} fills and {len(fund)} funding events to {out}")
    for name in (os.path.join("fills", EXTRACT), "funding_hourly_20251010.csv"):
        bundled, rebuilt = os.path.join(DATA, name), os.path.join(out, name)
        if os.path.exists(bundled):
            rd = pd.read_parquet if name.endswith(".parquet") else pd.read_csv
            print(f"bundled {name} identical to rebuild: {rd(bundled).equals(rd(rebuilt))}")
    for vault, v in VAULTS.items():  # cross-check against the S3-derived fills parquets
        p = pd.read_parquet(os.path.join(DATA, "fills", v["fills"]))
        p = p[(p.ts >= ex.ts.min()) & (p.ts <= ex.ts.max())]
        e = ex[ex.vault == vault]
        print(f"{vault:13} rebuild {len(e):>7,} fills, S3 parquet {len(p):>7,}; same tids: "
              f"{set(e.tid) == set(p.tid)}; Σ closedPnl diff {e.closed_pnl.sum() - p.closed_pnl.sum():.6f}")
    av_path = os.path.join(a.raw_dir, SNAP_AV)
    if os.path.exists(av_path):
        snap = {r["user"]: r for r in json.load(open(av_path)) if r["user"] in ADDR}
        s = pd.read_csv(os.path.join(DATA, "snapshot_account_values.csv"))
        for vault, v in VAULTS.items():
            ours = s[(s.vault == vault) & (s.block == SNAP_BLOCK)].account_value.iloc[0]
            r = snap[v["address"]]
            print(f"{vault:13} clearinghouse snapshot account value {r['account_value']:,.6f} "
                  f"(bundled {ours:,.6f}), long/short notional {r['total_long_notional']}/{r['total_short_notional']}")


def ledger_liquidations(raw_dir):
    rows = []
    for b in blocks(raw_dir, "misc"):
        for e in b["events"]:
            lu = e["inner"].get("LedgerUpdate")
            if lu and lu["delta"].get("type") == "liquidation":
                rows.append(dict(block=b["block_number"], user=lu["users"][0], av=float(lu["delta"]["accountValue"])))
    return pd.DataFrame(rows).groupby(["block", "user"]).av.sum()


def cmd_incoming(a):
    rows = []
    for b in blocks(a.raw_dir, "fills"):
        for user, f in b["events"]:
            liq = f.get("liquidation")
            if liq and liq["method"] == "backstop":
                rows.append((b["block_number"], user, f["tid"], f["side"], float(f["px"]), float(f["sz"]),
                             float(liq["markPx"]), liq["liquidatedUser"]))
    f = pd.DataFrame(rows, columns=["block", "user", "tid", "side", "px", "sz", "mark", "liq_user"])
    taker = f[f.user.isin(ADDR) & (f.liq_user != f.user)]
    tid_vault = dict(zip(taker.tid, taker.user.map(ADDR)))
    u = f[(f.user == f.liq_user) & f.tid.isin(tid_vault)].copy()
    # value the taking vault receives relative to markPx
    u["conc"] = (u.mark - u.px) * u.sz * np.where(u.side == "B", -1, 1)
    u["vault"] = u.tid.map(tid_vault)
    g = u.groupby(["block", "user"]).agg(conc=("conc", "sum"), ntl=("px", lambda s: (s * u.loc[s.index, "sz"]).sum()),
                                         vault=("vault", lambda s: " + ".join(sorted(set(s)))))
    g = g.join(ledger_liquidations(a.raw_dir).rename("av"))
    g["diff"] = g.av - g.conc
    print("Test 17: backstop takeovers into the vaults, price concession vs liquidated account value")
    print(f"liquidation events: {len(g):,}; matched to a ledger liquidation event: {g.av.notna().sum():,}")
    t = g.groupby("vault").agg(events=("conc", "size"), notional=("ntl", "sum"), account_value=("av", "sum"),
                               concession=("conc", "sum"), diff=("diff", "sum"))
    print(t.to_string(float_format=lambda x: f"{x:,.0f}"))
    print(f"total diff {g['diff'].sum():,.2f}; median {g['diff'].median():.4f}; "
          f"share within $1: {(g['diff'].abs() < 1).mean():.3f}")


def cmd_counterparties(a):
    ex = pd.read_parquet(os.path.join(DATA, "fills", EXTRACT))
    rounds, round_of = {}, {}
    for vault in VAULTS:
        r, _, _ = walk(vault, ex)
        rounds.update({(vault, blk): rem for blk, rem in zip(r.block, r.removed)})
        own = ex[(ex.vault == vault) & (ex.liq_role == "self")]
        round_of.update({t: (vault, blk) for t, blk in zip(own.tid, own.block)})
    cp = []
    for b in blocks(a.raw_dir, "fills"):
        if b["block_number"] <= SNAP_BLOCK:
            continue
        for user, f in b["events"]:
            if f["tid"] in round_of and user not in ADDR:
                cp.append(dict(user=user, key=round_of[f["tid"]], px=float(f["px"]), sz=float(f["sz"]),
                               mark=float(f["liquidation"]["markPx"]), side=f["side"]))
    cp = pd.DataFrame(cp)
    cp["ntl"] = cp.px * cp.sz
    cp["overpay"] = (cp.px - cp.mark) * cp.sz * np.where(cp.side == "B", 1, -1)  # paid vs markPx
    tot = cp.groupby("key")[["ntl", "overpay"]].transform("sum")
    removed = cp.key.map(rounds)
    cp["share_ntl"] = removed * cp.ntl / tot.ntl
    cp["share_overpay"] = removed * cp.overpay / tot.overpay
    shares = cp.groupby("user")[["ntl", "share_ntl", "share_overpay"]].sum()
    print(f"counterparties: {len(shares):,}; pro-rata shares sum to {shares.share_ntl.sum():,.2f}")
    users = set(shares.index)
    fills = []
    for b in blocks(a.raw_dir, "fills"):
        if b["block_number"] <= SNAP_BLOCK:
            continue
        for user, f in b["events"]:
            if user in users:
                fills.append(dict(user=user, coin=f["coin"], side=f["side"], px=float(f["px"]), sz=float(f["sz"]),
                                  start=float(f["startPosition"]), fee=float(f["fee"]), tid=f["tid"],
                                  adl=f["dir"] == "Auto-Deleveraging" and f["tid"] in round_of))
    fills = pd.DataFrame(fills)
    pos = {}
    for m in json.load(open(os.path.join(a.raw_dir, SNAP_POS))):
        coin = m["market_name"].split(":", 1)[1]
        for p in m["positions"]:
            if p["user"] in users:
                pos.setdefault(p["user"], {})[coin] = (p["size"], np.sign(p["size"]) * p["notional_size"])
    av0 = {r["user"]: r["account_value"] for r in json.load(open(os.path.join(a.raw_dir, SNAP_AV))) if r["user"] in users}
    fund = {}
    for b in blocks(a.raw_dir, "misc"):
        if b["block_number"] <= SNAP_BLOCK:
            continue
        for e in b["events"]:
            for dl in e["inner"].get("Funding", {}).get("deltas", []):
                if dl["user"] in users:
                    fund[dl["user"]] = fund.get(dl["user"], 0.0) + float(dl["funding_amount"])
    clean = []
    for u, g in fills.groupby("user"):
        held = pos.get(u, {})
        if not g.adl.all() or set(held) != set(g.coin) or u not in av0:
            continue  # only accounts whose whole snapshot book was closed by these rounds and nothing else
        s = np.where(g.side == "B", 1, -1)
        net = pd.Series(s * g.sz.values, index=g.coin.values).groupby(level=0).sum()
        if any(abs(held[c][0] + net[c]) > 1e-9 for c in held):
            continue
        cash = av0[u] - sum(v for _, v in held.values()) + float((-s * g.sz * g.px).sum() - g.fee.sum()) + fund.get(u, 0.0)
        clean.append(dict(user=u, predicted_cash=cash))
    c = pd.DataFrame(clean).set_index("user").join(shares).sort_values("share_ntl", ascending=False)
    print(f"accounts whose snapshot book was closed only by these rounds: {len(c):,}")
    c = c[c.share_ntl >= 0.5]  # a pro-rata credit this size would be visible to the cent
    print(f"  of which pro-rata notional share >= $0.50: {len(c):,} (querying up to {a.limit:,})")
    if not a.live:
        print("re-run with --live to compare against current HL balances")
        return
    import requests
    out, s = [], requests.Session()
    for u in c.index[: a.limit]:
        j = None
        for attempt in range(6):
            try:
                r = s.post("https://api.hyperliquid.xyz/info", json={"type": "clearinghouseState", "user": u}, timeout=20)
                if r.status_code == 429:
                    raise requests.RequestException("rate limited")
                j = r.json()
                break
            except (requests.RequestException, ValueError):
                time.sleep(2 * (attempt + 1))
        if j is None:
            continue  # skipped after repeated network errors
        out.append(dict(user=u, av_now=float(j["marginSummary"]["accountValue"]), positions=len(j["assetPositions"])))
        time.sleep(0.11)
    o = pd.DataFrame(out).set_index("user").join(c)
    o["diff"] = o.av_now - o.predicted_cash
    flat = o[o.positions == 0]
    none = flat["diff"].abs() < 0.01
    print(f"queried {len(o):,}; flat now {len(flat):,}")
    print(f"current balance == fill-implied cash (to the cent):                 {none.sum()}  "
          f"(their pro-rata shares total ${flat[none].share_ntl.sum():,.2f})")
    print(f"current balance == fill-implied cash + pro-rata share by notional:  {((flat['diff'] - flat.share_ntl).abs() < 0.01).sum()}")
    print(f"current balance == fill-implied cash + pro-rata share by overpay:   {((flat['diff'] - flat.share_overpay).abs() < 0.01).sum()}")
    print(flat[none].sort_values("share_ntl", ascending=False)[["ntl", "predicted_cash", "av_now", "diff", "share_ntl", "share_overpay"]]
          .reset_index(drop=True).to_string(float_format=lambda x: f"{x:,.4f}"))  # addresses left out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["extract", "incoming", "counterparties"])
    p.add_argument("--raw-dir", required=True)
    p.add_argument("--live", action="store_true")
    p.add_argument("--limit", type=int, default=2000)
    a = p.parse_args()
    {"extract": cmd_extract, "incoming": cmd_incoming, "counterparties": cmd_counterparties}[a.command](a)


if __name__ == "__main__":
    main()
