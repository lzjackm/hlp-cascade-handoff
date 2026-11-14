#!/usr/bin/env python3
"""Test 19b: audit every account on the exchange between consecutive daily snapshots.

For each account, between two clearinghouse snapshots:

    perp:  change in (account value - position value at the snapshot marks)
           against  fill-price cash - fees + funding + ledger flows
    spot:  change in spot USDC balance
           against  USDC-quoted spot fill cash - USDC fees + ledger flows

What is left over is USDC that moved into or out of the account with no fill,
funding payment or ledger event behind it. Summed over every account it shows
where unrecorded money left and where it arrived, with nothing assumed about
who the recipient is.

Inputs (none bundled; all public):
    --fills-dir      node_fills_by_block hourly files, FILLS/YYYYMMDD/H.lz4
    --misc-dir       misc_events_by_block hourly files, MISC/YYYYMMDD/H.lz4
    --snapshots-dir  Hydromancer Reservoir snapshots, SNAP/account_values/date=YYYY-MM-DD/,
                     SNAP/perp/all/date=..., SNAP/spot/date=...   (s3://hydromancer-reservoir/global/snapshots/)

    aws s3 cp --recursive --request-payer requester s3://hl-mainnet-node-data/node_fills_by_block/hourly/20251010/ FILLS/20251010/
    aws s3 cp --recursive --request-payer requester s3://hl-mainnet-node-data/misc_events_by_block/hourly/20251010/ MISC/20251010/
    aws s3 cp --recursive --request-payer requester s3://hydromancer-reservoir/global/snapshots/spot/date=2025-10-10/ SNAP/spot/date=2025-10-10/

Usage:
    python scripts/audit_all_accounts.py --fills-dir FILLS --misc-dir MISC --snapshots-dir SNAP \\
        --dates 2025-10-07 2025-10-08 2025-10-09 2025-10-10 2025-10-11 2025-10-12 2025-10-13

Writes data/account_audit_by_class.csv (one row per window and account class, no
addresses) and data/account_audit_windows.csv (one row per window). Pass --out-dir to
write elsewhere, for example when auditing other dates. Needs the lz4
command-line tool. Each window takes about 15 seconds on six cores.

Notes on the ledger model:
  - A withdrawal is debited when requested and logged when the bridge finalises it,
    minutes later. It is placed by its nonce (the request time), not its log time.
  - deposit, accountClassTransfer, internalTransfer, subAccountTransfer, send, the
    vault events and USDC spotTransfers move USDC between accounts or classes as logged.
  - rewardsClaim credits spot USDC from referral and builder rewards accrued earlier.
    It is reported on its own so spot results can be read net of it.
"""
import argparse
import collections
import glob
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_self_liquidation import DATA, VAULTS  # noqa: E402

ASSISTANCE_FUND = "0xfefefefefefefefefefefefefefefefefefefefe"
HLP_PARENT = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
VAULT_ADDRESSES = {v["address"] for v in VAULTS.values()}
CLASSES = ["liquidator vaults", "other ADL-closed accounts", "Assistance Fund", "HLP parent", "all other accounts"]


def stream(path):
    proc = subprocess.Popen(["lz4", "-dc", path], stdout=subprocess.PIPE)
    yield from proc.stdout
    if proc.wait() != 0:
        raise RuntimeError(f"lz4 failed on {path}")


def fills_worker(args):
    path, b0, b1, quote = args
    agg = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])  # perp cash, perp fee, spot cash, spot fee
    closed = collections.defaultdict(float)  # ADL-closed account -> received above markPx
    builder = 0.0
    for line in stream(path):
        rec = json.loads(line)
        if not b0 < rec["block_number"] <= b1:
            continue
        for user, f in rec["events"]:
            coin = f["coin"]
            cash = float(f["px"]) * float(f["sz"]) * (-1.0 if f["side"] == "B" else 1.0)
            a = agg[user]
            if f.get("builderFee"):
                builder += float(f["builderFee"])
            if coin.startswith("@") or "/" in coin:
                if quote.get(coin) == "USDC":
                    a[2] += cash
                if f.get("feeToken") == "USDC":
                    a[3] += float(f["fee"])
                continue
            a[0] += cash
            a[1] += float(f["fee"])
            liq = f.get("liquidation")
            if liq and f["dir"] == "Auto-Deleveraging":
                sign = 1.0 if f["side"] == "B" else -1.0
                closed[liq["liquidatedUser"]] += (float(f["px"]) - float(liq["markPx"])) * float(f["sz"]) * sign
    return dict(agg), dict(closed), builder


