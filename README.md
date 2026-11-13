# HLP Cascade Reconciliation: Oct 10-11 2025 Liquidator and Liquidator 2

Reproduction repo for a discrepancy in HLP backstop liquidator vault accounting during the Oct 10-11 2025 cascade, and for the mechanism that produces it. The bundled data and scripts reproduce both the gap and its reconciliation.

## The observation

For the window Oct 1 23:50 UTC through Oct 15 22:10 UTC, the sum of fills' `closedPnl` plus funding for Liquidator and Liquidator 2 differs from the `pnl_since_inception` delta the HL API reports for the same window by roughly $5.3-5.7M per vault:

| Vault | Address | HL API pnl_si Δ | Fills + funding | Difference |
|---|---|---|---|---|
| Liquidator | `0x2e3d94f0562703b25c83308a05046ddaf9a8dd14` | +$31,393,998 | +$37,083,535 | **−$5,689,537** |
| Liquidator 2 | `0xb0a55f13d22f66e6d495ac98113841b2326e9540` | +$9,592,827 | +$14,896,979 | **−$5,304,152** |
| **Total** | | | | **−$10,993,689** |

HL's own numbers tie out to one another: `account_value Δ = pnl_since_inception Δ + flow_cum Δ` holds to about $0.01 for both vaults. The difference is a real reduction in account value, not a quirk of how `pnl_since_inception` is computed.

## The mechanism

Both vaults were liquidated themselves during the cascade, and the cash each liquidation left behind was removed from the account.

- **Self-liquidation rounds.** Between 21:16:04 and 21:20:04 UTC on Oct 10, Liquidator is the liquidated user on its own backstop fills in 12 blocks and Liquidator 2 in 7. Node fills mark them with `liquidation.method = "backstop"` and `liquidation.liquidatedUser` equal to the vault's own address. The counterparties are traders whose fills read `Auto-Deleveraging`.
- **Whole book closed each round.** Each round closes every open position of the vault in one block: $700.2M of Liquidator notional and $1.30B of Liquidator 2 notional across the rounds. HL's `startPosition` field confirms the book is flat after every round.
- **Positive cash left behind.** The auto-deleveraging fills execute above the current mark for the vault's long positions, $8.40M (Liquidator) and $6.56M (Liquidator 2) above `markPx` in total, so each round leaves the vault holding positive cash according to its fills.
- **That cash is removed.** The vault's account value afterwards does not contain it. No fill, funding payment or ledger update records the removal. It is credited to the Assistance Fund and the HLP parent vault, like trading fees (see "Where the removed balance went").

| | Liquidator | Liquidator 2 |
|---|---|---|
| Self-liquidation rounds, Oct 10 UTC | 12, 21:16:04 to 21:20:04 | 7, 21:17:06 to 21:19:52 |
| Cash removed after the rounds | $5,631,665.60 | $5,258,596.14 |
| Headline difference restated as cash change outside fills | −$5,632,816.60 | −$5,258,657.60 |
| **Residual** | **−$1,151.00** | **−$61.46** |

The restatement adds back fees and the gap between fill-price cash and `closedPnl` (Test 15). Three further checks fit the same mechanism:

- **Timing.** With the removal, each of the 19 rounds fires when the vault's equity at the current mark is below zero (−$12K to −$1.21M). Without it, rounds 2 onward would fire while the vault held +$0.9M to +$5.3M.
- **Snapshot closure.** From the Oct 10 20:04:54 snapshot to the Oct 11 20:09:51 snapshot, fills, fees, funding and flows minus the removed cash land within $1,113 (Liquidator) and $61 (Liquidator 2) of the reported account value.
- **Nov 12 control.** Liquidator is liquidated once on Nov 12 (16:08:14 UTC, POPCAT), with a negative remainder of −$518,731 after the round. HL keeps that balance on the account (−$518,812 at 23:50, cleared by a $518,811.82 parent deposit on Nov 15), which is why that window reconciles.

The two other parties to these fills are settled exactly as recorded. Takeovers into the vaults price at each liquidated user's account value (Test 17). Traders auto-deleveraged against the vaults hold exactly the cash their fills imply (Test 18).

## Quick start

```bash
pip install -r requirements.txt
python scripts/verify_identity.py
python scripts/reconcile_cascade.py
python scripts/reconcile_controls.py
python scripts/check_fees_and_duplicates.py
python scripts/test_completeness_and_pricing.py
python scripts/test_self_liquidation.py
```

Expected output: the first three scripts reproduce the observation table and show the difference is specific to the cascade window and the backstop liquidator vaults. The fourth rules out fee netting and duplicate fills. The fifth shows the position book is flat at both window boundaries, which excludes every price-based explanation, and rules out isolated-margin accounting and uniform-rate charges. The sixth lists the self-liquidation rounds, reconciles the difference to the residuals above, and runs the Nov 12 control.

