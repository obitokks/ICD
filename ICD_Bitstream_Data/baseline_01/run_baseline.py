import collections
import csv
import hashlib
import json
import platform
import re
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import psutil
import zstandard as zstd

OUT = Path(__file__).resolve().parent
ROOT = OUT.parent / '03_intermediate'
BLOCK = 375
REGION = 1 << 20
LINE = re.compile(rb'\(\d+\.\d+\)\s+\S+\s+[0-9a-fA-F]+#([0-9a-fA-F]*)\s*')


def road_region(path, start, stop):
    with path.open('rb') as source:
        if start:
            source.seek(start - 1)
            if source.read(1) != b'\n':
                source.readline()
        while source.tell() < stop:
            line = source.readline()
            if not line or source.tell() > stop:
                break
            match = LINE.fullmatch(line)
            if not match:
                raise ValueError(f'Invalid candump record: {path.name} at {source.tell()}')
            value = bytes.fromhex(match[1].decode('ascii'))
            if len(value) != 8:
                raise ValueError(f'Unexpected ROAD data width: {path.name}')
            # 11-bit covered ID cannot be assumed after ROAD anonymization.
            # This placeholder is unused for CAN link-size accounting.
            yield value, None


def drone_records(path, audit):
    data = path.read_bytes()
    pos = 0
    lengths = {0x80: 89}
    while pos < len(data):
        if path.suffix == '.tlog':
            start = pos + 8
            magic = data[start]
            size = data[start + 1]
            if magic != 0xFE:
                raise ValueError(f'Unexpected MAVLink marker at {start}: {magic:#x}')
            header = 6
            end = start + header + size + 2
            if end > len(data):
                audit.append(dict(file=path.name, offset=pos, bytes=len(data)-pos, reason='incomplete final MAVLink record'))
                return
            yield pos, data[start+header:end-2], header + 2
        else:
            if len(data)-pos < 4096 and not any(data[pos:]):
                audit.append(dict(file=path.name, offset=pos, bytes=len(data)-pos, reason='final zero fill'))
                return
            if data[pos:pos+2] != b'\xa3\x95':
                raise ValueError(f'Unexpected DataFlash header at {pos}')
            kind = data[pos+2]
            end = pos + lengths[kind]
            if end > len(data):
                audit.append(dict(file=path.name, offset=pos, bytes=len(data)-pos, reason='incomplete final DataFlash record'))
                return
            if lengths[kind] < 3:
                raise ValueError(f'Invalid DataFlash record size at {pos}')
            if kind == 0x80:
                lengths[data[pos+3]] = data[pos+4]
            else:
                yield pos, data[pos+3:end], 3
        pos = end


def blocks(records):
    data = b''.join(value for value, _ in records)
    stop = len(data) - len(data) % BLOCK
    return [data[i:i+BLOCK] for i in range(0, stop, BLOCK)], len(data) - stop