def misc_worker(args):
    path, b0, b1, t0, t1 = args
    perp, spot, funding, rewards = (collections.defaultdict(float) for _ in range(4))
    ext = collections.defaultdict(float)  # deposits, withdrawals, withdrawal fees
    unmodelled = collections.Counter()
    for line in stream(path):
        rec = json.loads(line)
        inside = b0 < rec["block_number"] <= b1
        for e in rec["events"]:
            kind = next(iter(e["inner"]))
            body = e["inner"][kind]
            if kind == "LedgerUpdate" and body["delta"]["type"] == "withdraw":
                d = body["delta"]
                nonce = int(d["nonce"])
                nonce = nonce // 1000 if nonce > 10**14 else nonce
                if t0 < nonce <= t1:
                    perp[body["users"][0]] -= float(d["usdc"]) + float(d.get("fee") or 0)
                    ext["withdraw"] += float(d["usdc"])
                    ext["withdraw_fee"] += float(d.get("fee") or 0)
                continue
            if not inside:
                continue
            if kind == "Funding":
                for d in body["deltas"]:
                    funding[d["user"]] += float(d["funding_amount"])
                continue
            if kind != "LedgerUpdate":
                continue
            d, users = body["delta"], body.get("users", [])
            t = d["type"]
            amt = lambda key="usdc": float(d.get(key) or 0)  # noqa: E731
            if t == "deposit":
                perp[users[0]] += amt()
                ext["deposit"] += amt()
            elif t == "accountClassTransfer":
                s = 1.0 if d["toPerp"] else -1.0
                perp[users[0]] += s * amt()
                spot[users[0]] -= s * amt()
            elif t == "internalTransfer":
                perp[d["user"]] -= amt() + amt("fee")
                perp[d["destination"]] += amt()
                ext["other_fee"] += amt("fee")
            elif t == "subAccountTransfer":
                perp[d["user"]] -= amt()
                perp[d["destination"]] += amt()
            elif t == "vaultDeposit":
                depositor = [u for u in users if u != d["vault"]]
                perp[depositor[0] if depositor else d["vault"]] -= amt()
                perp[d["vault"]] += amt()
            elif t == "vaultWithdraw":
                perp[d["user"]] += amt("netWithdrawnUsd")
                perp[d["vault"]] -= amt("netWithdrawnUsd") + amt("commission")
            elif t == "vaultLeaderCommission":
                perp[d["user"]] += amt()
            elif t == "vaultDistribution":
                perp[users[0]] += amt()
                perp[d["vault"]] -= amt()
            elif t == "vaultCreate":
                creator = [u for u in users if u != d["vault"]][0]
                perp[creator] -= amt() + amt("fee")
                perp[d["vault"]] += amt()
                ext["other_fee"] += amt("fee")
            elif t == "rewardsClaim":
                if d.get("token") == "USDC":
                    rewards[users[0]] += float(d["amount"])
            elif t == "spotTransfer":
                if d.get("token") == "USDC":
                    spot[d["user"]] -= float(d["amount"])
                    spot[d["destination"]] += float(d["amount"])
            elif t == "send":
                # USDC moved between a user's spot balance and a perp dex, or between users.
                # Account values are summed over perp dexes, so any dex other than "spot" is perp.
                if d.get("token") == "USDC":
                    src = spot if d.get("sourceDex") == "spot" else perp
                    dst = spot if d.get("destinationDex") == "spot" else perp
                    src[d["user"]] -= float(d["amount"])
                    dst[d["destination"]] += float(d["amount"])
            elif t == "accountActivationGas":
                if d.get("token") == "USDC":
                    spot[users[0]] -= float(d["amount"])
                    ext["other_fee"] += float(d["amount"])
            elif t not in ("liquidation", "cStakingTransfer", "deployGasAuction", "spotGenesis"):
                unmodelled[t] += 1
    return dict(perp), dict(spot), dict(funding), dict(rewards), dict(ext), dict(unmodelled)


