#!/usr/bin/env python3
"""Tests 14-16: the backstop vaults were liquidated themselves during the cascade.

Between 21:16 and 21:20 UTC on Oct 10 2025, Liquidator and Liquidator 2 are the
liquidated user on their own backstop fills (liquidation.method == "backstop" and
liquidation.liquidatedUser == the vault's own address). Each such block closes the
vault's entire book against auto-deleveraged traders and leaves the vault holding
only cash according to its fills. That cash is absent from the vault's account
value afterwards, and the cash held at the end of each round adds up to the
cascade-window gap.

Test 14: the self-liquidation rounds, the cash each one leaves behind, and the
         vault equity at which each round fires.
Test 15: reconciliation of the headline gap and of the snapshot-to-snapshot
         account value change.
Test 16: the Nov 12 control, where Liquidator is liquidated once with a negative
         remainder that stays on the account.

Runs from bundled data only, no network required.

Usage:
    python scripts/test_self_liquidation.py
"""
import os

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, "data")

VAULTS = {
    "Liquidator": dict(address="0x2e3d94f0562703b25c83308a05046ddaf9a8dd14",
                       fills="Liquidator_aug1_nov19.parquet"),
    "Liquidator 2": dict(address="0xb0a55f13d22f66e6d495ac98113841b2326e9540",
                         fills="Liquidator_2_oct1_nov19.parquet"),
}
SNAP_BLOCK = 758750000  # 2025-10-10 20:04:54.218 UTC, same block as the Hydromancer snap
SNAP_BLOCK_END = 759830000  # 2025-10-11 20:09:51.920 UTC
EXTRACT = "cascade_L1_L2_20251010_h20_h21.parquet"
SIDE_SIGN = {"buy": 1, "sell": -1}


def read_csv(name):
    d = pd.read_csv(os.path.join(DATA, name))
    d["ts"] = pd.to_datetime(d["ts"], utc=True, format="ISO8601")
    return d


def load_extract():
    return pd.read_parquet(os.path.join(DATA, "fills", EXTRACT))


def load_fills(vault):
    d = pd.read_parquet(os.path.join(DATA, "fills", VAULTS[vault]["fills"]))
    d["ts"] = pd.to_datetime(d["ts"], utc=True)
    return d


def snapshot(vault, block):
    s = read_csv("snapshot_account_values.csv")
    row = s[(s.vault == vault) & (s.block == block)].iloc[0]
    return row.ts, float(row.account_value)


def is_self_liquidation(d):
    """Liquidator-side signature in the bundled fills, which lack the liquidation field.

    Taking over a liquidated long is a buy and taking over a short is a sell. When the
    vault is the liquidated user it sells its long or buys back its short instead.
    """
    liq = d.dir_raw.str.startswith("Liquidated")
    return liq & ((d.dir_raw.str.endswith("Long") & (d.side == "sell"))
                  | (d.dir_raw.str.endswith("Short") & (d.side == "buy")))