Two further scripts need inputs outside the repo. `crosscheck_hypedexer.py` re-pulls the cascade window from a second, independent indexer and needs a Hypedexer API key. `verify_from_raw.py` rebuilds the cascade extract from raw node data and runs Tests 17 and 18, which need other users' fills and balances:

```bash
export HYPEDEXER_API_KEY=...
python scripts/crosscheck_hypedexer.py

python scripts/verify_from_raw.py extract --raw-dir RAW
python scripts/verify_from_raw.py incoming --raw-dir RAW
python scripts/verify_from_raw.py counterparties --raw-dir RAW --live
```

`trace_destination.py` runs Test 19. It downloads about 3.5 GB of requester-pays S3 data (HL node data for Oct 10 01:00 to Oct 11 03:00 UTC and two Artemis account snapshots) and diffs every account's USDC:

```bash
python scripts/trace_destination.py download --raw-dir RAW19 --profile YOUR_AWS_PROFILE
python scripts/trace_destination.py run --raw-dir RAW19 --work WORK
```

For `verify_from_raw.py`, `RAW` holds the Oct 10 20:00-22:00 UTC node fills and misc events plus the clearinghouse snapshot at block 758750000. The script docstring lists the expected file names, all of which are mirrored in [ConejoCapital/HyperReplay](https://github.com/ConejoCapital/HyperReplay).

## Repo layout

```
data/
  vault_pnl_snapshots.csv         HL API snapshots, L1+L2+Strategy A+B, Oct 1 - Nov 19
  vault_flows.csv                 nonFundingLedger flow events, same vaults+window
  funding.csv                     funding events, same vaults+window (HL daily buckets, see below)
  funding_hourly_20251010.csv     hourly funding events for L1+L2, Oct 10 20:00-22:00 UTC
  snapshot_account_values.csv     L1+L2 account value at blocks 758750000 and 759830000
  fills/
    Liquidator_aug1_nov19.parquet         Raw fills for Liquidator (covers all control windows)
    Liquidator_2_oct1_nov19.parquet       Raw fills for Liquidator 2
    cascade_L1_L2_20251010_h20_h21.parquet  L1+L2 fills Oct 10 20:00-22:00 UTC with liquidation fields
    Strategy_A_daily_aggregate.csv        Daily closed_pnl sum (MM detail aggregated)
    Strategy_B_daily_aggregate.csv        Daily closed_pnl sum (MM detail aggregated)
scripts/
  verify_identity.py              Demonstrates HL API internal identity ties to $0.01
  reconcile_cascade.py            Shows the L1+L2 difference for Oct 1 → Oct 15 window
  reconcile_controls.py           Non-cascade control + Strategy A/B control
  check_fees_and_duplicates.py    Rules out fee netting and duplicate fills
  test_completeness_and_pricing.py  Boundary flatness, isolated margin, uniform-rate charges
  test_self_liquidation.py        Self-liquidation rounds, reconciliation, Nov 12 control
  crosscheck_hypedexer.py         Optional: tid-level diff against a second indexer (needs API key)
  verify_from_raw.py              Optional: rebuild the cascade extract, Tests 17-18 (needs raw node data)
  trace_destination.py            Optional: Test 19, per-account USDC diff across all accounts (needs S3 data)
  pull_data.py                    Optional: re-pull from HL API + S3 (for verification)
METHODOLOGY.md                    Full methodology and the tests that have been run
requirements.txt
```

## What the data shows

1. **The difference is the cash removed after the vaults' own liquidations.** It reconciles to −$1,151 and −$61 against the headline difference and to −$1,113 and −$61 against the snapshot account values (Tests 14 and 15). The removed cash went to the Assistance Fund (99%) and the HLP parent vault (1%) (Test 19).

2. **The difference shows up consistently across sources.** Three independent inputs (HL API `vault_pnl`, S3 `node_fills_by_block`, and HL API funding) give the same per-vault magnitudes.

3. **HL's published numbers tie out to one another.** `account_value Δ = pnl_since_inception Δ + flow_cum Δ` agrees at the cent level for both vaults. The removal lowers account value and `pnl_since_inception` together, so it surfaces as a gap against the sum of `closedPnl` over the fills HL itself records.

4. **The pattern is specific to the cascade and to the backstop role.** The cascade is the only window in which the vaults were liquidated with positive cash left over.
   - Non-cascade windows reconcile to under $3K for the same vault and the same code path.
   - On Nov 12 Liquidator was liquidated once, but the remainder was negative and stayed on the account. That window reconciles to within $1,010 (Test 16).
   - Strategy A and Strategy B over the cascade window (Oct 8 → Oct 15, the first `vault_pnl` snapshot available for these vaults pre-cascade is Oct 8) reconcile to within $44K and $129K.

5. **The difference is a cash change, not a valuation one.** Signed fill size per coin nets to zero over the window for all 165 Liquidator coins and all 4 Liquidator 2 coins, so unrealized MTM is zero at both ends under any mark convention (Test 10).

6. **The fill set is complete.** A running position started from zero at the Oct 10 snapshot matches HL's `startPosition` on every one of the 104,467 vault fills between 20:04 and 22:00 UTC (Test 14). A tid-level comparison against a second indexer finds zero fills held by that indexer and not by this repo (Test 13).

7. **Other candidate explanations fail.** `METHODOLOGY.md` covers ADL fill enumeration, nonFundingLedger capture, a replica_cmds chain-commit scan, SetGlobalAction mark prices, a cash-flow destination search, fee netting, duplicate fills, isolated-margin accounting and uniform-rate charges. Fee netting accounts for the small control-window residuals (`pnl_since_inception` is net of fees while `closedPnl` is gross), but cascade-window fees are $43K and $44K.

## Data provenance

| File | Source |
|---|---|
| `vault_pnl_snapshots.csv` | HL API `vault_pnl` endpoint, scope=all and scope=perp |
| `vault_flows.csv` | HL API `userNonFundingLedgerUpdates` endpoint |
| `funding.csv` | HL API `userFunding` endpoint |
| `funding_hourly_20251010.csv` | `misc_events_by_block` Funding events, Oct 10 20:00-22:00 UTC |
| `snapshot_account_values.csv` | Hydromancer Reservoir account values at blocks 758750000 and 759830000 |
| `fills/Liquidator*.parquet` | `s3://hl-mainnet-node-data/node_fills_by_block/`, address-filtered |
| `fills/cascade_L1_L2_20251010_h20_h21.parquet` | `node_fills_by_block` for Oct 10 20:00-22:00 UTC, address-filtered, keeping block order, `startPosition` and the `liquidation` fields |
| `fills/Strategy_*_daily_aggregate.csv` | Same S3 source, aggregated to per-day closed_pnl sums |
| `destination_summary.csv` | Output of `trace_destination.py run` (Test 19), aggregates only |

The cascade extract keeps the liquidation method and mark price of each fill, and records only whether the liquidated user is the vault itself, so no third-party address is bundled. It matches the S3-derived `fills/Liquidator*.parquet` files tid for tid over the same hours, and `verify_from_raw.py extract` rebuilds the same table from the raw files.

`funding.csv` comes from HL's `userFunding` endpoint, which returns older funding as one row per coin per UTC day stamped 00:00, with a `nSamples` count of the hourly payments folded in. Windows that start or end inside a day need either the hourly events or a check of when the vault held each coin. Test 15 does the latter.

Re-pull the HL API portion from source with `python scripts/pull_data.py`. The script writes fresh copies of `vault_pnl_snapshots.csv`, `vault_flows.csv`, and `funding.csv` to `data/from_api/` so they can be diffed against the bundled `data/*.csv`. The S3 fills portion requires AWS credentials with requester-pays access and is documented as a manual procedure at the bottom of `pull_data.py`.

Verifying that bundled data matches what HL currently publishes:

```bash
python scripts/pull_data.py
diff data/vault_pnl_snapshots.csv data/from_api/vault_pnl_snapshots.csv
diff data/vault_flows.csv          data/from_api/vault_flows.csv
diff data/funding.csv              data/from_api/funding.csv
```

The bundled CSV is filtered to the date windows relevant to the reconciliation; the API pull includes the full history. Inner-joining on `(ts, vault, scope)`, all 82 bundled `vault_pnl` rows tie to the API pull at $0.00 for `account_value` and `pnl_since_inception` and within $0.11 for `flow_cum` (derived as `account_value - pnl_since_inception`).

## Where the removed balance went

The removed balances were credited to the same accounts that receive Hyperliquid's protocol fees, in the same proportion, with no ledger event (Test 19):

| Recipient | Address | Received | Share |
|---|---|---|---|
| Assistance Fund | `0xfefefefefefefefefefefefefefefefefefefefe` | $11,827,162 | 98.99% |
| HLP parent vault | `0xdfc24b077bc1425ad1dea75bcb6f8158e10df303` | $120,744 | 1.01% |
| **Total** | | **$11,947,906** | |

- **Not only HLP's vaults.** The removal applies to every account liquidated through auto-deleveraging that evening: 1,400 accounts and $11,984,273 in total, of which $10,890,262 came from Liquidator and Liquidator 2 and $1,094,011 from 1,398 other traders. The amount credited to the two recipients matches the amount removed to within $36K (0.3%).
- **Booked like fee revenue.** In an 18-hour window without auto-deleveraging, the Assistance Fund receives 96.43% of protocol fees and the HLP parent 0.96%, a 99.01 / 0.99 split. The removed balances split 98.99 / 1.01.
- **Spent on HYPE.** The Assistance Fund's spending matches its fee income until the first self-liquidation round at 21:16:04 UTC; from then on it outspends fee income by up to $11.83M. Between 21:16 and 23:20 UTC it spent $14.16M of USDC, mostly buying HYPE on the HYPE/USDC book, before returning to its usual pace of about $0.35M per hour.

Tests 17 and 18 already show that neither the liquidated users nor the auto-deleveraged traders received it.
