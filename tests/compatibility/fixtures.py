#!/usr/bin/env python3
"""Check shared fixtures against their source of truth."""
import json
import struct
from pathlib import Path
from schema_check import check_message

ROOT = Path(__file__).resolve().parents[2]

def main():
    corpus = json.loads((ROOT/'tests/fixtures/conformance.json').read_text())
    count = 0
    for draft, roles in corpus['drafts'].items():
        for rows in roles.values():
            for row in rows:
                if row['channel'] < 9 or (int(draft) == 3 and row['channel'] == 1003):
                    check_message(int(draft), row['channel'], row['message'], contract='io.common-golf.open-connect-v1' if row['channel'] == 1003 else None)
                count += 1
    for row in corpus['datagrams']:
        assert (struct.pack('>IQ', row['channel'], row['sequence']) + bytes.fromhex(row['payload_hex'])).hex() == row['wire_hex']
    print(f'{count} shared message fixtures and datagram vectors verified.')

if __name__ == '__main__':
    main()
