from pathlib import Path
import csv
import hashlib

ROOT = Path(__file__).resolve().parents[1]
ROAD = ROOT / '03_intermediate/ROAD/road'


def sha256(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def map_captures():
    output = ROOT / '02_inspection/road_capture_mapping.csv'
    captures = sorted([*(ROAD / 'ambient').glob('*.log'), *(ROAD / 'attacks').rglob('*.log')])
    with output.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(['raw_capture', 'capture_category', 'raw_metadata_json',
                         'signal_attack_csv', 'signal_ambient_csv', 'identical_signal_files'])
        for path in captures:
            group = path.relative_to(ROAD).parts[0]
            category = group
            if 'masquerade' in path.stem:
                category = 'masquerade'
            elif 'fuzz' in path.stem:
                category = 'fuzzing'
            attack = ROAD / 'signal_extractions/attacks' / f'{path.stem}.csv'
            ambient = ROAD / 'signal_extractions/ambient' / f'{path.stem}.csv'
            attack_exists, ambient_exists = attack.is_file(), ambient.is_file()
            identical = sha256(attack) == sha256(ambient) if attack_exists and ambient_exists else ''
            writer.writerow([path.relative_to(ROAD).as_posix(), category,
                             f'{group}/capture_metadata.json',
                             attack.relative_to(ROAD).as_posix() if attack_exists else '',
                             ambient.relative_to(ROAD).as_posix() if ambient_exists else '', identical])


if __name__ == '__main__':
    map_captures()
