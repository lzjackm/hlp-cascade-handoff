#!/usr/bin/env python3
"""Tests 10-12: position flatness, isolated-margin accounting, uniform-rate charges.

All three run purely from bundled data, no network required.

Test 10 is the important one. If the position book is exactly flat at both ends
of a window, unrealized MTM is zero at both ends under any mark convention, so
no price-deviation story can contribute to the equity change across that window.
The same zero doubles as a fill-completeness check: a missing fill would leave a
phantom open position behind.

Usage:
    python scripts/test_completeness_and_pricing.py
"""
import os

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VAULTS = [
    ("Liquidator", "data/fills/Liquidator_aug1_nov19.parquet", -5_689_537.07),
    ("Liquidator 2", "data/fills/Liquidator_2_oct1_nov19.parquet", -5_304_152.74),
]

# Hydromancer snap anchors (ABCI blocks 758750000 / 759830000) and the
# headline reconciliation window used by scripts/reconcile_cascade.py.
WINDOWS = {
    "Hydromancer snap-to-snap": (
        pd.Timestamp("2025-10-10 20:04:54", tz="UTC"),
        pd.Timestamp("2025-10-11 20:09:51", tz="UTC"),
    ),
    "Headline cascade window": (
        pd.Timestamp("2025-10-01 23:50:00", tz="UTC"),
        pd.Timestamp("2025-10-15 22:10:01", tz="UTC"),
    ),
}

SIDE_SIGN = {"buy": 1, "sell": -1}
# Per-coin sums of float sizes carry rounding noise of order 1e-9 on sizes up to 1e9.
FLAT_TOL = 1e-6


def load(path):
    d = pd.read_parquet(os.path.join(REPO, path))
    d["ts"] = pd.to_datetime(d["ts"], utc=True)
    return d


def test_10_flatness(frames):
    print("=" * 78)
    print("Test 10: position flatness at window boundaries")
    print("=" * 78)
    print(
        "Net signed size per coin over a window is zero if and only if the book\n"
        "returns to its starting position. Combined with the cash-only vault\n"
        "snapshots at both anchors, zero means flat at both ends.\n"
    )
    for wname, (t0, t1) in WINDOWS.items():
        print(f"--- {wname}: {t0} -> {t1}")
        for name, d, _ in frames:
            w = d[(d.ts > t0) & (d.ts <= t1)].copy()
            if w.empty:
                print(f"    {name:14} no fills in window")
                continue
            sign = w.side.map(SIDE_SIGN)
            if sign.isna().any():
                raise ValueError(f"unmapped side values: {sorted(w.side[sign.isna()].unique())}")
            w["signed"] = w.sz * sign
            net = w.groupby("coin").signed.sum()
            nonzero = net[net.abs() > FLAT_TOL]
            print(
                f"    {name:14} fills={len(w):>7,}  coins={net.size:>3}  "
                f"coins_with_nonzero_net={len(nonzero):>3}  "
                f"max_abs_net={net.abs().max():.1e}"
            )
            if len(nonzero):
                print("      residual positions:")
                for c, v in nonzero.sort_values(key=abs, ascending=False).head(8).items():
                    print(f"        {c:12} {v:>18,.6f}")
        print()


def test_11_isolated(frames):
    print("=" * 78)
    print("Test 11: isolated-margin accounting")
    print("=" * 78)
    print(
        "Hypothesis: closedPnl on liquidated isolated-margin positions overstates\n"
        "cash accrual because realized PnL is capped by the isolated margin bucket.\n"
        "Direction is right (negative residual, no ledger footprint, cascade-only),\n"
        "so the test is one of magnitude.\n"
    )
    t0, t1 = WINDOWS["Hydromancer snap-to-snap"]
    for name, d, residual in frames:
        w = d[(d.ts > t0) & (d.ts <= t1)]
        if w.empty:
            continue
        by_dir = (
            w.groupby("dir_raw")
            .agg(n=("closed_pnl", "size"), closed_pnl=("closed_pnl", "sum"))
            .sort_values("closed_pnl", key=abs, ascending=False)
        )
        iso_mask = w.dir_raw.str.contains("Isolated", case=False, na=False)
        iso = w[iso_mask].closed_pnl.sum()
        print(f"--- {name}  (residual {residual:,.2f})")
        print(by_dir.to_string(float_format=lambda x: f"{x:,.2f}"))
        print(
            f"    isolated Σclosed_pnl = {iso:,.2f}  "
            f"= {iso / residual * 100:+.2f}% of residual "
            f"({'wrong sign, ' if (iso > 0) == (residual > 0) else ''}"
            f"{abs(iso / residual):.4f}x magnitude)"
        )
        print()


def test_12_uniform_rate(frames):
    print("=" * 78)
    print("Test 12: uniform-rate charge (notional haircut or leader profit share)")
    print("=" * 78)
    print(
        "If the residual were a structural charge at a fixed rate, the implied rate\n"
        "would agree across both vaults on some common base.\n"
    )
    t0, t1 = WINDOWS["Hydromancer snap-to-snap"]
    rows = []
    for name, d, residual in frames:
        w = d[(d.ts > t0) & (d.ts <= t1)].copy()
        if w.empty:
            continue
        w["ntl"] = w.px * w.sz
        closing = w[w.closed_pnl != 0]
        rows.append(
            {
                "vault": name,
                "residual": residual,
                "notional_all": w.ntl.sum(),
                "notional_closing": closing.ntl.sum(),
                "closed_pnl": w.closed_pnl.sum(),
                "pct_of_notional": abs(residual) / w.ntl.sum() * 100,
                "pct_of_closing_notional": abs(residual) / closing.ntl.sum() * 100,
                "pct_of_closed_pnl": abs(residual) / w.closed_pnl.sum() * 100,
            }
        )
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(t.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))
    if len(t) == 2:
        print("\n  implied-rate agreement between the two vaults:")
        for col in ("pct_of_notional", "pct_of_closing_notional", "pct_of_closed_pnl"):
            a, b = t[col].iloc[0], t[col].iloc[1]
            print(f"    {col:26} {a:8.4f}% vs {b:8.4f}%   ratio={a / b:.2f}x")
        print(
            "\n  Counter-observation (n=2, suggestive only): the residuals differ by "
            f"{abs(t.residual.iloc[0] / t.residual.iloc[1]) :.2f}x in dollars while "
            f"notional differs {t.notional_all.iloc[1] / t.notional_all.iloc[0]:.2f}x and "
            f"closed_pnl {t.closed_pnl.iloc[0] / t.closed_pnl.iloc[1]:.2f}x. Every activity\n"
            "  normalization makes the two vaults less alike, not more."
        )
    print()


def main():
    frames = [(name, load(path), res) for name, path, res in VAULTS]
    test_10_flatness(frames)
    test_11_isolated(frames)
    test_12_uniform_rate(frames)


if __name__ == "__main__":
    main()
