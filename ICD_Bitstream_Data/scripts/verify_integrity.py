import csv
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify():
    manifest = ROOT / '06_manifests/sample_manifest.csv'
    current = None
    handle = None
    count = 0
    try:
        with manifest.open(encoding='utf-8-sig', newline='') as rows:
            for row in csv.DictReader(rows):
                if row['sample_file'] != current:
                    if handle is not None:
                        if handle.read(1):
                            raise ValueError(f'Unlisted bytes in {current}')
                        handle.close()
                    current = row['sample_file']
                    handle = (ROOT / current).open('rb')
                offset = int(row['byte_offset'])
                sample = handle.read(375)
                if (row['bytes'] != '375' or row['bits'] != '3000'
                        or int(row['bit_offset']) != offset * 8
                        or handle.tell() - len(sample) != offset
                        or len(sample) != 375
                        or hashlib.sha256(sample).hexdigest() != row['sha256']):
                    raise ValueError(f"Invalid sample {row['sample_id']}")
                count += 1
        if handle is not None and handle.read(1):
            raise ValueError(f'Unlisted bytes in {current}')
        if not count:
            raise ValueError('Sample manifest is empty')
    finally:
        if handle is not None:
            handle.close()
    print(f'{count} samples passed length, offset and SHA-256 checks')


if __name__ == '__main__':
    verify()