def snapshot(root, date):
    def one(sub):
        files = glob.glob(os.path.join(root, sub, f"date={date}", "*.parquet"))
        if len(files) != 1:
            raise SystemExit(f"expected one snapshot file in {root}/{sub}/date={date}/")
        return files[0]
    av_file = one("account_values")
    block, ts = (int(x) for x in os.path.basename(av_file).split(".")[0].split("_"))
    av = pd.read_parquet(av_file).groupby("user").account_value.sum()
    pos = pd.read_parquet(one(os.path.join("perp", "all")))
    pos = (np.sign(pos["size"]) * pos.notional.abs()).groupby(pos.user).sum()
    spot = pd.read_parquet(one("spot"))
    usdc = spot[spot.token == "USDC"].groupby("user").balance.sum()
    return dict(block=block, ts=ts, av=av, pos=pos, usdc=usdc)


def fund_only(args, quote, pool):
    """The Assistance Fund's unrecorded inflow per window, from spot snapshots and fills alone.

    The fund has no ledger events, so its inflow is its USDC balance change plus what it
    spent on spot purchases. This works for dates before misc_events_by_block starts
    (2025-09-27), where the full audit cannot run.
    """
    snaps = []
    for date in args.dates:
        files = glob.glob(os.path.join(args.snapshots_dir, "spot", f"date={date}", "*.parquet"))
        if len(files) != 1:
            raise SystemExit(f"expected one spot snapshot for {date}")
        block, ts = (int(x) for x in os.path.basename(files[0]).split(".")[0].split("_"))
        spot = pd.read_parquet(files[0])
        usdc = spot[(spot.user == ASSISTANCE_FUND) & (spot.token == "USDC")].balance.sum()
        snaps.append(dict(block=block, ts=ts, usdc=usdc))
    print("Assistance Fund inflow between consecutive spot snapshots (fills and snapshots only)")
    print(f"  {'window (UTC)':<28}{'net fees':>12}{'fund inflow':>14}{'share':>9}{'ADL-closed accounts':>21}"
          f"{'received above markPx':>23}")
    rows = []
    for s0, s1 in zip(snaps, snaps[1:]):
        jobs = [(os.path.join(args.fills_dir, d, f"{h}.lz4"), s0["block"], s1["block"], quote)
                for d, h in hours(s0["ts"], s1["ts"])]
        fees = builder = fund_cash = 0.0
        closed = collections.defaultdict(float)
        for agg, c, bf in pool.map(fills_worker, jobs):
            fees += sum(v[1] + v[3] for v in agg.values())
            fund_cash += agg.get(ASSISTANCE_FUND, [0, 0, 0, 0])[2]
            builder += bf
            add(closed, c)
        inflow = (s1["usdc"] - s0["usdc"]) - fund_cash
        label = (f"{pd.Timestamp(s0['ts'], unit='ms').strftime('%m-%d %H:%M')} to "
                 f"{pd.Timestamp(s1['ts'], unit='ms').strftime('%m-%d %H:%M')}")
        rows.append(dict(window=label, net_fees=fees - builder, fund_inflow=inflow,
                         adl_closed_accounts=len(closed), adl_received_above_mark=sum(closed.values())))
        print(f"  {label:<28}{fees - builder:>12,.0f}{inflow:>14,.0f}{inflow / (fees - builder):>9.4f}"
              f"{len(closed):>21,}{sum(closed.values()):>23,.2f}")
    out = pd.DataFrame(rows)
    quiet = out[out.adl_closed_accounts == 0]
    share = quiet.fund_inflow.sum() / quiet.net_fees.sum()
    print(f"  share over the {len(quiet)} windows with no auto-deleveraging: {share:.4f} "
          f"(range {(quiet.fund_inflow / quiet.net_fees).min():.4f} to {(quiet.fund_inflow / quiet.net_fees).max():.4f})")
    for r in out[out.adl_closed_accounts > 0].itertuples():
        excess = r.fund_inflow - share * r.net_fees
        print(f"  {r.window}: fund inflow beyond that share ${excess:,.0f}, "
              f"{excess / r.adl_received_above_mark:.0%} of the ${r.adl_received_above_mark:,.0f} "
              f"the {r.adl_closed_accounts} closed-out accounts received above markPx")
    out.round(2).to_csv(os.path.join(args.out_dir, "fund_inflow_by_window.csv"), index=False)


def hours(t0, t1, before=0, after=0):
    a = pd.Timestamp(t0, unit="ms", tz="UTC").floor("h") - pd.Timedelta(hours=before)
    b = pd.Timestamp(t1, unit="ms", tz="UTC").floor("h") + pd.Timedelta(hours=after)
    return [(h.strftime("%Y%m%d"), h.hour) for h in pd.date_range(a, b, freq="h")]


