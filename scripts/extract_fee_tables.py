#!/usr/bin/env python3
"""Rebuild the market-wide tables behind Tests 19 and 20 from raw node fills.

Reads hourly node_fills_by_block files for Oct 1 to Oct 22 2025 and writes:

    data/assistance_fund_vs_fees_5min.csv   market-wide USDC fees, builder fees, volume
                                            and Assistance Fund purchases, 5-minute bins
    data/adl_closeouts_by_account_type.csv  every auto-deleveraging close-out in the peak
                                            hour (Oct 10 21:00-22:00 UTC), summed for
                                            Liquidator, Liquidator 2 and all other accounts
    data/from_raw/adl_closed_accounts.csv   the 25 largest of those other accounts
    data/from_raw/adl_counterparties.csv    the 25 largest traders auto-deleveraged
                                            against the vaults

The first two hold no third-party address and are bundled. The two under
data/from_raw/ list accounts, are gitignored, and feed test_adl_accounts.py.

Point --raw-dir at a directory laid out like the S3 prefix, RAW/YYYYMMDD/H.lz4:

    for d in $(seq 20251001 20251022); do
      aws s3 cp --recursive --request-payer requester \\
        s3://hl-mainnet-node-data/node_fills_by_block/hourly/$d/ RAW/$d/
    done
    python scripts/extract_fee_tables.py --raw-dir RAW

The 22 days are about 14.5 GB. Needs the lz4 command-line tool. Runtime is about
one minute on six cores.

Every trade appears twice in the files, once per side. What is measured:
    fee_usdc      sum of `fee` over fills with feeToken == "USDC". Maker rebates are
                  negative and included. Builder fees are inside `fee`.
    builder_fee   sum of `builderFee`, the part of `fee` paid on to the order's builder.
    volume        sum of px * sz over the buy side of each trade.
    af_buy_usdc   sum of px * sz over buys by the Assistance Fund (0xfefe...fefe). It
                  buys HYPE on the spot book and has no other kind of fill in these days.
Close-outs are read from the auto-deleveraged side (dir "Auto-Deleveraging"), which
carries liquidation.liquidatedUser and liquidation.markPx. closeout_value_mark_minus_px
is what the closed positions were worth at markPx minus what they were closed at.
"""
import argparse
import collections
import json
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data")
DAYS = [f"202510{d:02d}" for d in range(1, 23)]
PEAK = ("20251010", 21)
ASSISTANCE_FUND = "0xfefefefefefefefefefefefefefefefefefefefe"
VAULTS = {
    "0x2e3d94f0562703b25c83308a05046ddaf9a8dd14": "Liquidator",
    "0xb0a55f13d22f66e6d495ac98113841b2326e9540": "Liquidator 2",
}
BIN_MS = 5 * 60 * 1000


