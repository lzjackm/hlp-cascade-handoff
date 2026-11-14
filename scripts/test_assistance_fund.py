#!/usr/bin/env python3
"""Test 20: the Assistance Fund's income against market-wide fees.

The Assistance Fund (0xfefe...fefe) receives trading fees and spends them buying
HYPE on the spot book. No ledger event records money arriving in it. Two things
about it are public, and this test uses both:

  1. Its spending, fill by fill. It bids for HYPE in orders of at most $10,000
     and has no other kind of fill in these weeks.
  2. Hyperliquid's own running total for it. The fund's perp account value stays
     near $1,000 to $2,000 while its perpAllTime PnL series from the public
     `portfolio` endpoint climbs by tens of millions a week, so that series is
     HL's tally of what has passed through the account. For Oct 2025 the API
     now serves it as weekly samples.

If the fund's income is only fees, both should track market-wide fees at a
steady share. The test measures that share over Oct 1 to Oct 22 and compares the
excess around the vaults' self-liquidation rounds with the balances removed from
the vaults (Test 14).

Inputs, all bundled:
    data/assistance_fund_vs_fees_5min.csv   built by extract_fee_tables.py
    data/assistance_fund_pnl_samples.csv    --pull refreshes it from the HL info API
    data/adl_closeouts_by_account_type.csv  built by extract_fee_tables.py

Usage:
    python scripts/test_assistance_fund.py [--pull]
"""
import argparse
import json
import os
import sys
import urllib.request

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_self_liquidation import DATA, VAULTS, read_csv, walk  # noqa: E402

INFO_URL = "https://api.hyperliquid.xyz/info"
ASSISTANCE_FUND = "0xfefefefefefefefefefefefefefefefefefefefe"
FIRST_ROUND = pd.Timestamp("2025-10-10 21:16:04.831", tz="UTC")
EXCESS_START = pd.Timestamp("2025-10-10 21:15", tz="UTC")
EXCESS_END = pd.Timestamp("2025-10-10 23:20", tz="UTC")
CHECKPOINTS = ["2025-10-10 21:20", "2025-10-10 21:30", "2025-10-10 22:00", "2025-10-10 22:30",
               "2025-10-10 23:00", "2025-10-10 23:20", "2025-10-11 00:00", "2025-10-12 00:00",
               "2025-10-15 00:00", "2025-10-19 00:00", "2025-10-23 00:00"]


def utc(s):
    return pd.Timestamp(s, tz="UTC")


