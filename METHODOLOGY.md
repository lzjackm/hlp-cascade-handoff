# Methodology

This document details the reconciliation approach and the eliminative tests performed.

## Reconciliation identity

For each HLP sub-vault, between any two timestamps `t0` and `t1`, the following identity should hold:

```
pnl_since_inception(t1) - pnl_since_inception(t0)  ≈  Σ fills.closedPnl(t0..t1)  +  Σ funding(t0..t1)
```

The left side is HL's published lifetime PnL number for the vault, sampled at the two timestamps via the `vault_pnl` API. The right side is reconstructed from individual fill events and funding events, both of which HL also publishes.

For vaults that hold no inventory at the snapshot boundaries (true for Liquidator and Liquidator 2 at every Hydromancer snap), MTM contribution is zero and this identity should hold to noise level.

## What the data shows

| Window | Vault | pnl_si Δ | Fills + funding | Gap |
|---|---|---|---|---|
| 2025-08-20 → 2025-09-03 | Liquidator | +$48,473 | +$48,679 | −$206 |
| 2025-09-17 → 2025-10-01 | Liquidator | +$348,371 | +$351,141 | −$2,770 |
| **2025-10-01 → 2025-10-15 (cascade)** | **Liquidator** | **+$31,393,998** | **+$37,083,535** | **−$5,689,537** |
| **2025-10-01 → 2025-10-15 (cascade)** | **Liquidator 2** | **+$9,592,827** | **+$14,896,979** | **−$5,304,152** |
| 2025-10-29 → 2025-11-19 (covers Nov 12 $5.5M event) | Liquidator | −$5,497,447 | −$5,496,437 | −$1,010 |
| 2025-10-01 → 2025-10-15 (cascade) | Strategy A | +$2,527,088 | +$2,450,044 | +$77,044 |
| 2025-10-01 → 2025-10-15 (cascade) | Strategy B | −$2,740,462 | −$2,607,455 | −$133,006 |

The identity reconciles to under $3K for L1 across non-cascade windows and to $1,010 even when L1 has a $5.5M magnitude loss (Nov 12). It fails by $5.69M and $5.30M for the same vaults during the Oct 10-11 cascade window.

## Per-day decomposition of the cascade window

The gap is concentrated on a single Hydromancer-aligned date.

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

