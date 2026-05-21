"""
Optional: re-pull the data files in `data/` from their original sources.

Sources used:
1. HL API endpoints (public):
   - https://api.hyperliquid.xyz/info  (POST, type='vaultDetails', 'userFunding', 'userNonFundingLedgerUpdates')
2. S3 (requires AWS credentials with requester-pays access):
   - s3://hl-mainnet-node-data/node_fills_by_block/hourly/{YYYYMMDD}/{H}.lz4
   - Hyperliquid mainnet node data bucket; public bucket, requester-pays for egress.

Not implemented here: this stub describes the pull strategy. To re-pull, adapt this
script to your environment. The data files in `data/` are the authoritative artifacts
for the reconciliation reproduction; this script exists for transparency about
provenance.

Per-source descriptions:

vault_pnl_snapshots.csv:
  POST https://api.hyperliquid.xyz/info  body={"type": "vaultDetails", "user": "0x...", "vaultAddress": "0x..."}
  Returns vault data including portfolio entries per scope and timeframe.

vault_flows.csv:
  POST https://api.hyperliquid.xyz/info  body={"type": "userNonFundingLedgerUpdates", "user": "0x..."}
  Returns ledger entries including vaultDeposit, vaultWithdraw, internalTransfer.

funding.csv:
  POST https://api.hyperliquid.xyz/info  body={"type": "userFunding", "user": "0x..."}

fills/*.parquet:
  S3 bucket s3://hl-mainnet-node-data/node_fills_by_block/hourly/{YYYYMMDD}/{H}.lz4
  Each file is lz4-compressed JSONL of block records. Filter events to the 8 HLP
  vault addresses.

The 8 HLP vault addresses:
  HLP parent:    0xdfc24b077bc1425ad1dea75bcb6f8158e10df303
  Strategy A:    0x010461c14e146ac35fe42271bdc1134ee31c703a
  Strategy B:    0x31ca8395cf837de08b24da3f660e77761dfb974b
  Strategy X:    0x469f690213c467c39a23efacfd2816896009d7d8
  Liquidator:    0x2e3d94f0562703b25c83308a05046ddaf9a8dd14
  Liquidator 2:  0xb0a55f13d22f66e6d495ac98113841b2326e9540
  Liquidator 3:  0x5e177e5e39c0f4e421f5865a6d8beed8d921cb70
  Liquidator 4:  0x2ed5c4484ea3ff8b57d5f2fb152a40d9f2b68308
"""

if __name__ == '__main__':
    print(__doc__)
    print("This is a documentation stub. See module docstring for pull procedure.")
