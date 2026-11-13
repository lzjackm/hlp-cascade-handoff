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
- **No ledger event.** The removal is a balance change without a LedgerUpdate, which is why the destination search (Test 4) and the per-event walk found nothing to attribute it to.

Tests 14 to 18 below verify each step. The account that receives the removed balance is the one open item (see "Limits of external verification").

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

## Limits of external verification

The mechanism is visible in public node data. The self-liquidation fills, their timing and the cash each round leaves behind are all in `node_fills_by_block`. A fill missing from node data cannot be the source of the gap, for two reasons:

- **Position continuity.** HL's `startPosition` agrees with the running position on every vault fill in the cascade hours (Test 14).
- **Amounts.** The removed amounts match the cash left after each round to within $1,151 in total.

Fill completeness holds across the window more broadly as well. Test 13 verifies all 175,642 fills held by a second indexer, with zero mismatches and zero fills unique to that indexer, and Test 10 shows the position book nets to zero across 165 coins, which no lossy fill set would satisfy.

What public data does not show is where the removed balance goes. The removal produces no fill, funding payment or LedgerUpdate, and three candidate destinations are excluded:

- **The auto-deleveraged traders** do not receive it (Test 18).
- **HLP's other vaults** reconcile without it.
- **Ledger recipients:** no LedgerUpdate recipient matches its size (Test 4).

The replica_cmds scan (Test 5) and the validator constraints (Test 7) describe how far outside access reaches toward HL's internal state writes.

A third-party intraday equity series for the sub-vaults is not obtainable. The one indexer offering vault equity snapshots at higher-than-daily cadence begins collecting in June 2026, and its vault ledger endpoint returns empty. A snapshot has to be recorded at the moment it describes, and no external party runs HyperCore replay, so Oct 2025 intraday account value cannot be reconstructed after the fact from outside HL. The reconciliation does not need it, because the vaults are flat after every round and the removed amounts follow from cash alone.

The defensible form of the claim is therefore: about $10.89M of Liquidator and Liquidator 2 cash was removed immediately after the vaults' own liquidation rounds on Oct 10, in amounts equal to the positive cash each round left behind. Which account receives that remainder, if any, can only be answered from inside HL.

## What this repo intentionally excludes

This repo excludes:

- Raw Strategy A and Strategy B fills (replaced with daily closed_pnl aggregates). The MM-style order flow detail in those vaults' fills is not necessary for reconciliation and is excluded for both repo-size and information-handling reasons.
- HLP parent vault fills (parent is cash-only, doesn't take directional positions; not relevant to the L1/L2 reconciliation).
- Liquidator 3 and Liquidator 4 fills (these vaults were created post-cascade and have no activity in the relevant window).
- Dashboard product code, share-of-volume analysis, touch-flow methodology, or any non-reconciliation HLP analysis.
- Third-party account data. Tests 17 and 18 use other users' fills, ledger events and balances. `verify_from_raw.py` derives these from raw node data at run time and prints results without account addresses. No third-party address is bundled, and the cascade extract records only whether a fill's liquidated user is the vault itself.