99.1% of the cascade-window residual lands on the Oct 11 hydromancer-aligned date (which covers Oct 10 14:00 UTC through Oct 11 14:00 UTC under Hydromancer's snap timing).

## Tests run, with results

Each test below describes a specific check, what data was used, and the empirical result.

### Test 1: HL API internal identity (`account_value Δ = pnl_since_inception Δ + flow_cum Δ`)

Verified for both vaults across the cascade window. Identity holds to $0.01.

| Vault | account_value Δ | pnl_since_inception Δ | flow_cum Δ | Identity check |
|---|---|---|---|---|
| Liquidator | +$4,398,737 | +$31,393,998 | −$26,995,262 | −$0.00 |
| Liquidator 2 | +$22,500,000 | +$9,592,827 | +$12,907,173 | $0.00 |

HL's three published numbers per vault are self-consistent. The gap is not in a miscounted flow.

### Test 2: Pipeline flow_sum vs HL API flow_cum

The `vault_flows.csv` (derived from `userNonFundingLedgerUpdates`) reconciles exactly to HL's published `flow_cum` delta for the same window:

| Vault | HL API flow_cum Δ | vault_flows sum | Diff |
|---|---|---|---|
| Liquidator | −$26,995,261.54 | −$26,995,261.54 | $0.00 |
| Liquidator 2 | +$12,907,173.13 | +$12,907,173.13 | $0.00 |

No nonFundingLedger event type that touches L1 or L2 is missed.

### Test 3: ADL fills enumeration

`dir='Auto-Deleveraging'` fills exist in `s3://hl-mainnet-node-data/node_fills_by_block/` (35,022 fills in cascade peak hour Oct 10 21:00-22:00 UTC).

HLP vaults appear as counterparty in 89% of those events under `dir='Liquidated Cross Long/Short'`. Those counterparty fills are already included in the per-vault closed_pnl sum in this repo's data. Adding them does not close the gap because they are already counted.

### Test 4: Cash flow destination search

Across 18 of 24 hours in the hydro-aligned Oct 11 window, every `LedgerUpdate` event with USDC-bearing semantics was enumerated and aggregated by destination address.

| Candidate destination | Net USDC delta during window | Result |
|---|---|---|
| Assistance Fund (`0xfefefefefefefefefefefefefefefefefefefefe`) | 0 events of any kind | Not the destination |
| HLP parent (`0xdfc24b077bc1425ad1dea75bcb6f8158e10df303`) scope=perp pnl_si Δ | +$0.40M | Not the destination |
| Burn address (`0x222222...22`) | −$10.46M net OUTFLOW | Wrong direction |
| Any `0x2000...XXXX` system-prefixed address | Largest abs is −$5.7M, not net inflow | Not the destination |
| Any non-system address | Largest net inflow is $9.0M (round-number user deposit) | Not the destination |

No address in the LedgerUpdate stream receives within 20% of the $10.99M magnitude during the cascade window.

### Test 5: Replica_cmds chain-commit scan

Scanned `s3://hl-mainnet-node-data/replica_cmds/2025-09-27T09:28:27Z/20251010/758800000.lz4` (2.0GB compressed, 10,000 blocks, 9.7M actions, covers 21:15-21:29 UTC).

Total distinct action types found: 39. The following expected types have zero occurrences in the cascade peak file:

`Adl`, `AutoDeleverag`, `Settlement`, `SystemUsd`, `SystemAligned`, `AlignedQuote`, `InsuranceFund`, `PerformAdl`, `AdlShortfall`, `SetReserve`, `ModifyVault`, `Mint`, `Burn`, `ChainCommit`.

HLP-touching action types present: `liquidate` (11,494, pure routing instruction with no settlement state), `vaultTransfer` (128 touching HLP parent, captured in vault_flows), `NetChildVaultPositionsAction` (74 touching only Strategy A and Strategy B).

`VoteGlobalAction` instances (231) all have the uniform schema `{type: "VoteGlobalAction", assetsAtOpenInterestCap: [22,34,52,57,106,179]}`. No ADL discriminant variant present.

### Test 6: SetGlobalAction mark vs trade VWAP

Validator-set marks from `SetGlobalAction` diverge from same-minute trade VWAP during cascade peak. Examples:

| Minute (UTC) | Coin | Validator mark | Trade VWAP | Differential |
|---|---|---|---|---|
| 21:17 | BTC | $108,162.80 | $106,952.95 | +$1,209.85 (+1.13%) |
| 21:20 | ETH | $3,416.28 | $3,313.35 | +$102.92 (+3.11%) |
| 21:21 | SOL | $155.40 | $141.91 | +$13.49 (+9.51%) |
| 21:22 | BTC | $107,682.75 | $109,273.25 | −$1,590.50 (−1.46%) |

Sign reverses between chaos phase (validator marks above trade VWAP, 21:13-21:21) and cleanup phase (validator marks below trade VWAP, 21:22-21:29).

Sum across all HLP-counterparty ADL fills in hour 21 of `(execution_price − validator_mark) × signed_size`: +$258.9M. Magnitude is two orders larger than the cascade gap. Does not affect realized `pnl_si` because L1 and L2 hold zero inventory at the Hydromancer snap boundary.

### Test 7: Validator-side state-write access

Direct contact with a Hyperliquid validator confirmed three constraints on external visibility of the relevant state writes:

1. No cached pre-cascade RocksDB snapshot exists on validator nodes that started accumulating after the event.
2. The `--write-system-and-core-writer-actions` flag (which would log System-class CoreWriter actions) creates indexing overhead that drops a node out of the active set, so no production validator runs it.
3. The `replica_cmds` snapshot is not sufficient for full replay reproducing the System-action state writes.

## What the data does not include in this repo

This repo intentionally excludes:

- Raw Strategy A and Strategy B fills (replaced with daily closed_pnl aggregates). The MM-style order flow detail in those vaults' fills is not necessary for reconciliation and is excluded for both repo-size and information-handling reasons.
- HLP parent vault fills (parent is cash-only, doesn't take directional positions; not relevant to the L1/L2 reconciliation).
- Liquidator 3 and Liquidator 4 fills (these vaults were created post-cascade and have no activity in the relevant window).
- Dashboard product code, share-of-volume analysis, touch-flow methodology, or any non-reconciliation HLP analysis.
