"""
Re-pull the HL API portion of the bundled data from source.

Pulls three of the four data assets directly from the Hyperliquid public info
endpoint and writes them to `data/from_api/`. Compare those files against the
bundled `data/*.csv` to verify the bundled data faithfully reflects HL's
published numbers.

Endpoints used (all public, no auth required):
- POST https://api.hyperliquid.xyz/info  type='vaultDetails'             -> per-vault portfolio (snapshots)
- POST https://api.hyperliquid.xyz/info  type='userNonFundingLedgerUpdates' -> flow events
- POST https://api.hyperliquid.xyz/info  type='userFunding'              -> funding events

The fourth asset (fills) is in S3 at `s3://hl-mainnet-node-data/node_fills_by_block/`
and requires AWS credentials with requester-pays access. That pull is documented
at the bottom of this file but not executed automatically.

Run:
    python scripts/pull_data.py

Output files written to `data/from_api/`:
    vault_pnl_snapshots.csv
    vault_flows.csv
    funding.csv

Then to verify against bundled data:
    diff data/vault_pnl_snapshots.csv data/from_api/vault_pnl_snapshots.csv
"""
import json
import time
import sys
from pathlib import Path
import requests
import pandas as pd

INFO_URL = 'https://api.hyperliquid.xyz/info'
OUT = Path(__file__).resolve().parent.parent / 'data' / 'from_api'
OUT.mkdir(parents=True, exist_ok=True)

# Four HLP sub-vaults relevant to the reconciliation
VAULTS = {
    '0x2e3d94f0562703b25c83308a05046ddaf9a8dd14': 'Liquidator',
    '0xb0a55f13d22f66e6d495ac98113841b2326e9540': 'Liquidator 2',
    '0x010461c14e146ac35fe42271bdc1134ee31c703a': 'Strategy A',
    '0x31ca8395cf837de08b24da3f660e77761dfb974b': 'Strategy B',
}

# Window for non-snapshot endpoints (flow and funding events)
WIN_START_MS = int(pd.Timestamp('2025-08-01', tz='UTC').timestamp() * 1000)
WIN_END_MS   = int(pd.Timestamp('2025-11-19T23:59', tz='UTC').timestamp() * 1000)


REQUEST_INTERVAL_SECONDS = 0.4   # baseline throttle between HL API calls
_last_request_ts = 0.0


def post_info(body: dict, retries: int = 5) -> object:
    global _last_request_ts
    last_exc = None
    for attempt in range(retries):
        # Throttle to stay under HL's IP-level rate limit
        elapsed = time.time() - _last_request_ts
        if elapsed < REQUEST_INTERVAL_SECONDS:
            time.sleep(REQUEST_INTERVAL_SECONDS - elapsed)
        try:
            r = requests.post(INFO_URL, json=body, timeout=30)
            _last_request_ts = time.time()
            if r.status_code == 429:
                # Exponential backoff on rate limit
                wait = 2.0 * (attempt + 1) ** 2
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"POST {INFO_URL} failed after {retries} attempts: {last_exc}")


# HL's userFunding and userNonFundingLedgerUpdates endpoints cap results per
# call (around 500 entries). With both startTime and endTime supplied, the
# endpoint can also silently miss events in the requested range. The reliable
# pattern is cursor-based pagination: pass only startTime, dedupe by
# (time, coin) since funding events share a zero-placeholder hash, then advance
# the cursor to max(time)+1 each page until the page comes back empty.