def walk(vault, ex=None):
    """Cash walk from the snapshot through the vault's Oct 10 20:00-22:00 fills.

    Returns one row per self-liquidation round plus the startPosition check.
    cash_after is the vault's cash right after the round according to its fills;
    the book is flat at that point, so it does not depend on any mark price.
    """
    ex = load_extract() if ex is None else ex
    d = ex[(ex.vault == vault) & (ex.block > SNAP_BLOCK)].sort_values(["block", "idx"])
    t0, cash = snapshot(vault, SNAP_BLOCK)
    fund = read_csv("funding_hourly_20251010.csv")
    flows = read_csv("vault_flows.csv")
    events = sorted(
        [(r.ts, r.delta_usd) for r in fund[fund.vault_name == vault].itertuples()]
        + [(r.ts, r.delta_usd) for r in flows[(flows.vault_name == vault) & (flows.ts > t0)
                                              & (flows.ts <= d.ts.max())].itertuples()])
    pos, mismatches, rounds = {}, 0, []
    for blk, g in d.groupby("block", sort=True):
        t = g.ts.iloc[0]
        while events and events[0][0] < t:
            cash += events.pop(0)[1]
        conc = 0.0
        for r in g.itertuples():
            if abs(pos.get(r.coin, 0.0) - r.start_position) > 1e-6 * max(1.0, abs(r.start_position)):
                mismatches += 1
            s = SIDE_SIGN[r.side]
            cash += -s * r.sz * r.px - r.fee
            pos[r.coin] = r.start_position + s * r.sz
            if r.liq_role == "self":
                conc += (r.liq_mark_px - r.px) * r.sz * s
        own = g[g.liq_role == "self"]
        if len(own):
            rounds.append(dict(block=blk, ts=t, fills=len(own), other_fills=len(g) - len(own),
                               notional=float((own.px * own.sz).sum()), concession=conc, cash_after=cash,
                               open_after=sum(abs(p) > 1e-9 for p in pos.values())))
    r = pd.DataFrame(rounds)
    # A round leaves the vault flat with cash_after. If that balance is removed, the next
    # round starts from zero, so round k removes cash_after[k] - cash_after[k-1].
    r["removed"] = r.cash_after.diff().fillna(r.cash_after)
    # Equity at the current mark just before the round: closing at markPx instead of the
    # fill prices would have left cash_after - concession.
    r["equity_at_mark_fills_only"] = r.cash_after - r.concession
    r["equity_at_mark_with_removal"] = r.equity_at_mark_fills_only - r.cash_after.shift(1).fillna(0.0)
    return r, mismatches, len(d)


def headline(vault):
    """Same window and gap as scripts/reconcile_cascade.py, plus the fill-price cash."""
    vp = read_csv("vault_pnl_snapshots.csv")
    api = vp[(vp.vault_name == vault) & (vp.scope == "all")
             & (vp.ts.dt.date >= pd.Timestamp("2025-10-01").date())
             & (vp.ts.dt.date <= pd.Timestamp("2025-10-15").date())].sort_values("ts")
    pre, post = api.iloc[0], api.iloc[-1]
    f = load_fills(vault)
    w = f[(f.ts >= pre.ts) & (f.ts <= post.ts)]
    fund = read_csv("funding.csv")
    fu = fund[(fund.vault_name == vault) & (fund.ts >= pre.ts) & (fund.ts <= post.ts)].delta_usd.sum()
    cash = float((-w.side.map(SIDE_SIGN) * w.sz * w.px).sum())
    gap = post.pnl_since_inception - pre.pnl_since_inception - w.closed_pnl.sum() - fu
    return dict(t0=pre.ts, t1=post.ts, gap=gap, fee=w.fee.sum(), cash_minus_cpnl=cash - w.closed_pnl.sum())


def position_at(signed, coin, h, s0, s1):
    """Position in coin at time h, given the book is flat at both snapshots s0 and s1."""
    c = signed[signed.coin == coin]
    if h <= s0:
        return -c[(c.ts > h) & (c.ts <= s0)].s.sum()
    if h <= s1:
        return c[(c.ts > s0) & (c.ts <= h)].s.sum()
    return c[(c.ts > s1) & (c.ts <= h)].s.sum()


def funding_in_window(vault, f, s0, s1):
    """Funding earned inside (s0, s1].

    funding.csv comes from the HL userFunding endpoint, which returns older funding as
    one row per coin per UTC day stamped 00:00 (delta.nSamples hourly payments folded
    in). A row is assigned to the window by checking where the vault actually held that
    coin at the top of each hour of that day.
    """
    fund = read_csv("funding.csv")
    days = fund[(fund.vault_name == vault) & (fund.ts >= s0.normalize()) & (fund.ts <= s1)]
    signed = f.assign(s=f.side.map(SIDE_SIGN) * f.sz)
    inside, excluded = 0.0, []
    for r in days.itertuples():
        hours = pd.date_range(r.ts, r.ts + pd.Timedelta(hours=23), freq="h")
        held = [h for h in hours if abs(position_at(signed, r.coin, h, s0, s1)) > 1e-6]
        n_in = sum(s0 < h <= s1 for h in held)
        if held and n_in not in (0, len(held)):
            raise ValueError(f"{vault} {r.coin} {r.ts.date()}: held both inside and outside the window")
        if n_in:
            inside += r.delta_usd
        else:
            excluded.append((r.ts.date(), r.coin, r.delta_usd, [str(h.time()) for h in held]))
    return inside, excluded


