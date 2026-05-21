# HLP Cascade Reconciliation: Oct 10-11 2025 Liquidator and Liquidator 2

Reproduction repo for a discrepancy observed in HLP backstop liquidator vault accounting during the Oct 10-11 2025 cascade event. The goal of this repo is to share the data and methodology that produces the observation, so the underlying mechanism can be discussed.

## The observation

For the window Oct 1 23:50 UTC through Oct 15 22:10 UTC, the sum of fills' `closedPnl` plus funding for Liquidator and Liquidator 2 differs from the `pnl_since_inception` delta the HL API reports for the same window by roughly $5.3-5.7M per vault:

| Vault | Address | HL API pnl_si Δ | Fills + funding | Difference |
|---|---|---|---|---|
| Liquidator | `0x2e3d94f0562703b25c83308a05046ddaf9a8dd14` | +$31,393,998 | +$37,083,535 | **−$5,689,537** |
| Liquidator 2 | `0xb0a55f13d22f66e6d495ac98113841b2326e9540` | +$9,592,827 | +$14,896,979 | **−$5,304,152** |
| **Total** | | | | **−$10,993,689** |

The identity `account_value Δ = pnl_since_inception Δ + flow_cum Δ` ties out to about $0.01 in HL's own published numbers for both vaults, so this looks like an accounting question about how `pnl_since_inception` is computed rather than a missing-flow question.

## Quick start

```bash
pip install -r requirements.txt
python scripts/verify_identity.py
python scripts/reconcile_cascade.py
python scripts/reconcile_controls.py
```

Expected output: three scripts that reproduce the table above and demonstrate the difference is specific to the cascade window and the backstop liquidator vaults.

## Repo layout

```
data/
  vault_pnl_snapshots.csv         HL API snapshots, L1+L2+Strategy A+B, Oct 1 - Nov 19
  vault_flows.csv                 nonFundingLedger flow events, same vaults+window
  funding.csv                     funding events, same vaults+window
  fills/
    Liquidator_aug1_nov19.parquet         Raw fills for Liquidator (covers all control windows)
    Liquidator_2_oct1_nov19.parquet       Raw fills for Liquidator 2
    Strategy_A_daily_aggregate.csv        Daily closed_pnl sum (MM detail aggregated)
    Strategy_B_daily_aggregate.csv        Daily closed_pnl sum (MM detail aggregated)
scripts/
  verify_identity.py              Demonstrates HL API internal identity ties to $0.01
  reconcile_cascade.py            Shows the L1+L2 difference for Oct 1 → Oct 15 window
  reconcile_controls.py           Non-cascade control + Strategy A/B control
  pull_data.py                    Optional: re-pull from HL API + S3 (for verification)
METHODOLOGY.md                    Full methodology and the tests that have been run
requirements.txt
```

## What the data suggests

1. **The difference shows up consistently across sources.** Three independent inputs (HL API `vault_pnl`, S3 `node_fills_by_block`, and HL API funding) give the same per-vault magnitudes.

2. **HL's published numbers tie out to one another.** `account_value Δ = pnl_since_inception Δ + flow_cum Δ` agrees at the cent level for both vaults across the window. The difference described above sits between HL's `pnl_since_inception` value and the sum of `closedPnl` across the fills HL itself records for these vaults.

3. **The pattern looks specific to cascade events and backstop liquidator role.**
   - Non-cascade windows reconcile to under $3K for the same vault and the same code path.
   - The Nov 12 event (Liquidator independently lost ~$5.5M from a separate liquidation episode) reconciles to within $1,010.
   - Strategy A and Strategy B over the cascade window (Oct 8 → Oct 15, the first `vault_pnl` snapshot available for these vaults pre-cascade is Oct 8) reconcile to within $44K and $129K, two orders of magnitude smaller than the backstop-vault difference.

4. **Several candidate explanations have been tested.** See `METHODOLOGY.md` for the full set, including ADL fill enumeration, nonFundingLedger capture audit, replica_cmds chain-commit scan, SetGlobalAction mark price comparison, and a cash-flow destination search. None of them appear to account for the difference.

## Data provenance

| File | Source |
|---|---|
| `vault_pnl_snapshots.csv` | HL API `vault_pnl` endpoint, scope=all and scope=perp |
| `vault_flows.csv` | HL API `userNonFundingLedgerUpdates` endpoint |
| `funding.csv` | HL API funding history |
| `fills/Liquidator*.parquet` | `s3://hl-mainnet-node-data/node_fills_by_block/`, address-filtered |
| `fills/Strategy_*_daily_aggregate.csv` | Same S3 source, aggregated to per-day closed_pnl sums |

Re-pull the HL API portion from source with `python scripts/pull_data.py`. The script writes fresh copies of `vault_pnl_snapshots.csv`, `vault_flows.csv`, and `funding.csv` to `data/from_api/` so they can be diffed against the bundled `data/*.csv`. The S3 fills portion requires AWS credentials with requester-pays access and is documented as a manual procedure at the bottom of `pull_data.py`.

Verifying that bundled data matches what HL currently publishes:

```bash
python scripts/pull_data.py
diff data/vault_pnl_snapshots.csv data/from_api/vault_pnl_snapshots.csv
diff data/vault_flows.csv          data/from_api/vault_flows.csv
diff data/funding.csv              data/from_api/funding.csv
```

The bundled CSV is filtered to the date windows relevant to the reconciliation; the API pull includes the full history. Inner-joining on `(ts, vault, scope)`, all 82 bundled `vault_pnl` rows tie to the API pull at $0.00 for `account_value` and `pnl_since_inception` and within $0.11 for `flow_cum` (derived as `account_value - pnl_since_inception`).

## The open question

For the cascade window Oct 1 → Oct 15 2025, HL's published `pnl_since_inception` for Liquidator and Liquidator 2 is lower than the sum of their fills' `closedPnl` plus funding by $5,689,537 and $5,304,152 respectively. The same identity holds to $1,010 or better in all non-cascade windows tested, including a separate $5.5M Liquidator loss event on Nov 12 2025.

Is there an additional component in HL's `pnl_since_inception` computation that applies specifically to backstop liquidator vaults during cascade events? If so, knowing what it is and where it lives would let this reconcile cleanly.
