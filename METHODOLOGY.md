# Methodology

This document details the reconciliation approach, the mechanism behind the cascade-window gap, and the tests that have been run.

## Reconciliation identity

For each HLP sub-vault, between any two timestamps `t0` and `t1`, the following relationship is expected to hold approximately:

```
pnl_since_inception(t1) - pnl_since_inception(t0)  ≈  Σ fills.closedPnl(t0..t1)  +  Σ funding(t0..t1)
```

The left side is HL's published lifetime PnL number for the vault, sampled at the two timestamps via the `vault_pnl` API. The right side is reconstructed from individual fill events and funding events that HL also publishes.

For vaults that hold no inventory at the snapshot boundaries (which is the case for Liquidator and Liquidator 2 at every Hydromancer snap), MTM contribution is zero and this relationship is expected to tie out to noise level.

## What the data shows

| Window | Vault | pnl_si Δ | Fills + funding | Difference |
|---|---|---|---|---|
| 2025-08-20 → 2025-09-03 | Liquidator | +$48,473 | +$48,679 | −$206 |
| 2025-09-17 → 2025-10-01 | Liquidator | +$348,371 | +$351,141 | −$2,770 |
| **2025-10-01 → 2025-10-15 (cascade)** | **Liquidator** | **+$31,393,998** | **+$37,083,535** | **−$5,689,537** |
| **2025-10-01 → 2025-10-15 (cascade)** | **Liquidator 2** | **+$9,592,827** | **+$14,896,979** | **−$5,304,152** |
| 2025-10-29 → 2025-11-19 (covers Nov 12 $5.5M event) | Liquidator | −$5,497,447 | −$5,496,437 | −$1,010 |
| 2025-10-08 → 2025-10-15 (cascade) | Strategy A | +$2,527,088 | +$2,483,063 | +$44,025 |
| 2025-10-08 → 2025-10-15 (cascade) | Strategy B | −$2,740,462 | −$2,611,755 | −$128,707 |

The identity ties out to under $3K for L1 across non-cascade windows and to $1,010 even when L1 has a $5.5M magnitude loss on Nov 12. The same identity diverges by $5.69M and $5.30M for the same vaults during the Oct 10-11 cascade window. Strategy A and Strategy B run with the first available `vault_pnl` snapshot pre-cascade (Oct 8 21:50 UTC for Strategy A, Oct 8 23:00 UTC for Strategy B) through the same Oct 15 endpoint; over those slightly shorter windows the differences are roughly $44K and $129K, two orders of magnitude below the backstop-vault numbers.

One granularity caveat on the Strategy A/B rows: their fills are bundled as daily aggregates, so the reconstruction includes the full Oct 8 day while the `pnl_si` window starts at the first snapshot (21:50 / 23:00 UTC). The boundary mismatch this can introduce is bounded by the Oct 8 full-day closed_pnl: +$14,431 for Strategy A and +$4,406 for Strategy B. The $44K and $129K figures therefore carry up to that much boundary noise, which does not affect the order-of-magnitude comparison against the backstop-vault gaps.

## Mechanism: the backstop vaults were liquidated themselves

During the cascade both vaults ran out of equity while carrying the positions they had taken over, and HL liquidated them. Liquidator is the liquidated user on its own backstop fills in 12 blocks between 21:16:04 and 21:20:04 UTC on Oct 10, and Liquidator 2 in 7 blocks between 21:17:06 and 21:19:52. Each of these rounds follows the same sequence.

1. **Trigger.** The vault's equity at the current mark falls below zero.
2. **Close-out.** The vault's entire book is closed in one block against auto-deleveraged traders. The vault's fills carry `liquidation.method = "backstop"` with `liquidation.liquidatedUser` set to the vault's own address, and the counterparty fills read `Auto-Deleveraging`.
3. **Remainder.** The close-out prices sit above the current mark for the vault's long positions, so according to its fills the vault comes out of the round flat and holding positive cash.
4. **Removal.** That cash does not appear in the vault's account value afterwards, and no fill, funding payment or ledger update records where it went. The next round starts from zero equity.

| | Liquidator | Liquidator 2 |
|---|---|---|
| Rounds | 12 | 7 |
| Notional closed in the rounds | $700.2M | $1.30B |
| Received above `markPx` on the close-out fills | $8.40M | $6.56M |
| Cash removed after the rounds | $5,631,665.60 | $5,258,596.14 |
| Headline difference restated as cash change outside fills | −$5,632,816.60 | −$5,258,657.60 |
| **Residual** | **−$1,151.00** | **−$61.46** |

The removed balances account for every structural feature of the gap.

- **Cascade only.** The cascade is the only window in which either vault was liquidated with positive cash left over.
- **Backstop vaults only.** Strategy A and Strategy B were never liquidated.
- **Absolute, not proportional.** The amount is the cash each vault held at the end of its own rounds, unrelated to notional or `closedPnl`, which is why no uniform rate fits both vaults (Test 12).
- **Nov 12 reconciles.** Liquidator is liquidated once that day, but the remainder is negative and HL leaves it on the account (Test 16).
- **No ledger event.** The removal, and the matching credit to the fee recipients, are balance changes without a LedgerUpdate, which is why the destination search (Test 4) and the per-event walk found nothing to attribute it to.

Tests 14 to 18 below verify each step. Test 19 traces the removed balance to its recipients: the Assistance Fund (98.99%) and the HLP parent vault (1.01%), the accounts and proportions that receive protocol fees. Tests 19b to 21 repeat that measurement on other data: same-block daily snapshots over six days and a second event (19b), the fund's purchases and HL's own running total for it (20), and HL's reported PnL for individual liquidated accounts (21).

## Per-day decomposition of the cascade window

The bulk of the difference concentrates on a single Hydromancer-aligned date.

| Date (Hydromancer-aligned) | Liquidator residual | Liquidator 2 residual | All HLP vaults |
|---|---|---|---|
| 2025-10-07 | −$1 | $0 | +$2,447 |
| 2025-10-08 | $0 | $0 | +$93 |
| 2025-10-09 | −$1,558 | $0 | −$2,781 |
| 2025-10-10 | −$103,522 | −$28,587 | −$102,619 |
| **2025-10-11** | **−$5,570,535** | **−$5,274,512** | **−$10,873,852** |
| 2025-10-12 | −$61 | $0 | +$48 |
| 2025-10-13 | −$39 | $0 | +$1,567 |
| 2025-10-14 | $0 | $0 | +$833 |
| 2025-10-15 | −$3 | $0 | +$1,811 |

About 99% of the cascade-window residual lands on the Oct 11 Hydromancer-aligned date, which runs from the Oct 10 snapshot (block 758750000, 20:04:54 UTC) to the Oct 11 snapshot (block 759830000, 20:09:51 UTC) and contains all 19 self-liquidation rounds.