def parse_hour(args):
    path, peak = args
    bins = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])  # volume, fee, af buys, builder
    adl = collections.defaultdict(lambda: [0, 0.0, 0.0, set(), 0])  # fills, notional, mark-px, txs, time
    counterparties = collections.defaultdict(float)
    counterparty_fee = 0.0
    proc = subprocess.Popen(["lz4", "-dc", path], stdout=subprocess.PIPE)
    for line in proc.stdout:
        if not line.strip():
            continue
        for user, f in json.loads(line)["events"]:
            notional = float(f["px"]) * float(f["sz"])
            b = bins[f["time"] // BIN_MS * BIN_MS]
            if f["side"] == "B":
                b[0] += notional
                if user == ASSISTANCE_FUND:
                    b[2] += notional
            if f.get("feeToken", "USDC") == "USDC":
                b[1] += float(f["fee"])
            if f.get("builderFee"):
                b[3] += float(f["builderFee"])
            liq = f.get("liquidation")
            if peak and liq and f["dir"] == "Auto-Deleveraging":
                a = adl[liq["liquidatedUser"]]
                sign = 1.0 if f["side"] == "B" else -1.0  # the trader buys when a long is closed
                a[0] += 1
                a[1] += notional
                a[2] += (float(liq["markPx"]) - float(f["px"])) * float(f["sz"]) * sign
                a[3].add(f["hash"])
                a[4] = min(a[4] or f["time"], f["time"])
                if liq["liquidatedUser"] in VAULTS:
                    counterparties[user] += notional
                    counterparty_fee += float(f["fee"])
    if proc.wait() != 0:
        raise RuntimeError(f"lz4 failed on {path}")
    adl = {k: [v[0], v[1], v[2], sorted(v[3]), v[4]] for k, v in adl.items()}
    return dict(bins), adl, dict(counterparties), counterparty_fee


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--raw-dir", required=True, help="directory holding YYYYMMDD/H.lz4")
    raw = ap.parse_args().raw_dir
    jobs = []
    for day in DAYS:
        for hour in range(24):
            path = os.path.join(raw, day, f"{hour}.lz4")
            if not os.path.exists(path):
                raise SystemExit(f"missing {path}")
            jobs.append((path, (day, hour) == PEAK))

    bins = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    adl, counterparties, counterparty_fee = {}, {}, 0.0
    with ProcessPoolExecutor(max_workers=6) as pool:
        for b, a, c, fee in pool.map(parse_hour, jobs):
            for t, v in b.items():
                for i in range(4):
                    bins[t][i] += v[i]
            if a:
                adl, counterparties, counterparty_fee = a, c, fee

    five = pd.DataFrame(
        [(pd.Timestamp(t, unit="ms", tz="UTC"), *v) for t, v in sorted(bins.items())],
        columns=["ts", "volume", "fee_usdc", "af_buy_usdc", "builder_fee"],
    )
    start = pd.Timestamp(DAYS[0], tz="UTC")
    end = pd.Timestamp(DAYS[-1], tz="UTC") + pd.Timedelta(days=1)
    five = five[(five.ts >= start) & (five.ts < end)]
    five = five.round({"volume": 6, "fee_usdc": 6, "af_buy_usdc": 6, "builder_fee": 6})
    five.to_csv(os.path.join(DATA, "assistance_fund_vs_fees_5min.csv"), index=False)
    print(f"Wrote {len(five)} rows to data/assistance_fund_vs_fees_5min.csv")
    print(f"    USDC fees ${five.fee_usdc.sum():,.0f} (builder fees ${five.builder_fee.sum():,.0f}), "
          f"Assistance Fund purchases ${five.af_buy_usdc.sum():,.0f}, volume ${five.volume.sum():,.0f}")

    rows = collections.defaultdict(lambda: [set(), set(), 0, 0.0, 0.0])
    for user, (n, notional, mark_minus_px, txs, _) in adl.items():
        r = rows[VAULTS.get(user, "other accounts")]
        r[0].add(user)
        r[1].update(txs)
        r[2] += n
        r[3] += notional
        r[4] += mark_minus_px
    summary = pd.DataFrame(
        [(k, len(v[0]), len(v[1]), v[2], round(v[3], 2), round(v[4], 2)) for k, v in sorted(rows.items())],
        columns=["account_type", "accounts", "transactions", "fills",
                 "closeout_notional_px", "closeout_value_mark_minus_px"],
    )
    summary.to_csv(os.path.join(DATA, "adl_closeouts_by_account_type.csv"), index=False)
    print(f"Wrote {len(summary)} rows to data/adl_closeouts_by_account_type.csv")
    print(summary.to_string(index=False))

    os.makedirs(os.path.join(DATA, "from_raw"), exist_ok=True)
    closed = pd.DataFrame(
        [(user, pd.Timestamp(v[4], unit="ms", tz="UTC"), len(v[3]), v[0], round(v[1], 2), round(v[2], 2))
         for user, v in adl.items() if user not in VAULTS],
        columns=["account", "first_closeout_ts", "transactions", "fills",
                 "closeout_notional_px", "closeout_value_mark_minus_px"],
    ).sort_values("closeout_notional_px", ascending=False).head(25)
    closed.to_csv(os.path.join(DATA, "from_raw", "adl_closed_accounts.csv"), index=False)
    traders = pd.DataFrame(sorted(counterparties.items(), key=lambda kv: -kv[1])[:25],
                           columns=["account", "closeout_notional_px"])
    traders.to_csv(os.path.join(DATA, "from_raw", "adl_counterparties.csv"), index=False)
    print("Wrote the 25 largest accounts on each side to data/from_raw/ (not bundled)")

    total = sum(counterparties.values())
    ranked = sorted(counterparties.values(), reverse=True)
    print(f"Traders auto-deleveraged against the vaults: {len(counterparties):,} accounts, "
          f"${total:,.0f} notional, fees charged ${counterparty_fee:,.2f}")
    print(f"    largest 10 accounts {sum(ranked[:10]) / total:.1%} of notional, "
          f"largest 300 {sum(ranked[:300]) / total:.1%}")


if __name__ == "__main__":
    main()
