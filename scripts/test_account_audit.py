#!/usr/bin/env python3
"""Test 19b: where the removed cash went, from the audit of every account.

audit_all_accounts.py compares every account on the exchange, between consecutive
daily clearinghouse snapshots, with its own fills, fees, funding and ledger
flows. What is left over is USDC that moved with no record. Its results are
bundled as data/account_audit_by_class.csv and data/account_audit_windows.csv
(per account class, no addresses), and this script reads them.

It prints, for six consecutive windows around the cascade:
  1. unrecorded USDC by account class;
  2. the Assistance Fund's and the HLP parent's unrecorded inflow as a share of
     net trading fees, which is constant on days without auto-deleveraging;
  3. on the cascade day, each one's inflow beyond that share, against the cash
     removed from the accounts auto-deleveraging closed out.

It then prints the fund's inflow over seven windows in Aug 2025, around the only
other sizable auto-deleveraging event in the archive (Aug 26).

Runs from bundled data only, no network required.

Usage:
    python scripts/test_account_audit.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_self_liquidation import DATA  # noqa: E402


def main():
    by_class = pd.read_csv(os.path.join(DATA, "account_audit_by_class.csv"))
    w = pd.read_csv(os.path.join(DATA, "account_audit_windows.csv"))
    w["fund_share"] = w.fund_unrecorded_inflow / w.net_fees
    w["hlp_share"] = w.hlp_parent_unrecorded_inflow / w.net_fees

    print("Test 19b: every account between consecutive daily snapshots")
    print()
    print("  Unrecorded USDC by account class (perp: account value net of positions; spot: USDC balance)")
    for label in w.window:
        row = w[w.window == label].iloc[0]
        print(f"\n    {label} UTC   {int(row.accounts):,} accounts")
        print(f"      {'class':<28}{'accounts':>10}{'perp':>18}{'spot USDC':>18}")
        for r in by_class[by_class.window == label].itertuples():
            print(f"      {r.cls:<28}{int(r.accounts):>10,}{r.perp_unexplained:>18,.2f}{r.spot_unexplained:>18,.2f}")
        print(f"      all other accounts within $1: {int(row.rest_within_1_dollar):,}; "
              f"largest ${row.rest_largest_abs:,.0f}")

    print()
    print("  Conservation: change in total USDC owed to accounts against deposits and withdrawals")
    print(f"    {'window':<28}{'change in total':>18}{'deposits - withdrawals':>24}{'difference':>14}")
    for r in w.itertuples():
        net = r.deposits - r.withdrawals - r.withdrawal_fees
        print(f"    {r.window:<28}{r.total_usdc_end - r.total_usdc_start:>18,.0f}{net:>24,.0f}{r.conservation_residual:>14,.0f}")

    print()
    print("  Unrecorded inflow as a share of net fees (USDC fees less builder fees)")
    print(f"    {'window':<28}{'net fees':>12}{'fund inflow':>14}{'share':>9}{'HLP parent inflow':>19}{'share':>10}"
          f"{'ADL-closed':>12}{'removed from them':>19}")
    for r in w.itertuples():
        print(f"    {r.window:<28}{r.net_fees:>12,.0f}{r.fund_unrecorded_inflow:>14,.0f}{r.fund_share:>9.4f}"
              f"{r.hlp_parent_unrecorded_inflow:>19,.2f}{r.hlp_share:>10.5f}{int(r.adl_closed_accounts):>12,}"
              f"{r.removed_from_adl_closed:>19,.2f}")

    control = w[w.adl_closed_accounts == 0]
    event = w.sort_values("removed_from_adl_closed").iloc[-1]
    small = w[(w.adl_closed_accounts > 0) & (w.window != event.window)]
    fund = control.fund_unrecorded_inflow.sum() / control.net_fees.sum()
    hlp = control.hlp_parent_unrecorded_inflow.sum() / control.net_fees.sum()
    removed = event.removed_from_adl_closed
    fund_excess = event.fund_unrecorded_inflow - fund * event.net_fees
    hlp_excess = event.hlp_parent_unrecorded_inflow - hlp * event.net_fees

    print()
    print(f"  Windows with no auto-deleveraging ({len(control)}):")
    print(f"    fund share of net fees        {fund:.6f}   (range {control.fund_share.min():.6f} to {control.fund_share.max():.6f})")
    print(f"    HLP parent share of net fees  {hlp:.7f}  (range {control.hlp_share.min():.7f} to {control.hlp_share.max():.7f})")
    print(f"    HLP parent's part of the two  {hlp / (fund + hlp):.4%}")
    print()
    print(f"  Cascade window, {event.window} UTC:")
    print(f"    removed from the {int(event.adl_closed_accounts):,} accounts ADL closed out            ${removed:>16,.2f}")
    print(f"    fund inflow beyond its fee share                         ${fund_excess:>16,.2f}   {fund_excess / removed:.2%} of removed")
    print(f"      99% of removed                                         ${0.99 * removed:>16,.2f}   difference ${fund_excess - 0.99 * removed:,.2f}")
    print(f"    HLP parent inflow beyond its fee share                   ${hlp_excess:>16,.2f}   {hlp_excess / removed:.4%} of removed")
    print(f"      1% of removed                                          ${0.01 * removed:>16,.2f}   difference ${hlp_excess - 0.01 * removed:,.2f}")
    print(f"    the two together                                         ${fund_excess + hlp_excess:>16,.2f}   {(fund_excess + hlp_excess) / removed:.2%} of removed")
    lo = event.fund_unrecorded_inflow - control.fund_share.max() * event.net_fees
    hi = event.fund_unrecorded_inflow - control.fund_share.min() * event.net_fees
    print(f"    fund excess using the highest and lowest control share   ${lo:,.0f} to ${hi:,.0f}")
    lo = event.hlp_parent_unrecorded_inflow - control.hlp_share.max() * event.net_fees
    hi = event.hlp_parent_unrecorded_inflow - control.hlp_share.min() * event.net_fees
    print(f"    HLP excess using the highest and lowest control share    ${lo:,.0f} to ${hi:,.0f}")
    for r in small.itertuples():
        print()
        print(f"  Smaller event, {r.window} UTC: removed ${r.removed_from_adl_closed:,.2f} from {int(r.adl_closed_accounts)} accounts")
        print(f"    fund inflow beyond its fee share ${r.fund_unrecorded_inflow - fund * r.net_fees:,.2f} "
              f"(99% of removed ${0.99 * r.removed_from_adl_closed:,.2f})")
        print(f"    HLP parent inflow beyond its fee share ${r.hlp_parent_unrecorded_inflow - hlp * r.net_fees:,.2f} "
              f"(1% of removed ${0.01 * r.removed_from_adl_closed:,.2f})")

    # A second, unrelated event: Aug 26 2025. misc_events_by_block is not archived that far
    # back, so only the fund's own inflow can be measured (audit_all_accounts.py --fund-only).
    aug = pd.read_csv(os.path.join(DATA, "assistance_fund_inflow_aug2025.csv"))
    aug["share"] = aug.fund_inflow / aug.net_fees
    print()
    print("  Aug 2025: the fund's inflow between daily spot snapshots")
    print(f"    {'window (UTC)':<28}{'net fees':>12}{'fund inflow':>14}{'share':>9}{'ADL-closed':>12}{'received above markPx':>23}")
    for r in aug.itertuples():
        print(f"    {r.window:<28}{r.net_fees:>12,.0f}{r.fund_inflow:>14,.0f}{r.share:>9.4f}"
              f"{int(r.adl_closed_accounts):>12,}{r.adl_received_above_mark:>23,.2f}")
    quiet = aug[aug.adl_closed_accounts == 0]
    share = quiet.fund_inflow.sum() / quiet.net_fees.sum()
    print(f"    share over the {len(quiet)} windows with no auto-deleveraging: {share:.4f} "
          f"(range {quiet.share.min():.4f} to {quiet.share.max():.4f})")
    for r in aug[aug.adl_closed_accounts > 0].itertuples():
        excess = r.fund_inflow - share * r.net_fees
        print(f"    {r.window}: fund inflow beyond that share ${excess:,.0f}, "
              f"{excess / r.adl_received_above_mark:.0%} of what the {int(r.adl_closed_accounts)} "
              f"closed-out accounts received above markPx")


if __name__ == "__main__":
    main()