The Oct 10 row is mostly funding placed on the wrong side of the snapshot by its timestamp. HL's `userFunding` endpoint returns older funding as one row per coin per UTC day stamped 00:00, with a `nSamples` count of the hourly payments folded in. The funding both vaults earned on cascade inventory at 21:00 to 23:00 UTC on Oct 10 (+$100,608 for Liquidator, +$28,587 for Liquidator 2) therefore carries a 00:00 Oct 10 timestamp and falls into the Oct 10 date. For Liquidator 2 that is the whole Oct 10 residual. Taken together, the Oct 10 and Oct 11 rows are the cascade residual.

## Tests that have been run, with results

Each item below describes a specific check, what data was used, and the empirical result.

### Test 1: HL API internal identity (`account_value Δ = pnl_since_inception Δ + flow_cum Δ`)

Verified for both vaults across the cascade window. The identity ties out to $0.01.

| Vault | account_value Δ | pnl_since_inception Δ | flow_cum Δ | Identity residual |
|---|---|---|---|---|
| Liquidator | +$4,398,737 | +$31,393,998 | −$26,995,262 | −$0.00 |
| Liquidator 2 | +$22,500,000 | +$9,592,827 | +$12,907,173 | $0.00 |

HL's three published numbers per vault appear self-consistent. The difference described in the headline does not appear to be in a miscounted flow component.

### Test 2: Pipeline flow_sum vs HL API flow_cum

The `vault_flows.csv` (derived from `userNonFundingLedgerUpdates`) ties out to HL's published `flow_cum` delta for the same window:

| Vault | HL API flow_cum Δ | vault_flows sum | Diff |
|---|---|---|---|
| Liquidator | −$26,995,261.54 | −$26,995,261.54 | $0.00 |
| Liquidator 2 | +$12,907,173.13 | +$12,907,173.13 | $0.00 |

The nonFundingLedger event types that touch L1 or L2 appear to be fully captured by the pipeline.

### Test 3: ADL fills enumeration

`dir='Auto-Deleveraging'` fills exist in `s3://hl-mainnet-node-data/node_fills_by_block/` (35,022 fills in cascade peak hour Oct 10 21:00-22:00 UTC).

HLP vaults appear as counterparty in 89% of those events under `dir='Liquidated Cross Long/Short'`. Those counterparty fills are already included in the per-vault closed_pnl sum in this repo's data. Adding them does not change the result because they are already counted.

### Test 4: Cash flow destination search

Across 18 of 24 hours in the hydro-aligned Oct 11 window, every `LedgerUpdate` event with USDC-bearing semantics was enumerated and aggregated by destination address.

| Candidate destination | Net USDC delta during window | Result |
|---|---|---|
| Assistance Fund (`0xfefefefefefefefefefefefefefefefefefefefe`) | 0 events of any kind | Does not appear to match |
| HLP parent (`0xdfc24b077bc1425ad1dea75bcb6f8158e10df303`) scope=perp pnl_si Δ | +$0.40M | Does not match the magnitude |
| Burn address (`0x222222...22`) | −$10.46M net OUTFLOW | Wrong direction |
| Any `0x2000...XXXX` system-prefixed address | Largest abs is −$5.7M, not net inflow | Does not match |
| Any non-system address | Largest net inflow is $9.0M (round-number user deposit) | Does not match |

No address in the LedgerUpdate stream receives within 20% of the $10.99M magnitude during the cascade window.

### Test 5: Replica_cmds chain-commit scan

Scanned `s3://hl-mainnet-node-data/replica_cmds/2025-09-27T09:28:27Z/20251010/758800000.lz4` (2.0GB compressed, 10,000 blocks, 9.7M actions, covers 21:15-21:29 UTC).

Total distinct action types found: 39. The following expected types have zero occurrences in the cascade peak file:

`Adl`, `AutoDeleverag`, `Settlement`, `SystemUsd`, `SystemAligned`, `AlignedQuote`, `InsuranceFund`, `PerformAdl`, `AdlShortfall`, `SetReserve`, `ModifyVault`, `Mint`, `Burn`, `ChainCommit`.

HLP-touching action types present: `liquidate` (11,494, pure routing instruction with no settlement state), `vaultTransfer` (128 touching HLP parent, captured in vault_flows), `NetChildVaultPositionsAction` (74 touching only Strategy A and Strategy B).

`VoteGlobalAction` instances (231) all have the uniform schema `{type: "VoteGlobalAction", assetsAtOpenInterestCap: [22,34,52,57,106,179]}`. No ADL discriminant variant observed.

### Test 6: SetGlobalAction mark vs trade VWAP

Validator-set marks from `SetGlobalAction` diverge from same-minute trade VWAP during cascade peak. Examples:

| Minute (UTC) | Coin | Validator mark | Trade VWAP | Differential |
|---|---|---|---|---|
| 21:17 | BTC | $108,162.80 | $106,952.95 | +$1,209.85 (+1.13%) |
| 21:20 | ETH | $3,416.28 | $3,313.35 | +$102.92 (+3.11%) |
| 21:21 | SOL | $155.40 | $141.91 | +$13.49 (+9.51%) |
| 21:22 | BTC | $107,682.75 | $109,273.25 | −$1,590.50 (−1.46%) |

The sign reverses between the chaos phase (validator marks above trade VWAP, 21:13-21:21) and the cleanup phase (validator marks below trade VWAP, 21:22-21:29).

Sum across all HLP-counterparty ADL fills in hour 21 of `(execution_price − validator_mark) × signed_size`: +$258.9M. The magnitude is two orders larger than the cascade-window difference, and L1/L2 hold zero inventory at the Hydromancer snap boundary, so this differential does not appear to translate into realized `pnl_si` impact.

### Test 7: Validator-side state-write access

Direct contact with a Hyperliquid validator surfaced three practical constraints on external visibility of state writes:

1. No cached pre-cascade RocksDB snapshot exists on validator nodes that started accumulating after the event.
2. The `--write-system-and-core-writer-actions` flag (which would log System-class CoreWriter actions) creates indexing overhead that drops a node out of the active set, so production validators do not run it.
3. The `replica_cmds` snapshot is not sufficient for full replay reproducing the System-action state writes.

### Test 8: Fee netting

The gap has the direction `pnl_si Δ < Σ closedPnl + funding`, so one mundane explanation would be that fills' `closedPnl` is gross of fees while `pnl_since_inception` is net of fees. If so, the gap should equal Σ fees over the window. Reproducible from bundled data via `scripts/check_fees_and_duplicates.py`.

| Window | Vault | Gap | Σ fee in window |
|---|---|---|---|
| 2025-08-20 → 2025-09-03 | Liquidator | −$206 | $133 |
| 2025-09-17 → 2025-10-01 | Liquidator | −$2,770 | $2,562 |
| 2025-10-29 → 2025-11-19 (Nov 12 event) | Liquidator | −$1,010 | $884 |
| **2025-10-01 → 2025-10-15 (cascade)** | **Liquidator** | **−$5,689,537** | **$42,903** |
| **2025-10-01 → 2025-10-15 (cascade)** | **Liquidator 2** | **−$5,304,152** | **$44,441** |

Two readings:

1. Fee netting cannot account for the cascade gap. Cascade-window fees are two orders of magnitude too small, roughly 0.8% of the gap.
2. The control-window gaps land close to Σ fee in each window, so the small baseline residual is consistent with `pnl_since_inception` being net of fees while `closedPnl` is gross. That sharpens the anomaly: outside the cascade, the identity ties to roughly the fee level; during the cascade, the gap is about 130x the window's fees.

