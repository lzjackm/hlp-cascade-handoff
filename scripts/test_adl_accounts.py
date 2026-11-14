#!/usr/bin/env python3
"""Tests 18b and 21: accounts on both sides of the auto-deleveraging close-outs,
checked against their own fills through the public HL info API.

Test 18b, the largest auto-deleveraged traders. Test 18 shows that small accounts
untouched since the cascade hold exactly their fill-implied cash. This runs the
complementary check on the largest traders auto-deleveraged against the vaults,
where a pro-rata share of the removed balance would be hundreds of thousands of
dollars. If they had been credited anything beyond the fill, HL's reported PnL
for them would exceed what their own fills, fees and funding add up to.

Test 21, the other accounts closed out by auto-deleveraging. The vaults were not
the only accounts closed out that hour. If the same removal applied to the
others, HL's reported PnL for them would fall short of their own fills, by no
more than what their close-out fills received above markPx (part of that only
brings an underwater account back to zero).

For each account the script takes HL's `portfolio` PnL samples (perpAllTime),
finds the nearest sample on each side of the event at which the account held no
position, and compares

    HL PnL change between the two samples
    sum(closedPnl) - sum(fees) + sum(funding) between the two samples

With no position at either end there is no unrealised PnL, so the two are equal
unless something moved outside the fills. An account is skipped, with the reason
printed, when the API no longer serves its Oct 2025 fills, serves them with gaps
(a fill that does not start where the previous fill on that coin ended), or has
no sample at which it was flat on both sides.

The account lists are not bundled. Build them from raw node fills first:

    python scripts/extract_fee_tables.py --raw-dir RAW
    python scripts/test_adl_accounts.py [--traders 3] [--closed 25]

or pass addresses with --trader / --closed-account. Needs network access. The API
pages fills 2,000 at a time and throttles; the defaults take about 25 minutes.
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_self_liquidation import DATA, VAULTS, walk  # noqa: E402

INFO_URL = "https://api.hyperliquid.xyz/info"
EVENT_START = pd.Timestamp("2025-10-10 21:16", tz="UTC")
EVENT_END = pd.Timestamp("2025-10-10 21:21", tz="UTC")
FROM_RAW = os.path.join(DATA, "from_raw")
_last_call = [0.0]


def post(body, interval=0.7):
    for attempt in range(8):
        wait = interval - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        r = requests.post(INFO_URL, json=body, timeout=40)
        _last_call[0] = time.time()
        if r.status_code == 200:
            return r.json()
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{body['type']} failed: HTTP {r.status_code}")


def paged(kind, user, end_ms, page_size):
    out, seen, cursor = [], set(), 0
    while cursor <= end_ms:
        page = post({"type": kind, "user": user, "startTime": cursor, "endTime": end_ms})
        fresh = 0
        for e in page:
            key = e["tid"] if kind == "userFillsByTime" else (e["time"], e["delta"]["coin"])
            if key not in seen:
                seen.add(key)
                out.append(e)
                fresh += 1
        if len(page) < page_size or fresh == 0:
            break
        cursor = max(e["time"] for e in page)
    return out


class Account:
    """An account's PnL samples, perp fills and funding from the public API."""

    def __init__(self, user):
        portfolio = dict(post({"type": "portfolio", "user": user}))
        self.samples = [(pd.Timestamp(t, unit="ms", tz="UTC"), float(p))
                        for t, p in portfolio["perpAllTime"]["pnlHistory"]]
        end_ms = int(self.samples[-1][0].timestamp() * 1000) if self.samples else 0
        f = pd.DataFrame(paged("userFillsByTime", user, end_ms, 2000))
        if not f.empty:
            f = f[~(f.coin.str.startswith("@") | f.coin.str.contains("/"))].copy()
            f["ts"] = pd.to_datetime(f.time, unit="ms", utc=True)
            for col in ("px", "sz", "closedPnl", "fee", "startPosition"):
                f[col] = f[col].astype(float)
            signed = np.where(f.side == "B", 1, -1) * f.sz
            f["end_position"] = f.startPosition + signed
            f["cash"] = -signed * f.px
            f = f.sort_values("time", kind="stable")
        self.fills = f
        self.funding = pd.DataFrame(
            [dict(ts=pd.Timestamp(e["time"], unit="ms", tz="UTC"), usdc=float(e["delta"]["usdc"]))
             for e in paged("userFunding", user, end_ms, 500)], columns=["ts", "usdc"])

    def flat_at(self, t):
        """No open position at t, judging only from the fills the API serves.

        A coin's position at t is where its last fill before t left it, or, with no
        earlier fill, where its first later fill started from.
        """
        for _, g in self.fills.groupby("coin"):
            earlier = g[g.ts <= t]
            position = earlier.end_position.iloc[-1] if len(earlier) else g.startPosition.iloc[0]
            if abs(position) > 1e-9:
                return False
        return True

    @staticmethod
    def gaps(window):
        n = 0
        for _, g in window.groupby("coin"):
            n += int((np.abs(g.startPosition.values[1:] - g.end_position.values[:-1]) > 1e-6).sum())
        return n

    def across(self, start, end):
        """(result, None), or (None, reason) when the API cannot support the check."""
        if self.fills.empty or self.fills.ts.min() > start:
            return None, "the API no longer serves this account's Oct 2025 fills"
        before = [s for s in self.samples if s[0] < start and self.flat_at(s[0])]
        after = [s for s in self.samples if s[0] > end and self.flat_at(s[0])]
        if not before or not after:
            return None, "no PnL sample with no open position on both sides of the event"
        (ta, pa), (tb, pb) = before[-1], after[0]
        w = self.fills[(self.fills.ts > ta) & (self.fills.ts <= tb)]
        if self.gaps(w):
            return None, "the fill history the API serves for this account has gaps"
        fw = self.funding[(self.funding.ts > ta) & (self.funding.ts <= tb)]
        from_fills = w.closedPnl.sum() - w.fee.sum() + fw.usdc.sum()
        return dict(ta=ta, tb=tb, window=w, hl=pb - pa, from_fills=from_fills,
                    difference=pb - pa - from_fills), None