def stride(items, limit):
    return [items[i*len(items)//limit] for i in range(limit)] if len(items) > limit else items


def road_group(path):
    if path.parent.name == 'ambient':
        return 'ROAD ambient'
    if 'fuzz' in path.stem:
        return 'ROAD fuzzing'
    if 'masquerade' in path.stem:
        return 'ROAD masquerade'
    return 'ROAD other attacks'


def inputs():
    groups = collections.defaultdict(list)
    road = ROOT / 'ROAD/road'
    for path in sorted([*(road/'ambient').glob('*.log'), *(road/'attacks').rglob('*.log')]):
        size = path.stat().st_size
        midpoint = size // 2
        train = (0, min(midpoint, REGION))
        test = (max(midpoint, size - REGION), size)
        groups[road_group(path)].append((path, train, test))
    drone = ROOT / 'DroneKit/dronekit-la-testdata-master'
    for name in ('flight.tlog', 'log171.bin'):
        path = drone/name
        size = path.stat().st_size
        split = size * 3 // 10
        groups['DroneKit '+name].append((path, (0, split), (split, size)))
    return groups


def codecs(dictionary):
    pairs = {}
    for level in (1, 6):
        pairs[f'zlib-{level}'] = (lambda value, n=level: zlib.compress(value, n), zlib.decompress)
    for label, shared in [('zstd-1', None), ('zstd-1-dict', dictionary)]:
        pairs[label] = (
            zstd.ZstdCompressor(level=1, dict_data=shared, write_checksum=True).compress,
            zstd.ZstdDecompressor(dict_data=shared).decompress,
        )
    return pairs


def evaluate(samples, encode, decode):
    lengths, encode_us, decode_us = [], [], []
    expanded = fallback = achieved = frame_achieved = 0
    original_total = sent_total = original_frame_total = sent_frame_total = 0
    for value, header in samples[:128]:
        if decode(encode(value)) != value:
            raise ValueError('Warmup round trip failed')
    for value, header in samples:
        start = time.perf_counter_ns()
        compressed = encode(value)
        encoded = time.perf_counter_ns()
        recovered = decode(compressed)
        decoded = time.perf_counter_ns()
        if recovered != value:
            raise ValueError('Codec round trip failed')
        use_raw = len(compressed) >= len(value)
        packet = bytes([int(not use_raw)]) + (value if use_raw else compressed)
        recovered_wire = packet[1:] if not packet[0] else decode(packet[1:])
        if recovered_wire != value:
            raise ValueError('Selected wire mode round trip failed')
        expanded += 1 + len(compressed) > len(value)
        fallback += use_raw
        original_total += len(value)
        sent_total += len(packet)
        achieved += 5*len(packet) <= 4*len(value)
        original_frame_total += len(value) + (header or 0)
        sent_frame_total += len(packet) + (header or 0)
        frame_achieved += 5*(len(packet)+(header or 0)) <= 4*(len(value)+(header or 0))
        lengths.append(8*len(packet))
        encode_us.append((encoded-start)/1000)
        decode_us.append((decoded-encoded)/1000)
    count = len(samples)
    percentile = lambda values, q: round(float(np.percentile(values,q)), 3)
    return dict(samples=count, average_bits=round(sum(lengths)/count,3),
                payload_ratio=sent_total/original_total, payload_original_bytes=original_total,
                payload_sent_bytes=sent_total,
                proxy_frame_ratio=sent_frame_total/original_frame_total if samples[0][1] else '',
                proxy_frame_hit20=frame_achieved/count if samples[0][1] else '',
                p50_bits=percentile(lengths,50), p95_bits=percentile(lengths,95),
                hit20=achieved/count, fallback=fallback/count, expanded=expanded/count,
                encode_p50_us=percentile(encode_us,50), encode_p95_us=percentile(encode_us,95),
                decode_p50_us=percentile(decode_us,50), decode_p95_us=percentile(decode_us,95),
                bit_errors=0)


def main():
    results, source_rows, dict_rows, audit = [], [], [], []
    process = psutil.Process()
    start = time.perf_counter()
    for group, entries in inputs().items():
        training, testing, train_frames, frames = [], [], [], []
        for path, train, test in entries:
            if group.startswith('ROAD'):
                train_items = list(road_region(path, *train))
                test_items = list(road_region(path, *test))
            else:
                records = list(drone_records(path, audit))
                train_items = [(x,h) for offset,x,h in records if offset < train[1]]
                test_items = [(x,h) for offset,x,h in records if offset >= test[0]]
            train_blocks, train_tail = blocks(train_items)
            test_blocks, test_tail = blocks(test_items)
            training.extend(stride(train_blocks, 512))
            testing.extend(test_blocks)
            train_frames.extend(stride([x for x,h in train_items if x], 1024))
            frames.extend(stride([(x,h) for x,h in test_items if x], 512))
            source_rows.append(dict(group=group, source=path.relative_to(ROOT).as_posix(),
                                    train_start=train[0], train_end=train[1], test_start=test[0],
                                    test_end=test[1], train_records=len(train_items),
                                    test_records=len(test_items), test_blocks=len(test_blocks),
                                    train_tail_bytes=train_tail, test_tail_bytes=test_tail))
        for workload, train, tests in (
            ('375B', training, [(x,None) for x in testing]),
            ('frame', train_frames, frames),
        ):
            dictionary = zstd.train_dictionary(4096, train)
            binary = dictionary.as_bytes()
            dictionary_name = f'{group.replace(" ", "_")}_{workload}.zdict'
            (OUT/dictionary_name).write_bytes(binary)
            dict_rows.append(dict(group=group, workload=workload, file=dictionary_name,
                                  bytes=len(binary), dictionary_id=dictionary.dict_id(),
                                  sha256=hashlib.sha256(binary).hexdigest(), train_samples=len(train)))
            print(f'{group} / {workload} / {len(tests):,} test samples', flush=True)
            for name, (encode, decode) in codecs(dictionary).items():
                repeats = [evaluate(tests, encode, decode) for _ in range(3)]
                result = repeats[0]
                for metric in ('encode_p50_us', 'encode_p95_us', 'decode_p50_us', 'decode_p95_us'):
                    result[metric] = round(float(np.median([row[metric] for row in repeats])), 3)
                results.append(dict(group=group, workload=workload, codec=name, **result))
                print(f'  {name}: ratio={result["payload_ratio"]:.2%}; hit20={result["hit20"]:.2%}; bit errors=0', flush=True)
    for filename, rows in [('results.csv',results),('test_sources.csv',source_rows),('dictionaries.csv',dict_rows)]:
        with (OUT/filename).open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (OUT/'record_audit.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    metadata = dict(python=sys.version, cpu=platform.processor(), system=platform.platform(),
                    zlib=zlib.ZLIB_RUNTIME_VERSION, zstandard=zstd.__version__,
                    zstd_backend=zstd.ZSTD_VERSION, seconds=time.perf_counter()-start,
                    peak_working_set_bytes=process.memory_info().peak_wset)
    (OUT/'environment.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
