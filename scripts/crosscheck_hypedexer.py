#!/usr/bin/env python3
"""Test 13: independent second-indexer verification of the cascade fills.

Re-pulls the cascade window from the Hypedexer indexer (a separate indexing
pipeline from the S3 node-data path this repo uses) and does a tid-level set
comparison against the bundled fills.

The question is not whether the counts match. It is whether the other indexer
holds any fill this repo lacks, and whether closedPnl agrees on the overlap.

Requires a Hypedexer API key:
    export HYPEDEXER_API_KEY=...
    python scripts/crosscheck_hypedexer.py

Runtime is roughly 2.5 minutes (about 180 paged requests).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.hypedexer.com"
START, END = "2025-10-10T00:00:00", "2025-10-12T00:00:00"
W0 = pd.Timestamp("2025-10-10", tz="UTC")
W1 = pd.Timestamp("2025-10-12", tz="UTC")

TARGETS = [
    ("Liquidator", "0x2e3d94f0562703b25c83308a05046ddaf9a8dd14",
     "data/fills/Liquidator_aug1_nov19.parquet"),
    ("Liquidator 2", "0xb0a55f13d22f66e6d495ac98113841b2326e9540",
     "data/fills/Liquidator_2_oct1_nov19.parquet"),
]


def get(path, params, key, max_retries=6):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    delay = 1.0
    for _ in range(max_retries):
        req = urllib.request.Request(
            url,
            headers={
                "X-API-Key": key,
                # Cloudflare rejects the default python-urllib User-Agent
                "User-Agent": "curl/8.7.1",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(float(e.headers.get("Retry-After") or delay), 60))
                delay = min(delay * 2, 60)
                continue
            raise
        except Exception:
            time.sleep(delay)
            delay = min(delay * 2, 60)
    raise RuntimeError(f"exhausted retries on {path}")


def pull(address, key):
    """Page /fills/ by cursor. Returns list of fill dicts."""
    rows, cursor, seen = [], None, set()
    while True:
        p = {"user": address, "start_time": START, "end_time": END, "limit": 1000}
        if cursor:
            p["cursor"] = cursor
        resp = get("/fills/", p, key)
        data = resp.get("data") or []
        rows.extend(data)
        nxt = resp.get("next_cursor")
        if not data or not nxt or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
        if len(rows) % 20000 == 0:
            print(f"    {len(rows):,} rows", flush=True)
    return rows


def main():
    key = os.environ.get("HYPEDEXER_API_KEY")
    if not key:
        sys.exit("HYPEDEXER_API_KEY not set")

    for name, addr, parquet in TARGETS:
        print("=" * 78)
        print(f"{name}  {addr}")
        print("=" * 78)

        ours = pd.read_parquet(os.path.join(REPO, parquet))
        ours["ts"] = pd.to_datetime(ours["ts"], utc=True)
        ours = ours[(ours.ts >= W0) & (ours.ts < W1)]

        rows = pull(addr, key)
        theirs = pd.DataFrame(rows)
        # their `time` mixes "...%H:%M:%S" and "...%H:%M:%S.%f"
        theirs["ts"] = pd.to_datetime(theirs["time"], utc=True, format="ISO8601")
        theirs["closed_pnl"] = pd.to_numeric(
            theirs["closedPnl"], errors="coerce"
        ).fillna(0.0)
        theirs = theirs.drop_duplicates(subset="tid")
        theirs = theirs[(theirs.ts >= W0) & (theirs.ts < W1)]

        o_tid = set(ours.tid.astype("int64"))
        t_tid = set(theirs.tid.astype("int64"))
        both, only_o, only_t = o_tid & t_tid, o_tid - t_tid, t_tid - o_tid

        print(f"  this repo  n={len(ours):>7,}  Σclosed_pnl={ours.closed_pnl.sum():>18,.4f}")
        print(f"  hypedexer  n={len(theirs):>7,}  Σclosed_pnl={theirs.closed_pnl.sum():>18,.4f}")
        print(f"  in both              {len(both):>7,}")
        print(f"  only this repo       {len(only_o):>7,}")
        print(f"  ONLY hypedexer       {len(only_t):>7,}   <- any nonzero is a new lead")

        if only_t:
            ot = theirs[theirs.tid.astype("int64").isin(only_t)]
            print(f"    their-only Σclosed_pnl = {ot.closed_pnl.sum():,.4f}")
            print(f"    their-only range       = {ot.ts.min()} -> {ot.ts.max()}")

        if only_o:
            oo = ours[ours.tid.astype("int64").isin(only_o)]
            print(f"    repo-only Σclosed_pnl  = {oo.closed_pnl.sum():,.4f}")
            print(f"    repo-only range        = {oo.ts.min()} -> {oo.ts.max()}")

        # closedPnl agreement on the shared subset
        om = ours[ours.tid.astype("int64").isin(both)][["tid", "closed_pnl"]].copy()
        tm = theirs[theirs.tid.astype("int64").isin(both)][["tid", "closed_pnl"]].copy()
        om["tid"] = om.tid.astype("int64")
        tm["tid"] = tm.tid.astype("int64")
        m = om.merge(tm, on="tid", suffixes=("_repo", "_hd"))
        diff = (m.closed_pnl_repo - m.closed_pnl_hd).abs()
        print(
            f"  shared-subset closedPnl: maxabsdiff={diff.max():.6f}  "
            f"n_mismatch(>0.01)={int((diff > 0.01).sum()):,}"
        )

        # where their coverage drops, by hour
        oh = ours.ts.dt.floor("h").value_counts().sort_index()
        th = theirs.ts.dt.floor("h").value_counts().sort_index()
        gaps = [
            (h, int(oh.get(h, 0)), int(th.get(h, 0)))
            for h in sorted(set(oh.index) | set(th.index))
            if int(oh.get(h, 0)) - int(th.get(h, 0)) != 0
        ]
        if gaps:
            print("  hours where coverage differs (repo / hypedexer / missing):")
            for h, a, b in gaps:
                print(f"    {h:%Y-%m-%d %H}:00   {a:>7,} / {b:>7,} / {a - b:>7,}")
        print()


if __name__ == "__main__":
    main()