def test_18b(traders, rate):
    print("Test 18b: the largest auto-deleveraged traders, HL-reported PnL against their own fills")
    for user in traders:
        acct = Account(user)
        res, why = acct.across(EVENT_START, EVENT_END)
        label = user[:10] + "..."
        if res is None:
            print(f"    {label}  {why}")
            continue
        adl = res["window"][res["window"].dir == "Auto-Deleveraging"]
        share = notional = 0.0
        for _, f in adl.iterrows():
            vault = rate.get((f.get("liquidation") or {}).get("liquidatedUser"))
            if vault is not None:
                notional += f.px * f.sz
                share += f.px * f.sz * vault
        if notional == 0:
            print(f"    {label}  the API does not serve this account's fills at the close-outs")
            continue
        print(f"    {label}  flat samples {res['ta'].strftime('%Y-%m-%d %H:%M')} to "
              f"{res['tb'].strftime('%Y-%m-%d %H:%M')} UTC, {len(res['window']):,} fills in between")
        print(f"        close-out notional against the vaults   ${notional:>16,.0f}")
        print(f"        HL PnL change                           ${res['hl']:>19,.2f}")
        print(f"        sum(closedPnl) - fees + funding         ${res['from_fills']:>19,.2f}")
        print(f"        difference                              ${res['difference']:>19,.2f}")
        print(f"        pro-rata share by notional would be     ${share:>16,.0f}")
        opened_flat = bool((acct.fills.groupby("coin").startPosition.first().abs() < 1e-9).all())
        if opened_flat and acct.flat_at(acct.samples[-1][0]):
            cash = acct.fills.cash.sum() - acct.fills.fee.sum() + acct.funding.usdc.sum()
            print(f"        whole life: HL PnL ${acct.samples[-1][1]:,.2f}, fill-price cash - fees + funding "
                  f"${cash:,.2f} (difference ${acct.samples[-1][1] - cash:,.2f})")


