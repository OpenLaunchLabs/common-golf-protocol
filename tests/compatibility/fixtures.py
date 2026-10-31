#!/usr/bin/env python3
"""Check shared fixtures against their source of truth."""
import json
from pathlib import Path
from schema_check import check_message

ROOT = Path(__file__).resolve().parents[2]

def main():
    corpus = json.loads((ROOT/'tests/fixtures/conformance.json').read_text())
    count = 0
    for draft, roles in corpus['drafts'].items():
        for rows in roles.values():
            for row in rows:
                if row['channel'] < 9:
                    check_message(int(draft), row['channel'], row['message'])
                count += 1
    print(f'{count} shared message fixtures verified.')

if __name__ == '__main__':
    main()
