"""
Control tests: prove the cascade gap is specific to (a) cascade event, (b) backstop liquidator role.

Control 1a: Liquidator across two quiet windows (Aug 20 → Sep 3 and Sep 17 → Oct 1).
Same vault, no cascade activity, modest fill counts. Expected gap: under $3K.

Control 1b: Liquidator across Oct 29 → Nov 19. Window covers the Nov 12 event where
Liquidator independently lost approximately $5.5M from a liquidation. Same vault, same
data path, same magnitude. Expected gap: under $1,500.

Control 2: Strategy A and Strategy B across Oct 8 → Oct 15. The first `vault_pnl`
snapshot available pre-cascade for these vaults is Oct 8 21:50 UTC (Strategy A) and
Oct 8 23:00 UTC (Strategy B), so the window is slightly shorter than the L1/L2 Oct 1
→ Oct 15 window. Different vault role (market makers, not backstop). Expected gap:
roughly $44K and -$129K respectively, two orders of magnitude smaller than the
backstop-vault gap.

Note on Control 2 boundary granularity: Strategy A/B fills are bundled as daily
aggregates, so the reconstruction includes the full Oct 8 day while the pnl_si
window starts at the first snapshot (21:50 / 23:00 UTC). The mismatch this can
introduce is bounded by the Oct 8 full-day closed_pnl: +$14,431 (Strategy A) and
+$4,406 (Strategy B). The reported gaps therefore carry up to that much
boundary noise, which does not affect the order-of-magnitude conclusion.
"""
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'
vp   = pd.read_csv(DATA / 'vault_pnl_snapshots.csv')
vp['ts'] = pd.to_datetime(vp['ts'], utc=True, format='ISO8601')
fund = pd.read_csv(DATA / 'funding.csv')
fund['ts'] = pd.to_datetime(fund['ts'], utc=True, format='ISO8601')

def reconcile(vault, t_start, t_end, fills_source=None, label=""):
    api = vp[
        (vp['vault_name'] == vault) &
        (vp['scope'] == 'all') &
        (vp['ts'] >= t_start) &
        (vp['ts'] <= t_end)
    ].sort_values('ts')
    if len(api) < 2:
        print(f"\n{vault} {label}: insufficient snapshots ({len(api)})")
        return
    pre, post = api.iloc[0], api.iloc[-1]
    pnl_si_delta = post['pnl_since_inception'] - pre['pnl_since_inception']

    if fills_source.endswith('.parquet'):
        fills = pd.read_parquet(DATA / 'fills' / fills_source, columns=['ts', 'closed_pnl'])
        fills['ts'] = pd.to_datetime(fills['ts'], utc=True)
        in_window = (fills['ts'] >= pre['ts']) & (fills['ts'] <= post['ts'])
        cpnl_sum = fills.loc[in_window, 'closed_pnl'].sum()
        n_fills = int(in_window.sum())
    else:
        agg = pd.read_csv(DATA / 'fills' / fills_source)
        agg['date'] = pd.to_datetime(agg['date'], utc=True, format='ISO8601')
        in_window = (agg['date'] >= pre['ts'].normalize()) & (agg['date'] <= post['ts'].normalize())
        cpnl_sum = agg.loc[in_window, 'closed_pnl_sum'].sum()
        n_fills = int(agg.loc[in_window, 'n_fills'].sum())

    f = fund[(fund['vault_name'] == vault) & (fund['ts'] >= pre['ts']) & (fund['ts'] <= post['ts'])]
    fund_sum = f['delta_usd'].sum()
    reconstructed = cpnl_sum + fund_sum
    gap = pnl_si_delta - reconstructed

    print(f"\n{vault} {label}")
    print(f"  Window: {pre['ts']} → {post['ts']}")
    print(f"  HL API pnl_si Δ:               ${pnl_si_delta:>17,.2f}")
    print(f"  Σ fills.closedPnl ({n_fills:>9} fills): ${cpnl_sum:>17,.2f}")
    print(f"  Σ funding:                     ${fund_sum:>17,.2f}")
    print(f"  Reconstructed:                 ${reconstructed:>17,.2f}")
    print(f"  Gap:                           ${gap:>17,.2f}")


print("=" * 90)
print("CONTROL 1a: Liquidator quiet windows (no cascade activity)")
print("=" * 90)
reconcile('Liquidator', pd.Timestamp('2025-08-20', tz='UTC'), pd.Timestamp('2025-09-03T23:59', tz='UTC'),
          fills_source='Liquidator_aug1_nov19.parquet',
          label='(Aug 20 → Sep 3 quiet window)')
reconcile('Liquidator', pd.Timestamp('2025-09-17', tz='UTC'), pd.Timestamp('2025-10-01T23:59', tz='UTC'),
          fills_source='Liquidator_aug1_nov19.parquet',
          label='(Sep 17 → Oct 1 quiet window)')

print()
print("=" * 90)
print("CONTROL 1b: Liquidator, Oct 29 → Nov 19 (covers Nov 12 $5.5M event)")
print("=" * 90)
reconcile('Liquidator', pd.Timestamp('2025-10-29', tz='UTC'), pd.Timestamp('2025-11-19T23:59', tz='UTC'),
          fills_source='Liquidator_aug1_nov19.parquet',
          label='(Nov 12 magnitude-control window)')

print()
print("=" * 90)
print("CONTROL 2: Strategy A and Strategy B, Oct 8 → Oct 15 (cascade window, MM role)")
print("Note: first vault_pnl snapshot available pre-cascade for these vaults is Oct 8,")
print("not Oct 1. Window is therefore Oct 8 21:50/23:00 UTC → Oct 15 23:50 UTC.")
print("=" * 90)
reconcile('Strategy A', pd.Timestamp('2025-10-08', tz='UTC'), pd.Timestamp('2025-10-15T23:59', tz='UTC'),
          fills_source='Strategy_A_daily_aggregate.csv',
          label='(market-maker control, cascade window)')
reconcile('Strategy B', pd.Timestamp('2025-10-08', tz='UTC'), pd.Timestamp('2025-10-15T23:59', tz='UTC'),
          fills_source='Strategy_B_daily_aggregate.csv',
          label='(market-maker control, cascade window)')

print()
print("Expected: both controls reconcile to far below the $5M-per-vault cascade gap.")
print("This rules out: pipeline bug, structural backstop-accounting issue, generic cascade artifact.")