def pull_samples():
    body = json.dumps({"type": "portfolio", "user": ASSISTANCE_FUND}).encode()
    req = urllib.request.Request(INFO_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        series = dict(json.load(resp))["perpAllTime"]
    rows = [dict(ts=pd.Timestamp(t, unit="ms", tz="UTC"), pnl=float(p), account_value=float(a[1]))
            for (t, p), a in zip(series["pnlHistory"], series["accountValueHistory"])]
    pd.DataFrame(rows).to_csv(os.path.join(DATA, "assistance_fund_pnl_samples.csv"), index=False)
    print(f"Wrote {len(rows)} rows to data/assistance_fund_pnl_samples.csv")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pull", action="store_true",
                    help="refresh the fund's PnL samples from the HL info API first")
    if ap.parse_args().pull:
        pull_samples()

    x = read_csv("assistance_fund_vs_fees_5min.csv").set_index("ts").sort_index()
    x["net_fee"] = x.fee_usdc - x.builder_fee
    samples = read_csv("assistance_fund_pnl_samples.csv")
    adl = pd.read_csv(os.path.join(DATA, "adl_closeouts_by_account_type.csv")).set_index("account_type")
    removed = {v: walk(v)[0].removed.sum() for v in VAULTS}

    def span(a, b):
        return x[(x.index >= a) & (x.index < b)]

    print("Test 20: the Assistance Fund's income against market-wide fees")
    print()
    print("  Purchases against USDC fees net of builder fees")
    print(f"    {'span (UTC)':<24}{'USDC fees':>13}{'builder fees':>14}{'net fees':>13}"
          f"{'fund purchases':>16}{'share of net':>14}")
    days = pd.date_range(x.index[0].normalize(), x.index[-1].normalize(), freq="D")
    rows = [(d.strftime("Oct %-d"), d, d + pd.Timedelta(days=1)) for d in days
            if d != FIRST_ROUND.normalize()]
    rows += [("Oct 10 00:00 to 21:15", utc("2025-10-10"), EXCESS_START),
             ("Oct 10 21:15 to 23:20", EXCESS_START, EXCESS_END),
             ("Oct 10 23:20 to 24:00", EXCESS_END, utc("2025-10-11"))]
    for label, a, b in sorted(rows, key=lambda r: r[1]):
        w = span(a, b)
        print(f"    {label:<24}{w.fee_usdc.sum():>13,.0f}{w.builder_fee.sum():>14,.0f}"
              f"{w.net_fee.sum():>13,.0f}{w.af_buy_usdc.sum():>16,.0f}"
              f"{w.af_buy_usdc.sum() / w.net_fee.sum():>14.3f}")

    base = x[x.index < EXCESS_START]
    share = base.af_buy_usdc.sum() / base.net_fee.sum()
    cum = (x.af_buy_usdc - share * x.net_fee).cumsum()
    cum -= cum[cum.index < EXCESS_START].iloc[-1]
    before, after = cum[cum.index < EXCESS_START], cum[cum.index >= EXCESS_END]
    later = x[x.index >= EXCESS_END]
    print()
    print(f"    share over the {len(base) / 288:.1f} days before 21:15 on Oct 10: {share:.4f}")
    print(f"    share over the {len(later) / 288:.1f} days after 23:20 on Oct 10:  "
          f"{later.af_buy_usdc.sum() / later.net_fee.sum():.4f}")
    print("    cumulative purchases minus share x net fees, relative to 21:15 on Oct 10:")
    print(f"      before: between ${before.min():,.0f} and ${before.max():,.0f}")
    for m in CHECKPOINTS:
        print(f"      up to {utc(m).strftime('%m-%d %H:%M')}   ${cum[cum.index < utc(m)].iloc[-1]:>14,.0f}")
    by_spend = cum[(cum.index >= EXCESS_END) & (cum.index < utc("2025-10-12"))].mean()
    print(f"      Oct 10 23:20 to Oct 12: mean ${by_spend:,.0f}; "
          f"Oct 10 23:20 to Oct 23: between ${after.min():,.0f} and ${after.max():,.0f}")

    print()
    print("  Hyperliquid's own running total for the fund (perpAllTime PnL), by sample interval")
    s = samples[(samples.ts >= x.index[0]) & (samples.ts <= x.index[-1] + pd.Timedelta(minutes=5))]
    s = s.reset_index(drop=True)
    print(f"    {'interval (UTC)':<30}{'HL tally change':>17}{'fund purchases':>16}{'net fees':>13}"
          f"{'tally / net fees':>18}")
    weeks = []
    for i in range(1, len(s)):
        a, b = s.ts[i - 1], s.ts[i]
        w = span(a.floor("5min"), b.floor("5min"))
        d = s.pnl[i] - s.pnl[i - 1]
        weeks.append(dict(tally=d, net=w.net_fee.sum(), event=a < FIRST_ROUND < b))
        print(f"    {a.strftime('%m-%d %H:%M')} to {b.strftime('%m-%d %H:%M'):<15}{d:>17,.0f}"
              f"{w.af_buy_usdc.sum():>16,.0f}{w.net_fee.sum():>13,.0f}{d / w.net_fee.sum():>18.3f}")
    control = [w for w in weeks if not w["event"]]
    event = [w for w in weeks if w["event"]]
    ratio = sum(w["tally"] for w in control) / sum(w["net"] for w in control)
    by_tally = event[0]["tally"] - ratio * event[0]["net"]
    print(f"    tally / net fees over the other intervals: {ratio:.4f}")
    print(f"    interval containing Oct 10: tally minus that ratio x net fees = ${by_tally:,.0f}")
    print(f"    fund account value at those samples: {', '.join(f'${v:,.0f}' for v in s.account_value)}")

    print()
    print("  Size")
    vaults = sum(removed.values())
    other_cap = -adl.at["other accounts", "closeout_value_mark_minus_px"]
    print(f"    excess by the fund's purchases                              ${by_spend:>14,.0f}")
    print(f"    excess by HL's own tally                                    ${by_tally:>14,.0f}")
    print(f"    removed from the two vaults (Test 14)                       ${vaults:>14,.2f}")
    print(f"    excess not accounted for by the vaults                      ${by_spend - vaults:>14,.0f}")
    print(f"    other accounts closed out by ADL in the peak hour: {int(adl.at['other accounts', 'accounts']):,} accounts, "
          f"${adl.at['other accounts', 'closeout_notional_px']:,.0f} notional,")
    print(f"      received above markPx (cap on what was removed from them) ${other_cap:>14,.0f}")
    print(f"    vaults alone to vaults plus that cap: ${vaults:,.0f} to ${vaults + other_cap:,.0f}")


if __name__ == "__main__":
    main()