### Test 9: Duplicate fills and scope mismatch

The other reconstruction-side error that could produce a too-high `Σ closedPnl` is the same fill counted twice. Checked via duplicate `tid` and duplicate full-row `(tid, hash, coin, px, sz, closed_pnl)` counts: zero duplicates in the cascade window and in every control window, for both vaults.

Both fills parquets also contain zero spot fills (no `@`-prefixed or pair-style coins), so pairing scope=all `pnl_si` with perp fills introduces no scope mismatch.

### Test 10: position flatness at the window boundaries

Reproducible via `scripts/test_completeness_and_pricing.py`.

Summing signed fill size per coin over a window gives zero if and only if the book returns to its starting position. Both vaults hold only cash at the bracketing vault snapshots, so zero net means the book is flat at both ends.

| Window | Vault | Fills | Coins | Coins with nonzero net | Largest per-coin abs net |
|---|---|---|---|---|---|
| Hydromancer snap-to-snap | Liquidator | 175,565 | 165 | 0 | 2.8e-09 |
| Hydromancer snap-to-snap | Liquidator 2 | 29,341 | 4 | 0 | 1.2e-12 |
| Headline cascade window | Liquidator | 185,741 | 165 | 0 | 2.8e-09 |
| Headline cascade window | Liquidator 2 | 29,341 | 4 | 0 | 1.2e-12 |

The residual nets are floating-point rounding in sums of sizes up to 1e9. A coin counts as nonzero above 1e-6.

Two consequences:

1. **No price-based explanation can contribute to the gap.** Mark price only affects open positions. If the book is flat at both ends of the window, unrealized MTM is zero at both ends under any mark convention, however stale, deviating, or mismarked. The HL/Binance dislocation during the cascade (roughly 11 percentage points inside seven minutes) therefore cannot move the reconciliation identity across these boundaries. Intraday mark deviation, snap-time mismarking, and cost-basis convention are all excluded by construction rather than by measurement.

2. **It is an independent fill-completeness check.** A fill missing from the dataset would leave a phantom open position behind and show up as nonzero net for that coin. Zero across 165 coins and 185,741 fills is not achievable under random fill loss. This argument needs no external data source. For Oct 10 20:00-22:00 UTC, HL's own `startPosition` field gives a fill-by-fill version of the same check (Test 14).

### Test 11: isolated-margin accounting

Reproducible via `scripts/test_completeness_and_pricing.py`.

A liquidated isolated-margin position has its own margin bucket, so realized PnL could in principle be capped by that bucket while `closedPnl` reports the full amount. The direction fits: it would overstate cash accrual, produce a negative residual, leave no ledger footprint, and concentrate in a mass-liquidation event. The test is one of magnitude.

| Vault | Isolated Σ closedPnl | Residual | Share |
|---|---|---|---|
| Liquidator | $43,075 | −$5,689,537 | 0.8%, wrong sign |
| Liquidator 2 | $283,361 | −$5,304,152 | 5.3%, wrong sign |

Two orders of magnitude too small on Liquidator. Rejected.

### Test 12: uniform-rate charge

Reproducible via `scripts/test_completeness_and_pricing.py`.

If the residual were a structural charge levied at a fixed rate, whether a notional haircut or a leader profit share, the implied rate would agree across the two vaults on some common base. It does not agree on any base tried.

| Base | Liquidator | Liquidator 2 | Ratio |
|---|---|---|---|
| % of window notional | 0.3575% | 0.1848% | 1.94x |
| % of closing notional | 0.6982% | 0.3676% | 1.90x |
| % of Σ closedPnl | 15.57% | 35.67% | 0.44x |

A flat profit-share rate is excluded by the same figures.

One counter-observation, offered as a shape rather than a mechanism, and weak at n=2: the two residuals differ by only 1.07x in dollars while window notional differs 1.80x and Σ closedPnl differs 2.46x. Every activity-based normalization makes the two vaults less alike, not more. That is more consistent with a fixed per-vault adjustment than with anything proportional to trading activity. Test 14 identifies the adjustment: the cash each vault held at the end of its own liquidation rounds.

### Test 13: independent second-indexer verification

Reproducible via `scripts/crosscheck_hypedexer.py` (requires a `HYPEDEXER_API_KEY`).

The cascade window was re-pulled from the Hypedexer indexer, a separate indexing pipeline from the S3 node-data path used here, and compared at `tid` level. The question is not whether the totals match, but whether the other index holds any fill this repo lacks.

| | Liquidator | Liquidator 2 |
|---|---|---|
| In both | 151,395 | 24,247 |
| Only this repo | 29,733 | 5,094 |
| **Only Hypedexer** | **0** | **0** |
| Shared-subset closedPnl max abs diff | **0.000000** | **0.000000** |
| Shared-subset mismatches | 0 | 0 |

No external index holds a fill this repo lacks, and every one of the 175,642 shared fills agrees on `closedPnl` at full precision.

Their shortfall is a defect in their ingest, not a signal. Coverage is exact in every hour of the window except 21:00 on Oct 10, where everything from roughly 21:29:07 onward is absent (Liquidator 29,733 fills / $13,876,563.78; Liquidator 2 5,094 fills / $9,135,432.08). The Liquidator 2 figure is identical to the under-report in the same provider's slim CSV export, indicating one shared upstream truncation rather than two separate export bugs. It lands on the 21:22 to 21:59 cleanup phase.

### Test 13b: a third retrieval path covering the whole window

Reproducible via `scripts/crosscheck_hydromancer.py` (requires a `HYDROMANCER_API_KEY`).

Hydromancer keeps a backfill of HL's own fills API. Re-pulling every fill between the Oct 1 and Oct 15 `vault_pnl` snapshots from it has no hole at 21:29, so it also covers the 34,827 fills Test 13 could not compare.

| | Liquidator | Liquidator 2 |
|---|---|---|
| In both | 185,741 | 29,341 |
| Only this repo | 0 | 0 |
| Only Hydromancer | 0 | 0 |
| Largest difference in price, size or fee | 0 | 0 |
| Largest difference in closedPnl | 0 | 9.3e-10 |
| Hash mismatches | 0 | 0 |

### Test 14: self-liquidation rounds

Reproducible via `scripts/test_self_liquidation.py`.

The input is `fills/cascade_L1_L2_20251010_h20_h21.parquet`: every Liquidator and Liquidator 2 fill from 20:00 to 22:00 UTC on Oct 10 (104,467 fills), with block order, `startPosition`, and the liquidation fields HL attaches to each fill (method `market` or `backstop`, `markPx`, and whether the liquidated user is the vault itself). The walk starts from the account values at block 758750000 (`snapshot_account_values.csv`), when both vaults hold only cash. It then applies fills at fill price, fees, the 21:00 funding payment and the one ledger flow in the window, a $6,046.05 withdrawal from Liquidator 2 at 20:59:48.

