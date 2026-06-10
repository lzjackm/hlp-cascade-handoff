# Methodology

This document details the reconciliation approach and the tests that have been run.

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

About 99% of the cascade-window residual lands on the Oct 11 hydromancer-aligned date (which covers Oct 10 14:00 UTC through Oct 11 14:00 UTC under Hydromancer's snap timing).

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

## Limits of external verification

The fills data here comes from `s3://hl-mainnet-node-data/node_fills_by_block/`, and the independent per-event walk that confirmed the same gap used `misc_events_by_block` from the same bucket. Both are produced by HL's node-data pipeline, so they share an upstream. A fill emitted by the matching engine but never written to node data would be invisible to both, and externally indistinguishable from a balance write that produces no logged event.

At the time of the original analysis, 2,000 sampled fills matched HL's `userFillsByTime` API to four decimal places. A full-window independent re-pull from that API is no longer possible because it retains only recent fills per user, and the cascade window contains 185K+ fills for Liquidator alone.

The defensible form of the claim is therefore: roughly $10.9M of combined Liquidator and Liquidator 2 equity change during the cascade window has no corresponding event in any externally visible channel (fills, funding, ledger updates, or chain-committed actions). Whether the mechanism is an unrecorded fill or an unlogged state write cannot be distinguished from outside HL.

## What this repo intentionally excludes

This repo excludes:

- Raw Strategy A and Strategy B fills (replaced with daily closed_pnl aggregates). The MM-style order flow detail in those vaults' fills is not necessary for reconciliation and is excluded for both repo-size and information-handling reasons.
- HLP parent vault fills (parent is cash-only, doesn't take directional positions; not relevant to the L1/L2 reconciliation).
- Liquidator 3 and Liquidator 4 fills (these vaults were created post-cascade and have no activity in the relevant window).
- Dashboard product code, share-of-volume analysis, touch-flow methodology, or any non-reconciliation HLP analysis.
