import argparse
import csv
import hashlib
import json
import math
import numpy as np
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {"ROAD":["road.zip","https://zenodo.org/records/10462796/files/road.zip?download=1"],"Car_Hacking":["car-hacking.zip","https://www.dropbox.com/scl/fo/9rwsf9pclhvv9xxloojom/AF7JeRW893grZkigkulkAHk?dl=1&rlkey=3h6zamu3kc262lrnipu5qden8"],"DroneKit":["dronekit.zip","https://codeload.github.com/dronekit/dronekit-la-testdata/zip/refs/heads/master"]}
NOTES = {
    'ROAD': 'Zenodo 10462796 v1. CC Attribution per https://0xsam.com/road/. Cite DOI 10.1371/journal.pone.0296879.',
    'Car_Hacking': 'HCRL academic release. Cite Song, Woo and Kim (2020), 100198. Redistribution terms unconfirmed.',
    'DroneKit': 'CC BY-SA 3.0 per upstream README.',
}
SIZE = 375


def digest(path, algorithm='sha256'):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, algorithm).hexdigest()


def relative(path):
    return path.relative_to(ROOT).as_posix()


def table(name, columns, rows):
    with (ROOT / name).open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(columns.split())
        writer.writerows(rows)


def download(source, archive, url):
    partial = archive.with_suffix('.part')
    result = subprocess.run(['curl.exe', '-fLsS', '--connect-timeout', '15',
                             '--max-time', '600', '--retry', '2', url, '-o', str(partial)])
    if result.returncode:
        raise ValueError(f'curl exit {result.returncode}')
    validate(source, partial)
    partial.replace(archive)


def validate(source, archive):
    if source == 'ROAD' and digest(archive, 'md5') != 'cab184cfc2fe12c0834bc46188c0f330':
        raise ValueError('Zenodo MD5 mismatch')
    with ZipFile(archive) as zipped:
        bad = zipped.testzip()
        if bad:
            raise ValueError(f'ZIP CRC failed for {bad}')


def unpack(archive, destination):
    with ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError(f'Unsafe archive path: {entry.filename}')
            if entry.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(entry) as original, target.open('wb') as output:
                shutil.copyfileobj(original, output)


def inspect(path):
    widths, flags = Counter(), Counter()
    bad = reversals = rows = blanks = 0
    last = None
    suffix = path.suffix.lower()
    if suffix not in {'.csv', '.log', '.txt'}:
        return {}
    if '__MACOSX' in path.parts or path.name.startswith('.'):
        return {}
    with path.open('rb') as handle:
        first = handle.readline().decode('utf-8-sig', errors='replace').strip()
        can_csv = suffix == '.csv' and path.is_relative_to(ROOT / '03_intermediate/Car_Hacking')
        handle.seek(0)
        for i, line in enumerate(handle):
            rows += 1
            if not can_csv:
                continue
            fields = line.decode('ascii', errors='replace').strip().split(',')
            widths[len(fields)] += 1
            blanks += sum(not field.strip() for field in fields)
            if i == 0 and fields[0].lower() == 'timestamp':
                continue
            if fields[-1] in {'R', 'T'}:
                flags[fields.pop()] += 1
            try:
                timestamp, identifier, dlc = fields[:3]
                stamp = float(timestamp)
                reversals += last is not None and stamp < last
                last = stamp
                size = int(dlc)
                data = fields[3:]
                if (not 0 <= size <= 8 or not 0 <= int(identifier, 16) <= 0x1fffffff
                        or len(data) not in {size, 8}
                        or any(len(x) != 2 or not 0 <= int(x, 16) <= 255
                               for x in data[:size])):
                    bad += 1
            except ValueError:
                bad += 1
    return {'first_line': first[:200], 'lines': rows,
            'field_widths': widths, 'flags': flags, 'empty_cells': blanks,
            'invalid_rows': bad, 'timestamp_reversals': reversals}