A running position per coin, started from zero at the snapshot, equals HL's `startPosition` on every one of the 85,739 Liquidator and 18,728 Liquidator 2 fills. No round leaves an open position, and no other fill shares a block with a round.

Liquidator:

| Time (UTC) | Notional closed | Received above `markPx` | Cash after round | Removed | Equity at mark when it fires |
|---|---|---|---|---|---|
| 21:16:04 | $185.5M | $2,197,785 | $984,981 | $984,981 | −$1,212,804 |
| 21:16:56 | $99.0M | $1,295,157 | $2,211,296 | $1,226,314 | −$68,843 |
| 21:17:14 | $74.0M | $700,394 | $2,509,457 | $298,161 | −$402,233 |
| 21:17:26 | $7.2M | $93,581 | $2,584,093 | $74,636 | −$18,945 |
| 21:18:23 | $148.9M | $982,096 | $3,172,213 | $588,120 | −$393,976 |
| 21:18:44 | $58.3M | $1,200,490 | $4,052,181 | $879,968 | −$320,522 |
| 21:18:50 | $27.5M | $291,477 | $4,278,936 | $226,756 | −$64,721 |
| 21:19:18 | $23.0M | $671,216 | $4,796,044 | $517,108 | −$154,108 |
| 21:19:25 | $31.7M | $418,729 | $5,171,728 | $375,684 | −$43,045 |
| 21:19:40 | $10.5M | $106,385 | $5,232,116 | $60,388 | −$45,996 |
| 21:19:46 | $6.0M | $84,801 | $5,282,856 | $50,740 | −$34,060 |
| 21:20:04 | $28.5M | $361,257 | $5,631,666 | $348,809 | −$12,448 |
| **Total** | **$700.2M** | **$8,403,368** | | **$5,631,665.60** | |

Liquidator 2:

| Time (UTC) | Notional closed | Received above `markPx` | Cash after round | Removed | Equity at mark when it fires |
|---|---|---|---|---|---|
| 21:17:06 | $386.6M | $1,653,661 | $1,045,026 | $1,045,026 | −$608,636 |
| 21:17:47 | $296.1M | $1,706,195 | $2,647,657 | $1,602,632 | −$103,563 |
| 21:19:08 | $113.8M | $623,741 | $3,223,312 | $575,655 | −$48,086 |
| 21:19:22 | $234.1M | $1,359,892 | $4,393,852 | $1,170,540 | −$189,352 |
| 21:19:25 | $102.6M | $604,249 | $4,941,099 | $547,247 | −$57,002 |
| 21:19:34 | $45.2M | $227,045 | $4,992,845 | $51,746 | −$175,299 |
| 21:19:52 | $120.8M | $386,013 | $5,258,596 | $265,751 | −$120,262 |
| **Total** | **$1.30B** | **$6,560,796** | | **$5,258,596.14** | |

How the columns are built:

- **Cash after round:** the vault's cash right after the block according to its fills. The book is flat at that point, so no mark price is involved.
- **Removed:** the part of that cash not already removed in earlier rounds.
- **Equity at mark when it fires:** cash after the round, minus what the close-out fills received above `markPx`, minus everything removed in earlier rounds. This is the vault's equity at the current mark just before the round.

The last column is the timing check. With the earlier removals applied, every round fires with the vault slightly underwater, between −$12,448 and −$1,212,804. Without them, only the first round of each vault fires below zero. The other 17 would fire with the vault holding +$0.92M to +$5.27M (Liquidator) or +$0.94M to +$4.87M (Liquidator 2) of equity. The observed trigger pattern requires the removals.

### Test 15: reconciliation with the removed balances

Reproducible via `scripts/test_self_liquidation.py`.

**Headline window.** The headline difference compares `pnl_since_inception` with `closedPnl` plus funding. Restating it as the change in cash not explained by fills takes two adjustments: add back fees (`pnl_since_inception` is net of fees, `closedPnl` gross), and subtract the difference between fill-price cash and `closedPnl` over the window. The book is flat at both ends, so fill-price cash is the exact trading result.

| Vault | Headline difference | Fees | Fill-price cash − Σ closedPnl | Cash change outside fills | Removed | Residual |
|---|---|---|---|---|---|---|
| Liquidator | −$5,689,537.07 | $42,902.86 | −$13,817.62 | −$5,632,816.60 | $5,631,665.60 | **−$1,151.00** |
| Liquidator 2 | −$5,304,152.74 | $44,441.38 | −$1,053.77 | −$5,258,657.60 | $5,258,596.14 | **−$61.46** |

**Snapshot to snapshot.** This check runs from block 758750000 (Oct 10 20:04:54 UTC) to block 759830000 (Oct 11 20:09:51 UTC), with Hydromancer account values at both ends.

| Vault | Start | Fill-price cash | Fees | Funding | Flows | Predicted from fills | Predicted with removal | Reported | Residual |
|---|---|---|---|---|---|---|---|---|---|
| Liquidator | $904,733.13 | $36,519,473.11 | $38,406.28 | $101,232.89 | −$26,854,021.13 | $10,633,011.72 | $5,001,346.12 | $5,000,233.43 | **−$1,112.69** |
| Liquidator 2 | $7,500,000.00 | $14,867,338.47 | $44,441.38 | $28,587.38 | $12,907,173.13 | $35,258,657.59 | $30,000,061.46 | $30,000,000.00 | **−$61.46** |

Funding here comes from `funding.csv`, whose rows are HL daily buckets stamped 00:00 UTC. Each row is assigned to the window by checking where the vault held that coin at the top of each hour of that day, given flat books at both snapshots. One row falls outside: Liquidator's Oct 10 WLFI row (−$41.13), from a position held only at 16:00 UTC.

### Test 16: Nov 12 control

Reproducible via `scripts/test_self_liquidation.py`.

The main fills parquets lack the liquidation fields, but self-liquidation fills still have a signature there. Under a `Liquidated ...` direction, the vault buys a long or sells a short when it takes over a liquidated position. When the vault is itself the liquidated user, it sells its long or buys back its short. On the Oct 10 extract the signature and the liquidation field agree on all 24,769 Liquidator and 6,397 Liquidator 2 self-liquidation fills.

Across Aug 1 to Nov 19 the signature appears in two hours, both for Liquidator: Oct 10 21:00 (24,769 fills, 155 coins) and Nov 12 16:00 (336 fills, one POPCAT round at 16:08:14.487 UTC). Liquidator 2 shows it only on Oct 10.

On Nov 12 the remainder after the round is negative:

- **Walk.** Starting from Liquidator's Oct 29 22:10 `vault_pnl` snapshot ($5,000,000.00), fills, fees, funding and flows give −$518,730.56 of cash and no open position right after the round.
- **HL.** HL reports −$518,811.82 at 23:50, $81.26 away.
- **Parent deposit.** The parent vault deposits exactly $518,811.82 on Nov 15 at 08:18, followed by a further $1,000,000.

A negative remainder stays on the account. On Oct 10 every remainder is positive and is removed. That is why the Nov 12 window reconciles to $1,010 and the cascade window does not.

### Test 17: takeover pricing

Reproducible via `scripts/verify_from_raw.py incoming` (raw node data).

