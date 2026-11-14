#!/usr/bin/env python3
"""Test 13b: a third retrieval path for the cascade fills, covering the whole window.

Re-pulls every Liquidator and Liquidator 2 fill between the Oct 1 and Oct 15
vault_pnl snapshots from Hydromancer, which keeps a backfill of HL's own fills
API, and compares it with the bundled parquets trade id by trade id.

Test 13 does the same against Hypedexer, whose index stops at 21:29:07 on Oct 10.
This pull has no such hole, so it also covers the 34,827 fills Test 13 could not
compare.

Requires a Hydromancer API key:
    export HYDROMANCER_API_KEY=...
    python scripts/crosscheck_hydromancer.py

Runtime is roughly 2 minutes (about 110 paged requests).
"""
import os
import sys
import time

import pandas as pd
import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://api.hydromancer.xyz/info"
PAGE = 2000

# (vault, address, bundled parquet, first and last scope=all snapshot of the cascade window)
TARGETS = [
    ("Liquidator", "0x2e3d94f0562703b25c83308a05046ddaf9a8dd14",
     "data/fills/Liquidator_aug1_nov19.parquet", "2025-10-01 23:50:00.008", "2025-10-15 22:10:00.103"),
    ("Liquidator 2", "0xb0a55f13d22f66e6d495ac98113841b2326e9540",
     "data/fills/Liquidator_2_oct1_nov19.parquet", "2025-10-01 23:50:00.008", "2025-10-15 23:50:00.052"),
]


def post(body, key, retries=6):
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60)
        except requests.RequestException as exc:
            last = type(exc).__name__
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        last = f"HTTP {r.status_code}"
        if r.status_code in (400, 401, 403, 404, 422):
            break
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{body.get('type')} failed: {last}")


def pull(user, start_ms, end_ms, key):
    """All fills for `user` in [start, end], paged with the time_txIndex cursor."""
    seen, out = set(), []
    body = {"type": "userFillsByTime", "user": user, "startTime": start_ms, "endTime": end_ms}
    while True:
        page = post(body, key)
        fresh = [f for f in page if (f["tid"], f["side"]) not in seen]
        seen.update((f["tid"], f["side"]) for f in fresh)
        out += fresh
        if len(page) < PAGE or not fresh:
            break
        last = page[-1]
        body = {"type": "userFillsByTime", "user": user,
                "cursor": f"{last['time']}_{last['txIndex']}", "endTime": end_ms}
        time.sleep(0.15)
    return pd.DataFrame([dict(tid=f["tid"], time=f["time"], px=float(f["px"]), sz=float(f["sz"]),
                              closed_pnl=float(f["closedPnl"]), fee=float(f["fee"]), hash=f["hash"])
                         for f in out])


def main():
    key = os.environ.get("HYDROMANCER_API_KEY")
    if not key:
        sys.exit("set HYDROMANCER_API_KEY")
    print("Test 13b: full-window re-pull from Hydromancer against the bundled fills")
    for vault, address, parquet, start, end in TARGETS:
        t0, t1 = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
        ours = pd.read_parquet(os.path.join(REPO, parquet))
        ours["ts"] = pd.to_datetime(ours.ts, utc=True)
        ours = ours[(ours.ts > t0) & (ours.ts <= t1)]
        ms0, ms1 = int(t0.timestamp() * 1000), int(t1.timestamp() * 1000)
        theirs = pull(address, ms0, ms1, key)
        theirs = theirs[(theirs.time > ms0) & (theirs.time <= ms1)]
        m = ours.merge(theirs, on="tid", how="outer", suffixes=("_ours", "_theirs"), indicator=True)
        both = m[m._merge == "both"]
        print(f"    {vault}: this repo {len(ours):,} fills, Hydromancer {len(theirs):,}, in both {len(both):,}, "
              f"only this repo {(m._merge == 'left_only').sum()}, only Hydromancer {(m._merge == 'right_only').sum()}")
        print(f"        largest difference  px {(both.px_ours - both.px_theirs).abs().max():.3g}"
              f"  sz {(both.sz_ours - both.sz_theirs).abs().max():.3g}"
              f"  closedPnl {(both.closed_pnl_ours - both.closed_pnl_theirs).abs().max():.3g}"
              f"  fee {(both.fee_ours - both.fee_theirs).abs().max():.3g}"
              f"  hash mismatches {(both.hash_ours != both.hash_theirs).sum()}")


if __name__ == "__main__":
    main()