def test_14(results):
    print("=" * 78)
    print("Test 14: self-liquidation rounds and the cash each one leaves behind")
    print("=" * 78)
    for vault, (r, mismatches, n) in results.items():
        print(f"\n--- {vault}: {len(r)} rounds, "
              f"{r.ts.iloc[0].strftime('%H:%M:%S')} -> {r.ts.iloc[-1].strftime('%H:%M:%S')} UTC Oct 10")
        print(f"    startPosition vs running position from flat at the snapshot: "
              f"{mismatches} mismatches over {n:,} fills")
        print(f"    rounds leaving an open position: {(r.open_after > 0).sum()};  "
              f"rounds with other fills in the same block: {(r.other_fills > 0).sum()}")
        t = r.assign(time=r.ts.dt.strftime("%H:%M:%S"))[
            ["time", "block", "fills", "notional", "concession", "cash_after", "removed",
             "equity_at_mark_fills_only", "equity_at_mark_with_removal"]]
        print(t.to_string(index=False, float_format=lambda x: f"{x:,.0f}"))
        print(f"    Σ removed = ${r.removed.sum():,.2f}")
        print(f"    equity at mark when each round fires: with removal {r.equity_at_mark_with_removal.min():,.0f} "
              f"to {r.equity_at_mark_with_removal.max():,.0f} (all negative: "
              f"{bool((r.equity_at_mark_with_removal < 0).all())}); fills only "
              f"{r.equity_at_mark_fills_only.min():,.0f} to {r.equity_at_mark_fills_only.max():,.0f}")
    print()


def test_15(results):
    print("=" * 78)
    print("Test 15: reconciliation with the removed balances")
    print("=" * 78)
    print("\n--- Headline window (gap as in scripts/reconcile_cascade.py)")
    print("    off-fill change = gap + fees - (Σ fill-price cash - Σ closedPnl);  residual = off-fill + Σ removed")
    for vault, (r, _, _) in results.items():
        h = headline(vault)
        off = h["gap"] + h["fee"] - h["cash_minus_cpnl"]
        print(f"    {vault:13} gap {h['gap']:>15,.2f}  fees {h['fee']:>10,.2f}  cash-cpnl {h['cash_minus_cpnl']:>11,.2f}  "
              f"off-fill {off:>15,.2f}  removed {r.removed.sum():>14,.2f}  residual {off + r.removed.sum():>10,.2f}")
    print("\n--- Snapshot to snapshot (blocks 758750000 -> 759830000)")
    for vault, (r, _, _) in results.items():
        s0, e0 = snapshot(vault, SNAP_BLOCK)
        s1, e1 = snapshot(vault, SNAP_BLOCK_END)
        f = load_fills(vault)
        w = f[(f.ts > s0) & (f.ts <= s1)]
        cash = float((-w.side.map(SIDE_SIGN) * w.sz * w.px).sum())
        flows = read_csv("vault_flows.csv")
        fl = flows[(flows.vault_name == vault) & (flows.ts > s0) & (flows.ts <= s1)].delta_usd.sum()
        fu, excluded = funding_in_window(vault, f, s0, s1)
        fills_only = e0 + cash - w.fee.sum() + fu + fl
        print(f"    {vault:13} start {e0:>14,.2f}  fill cash {cash:>15,.2f}  fees {w.fee.sum():>10,.2f}  "
              f"funding {fu:>11,.2f}  flows {fl:>16,.2f}")
        print(f"    {'':13} reported {e1:>14,.2f}  predicted from fills {fills_only:>15,.2f} (diff {e1 - fills_only:>14,.2f})  "
              f"with removal {fills_only - r.removed.sum():>15,.2f} (diff {e1 - fills_only + r.removed.sum():>10,.2f})")
        for day, coin, usd, held in excluded:
            print(f"    {'':13} funding row {day} {coin} {usd:,.2f} excluded: held only at {', '.join(held)} UTC")
    print()