When a regular user is backstop-liquidated into one of the vaults, `misc_events_by_block` records a `liquidation` LedgerUpdate carrying that user's account value. The table compares it with what the vault received relative to `markPx` on the takeover fills, per liquidation event:

| Taking vault | Events | Notional | Liquidated account value | Received relative to `markPx` | Difference |
|---|---|---|---|---|---|
| Liquidator | 9,413 | $596.2M | $24,694,600 | $24,702,729 | −$8,202 |
| Both vaults | 570 | $754.0M | $9,946,162 | $9,951,926 | −$5,764 |
| Liquidator 2 | 4,061 | $898.3M | $7,378,019 | $7,378,739 | −$721 |

14,043 of the 14,044 events match a ledger event. The median difference is −$0.0013, 95.8% of events agree within $1, and the total is −$14,688 on $2.25B. Each takeover is priced so that the liquidated user ends at exactly zero, its bankruptcy price, and the vault receives that user's equity through the fill, negative equity included. Incoming takeovers carry no cash outside the fills.

### Test 18: settlement of the auto-deleveraged traders

Reproducible via `scripts/verify_from_raw.py counterparties --live` (raw node data, the clearinghouse snapshot at block 758750000, and the HL info API).

If the removed balance went to the traders on the other side of the close-out fills, each would hold more cash than its fills imply. Pro rata by notional within each round, the predicted shares sum to $10,890,261.74 across 18,706 traders.

The test uses accounts whose whole book at the 20:04:54 snapshot was closed by these rounds, with no other fills in the hour: 8,605 accounts, 7,142 of them with a predicted share of at least $0.50. Each account's expected cash is:

```
snapshot account value − snapshot position value + close-out fill cash − fees + 21:00 funding
```

An account that has not traded or moved funds since the cascade still holds exactly that amount today, plus its share if it received one.

Querying the 2,000 accounts with the largest predicted shares (the script default) returns 1,791 that are flat today:

| Current balance equals | Accounts |
|---|---|
| Fill-implied cash, to the cent | 13 |
| Fill-implied cash plus pro-rata share by notional | 0 |
| Fill-implied cash plus pro-rata share by amount paid above `markPx` | 0 |

The 13 untouched accounts would have been owed $578.26 between them under a pro-rata credit, up to $104.17 for a single account. Each holds exactly its fill-implied cash instead. The traders on the other side of the close-out fills were settled at the recorded fill prices and received none of the removed balance. The remaining 1,778 flat accounts have traded or moved funds since and do not bear on the test.

### Test 18b: the largest auto-deleveraged traders

Reproducible via `scripts/test_adl_accounts.py` (public HL info API; the account list comes from `extract_fee_tables.py`).

Test 18 settles the question on accounts whose pro-rata share would have been at most $104. This is the complementary check on the largest traders, where a share would be hundreds of thousands of dollars. Every self-liquidation fill has a trader fill at the same price with zero fee, 18,706 accounts in all for $1,999.3M of notional; the largest 10 hold 46.1% of it.

The check takes HL's `portfolio` PnL samples for an account, picks the nearest samples on each side of the rounds at which the account held no position, and compares the PnL change with `Σ closedPnl − Σ fees + Σ funding` in between. With no position at either end there is no unrealised PnL, so the two are equal unless something moved outside the fills.

| Trader, by close-out notional | Close-out notional | Flat PnL samples (UTC) | HL PnL change | From its own fills | Difference | A pro-rata share would be |
|---|---|---|---|---|---|---|
| Largest | | the API no longer serves its Oct 2025 fills | | | | |
| Second | $231.3M | Oct 8 21:52 to Oct 15 23:12 | $72,333,532 | $72,333,262 | $270 | $936,071 |
| Third | $111.8M | Oct 1 23:46 to Oct 15 23:46 | $20,243,954 | $20,243,593 | $361 | $452,567 |

The second and third, together 17% of the notional, received no share. The third has a complete history from its first trade and holds nothing now: over its whole life HL's PnL is $17,963,815.74 and its fill-price cash minus fees plus funding is $17,963,815.75.

### Test 19: recipients of the removed balances

Reproducible via `scripts/trace_destination.py` (about 3.5 GB of requester-pays S3 data). Results in `data/destination_summary.csv`.

The test diffs every Hyperliquid account's USDC between two states, where USDC means perp raw cash (account value minus signed position value) plus spot USDC. Perp and spot transfers therefore cancel and no mark price is needed:

```
residual = USDC at the end − (USDC at the start + fill cash − fees + funding + ledger flows)
```

The states are the clearinghouse snapshot at block 758750000 (20:04:54 UTC Oct 10) and the Artemis account crawls of Oct 10 (about 01:56 UTC) and Oct 11 (about 01:54 UTC), each account taken at its own query time. HL credits some accounts without a ledger event, protocol fees in particular, and those accounts show a positive residual.

**Control window: Artemis Oct 10 → snapshot, no auto-deleveraging.** Of 547,775 accounts, 89 are off by more than $1. Accounts that receive fees show positive residuals, which give the fee split:

| | Residual | Share of protocol fees (net of builder fees) |
|---|---|---|
| Protocol fees in window | $4,061,898 | |
| Assistance Fund | $3,916,792 | 96.43% |
| HLP parent vault | $39,087 | 0.96% |

The remaining 2.61% is consistent with referral rewards, which accrue off-balance until claimed.

**Cascade window: snapshot → Artemis Oct 11.** Of 616,010 accounts, 1,946 are off by more than $1. Two accounts gain large amounts with no event behind them:

| | Residual | Fee share at control rates | Excess |
|---|---|---|---|
| Assistance Fund | $16,585,727 | $4,758,566 | **$11,827,162** |
| HLP parent vault | $168,231 | $47,488 | **$120,744** |
| **Total** | | | **$11,947,906** |

The accounts liquidated through auto-deleveraging during the cascade (backstop fills whose counterparty reads `Auto-Deleveraging`) number 1,400. 1,019 of them show a negative residual beyond $1, with a median of −$6.97. The removal therefore applies to every account liquidated this way, not only the two vaults:

| Removed from | Amount |
|---|---|
| Liquidator and Liquidator 2 (Test 14) | $10,890,262 |
| 1,398 other accounts liquidated through auto-deleveraging | $1,094,011 |
| **Total removed** | **$11,984,273** |

The excess credited to the Assistance Fund and the HLP parent matches the total removed to within −$36K (0.3%). It splits 98.99 / 1.01 between them, against a fee split of 99.01 / 0.99.

Timing points the same way. The Assistance Fund spends its USDC on HYPE as it arrives. Its implied balance, built from fee income at the control share alone, never falls more than $1,485 below zero before the first self-liquidation round at 21:16:04 UTC, which is ordinary timing between fee credits and purchases. After that round it falls to −$11,826,039, so the extra USDC it spent arrived from 21:16 onward. Between 21:16 and 23:20 UTC it spent $14.16M of USDC, mostly buying HYPE on the HYPE/USDC book, before returning to its usual pace of about $0.35M per hour.

Two caveats:

