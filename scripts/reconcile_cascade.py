"""
Reconcile L1 and L2 cascade-window PnL.

For each backstop vault, compute:
  expected_pnl = pnl_since_inception(end) - pnl_since_inception(start)  [from HL API]
  reconstructed = Σ fills.closedPnl(window) + Σ funding(window)         [from S3 fills + funding events]
  gap = expected_pnl - reconstructed

Expected output: gap of approximately -$5.69M for Liquidator and -$5.30M for Liquidator 2.
"""
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'

vp = pd.read_csv(DATA / 'vault_pnl_snapshots.csv')
vp['ts'] = pd.to_datetime(vp['ts'], utc=True, format='ISO8601')
fund = pd.read_csv(DATA / 'funding.csv')
fund['ts'] = pd.to_datetime(fund['ts'], utc=True, format='ISO8601')
WIN_START = pd.Timestamp('2025-10-01').date()
WIN_END   = pd.Timestamp('2025-10-15').date()

print(f"Cascade-window reconciliation: {WIN_START} → {WIN_END}")
print("=" * 90)

for vault, fills_file in [
    ('Liquidator',   'Liquidator_oct1_nov19.parquet'),
    ('Liquidator 2', 'Liquidator_2_oct1_nov19.parquet'),
]:
    # HL API pnl_si delta
    api = vp[
        (vp['vault_name'] == vault) &
        (vp['scope'] == 'all') &
        (vp['ts'].dt.date >= WIN_START) &
        (vp['ts'].dt.date <= WIN_END)
    ].sort_values('ts')
    pre, post = api.iloc[0], api.iloc[-1]
    pnl_si_delta = post['pnl_since_inception'] - pre['pnl_since_inception']
    t_pre, t_post = pre['ts'], post['ts']

    # Fills closedPnl sum across the exact same window
    fills = pd.read_parquet(DATA / 'fills' / fills_file, columns=['ts', 'closed_pnl'])
    fills['ts'] = pd.to_datetime(fills['ts'], utc=True)
    in_window = (fills['ts'] >= t_pre) & (fills['ts'] <= t_post)
    cpnl_sum = fills.loc[in_window, 'closed_pnl'].sum()
    n_fills = in_window.sum()

    # Funding sum
    f = fund[
        (fund['vault_name'] == vault) &
        (fund['ts'] >= t_pre) &
        (fund['ts'] <= t_post)
    ]
    fund_sum = f['delta_usd'].sum()

    reconstructed = cpnl_sum + fund_sum
    gap = pnl_si_delta - reconstructed

    print(f"\n{vault}")
    print(f"  Window: {t_pre} → {t_post}")
    print(f"  HL API pnl_si Δ:              ${pnl_si_delta:>17,.2f}")
    print(f"  Σ fills.closedPnl ({n_fills:>6} fills): ${cpnl_sum:>17,.2f}")
    print(f"  Σ funding:                    ${fund_sum:>17,.2f}")
    print(f"  Reconstructed (cpnl + funding):${reconstructed:>17,.2f}")
    print(f"  Gap (pnl_si Δ − reconstructed):${gap:>17,.2f}")
