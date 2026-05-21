# HLP Cascade Reconciliation: Oct 10-11 2025 Liquidator and Liquidator 2 Residual

Reproduction repo for an unexplained discrepancy in HLP backstop liquidator vault accounting during the Oct 10-11 2025 cascade event.

## The finding

For the window Oct 1 23:50 UTC through Oct 15 22:10 UTC, the sum of fills' `closedPnl` plus funding for Liquidator and Liquidator 2 exceeds the `pnl_since_inception` delta the HL API reports for the same window:

| Vault | Address | HL API pnl_si Δ | Fills + funding | Gap |
|---|---|---|---|---|
| Liquidator | `0x2e3d94f0562703b25c83308a05046ddaf9a8dd14` | +$31,393,998 | +$37,083,535 | **−$5,689,537** |
| Liquidator 2 | `0xb0a55f13d22f66e6d495ac98113841b2326e9540` | +$9,592,827 | +$14,896,979 | **−$5,304,152** |
| **Total** | | | | **−$10,993,689** |

The identity `account_value Δ = pnl_since_inception Δ + flow_cum Δ` holds to $0.01 in HL's own published numbers, ruling out a missing-flow explanation.

## Quick start

```bash
pip install -r requirements.txt
python scripts/verify_identity.py
python scripts/reconcile_cascade.py
python scripts/reconcile_controls.py
```

Expected output: three checks, all passing as described in the table above.

## Repo layout

```
data/
  vault_pnl_snapshots.csv         HL API snapshots, L1+L2+Strategy A+B, Oct 1 - Nov 19
  vault_flows.csv                 nonFundingLedger flow events, same vaults+window
  funding.csv                     funding events, same vaults+window
  fills/
    Liquidator_oct1_nov19.parquet         Raw fills for Liquidator
    Liquidator_2_oct1_nov19.parquet       Raw fills for Liquidator 2
    Strategy_A_daily_aggregate.csv        Daily closed_pnl sum (MM detail aggregated)
    Strategy_B_daily_aggregate.csv        Daily closed_pnl sum (MM detail aggregated)
scripts/
  verify_identity.py              Demonstrates HL API internal identity holds to $0.01
  reconcile_cascade.py            Shows the L1+L2 gap for Oct 1 → Oct 15 window
  reconcile_controls.py           Non-cascade control + Strategy A/B control
  pull_data.py                    Optional: re-pull from HL API + S3 (for verification)
METHODOLOGY.md                    Full methodology and eliminative tests
requirements.txt
```

## What this repo demonstrates

1. **The gap is real.** Three independent data sources (HL API `vault_pnl`, S3 `node_fills_by_block`, and pipeline-aggregated parquets) agree on the per-vault magnitudes.

2. **HL's own numbers are internally consistent.** `account_value Δ = pnl_since_inception Δ + flow_cum Δ` ties out to the cent for both vaults across the window.

3. **The gap is specific to cascade events and backstop liquidator vaults.**
   - Non-cascade windows reconcile to under $3K for the same vault, same code path
   - The Nov 12 event (Liquidator lost $5.5M from a separate liquidation) reconciles to within $1,010
   - Strategy A and Strategy B during the same cascade window reconcile to within $77K and $133K (two orders smaller than the backstop gap)

4. **Standard explanations have been ruled out.** See `METHODOLOGY.md` for the full eliminative ladder, including ADL fill enumeration, nonFundingLedger capture audit, replica_cmds chain-commit scan, SetGlobalAction mark price test, and cash-flow destination search.

## Data provenance

| File | Source |
|---|---|
| `vault_pnl_snapshots.csv` | HL API `vault_pnl` endpoint, scope=all and scope=perp |
| `vault_flows.csv` | HL API `userNonFundingLedgerUpdates` endpoint |
| `funding.csv` | HL API funding history |
| `fills/Liquidator*.parquet` | `s3://hl-mainnet-node-data/node_fills_by_block/`, address-filtered |
| `fills/Strategy_*_daily_aggregate.csv` | Same S3 source, aggregated to per-day closed_pnl sums |

Re-pull from source with `python scripts/pull_data.py` (requires AWS credentials with requester-pays access to the HL S3 bucket).

## The question for the Hyperliquid team

For the cascade window Oct 1 → Oct 15 2025, HL's published `pnl_since_inception` for Liquidator and Liquidator 2 is lower than the sum of their fills' `closedPnl` plus funding by $5,689,537 and $5,304,152 respectively. The same identity holds to $1,010 or better in all non-cascade windows tested, including a separate $5.5M Liquidator loss event on Nov 12 2025.

What additional component does HL's `pnl_since_inception` computation include for backstop liquidator vaults during cascade events that does not appear as fills, funding, or any nonFundingLedger event?