- **Calibration window.** The fee shares come from a different window, which is the most likely source of the 0.3% difference.
- **Snapshot artifacts.** A few accounts show residuals of equal size and opposite sign in the two windows, for example $250,000. Their balances are misstated in the snapshot that both windows share. None of them is a recipient here.

### Test 19b: the same diff between same-block daily snapshots

Reproducible via `scripts/test_account_audit.py` (bundled results, no network) and `scripts/audit_all_accounts.py` (rebuilds them from raw node fills, misc events and Hydromancer Reservoir snapshots).

Test 19 ends its cascade window at the Artemis crawl, which reads each account at its own query time about six hours after the snapshot, and takes the fee shares from one 18-hour window. This test repeats the diff on different end states. Hydromancer Reservoir publishes one clearinghouse snapshot per day with every account at the same block: perp account value and positions, and every spot balance. Six consecutive windows from Oct 7 to Oct 13 give four with no auto-deleveraging, the cascade window (blocks 758750000 to 759830000, 24 hours) and one with a small event. Perp and spot are kept apart:

- **Perp:** change in account value net of position value at the snapshot marks, against fill-price cash − fees + funding + ledger flows.
- **Spot:** change in USDC balance, against USDC-quoted spot fill cash − USDC fees + ledger flows − reward claims.

**Withdrawals are placed by their nonce.** A withdrawal is debited when requested and logged when the bridge finalises it, usually about four minutes later. Placed by log time, a withdrawal in flight at a snapshot leaves a residual in the window before it and the opposite residual in the window after. 29 withdrawals totalling $397,339 were requested before block 758750000 and logged after it. The largest is $249,999 plus its $1 fee, requested 144 seconds before the snapshot, which fits the $250,000 example among the equal-and-opposite residuals Test 19 lists as snapshot artifacts. On that reading the snapshot is right and the event is late. Placed by nonce, 25 of the 29 accounts fall within $1. The nonce is when the request was signed, and a few withdrawals were signed hours before they were applied, so those keep a residual. The largest residual among ordinary accounts in the cascade window, $50,000, is one of them: a $49,999 withdrawal plus its $1 fee, signed 5.6 hours before the end snapshot and logged 71 minutes after it.

**Nothing left the exchange.** Total USDC owed to accounts (perp account value plus spot USDC, all accounts) against deposits less withdrawals:

| Window (UTC) | Change in total | Deposits − withdrawals | Difference |
|---|---|---|---|
| Oct 7 20:12 to Oct 8 20:02 | −$108,509,902 | −$108,289,757 | −$220,146 |
| Oct 8 20:02 to Oct 9 20:12 | −$13,234,584 | −$13,038,313 | −$196,271 |
| Oct 9 20:12 to Oct 10 20:04 | −$90,429,598 | −$90,106,214 | −$323,385 |
| **Oct 10 20:04 to Oct 11 20:09** | −$670,807,586 | −$670,630,310 | **−$177,276** |
| Oct 11 20:09 to Oct 12 20:11 | −$139,706,062 | −$139,406,003 | −$300,059 |
| Oct 12 20:11 to Oct 13 20:03 | +$128,406,467 | +$128,735,770 | −$329,302 |

The difference is $0.2M to $0.3M every day, the size of builder and referral rewards that accrue off-balance until claimed. The cascade day is no different. Had the removed cash been destroyed, that day would show about −$12M.

**The cascade window,** 714,808 accounts:

| Account class | Accounts | Perp, unrecorded | Spot USDC, unrecorded |
|---|---|---|---|
| Liquidator and Liquidator 2 | 2 | −$10,891,435.88 | $0.00 |
| Other accounts liquidated through auto-deleveraging | 1,398 | −$1,067,709.96 | −$2.01 |
| Assistance Fund | 1 | −$7.46 | **+$18,604,327.55** |
| HLP parent | 1 | **+$187,927.21** | $0.00 |
| All other accounts | 713,406 | +$73,060.92 | −$2,611.22 |

Of the 713,406 other accounts, 711,858 are within $1 and the largest is $50,000, the late-applied withdrawal above. Unrecorded money left 1,400 accounts and arrived at two.

The vault figures are Test 15's again (−$5,632,778.29 and −$5,258,657.59). Of the 1,398 other accounts, 1,021 lost more than $1, 365 are within $1 and 12 gained more than $1, $3,119 between them. The net is 69.3% of the $1,539,766 their close-out fills received above `markPx`.

**Two accounts receive unrecorded USDC every day.** Net fees are USDC fees less builder fees.

| Window (UTC) | Net fees | Fund inflow | Share | HLP parent inflow | Share | Accounts liquidated through ADL | Removed from them |
|---|---|---|---|---|---|---|---|
| Oct 7 20:12 to Oct 8 20:02 | $2,874,720 | $2,778,345 | 0.9665 | $28,065.50 | 0.00976 | 79 | $14,524.83 |
| Oct 8 20:02 to Oct 9 20:12 | $3,670,364 | $3,532,553 | 0.9625 | $35,684.08 | 0.00972 | 0 | |
| Oct 9 20:12 to Oct 10 20:04 | $4,463,670 | $4,303,408 | 0.9641 | $43,470.83 | 0.00974 | 0 | |
| **Oct 10 20:04 to Oct 11 20:09** | $7,028,960 | **$18,604,328** | **2.6468** | **$187,927.21** | **0.02674** | 1,400 | $11,959,145.84 |
| Oct 11 20:09 to Oct 12 20:11 | $3,273,152 | $3,151,975 | 0.9630 | $31,840.16 | 0.00973 | 0 | |
| Oct 12 20:11 to Oct 13 20:03 | $3,110,516 | $2,992,623 | 0.9621 | $30,166.69 | 0.00970 | 0 | |

On the four windows with no auto-deleveraging the fund receives 0.9630 of net fees (0.9621 to 0.9641) and the HLP parent 0.009723 (0.009698 to 0.009739). The HLP parent's part of the two is 0.9996%, so what is left after builder fees and referral rewards goes 99% to the Assistance Fund and 1% to HLP.

**On the cascade day both receive more, and the excess is the removed cash, split the same way.**

| | Amount | Share of removed |
|---|---|---|
| Removed from the 1,400 accounts liquidated through auto-deleveraging | $11,959,145.84 | |
| Fund inflow beyond its fee share | $11,835,433.63 | 98.97% |
| 99% of removed | $11,839,554.38 | |
| HLP parent inflow beyond its fee share | $119,581.66 | 0.9999% |
| 1% of removed | $119,591.46 | |
| The two together | $11,955,015.29 | 99.97% |

The HLP parent's figure is $9.80 from 1% of the removed cash, and the fund's is $4,121 from 99% of it. Using the highest and lowest control-window shares instead of their average moves the fund's excess between $11,827,734 and $11,841,774 and the HLP parent's between $119,474 and $119,758. Both ranges contain the 99% and 1% figures.

**Against Test 19.**

