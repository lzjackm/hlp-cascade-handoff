"""
Rule out two reconstruction-side explanations for the cascade gap.

The gap has the direction `pnl_si Δ < Σ closedPnl + funding`, i.e. the
reconstruction is HIGHER than HL's published number. Exactly two mundane
reconstruction-side errors could produce that direction:

1. Fee netting: fills' `closedPnl` is gross of fees while `pnl_since_inception`
   is net of fees. If so, the gap should equal Σ fees over the window.
2. Duplicate fills: the same fill counted twice inflates Σ closedPnl.

This script tests both against the bundled data, for the cascade window and
for every control window.

Expected output:
- Σ fee over the cascade window is roughly $43K (Liquidator) and $44K
  (Liquidator 2), two orders of magnitude below the $5.69M / $5.30M gaps.
  Fee netting cannot account for the cascade gap.
- The small control-window gaps land close to Σ fee in each window
  (-$206 vs $133, -$2,770 vs $2,562, -$1,010 vs $884), so the baseline noise
  is consistent with fee netting. That sharpens the anomaly: outside the
  cascade the identity ties to roughly the fee level; during the cascade the
  gap is about 130x the window's fees.
- Zero duplicate trade ids and zero fully-duplicated rows in every window.
- Zero spot fills in either parquet, so pairing scope=all pnl_si with
  perp fills introduces no scope mismatch.
"""
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'

vp = pd.read_csv(DATA / 'vault_pnl_snapshots.csv')
vp['ts'] = pd.to_datetime(vp['ts'], utc=True, format='ISO8601')


def snap_window(vault, d0, d1):
    w = vp[
        (vp['vault_name'] == vault) &
        (vp['scope'] == 'all') &
        (vp['ts'] >= pd.Timestamp(d0, tz='UTC')) &
        (vp['ts'] <= pd.Timestamp(d1, tz='UTC'))
    ].sort_values('ts')
    return w.iloc[0]['ts'], w.iloc[-1]['ts']


WINDOWS = [
    ('Liquidator',   'Liquidator_aug1_nov19.parquet',   '2025-10-01', '2025-10-15T23:59', 'cascade'),
    ('Liquidator 2', 'Liquidator_2_oct1_nov19.parquet', '2025-10-01', '2025-10-15T23:59', 'cascade'),
    ('Liquidator',   'Liquidator_aug1_nov19.parquet',   '2025-08-20', '2025-09-03T23:59', 'quiet control'),
    ('Liquidator',   'Liquidator_aug1_nov19.parquet',   '2025-09-17', '2025-10-01T23:59', 'quiet control'),
    ('Liquidator',   'Liquidator_aug1_nov19.parquet',   '2025-10-29', '2025-11-19T23:59', 'Nov 12 control'),
]

print("Fee-netting and duplicate-fill checks")
print("=" * 96)
for vault, pq, d0, d1, label in WINDOWS:
    t0, t1 = snap_window(vault, d0, d1)
    fills = pd.read_parquet(DATA / 'fills' / pq)
    fills['ts'] = pd.to_datetime(fills['ts'], utc=True)
    w = fills[(fills['ts'] >= t0) & (fills['ts'] <= t1)]
    dup_tid = int(w.duplicated(subset=['tid']).sum())
    dup_row = int(w.duplicated(subset=['tid', 'hash', 'coin', 'px', 'sz', 'closed_pnl']).sum())
    print(f"\n{vault} ({label}: {t0.date()} → {t1.date()})")
    print(f"  fills:           {len(w):>10,}")
    print(f"  Σ closedPnl:     ${w['closed_pnl'].sum():>15,.2f}")
    print(f"  Σ fee:           ${w['fee'].sum():>15,.2f}")
    print(f"  duplicate tids:  {dup_tid}   duplicate rows: {dup_row}")

print()
print("Spot-fill check (scope=all pnl_si vs perp fills)")
print("=" * 96)
for pq in ['Liquidator_aug1_nov19.parquet', 'Liquidator_2_oct1_nov19.parquet']:
    coins = pd.read_parquet(DATA / 'fills' / pq, columns=['coin'])['coin']
    n_spot = int((coins.str.startswith('@') | coins.str.contains('/')).sum())
    print(f"  {pq}: {n_spot} spot fills out of {len(coins):,}")