def post_info_paginated(endpoint_type: str, user: str, start_ms: int, end_ms: int) -> list:
    """Cursor-paginate a time-windowed HL info endpoint.

    Pulls events forward from start_ms, advancing the cursor to max(time)+1
    each page. Stops when the page is empty or the cursor passes end_ms.
    Dedupes by `hash` when available; for events with a zero placeholder hash
    (funding), dedupes by (time, delta.coin).
    """
    out = []
    seen = set()
    cursor = start_ms
    while cursor <= end_ms:
        d = post_info({
            'type': endpoint_type,
            'user': user,
            'startTime': cursor,
        })
        if not isinstance(d, list) or not d:
            break
        added_any = False
        max_ts = cursor
        for e in d:
            t = int(e.get('time', 0))
            if t > max_ts:
                max_ts = t
            if t > end_ms:
                continue   # past the window, skip
            h = e.get('hash', '')
            delta = e.get('delta', {}) or {}
            if h and not all(c == '0' for c in h[2:]):
                key = h
            else:
                key = (t, delta.get('coin', ''))
            if key in seen:
                continue
            seen.add(key)
            out.append(e)
            added_any = True
        if max_ts <= cursor:
            break   # cursor not advancing; protect against infinite loop
        cursor = max_ts + 1
        if not added_any and all(int(e.get('time', 0)) > end_ms for e in d):
            break   # all returned events are past the window
    return out


def pull_vault_pnl() -> pd.DataFrame:
    """For each vault, pull vaultDetails portfolio and emit snapshots for allTime + perpAllTime."""
    rows = []
    for addr, name in VAULTS.items():
        print(f'  vaultDetails: {name} ({addr})')
        d = post_info({'type': 'vaultDetails', 'vaultAddress': addr})
        portfolio = d.get('portfolio', [])
        for entry in portfolio:
            if not (isinstance(entry, list) and len(entry) == 2):
                continue
            tf, body = entry
            if tf not in ('allTime', 'perpAllTime'):
                continue
            scope = 'all' if tf == 'allTime' else 'perp'
            avh = body.get('accountValueHistory', []) or []
            pnh = body.get('pnlHistory', []) or []
            pnl_map = {ts: float(v) for ts, v in pnh}
            for ts, av_str in avh:
                av = float(av_str)
                pnl_si = pnl_map.get(ts, float('nan'))
                # By HL convention, flow_cum = account_value - pnl_since_inception for scope=all,
                # and flow_cum = 0 for scope=perp.
                flow_cum = (av - pnl_si) if scope == 'all' else 0.0
                rows.append({
                    'ts': pd.Timestamp(ts, unit='ms', tz='UTC'),
                    'vault': addr,
                    'vault_name': name,
                    'scope': scope,
                    'timeframe': tf,
                    'account_value': av,
                    'flow_cum': flow_cum,
                    'pnl_since_inception': pnl_si,
                })
    df = pd.DataFrame(rows).sort_values(['vault_name', 'scope', 'ts'])
    return df


def pull_flows() -> pd.DataFrame:
    """For each vault, pull userNonFundingLedgerUpdates within the window (paginated)."""
    rows = []
    for addr, name in VAULTS.items():
        print(f'  userNonFundingLedgerUpdates: {name}')
        d = post_info_paginated('userNonFundingLedgerUpdates', addr, WIN_START_MS, WIN_END_MS)
        for entry in d:
            delta = entry.get('delta', {}) or {}
            kind = delta.get('type', '')
            # Net signed amount: deposits are positive into the vault, withdrawals are negative.
            usdc = delta.get('usdc')
            if usdc is None:
                # Some withdraw events use netWithdrawnUsd or requestedUsd
                usdc = delta.get('netWithdrawnUsd') or delta.get('requestedUsd') or 0
            try:
                amt = float(usdc)
            except (ValueError, TypeError):
                amt = 0.0
            sign = +1
            if kind == 'vaultWithdraw':
                sign = -1 if delta.get('vault', '').lower() == addr.lower() else +1
            elif kind == 'vaultDeposit':
                sign = +1 if delta.get('vault', '').lower() == addr.lower() else -1
            delta_usd = sign * amt
            rows.append({
                'ts': pd.Timestamp(entry.get('time', 0), unit='ms', tz='UTC'),
                'vault': addr,
                'kind': kind,
                'delta_usd': delta_usd,
                'vault_name': name,
            })
    df = pd.DataFrame(rows).sort_values(['vault_name', 'ts'])
    return df