def add(total, part):
    for k, v in part.items():
        total[k] += v


def audit(args, s0, s1, quote, pool):
    b0, b1, t0, t1 = s0["block"], s1["block"], s0["ts"], s1["ts"]
    fills = collections.defaultdict(lambda: np.zeros(4))
    closed, builder = collections.defaultdict(float), 0.0
    jobs = [(os.path.join(args.fills_dir, d, f"{h}.lz4"), b0, b1, quote) for d, h in hours(t0, t1)]
    for agg, c, bf in pool.map(fills_worker, jobs):
        for k, v in agg.items():
            fills[k] += v
        add(closed, c)
        builder += bf
    perp, spot, funding, rewards, ext = (collections.defaultdict(float) for _ in range(5))
    unmodelled = collections.Counter()
    # withdrawals are logged after they are requested, so read a few hours past the window
    jobs = [(os.path.join(args.misc_dir, d, f"{h}.lz4"), b0, b1, t0, t1) for d, h in hours(t0, t1, before=1, after=3)
            if os.path.exists(os.path.join(args.misc_dir, d, f"{h}.lz4"))]
    for p, s, f, r, e, u in pool.map(misc_worker, jobs):
        add(perp, p), add(spot, s), add(funding, f), add(rewards, r), add(ext, e)
        unmodelled.update(u)

    df = pd.DataFrame.from_dict(fills, orient="index", columns=["perp_cash", "perp_fee", "spot_cash", "spot_fee"])
    for name, series in [("perp_flow", perp), ("spot_flow", spot), ("funding", funding), ("rewards", rewards),
                         ("av0", s0["av"]), ("av1", s1["av"]), ("pos0", s0["pos"]), ("pos1", s1["pos"]),
                         ("usdc0", s0["usdc"]), ("usdc1", s1["usdc"])]:
        df = df.join(pd.Series(series, name=name, dtype=float), how="outer")
    df = df.fillna(0.0)
    df["perp_unexplained"] = ((df.av1 - df.pos1) - (df.av0 - df.pos0)
                              - (df.perp_cash - df.perp_fee + df.funding + df.perp_flow))
    df["spot_unexplained"] = (df.usdc1 - df.usdc0) - (df.spot_cash - df.spot_fee + df.spot_flow) - df.rewards
    df["cls"] = "all other accounts"
    df.loc[df.index.isin(set(closed) - VAULT_ADDRESSES), "cls"] = "other ADL-closed accounts"
    df.loc[df.index.isin(VAULT_ADDRESSES), "cls"] = "liquidator vaults"
    df.loc[df.index == ASSISTANCE_FUND, "cls"] = "Assistance Fund"
    df.loc[df.index == HLP_PARENT, "cls"] = "HLP parent"

    by_class = df.groupby("cls").agg(accounts=("av0", "size"), perp_unexplained=("perp_unexplained", "sum"),
                                     spot_unexplained=("spot_unexplained", "sum")).reindex(CLASSES).fillna(0.0)
    rest = df[df.cls == "all other accounts"]
    fees = df.perp_fee.sum() + df.spot_fee.sum()
    total0 = s0["av"].sum() + s0["usdc"].sum()
    total1 = s1["av"].sum() + s1["usdc"].sum()
    window = dict(
        start=pd.Timestamp(t0, unit="ms", tz="UTC"), end=pd.Timestamp(t1, unit="ms", tz="UTC"),
        block_start=b0, block_end=b1, accounts=len(df),
        total_usdc_start=total0, total_usdc_end=total1,
        deposits=ext["deposit"], withdrawals=ext["withdraw"], withdrawal_fees=ext["withdraw_fee"],
        conservation_residual=(total1 - total0) - (ext["deposit"] - ext["withdraw"] - ext["withdraw_fee"]),
        usdc_fees=fees, builder_fees=builder, net_fees=fees - builder, rewards_claimed=df.rewards.sum(),
        adl_closed_accounts=len(closed), adl_received_above_mark=sum(closed.values()),
        removed_from_adl_closed=-(by_class.at["liquidator vaults", "perp_unexplained"]
                                  + by_class.at["other ADL-closed accounts", "perp_unexplained"]) if closed else 0.0,
        fund_unrecorded_inflow=by_class.at["Assistance Fund", "spot_unexplained"],
        hlp_parent_unrecorded_inflow=by_class.at["HLP parent", "perp_unexplained"],
        rest_within_1_dollar=int((rest.perp_unexplained.abs() <= 1).sum()),
        rest_largest_abs=float(rest.perp_unexplained.abs().max()),
        unmodelled_ledger_events=sum(unmodelled.values()),
    )
    return by_class, window


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fills-dir", required=True)
    ap.add_argument("--misc-dir", help="not needed with --fund-only")
    ap.add_argument("--snapshots-dir", required=True)
    ap.add_argument("--dates", nargs="+", required=True, help="snapshot dates, YYYY-MM-DD, in order")
    ap.add_argument("--fund-only", action="store_true",
                    help="measure only the Assistance Fund's inflow, from spot snapshots and fills; "
                         "needs no misc events, so it also works before 2025-09-27")
    ap.add_argument("--out-dir", default=DATA,
                    help="where to write the two CSVs (default: data/, the bundled copies)")
    args = ap.parse_args()

    req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps({"type": "spotMeta"}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        meta = json.load(resp)
    token = {t["index"]: t["name"] for t in meta["tokens"]}
    quote = {u["name"]: token[u["tokens"][1]] for u in meta["universe"]}

    if args.fund_only:
        with ProcessPoolExecutor(max_workers=6) as pool:
            fund_only(args, quote, pool)
        return
    if not args.misc_dir:
        raise SystemExit("--misc-dir is required without --fund-only")
    snaps = [snapshot(args.snapshots_dir, d) for d in args.dates]
    classes, windows = [], []
    print("Test 19b: every account between consecutive snapshots")
    with ProcessPoolExecutor(max_workers=6) as pool:
        for s0, s1 in zip(snaps, snaps[1:]):
            by_class, w = audit(args, s0, s1, quote, pool)
            label = f"{w['start'].strftime('%m-%d %H:%M')} to {w['end'].strftime('%m-%d %H:%M')}"
            classes.append(by_class.reset_index().assign(window=label))
            windows.append(dict(window=label, **w))
            print(f"\n  {label} UTC   blocks {w['block_start']} to {w['block_end']}   {w['accounts']:,} accounts")
            print(f"    {'class':<28}{'accounts':>10}{'perp unrecorded':>20}{'spot USDC unrecorded':>24}")
            for cls, r in by_class.iterrows():
                print(f"    {cls:<28}{int(r.accounts):>10,}{r.perp_unexplained:>20,.2f}{r.spot_unexplained:>24,.2f}")
            print(f"    all other accounts within $1: {w['rest_within_1_dollar']:,}; largest ${w['rest_largest_abs']:,.0f}")
            print(f"    total USDC owed to accounts {w['total_usdc_start']:,.0f} -> {w['total_usdc_end']:,.0f}; "
                  f"deposits {w['deposits']:,.0f}, withdrawals {w['withdrawals']:,.0f}; "
                  f"change not explained by them {w['conservation_residual']:,.0f}")
            print(f"    USDC fees {w['usdc_fees']:,.0f}, builder fees {w['builder_fees']:,.0f}, net {w['net_fees']:,.0f}; "
                  f"fund inflow / net fees {w['fund_unrecorded_inflow'] / w['net_fees']:.4f}; "
                  f"HLP parent inflow / net fees {w['hlp_parent_unrecorded_inflow'] / w['net_fees']:.4f}")
            if w["adl_closed_accounts"]:
                print(f"    ADL closed out {w['adl_closed_accounts']:,} accounts; removed from them "
                      f"${w['removed_from_adl_closed']:,.2f} of ${w['adl_received_above_mark']:,.2f} received above markPx")

    pd.concat(classes)[["window", "cls", "accounts", "perp_unexplained", "spot_unexplained"]].round(2).to_csv(
        os.path.join(args.out_dir, "account_audit_by_class.csv"), index=False)
    out = pd.DataFrame(windows)
    num = out.select_dtypes("number").columns
    out[num] = out[num].round(2)
    out.to_csv(os.path.join(args.out_dir, "account_audit_windows.csv"), index=False)
    print(f"\nWrote account_audit_by_class.csv and account_audit_windows.csv to {args.out_dir}")


if __name__ == "__main__":
    main()