def test_20(closed, total_cap, n_accounts):
    print("Test 21: other accounts closed out by auto-deleveraging, HL-reported PnL against their own fills")
    print(f"    {'account':<15}{'notional':>14}{'cap':>12}{'HL PnL - own fills':>21}{'of cap':>9}   flat samples (UTC)")
    done = []
    for row in closed.itertuples():
        cap = -row.closeout_value_mark_minus_px
        label = row.account[:10] + "..."
        try:
            res, why = Account(row.account).across(row.first_closeout_ts,
                                                   row.first_closeout_ts + pd.Timedelta(minutes=5))
        except RuntimeError as exc:
            res, why = None, str(exc)
        if res is None:
            print(f"    {label:<15}{row.closeout_notional_px:>14,.0f}{cap:>12,.0f}   {why}")
            continue
        done.append((cap, res["difference"]))
        print(f"    {label:<15}{row.closeout_notional_px:>14,.0f}{cap:>12,.0f}{res['difference']:>21,.0f}"
              f"{-res['difference'] / cap:>9.0%}   {res['ta'].strftime('%Y-%m-%d %H:%M')} to "
              f"{res['tb'].strftime('%Y-%m-%d %H:%M')}")
    if done:
        caps, diffs = sum(c for c, _ in done), sum(d for _, d in done)
        print(f"    accounts checked: {len(done)}; short by ${-diffs:,.0f} of a ${caps:,.0f} cap ({-diffs / caps:.0%})")
        print(f"    the same share of the ${total_cap:,.0f} cap over all {n_accounts:,} other accounts "
              f"would be ${total_cap * -diffs / caps:,.0f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--traders", type=int, default=3, help="how many of the largest traders to check")
    ap.add_argument("--closed", type=int, default=25, help="how many of the largest closed-out accounts to check")
    ap.add_argument("--trader", action="append", default=[], help="check this trader address (repeatable)")
    ap.add_argument("--closed-account", action="append", default=[],
                    help="check this closed-out account (repeatable; must be in data/from_raw/adl_closed_accounts.csv)")
    args = ap.parse_args()

    summary = pd.read_csv(os.path.join(DATA, "adl_closeouts_by_account_type.csv")).set_index("account_type")
    # removed balance per dollar of close-out notional, per vault
    rate = {VAULTS[v]["address"]: walk(v)[0].removed.sum() / summary.at[v, "closeout_notional_px"]
            for v in VAULTS}

    traders = [a.lower() for a in args.trader]
    closed = None
    closed_path = os.path.join(FROM_RAW, "adl_closed_accounts.csv")
    if not traders and not args.closed_account:
        try:
            traders = list(pd.read_csv(os.path.join(FROM_RAW, "adl_counterparties.csv")).account[:args.traders])
        except FileNotFoundError:
            raise SystemExit("data/from_raw/ is empty: run extract_fee_tables.py first, or pass --trader")
    if os.path.exists(closed_path):
        closed = pd.read_csv(closed_path)
        closed["first_closeout_ts"] = pd.to_datetime(closed.first_closeout_ts, utc=True, format="ISO8601")
        if args.closed_account:
            closed = closed[closed.account.isin([a.lower() for a in args.closed_account])]
        elif args.trader:
            closed = None
        else:
            closed = closed.head(args.closed)

    if traders:
        test_18b(traders, rate)
    if closed is not None and len(closed):
        if traders:
            print()
        test_20(closed, -summary.at["other accounts", "closeout_value_mark_minus_px"],
                int(summary.at["other accounts", "accounts"]))


if __name__ == "__main__":
    main()