def build_stream(path, source, group, streams, samples, stats):
    name = path.relative_to(ROOT / '03_intermediate').as_posix()
    stream = ROOT / '04_bitstreams' / group / (name + '.bytes')
    stream.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, stream)
    original_hash = digest(path)
    if digest(stream) != original_hash:
        raise ValueError(f'Stream differs from {name}')
    packed = ROOT / '05_samples_3000bit' / group / (name + '.samples')
    packed.parent.mkdir(parents=True, exist_ok=True)
    frequency = Counter()
    index = 0
    previous_blocks = {}
    runs = Counter()
    previous_bit = None
    pending_run = 0
    repetitions = Counter()
    block_totals = Counter()
    total = stream.stat().st_size
    tail = total % SIZE
    with stream.open('rb') as handle, packed.open('wb') as output:
        while chunk := handle.read(SIZE * 2048):
            octets = np.frombuffer(chunk, dtype=np.uint8)
            frequency.update({i: int(n) for i, n in enumerate(np.bincount(octets)) if n})
            bits = np.unpackbits(octets)
            changes = np.flatnonzero(bits[1:] != bits[:-1]) + 1
            if previous_bit is not None and previous_bit != bits[0]:
                runs[pending_run] += 1
                pending_run = 0
            previous_bit = bits[-1]
            if len(changes):
                runs[pending_run + int(changes[0])] += 1
                sizes = np.diff(changes)
                runs.update({i: int(n) for i, n in enumerate(np.bincount(sizes)) if n})
                pending_run = len(bits) - int(changes[-1])
            else:
                pending_run += len(bits)
            for width in (1, 2, 4, 8):
                count = len(chunk) // width
                block_totals[width] += count
                if count:
                    repetitions[width] += previous_blocks.get(width) == chunk[:width]
                    previous_blocks[width] = chunk[(count-1)*width:count*width]
                    blocks = np.frombuffer(chunk, dtype=f'<u{width}', count=count)
                    repetitions[width] += int(np.count_nonzero(blocks[1:] == blocks[:-1]))
            full = len(chunk) // SIZE * SIZE
            output.write(chunk[:full])
            for offset in range(0, full, SIZE):
                sample = chunk[offset:offset+SIZE]
                position = index * SIZE
                samples.writerow([f'{group}/{name}#{index}', source, name, relative(stream),
                                  relative(packed), position, position * 8, SIZE, SIZE * 8,
                                  hashlib.sha256(sample).hexdigest()])
                index += 1
    with packed.open('rb') as actual, stream.open('rb') as expected:
        while chunk := actual.read(SIZE * 2048):
            if expected.read(len(chunk)) != chunk:
                raise ValueError(f'Sample bytes differ from {name}')
        if len(expected.read()) != tail or packed.stat().st_size != index * SIZE:
            raise ValueError(f'Unexpected tail in {name}')
    if pending_run:
        runs[pending_run] += 1
    ones = sum(value.bit_count() * count for value, count in frequency.items())
    entropy = -sum((n / total) * math.log2(n / total) for n in frequency.values())
    streams.append([source, group, name, relative(path), relative(stream),
                    total, total * 8, original_hash, 'identity', index, tail])
    stats.append(dict(source=source, group=group, file=name, bytes=total, samples=index,
                      tail_bytes=tail, ones=ones, zeros=total*8-ones, byte_entropy=entropy,
                      one_ratio=ones/(total*8) if total else None,
                      unique_bytes=len(frequency),
                      adjacent_bit_match_ratio=(total*8-sum(runs.values()))/(total*8-1) if total else None,
                      bit_run_length_frequency=dict(sorted(runs.items())),
                      byte_frequency=dict(sorted(frequency.items())),
                      adjacent_block_matches=repetitions, block_totals=block_totals))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    for folder in ('00_docs', '02_inspection', '03_intermediate', '04_bitstreams',
                   '05_samples_3000bit', '06_manifests', '07_reports'):
        (ROOT / folder).mkdir(exist_ok=True)
    registry, inventory, streams, stats = [], [], [], []
    report = ['# Dataset status', '']
    with (ROOT / '06_manifests/sample_manifest.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        samples = csv.writer(handle)
        samples.writerow('sample_id source source_member stream sample_file byte_offset bit_offset bytes bits sha256'.split())
        for source, (filename, url) in SOURCES.items():
            archive = ROOT / '01_raw' / source / filename
            archive.parent.mkdir(parents=True, exist_ok=True)
            try:
                if args.download and not archive.exists():
                    download(source, archive, url)
                validate(source, archive)
            except (OSError, ValueError, RuntimeError, BadZipFile) as error:
                registry.append([source, url, filename, 'unavailable', '', '', '', NOTES[source], str(error)])
                report.append(f'- {source}: unavailable ({error}).')
                continue
            date = datetime.fromtimestamp(archive.stat().st_mtime).astimezone().isoformat()
            registry.append([source, url, filename, 'verified', archive.stat().st_size,
                             digest(archive), date, NOTES[source], ''])
            archive.chmod(0o444)
            destination = ROOT / '03_intermediate' / source
            unpack(archive, destination)
            members = sorted(path for path in destination.rglob('*') if path.is_file())
            report.append(f'- {source}: verified ZIP; {len(members)} extracted files.')
            for path in [archive, *members]:
                with path.open('rb') as binary:
                    header = binary.read(32).hex()
                inventory.append([source, relative(path), path.suffix, path.stat().st_size,
                                  digest(path), header, json.dumps(inspect(path), ensure_ascii=False)])
                suffix = path.suffix.lower()
                if source == 'DroneKit' and suffix in {'.bin', '.tlog'}:
                    build_stream(path, source, 'native_binary', streams, samples, stats)
                elif source == 'ROAD' and suffix == '.csv' and 'signal_extractions' in path.parts and '__MACOSX' not in path.parts and not path.name.startswith('.'):
                    build_stream(path, source, 'road_signal_csv_text', streams, samples, stats)
                elif source == 'ROAD' and suffix == '.log' and '__MACOSX' not in path.parts and not path.name.startswith('.'):
                    build_stream(path, source, 'road_candump_text', streams, samples, stats)
                elif source == 'Car_Hacking' and suffix in {'.csv', '.log', '.txt'}:
                    build_stream(path, source, 'csv_text_bytes', streams, samples, stats)
    table('00_docs/source_register.csv', 'source url file status bytes sha256 local_mtime terms error', registry)
    table('02_inspection/file_inventory.csv', 'source path extension bytes sha256 first_32_bytes_hex format_inspection_json', inventory)
    table('06_manifests/bitstream_manifest.csv', 'source group member input output bytes bits sha256 transform samples tail_bytes', streams)
    (ROOT / '07_reports/dataset_summary.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    (ROOT / '07_reports/statistics.json').write_text(json.dumps(stats, indent=2), encoding='utf-8')
    summary = ['# Byte statistics', '', '| Source | Group | File | Bytes | Samples | Tail | Ones | Byte H0 |',
               '|---|---|---|---:|---:|---:|---:|---:|']
    for row in stats:
        summary.append('| {source} | {group} | {file} | {bytes} | {samples} | {tail_bytes} | {ones} | {byte_entropy:.6f} |'.format(**row))
    (ROOT / '07_reports/statistics_report.md').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    print('\n'.join(report))
    print(sum(row['samples'] for row in stats), 'verified samples')


if __name__ == '__main__':
    main()