def test_16():
    print("=" * 78)
    print("Test 16: Nov 12 control, one self-liquidation with a negative remainder")
    print("=" * 78)
    ex = load_extract()
    for vault in VAULTS:
        e = ex[ex.vault == vault]
        sig, lab = is_self_liquidation(e), e.liq_role == "self"
        print(f"    {vault:13} Oct 10 extract: signature fills {sig.sum():>6,}, liquidation-field fills "
              f"{lab.sum():>6,}, disagreements {(sig != lab).sum()}")
    vault = "Liquidator"
    f = load_fills(vault)
    sl = f[is_self_liquidation(f)]
    print(f"\n    {vault} self-liquidation fills by hour, Aug 1 - Nov 19:")
    print(sl.groupby(sl.ts.dt.strftime("%Y-%m-%d %H:00")).agg(
        fills=("sz", "size"), coins=("coin", "nunique"), closed_pnl=("closed_pnl", "sum")
    ).to_string(float_format=lambda x: f"{x:,.0f}"))
    vp = read_csv("vault_pnl_snapshots.csv")
    vp = vp[(vp.vault_name == vault) & (vp.scope == "all")].set_index("ts").sort_index()
    t0 = vp.loc["2025-10-29"].index[0]
    av0 = vp.loc[t0, "account_value"]
    rnd = sl[sl.ts.dt.date == pd.Timestamp("2025-11-12").date()].ts.max()
    w = f[(f.ts > t0) & (f.ts <= rnd)]
    cash = float((-w.side.map(SIDE_SIGN) * w.sz * w.px).sum() - w.fee.sum())
    flows, fund = read_csv("vault_flows.csv"), read_csv("funding.csv")
    fl = flows[(flows.vault_name == vault) & (flows.ts > t0) & (flows.ts <= rnd)].delta_usd.sum()
    fu = fund[(fund.vault_name == vault) & (fund.ts > t0) & (fund.ts <= rnd)].delta_usd.sum()
    net = (w.side.map(SIDE_SIGN) * w.sz).groupby(w.coin).sum()
    t1 = vp.loc["2025-11-12"].index[-1]
    after = flows[(flows.vault_name == vault) & (flows.ts > t1) & (flows.ts <= "2025-11-20")]
    print(f"\n    start {t0} account value {av0:,.2f}")
    print(f"    cash after the {rnd.strftime('%H:%M:%S.%f')[:-3]} UTC round from fills, funding and flows: "
          f"{av0 + cash + fl + fu:,.2f}  (open positions: {int((net.abs() > 1e-6).sum())})")
    print(f"    HL account value {t1}: {vp.loc[t1, 'account_value']:,.2f}  "
          f"(diff {vp.loc[t1, 'account_value'] - (av0 + cash + fl + fu):,.2f})")
    print("    next flows: " + ", ".join(f"{r.ts:%Y-%m-%d %H:%M} {r.kind} {r.delta_usd:,.2f}" for r in after.itertuples()))
    print()


def main():
    ex = load_extract()
    results = {vault: walk(vault, ex) for vault in VAULTS}
    test_14(results)
    test_15(results)
    test_16()
    total = sum(r.removed.sum() for r, _, _ in results.values())
    print(f"Total removed across both vaults: ${total:,.2f}")


if __name__ == "__main__":
    main()