| | Test 19 | This test |
|---|---|---|
| Cascade window ends | Artemis crawl, about 01:54 UTC Oct 11, each account at its own query time | block 759830000, 20:09:51 UTC Oct 11, every account |
| Fee shares from | one 18-hour window | four 24-hour windows |
| Fund share of net fees | 0.9643 | 0.9630 |
| HLP parent share of net fees | 0.00962 | 0.00972 |
| Fund beyond its share | $11,827,162 | $11,835,434 |
| HLP parent beyond its share | $120,744 | $119,582 |
| Removed from the two vaults | $10,890,262 | $10,891,436 |
| Removed from the 1,398 other accounts | $1,094,011 | $1,067,710 |
| Received less removed | −$36,367 | −$4,131 |

The two agree on the recipients, the split and the size. Most of the difference in closure is the other accounts' figure, $26,301 higher in Test 19. Test 19 sums only the accounts with a negative residual, which is worth $3,119 on this test's data. The remaining $23,182 was not traced; it lies in the different end states. The vault figure differs by $1,174 because Test 19 takes it from the Test 14 walk and this test from the snapshots, which is the −$1,113 and −$61 of Test 15.

The first window contains the only other auto-deleveraging in these six days, 79 accounts at about 04:00 UTC on Oct 8, with $14,524.83 removed. The fund is $9,988 above its share that day (99% would be $14,380) and the HLP parent $113 above (1% would be $145). That is the right direction and size, at the edge of what the control shares can resolve.

**A second event.** The Reservoir archive lists auto-deleveraging fills on 19 dates since July 2025. Oct 10 is by far the largest and Aug 26 2025, the XPL squeeze, is the clear second: 789 accounts liquidated this way, none of them an HLP vault, receiving $2,451,501 above `markPx`. `misc_events_by_block` is not archived before Sep 27 2025, so the full diff cannot run there, but the fund has no ledger events and its inflow needs only spot snapshots and fills (`audit_all_accounts.py --fund-only`):

| Window (UTC) | Net fees | Fund inflow | Share | Accounts liquidated through ADL |
|---|---|---|---|---|
| Aug 22 00:11 to Aug 23 00:12 | $6,102,232 | $5,784,316 | 0.9479 | 0 |
| Aug 23 00:12 to Aug 24 00:00 | $2,590,934 | $2,452,481 | 0.9466 | 0 |
| Aug 24 00:00 to Aug 25 00:03 | $5,565,653 | $5,279,281 | 0.9485 | 0 |
| Aug 25 00:03 to Aug 26 00:06 | $5,895,722 | $5,588,994 | 0.9480 | 0 |
| **Aug 26 00:06 to Aug 27 00:09** | $4,615,311 | **$5,820,414** | **1.2611** | 789 |
| Aug 27 00:09 to Aug 28 20:05 | $6,492,557 | $6,136,907 | 0.9452 | 0 |
| Aug 28 20:05 to Aug 29 20:07 | $4,562,571 | $4,331,987 | 0.9495 | 0 |

Over the six windows without auto-deleveraging the fund's share is 0.9476 (0.9452 to 0.9495). In the Aug 26 window its inflow is $1,446,991 above that, 59% of what the liquidated accounts received above `markPx`; on Oct 10 the other accounts lost 69% of theirs.

**What this adds to Test 19.**

- **The same recipients and split from different end states.** Every one of 714,808 accounts is read at the same block, over a full day. The amounts close to 0.03% and the HLP parent's 1% closes to $10.
- **Nothing was destroyed.** Total USDC owed to accounts tracks deposits and withdrawals on the cascade day as on the days around it.
- **It is not specific to Oct 10 or to HLP's vaults.** The fund's excess appears again at the Aug 26 event, where no liquidator vault was involved.
- **Still not observed:** an individual credit. No event records either side, so what is measured is the two accounts' totals over the window, not a transfer per round.

### Test 20: the Assistance Fund's purchases and HL's own tally, Oct 1 to Oct 22

Reproducible via `scripts/test_assistance_fund.py` (bundled data). `scripts/extract_fee_tables.py` rebuilds `assistance_fund_vs_fees_5min.csv` from raw node fills for Oct 1 to Oct 22, and `--pull` refreshes `assistance_fund_pnl_samples.csv` from the HL info API.

Test 19 shows the fund's spending running ahead of its fee income from the first round on, in a window that ends five hours later. This test covers 22 days and adds a measurement that needs no balance snapshot: HL's own running total for the fund.

**What can and cannot be seen.** The Assistance Fund (`0xfefe…fefe`) receives trading fees and spends them buying HYPE on the spot book.

- **Money out is fully visible.** Every purchase is a fill. The fund bids in orders of at most $10,000 and has no other kind of fill in these three weeks.
- **Money in has no per-credit record.** The fund's ledger for Oct 1 to Oct 22 holds two `spotTransfer` events and nothing else, which is what Test 4 saw. Its account value is almost all HYPE and moves with the price by hundreds of millions.
- **HL publishes a running total.** The fund's perp account value stays near $1,000 to $2,000 while its `perpAllTime` PnL series climbs by tens of millions a week, so that series is HL's own tally of what has passed through the account. For Oct 2025 the API now serves it as weekly samples.
- **Historical balances are not served.** The HyperEVM read precompiles (`spotBalance`, `withdrawable`, `accountMarginSummary`) answer at any block tag on the public RPC, but with the current state: a call pinned to an Oct 2025 block returns today's core block number and today's balances.

If the fund's income is only fees, both its purchases and HL's tally should track market-wide fees at a steady share.

**Purchases, day by day.** Net fees are USDC fees less builder fees, which are paid on to the order's builder.

| Span (UTC) | USDC fees | Builder fees | Net fees | Fund purchases | Share of net |
|---|---|---|---|---|---|
| Oct 1 to Oct 9, each day | $1.72M to $4.85M | | | | 0.939 to 0.990 |
| Oct 10 00:00 to 21:15 | $6,166,546 | $379,819 | $5,786,727 | $5,580,454 | 0.964 |
| **Oct 10 21:15 to 23:20** | $2,646,505 | $137,137 | $2,509,368 | **$14,253,327** | **5.680** |
| Oct 10 23:20 to 24:00 | $333,961 | $21,399 | $312,562 | $300,874 | 0.963 |
| Oct 11 to Oct 22, each day | $1.23M to $4.92M | | | | 0.927 to 0.979 |

The share is 0.9631 over the 9.9 days before 21:15 on Oct 10 and 0.9586 over the 12 days after 23:20. The fund buys in batches, so its cumulative purchases run up to $586K behind share × cumulative net fees before catching up. The first self-liquidation round is at 21:16:04. From that 5-minute bin on:

| Up to (UTC) | Cumulative purchases beyond 0.9631 × net fees |
|---|---|
| Oct 10 21:15 | $0 |
| Oct 10 21:20 | $335,783 |
| Oct 10 21:30 | $1,508,467 |
| Oct 10 22:00 | $2,596,304 |
| Oct 10 22:30 | $7,273,118 |
| Oct 10 23:00 | $9,927,455 |
| Oct 10 23:20 | $11,836,567 |
| Oct 11 00:00 | $11,836,414 |
| Oct 12 00:00 | $11,842,102 |
| Oct 15 00:00 | $11,846,234 |
| Oct 23 00:00 | $11,671,999 |