def pull_funding() -> pd.DataFrame:
    """For each vault, pull userFunding within the window (paginated)."""
    rows = []
    for addr, name in VAULTS.items():
        print(f'  userFunding: {name}')
        d = post_info_paginated('userFunding', addr, WIN_START_MS, WIN_END_MS)
        for entry in d:
            delta = entry.get('delta', {}) or {}
            try:
                amt = float(delta.get('usdc', 0))
            except (ValueError, TypeError):
                amt = 0.0
            rows.append({
                'ts': pd.Timestamp(entry.get('time', 0), unit='ms', tz='UTC'),
                'vault': addr,
                'vault_name': name,
                'coin': delta.get('coin', ''),
                'delta_usd': amt,
                'szi': float(delta.get('szi', 0)) if delta.get('szi') is not None else 0,
                'funding_rate': float(delta.get('fundingRate', 0)) if delta.get('fundingRate') is not None else 0,
            })
    df = pd.DataFrame(rows).sort_values(['vault_name', 'ts'])
    return df


def main():
    print('Pulling vault_pnl snapshots from HL API...')
    vp = pull_vault_pnl()
    print(f'  -> {len(vp)} rows')

    print('Pulling vault_flows from HL API...')
    vf = pull_flows()
    print(f'  -> {len(vf)} rows')

    print('Pulling funding from HL API...')
    fund = pull_funding()
    print(f'  -> {len(fund)} rows')

    vp.to_csv(OUT / 'vault_pnl_snapshots.csv', index=False)
    vf.to_csv(OUT / 'vault_flows.csv', index=False)
    fund.to_csv(OUT / 'funding.csv', index=False)

    print(f'\nWrote three CSVs to {OUT}')
    print('To verify the bundled data matches what HL currently publishes, diff against `data/`:')
    print(f'  diff data/vault_pnl_snapshots.csv {OUT.relative_to(OUT.parent.parent)}/vault_pnl_snapshots.csv')
    print(f'  diff data/vault_flows.csv          {OUT.relative_to(OUT.parent.parent)}/vault_flows.csv')
    print(f'  diff data/funding.csv              {OUT.relative_to(OUT.parent.parent)}/funding.csv')
    print('\nNote: bundled CSVs are filtered to specific date windows and may differ in row count;')
    print('the key check is that overlapping rows agree on values.')


# ---------------------------------------------------------------------------
# S3 fills pull (manual; requires AWS credentials with requester-pays access)
# ---------------------------------------------------------------------------
"""
The fills data is in S3 at:
    s3://hl-mainnet-node-data/node_fills_by_block/hourly/{YYYYMMDD}/{H}.lz4

Each file is lz4-compressed JSONL of block records. Filter events to the 8 HLP
vault addresses. Hour partitioning is UTC.

Example pull command (requires boto3, lz4, AWS credentials):

    AWS_PROFILE=<your_profile> aws s3 cp \\
        s3://hl-mainnet-node-data/node_fills_by_block/hourly/20251010/21.lz4 \\
        ./fills_oct10_h21.lz4 \\
        --request-payer requester

To extract HLP fills from a downloaded hour file:

    import lz4.frame, json
    HLP_ADDRS = {
        '0x2e3d94f0562703b25c83308a05046ddaf9a8dd14',  # Liquidator
        '0xb0a55f13d22f66e6d495ac98113841b2326e9540',  # Liquidator 2
        '0x010461c14e146ac35fe42271bdc1134ee31c703a',  # Strategy A
        '0x31ca8395cf837de08b24da3f660e77761dfb974b',  # Strategy B
        # plus parent + Strategy X + Liquidator 3/4 if needed
    }
    with open('fills_oct10_h21.lz4', 'rb') as f:
        text = lz4.frame.decompress(f.read()).decode()
    for line in text.split('\\n'):
        if not line.strip(): continue
        rec = json.loads(line)
        for ev in rec.get('events', []):
            if isinstance(ev, list) and len(ev) == 2:
                user, fill = ev
                if (user or '').lower() in HLP_ADDRS:
                    # fill has: coin, px, sz, side, time, dir, closedPnl, fee, tid, hash
                    pass
"""

if __name__ == '__main__':
    main()
