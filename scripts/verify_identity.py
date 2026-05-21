"""
Verify HL's internal identity holds: account_value Δ = pnl_since_inception Δ + flow_cum Δ.

Reads vault_pnl_snapshots.csv. For each vault in {Liquidator, Liquidator 2},
takes the first and last scope=all snapshot in the cascade window and computes
the identity check.

Expected: identity holds to less than $1 for both vaults.
"""
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data'

vp = pd.read_csv(DATA / 'vault_pnl_snapshots.csv')
vp['ts'] = pd.to_datetime(vp['ts'], utc=True, format='ISO8601')

print("HL API internal identity check: account_value Δ = pnl_since_inception Δ + flow_cum Δ")
print("=" * 80)
for vault in ['Liquidator', 'Liquidator 2']:
    w = vp[
        (vp['vault_name'] == vault) &
        (vp['scope'] == 'all') &
        (vp['ts'].dt.date >= pd.Timestamp('2025-10-01').date()) &
        (vp['ts'].dt.date <= pd.Timestamp('2025-10-15').date())
    ].sort_values('ts')
    if w.empty:
        print(f"\n{vault}: no snapshots found in window")
        continue
    pre, post = w.iloc[0], w.iloc[-1]
    av_d   = post['account_value']        - pre['account_value']
    pnl_d  = post['pnl_since_inception']  - pre['pnl_since_inception']
    flow_d = post['flow_cum']             - pre['flow_cum']
    resid  = av_d - (pnl_d + flow_d)
    print(f"\n{vault}")
    print(f"  Window: {pre['ts']} → {post['ts']}")
    print(f"  account_value Δ:        ${av_d:>17,.2f}")
    print(f"  pnl_since_inception Δ:  ${pnl_d:>17,.2f}")
    print(f"  flow_cum Δ:             ${flow_d:>17,.2f}")
    print(f"  Identity residual:      ${resid:>17,.2f}  ({'OK' if abs(resid) < 1 else 'FAIL'})")