After 23:20 the excess stops growing. It averages $11.82M over the following day and a half and is still $11.67M twelve days later, the slow drift being the slightly lower share after the event. Nothing comparable appears on any other day from Oct 1 to Oct 22.

**HL's own tally, week by week.**

| Sample interval (UTC) | HL tally change | Fund purchases | Net fees | Tally / net fees |
|---|---|---|---|---|
| Oct 1 22:36 to Oct 8 23:36 | $20,762,692 | $20,836,454 | $21,590,675 | 0.962 |
| **Oct 8 23:36 to Oct 15 21:56** | **$39,814,724** | $39,843,126 | $29,066,645 | **1.370** |
| Oct 15 21:56 to Oct 22 23:56 | $18,740,693 | $18,627,012 | $19,507,571 | 0.961 |

Two things follow. HL's tally matches the fund's purchases within 0.6% in every interval, so the tally is the fund's income and the fund spends what it receives. And in the weeks either side the tally is 0.961 to 0.962 of net fees, while the week containing Oct 10 is $11,876,041 above that ratio. This measurement depends on neither a balance snapshot nor the fund's buying pattern.

**Size.** The fund's 99% of the removed balances is $11.84M (Test 19b). Its excess is $11.82M by its purchases and $11.88M by HL's tally.

### Test 21: other liquidated accounts, through HL's reported PnL

Reproducible via `scripts/test_adl_accounts.py` (public HL info API; the account list comes from `extract_fee_tables.py`).

Tests 19 and 19b find that the 1,398 other accounts liquidated through auto-deleveraging lost balances too, from account balances. This is the same question put to HL's own reported PnL. If the removal applied to them, HL's PnL for them would fall short of their own fills, by no more than what their close-out fills received above `markPx` (the cap, $1,539,766 across the 1,398, part of which only brings an underwater account back to zero). The check is the one in Test 18b, run on the 25 largest of them by close-out notional.

| Account, by close-out notional | Close-out notional | Cap | HL PnL − own fills | Share of cap | Flat PnL samples |
|---|---|---|---|---|---|
| 4th | $3,082,218 | $29,248 | −$26,003 | 89% | 2025-10-08 to 2025-10-22 |
| 7th | $2,553,066 | $24,227 | −$20,887 | 86% | 2025-04-16 to 2026-10-29 |
| 9th | $1,471,682 | $93,425 | −$75,607 | 81% | 2025-09-24 to 2025-10-15 |
| 10th | $1,133,417 | $10,755 | −$10,097 | 94% | 2025-07-09 to 2025-10-15 |
| 13th | $828,541 | $5,873 | −$5,292 | 90% | 2025-10-08 to 2025-10-15 |
| 14th | $820,306 | $7,784 | −$7,622 | 98% | 2025-10-08 to 2025-10-15 |
| 15th | $787,045 | $3,243 | −$2,980 | 92% | 2025-08-27 to 2025-10-29 |
| 18th | $535,706 | $3,797 | −$2,464 | 65% | 2025-09-17 to 2025-11-19 |
| 24th | $376,867 | $31,770 | −$21,308 | 67% | 2025-10-01 to 2025-10-15 |
| 25th | $322,501 | $1,000 | −$407 | 41% | 2025-10-08 to 2025-11-05 |
| **Total** | | **$211,122** | **−$172,667** | **82%** | |

The other 15 cannot be checked this way. For six the public API no longer serves their Oct 2025 fills, for one it serves them with gaps (the script tests that each fill starts where the previous fill on that coin ended), and eight have no PnL sample at which they held no position on both sides of the event.

All ten accounts that can be checked came out short, by 41% to 98% of the cap, where the traders in Test 18b came out even. Where Test 19b measures the same account from the snapshots, the two methods agree within tens of dollars ($26,003 here against $25,998 there, $75,607 against $75,593, $21,308 against $21,296). The ten are the largest the API can still verify, not a random sample: they lost 82% of their cap, against 69% for all 1,398.

## Limits of external verification

The mechanism is visible in public node data. The self-liquidation fills, their timing and the cash each round leaves behind are all in `node_fills_by_block`. A fill missing from node data cannot be the source of the gap, for two reasons:

- **Position continuity.** HL's `startPosition` agrees with the running position on every vault fill in the cascade hours (Test 14).
- **Amounts.** The removed amounts match the cash left after each round to within $1,151 in total.

Fill completeness holds across the window more broadly as well. Test 13 verifies all 175,642 fills held by a second indexer, with zero mismatches and zero fills unique to that indexer, and Test 10 shows the position book nets to zero across 165 coins, which no lossy fill set would satisfy.

The removal and the matching credit to the fee recipients produce no LedgerUpdate, so neither appears as an event. Both show up only as unexplained balance changes in a full-account diff (Test 19). A second diff between same-block daily snapshots gives the same recipients and amounts, and shows total USDC owed to accounts tracking deposits and withdrawals to within $0.2M on the cascade day, so nothing was destroyed (Test 19b).

The replica_cmds scan (Test 5) and the validator constraints (Test 7) describe how far outside access reaches toward HL's internal state writes.

A third-party intraday equity series for the sub-vaults is not obtainable. The one indexer offering vault equity snapshots at higher-than-daily cadence begins collecting in June 2026, and its vault ledger endpoint returns empty. A snapshot has to be recorded at the moment it describes, and no external party runs HyperCore replay, so Oct 2025 intraday account value cannot be reconstructed after the fact from outside HL. The reconciliation does not need it, because the vaults are flat after every round and the removed amounts follow from cash alone.

The defensible form of the claim is therefore: about $10.89M of Liquidator and Liquidator 2 cash, and $1.09M from 1,398 other accounts, was removed immediately after those accounts were liquidated through auto-deleveraging on Oct 10, in amounts equal to the positive cash each liquidation left behind. The Assistance Fund and the HLP parent vault received it, split like protocol fees. Why the clearinghouse treats this remainder as fee revenue is a design question only HL can answer.

## What this repo intentionally excludes

This repo excludes:

- Raw Strategy A and Strategy B fills (replaced with daily closed_pnl aggregates). The MM-style order flow detail in those vaults' fills is not necessary for reconciliation and is excluded for both repo-size and information-handling reasons.
- HLP parent vault fills (parent is cash-only, doesn't take directional positions; not relevant to the L1/L2 reconciliation).
- Liquidator 3 and Liquidator 4 fills (these vaults were created post-cascade and have no activity in the relevant window).
- Dashboard product code, share-of-volume analysis, touch-flow methodology, or any non-reconciliation HLP analysis.
- Third-party account data. Tests 17, 18 and 19 use other users' fills, ledger events and balances. `verify_from_raw.py` and `trace_destination.py` derive these from raw data at run time and output aggregates without account addresses, apart from the two protocol recipients. No third-party address is bundled, and the cascade extract records only whether a fill's liquidated user is the vault itself. Tests 18b, 19b and 21 follow the same rule: `extract_fee_tables.py` writes its account lists to the gitignored `data/from_raw/`, `test_adl_accounts.py` prints accounts by rank, and the bundled `account_audit_*.csv` tables are aggregated by account class.
