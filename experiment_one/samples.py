"""Construct independent blocks and preserve record-level provenance."""
import hashlib
import random

from source_data import REGION, drone_records, paths, road_records

MIN_PAYLOAD_BYTES = 376


def windows(records, mode, source, relative, sha, minimum, audit, partition):
    headers, payloads, trailers, members = [], [], [], []
    size = 0
    for record in records:
        if not record.payload:
            continue
        if mode == 'A':
            headers.append(record.header)
            trailers.append(record.trailer)
        payloads.append(record.payload)
        members.append(dict(record=record.number, start=record.start, end=record.end,
                            payload_bytes=len(record.payload), boundaries=record.fields))
        size += len(record.payload)
        if size < minimum:
            continue
        header, payload, trailer = map(b''.join, (headers, payloads, trailers))
        yield header + payload + trailer, dict(
            source=source, file=relative, sha256=sha, start=members[0]['start'],
            end=members[-1]['end'], records=members, header_bits=len(header)*8,
            trailer_bits=len(trailer)*8)
        headers, payloads, trailers, members = [], [], [], []
        size = 0
    if members:
        audit.append([relative,members[0]['start'],members[-1]['end'],
                      f'{partition}: trailing group below minimum ({size*8} payload bits)'])


def select(root, config, announce):
    rng = random.Random(config['seed'])
    minimum = config.get('minimum_payload_bytes',1)
    training, testing, audit = {}, [], []
    for source in config['sources']:
        chosen, train = [], []
        seen = 0
        files = paths(root,source)
        if not files:
            raise ValueError(f'No source files for {source}')
        for path in files:
            announce(f'读取 {path.name}')
            with path.open('rb') as handle:
                sha = hashlib.file_digest(handle,'sha256').hexdigest()
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            if source == 'ROAD':
                split = size//2
                left = road_records(path,0,min(split,REGION))
                right = road_records(path,max(split,size-REGION),size)
            else:
                records = list(drone_records(path,source,audit))
                split = size*3//10
                left = (r for r in records if r.end <= split)
                right = (r for r in records if r.start >= split)
            if 'zstd-dict' in config['algorithms'] and len(train) < 4096:
                for i, (raw, meta) in enumerate(windows(left,config['mode'],source,relative,sha,minimum,audit,'train')):
                    start = meta['header_bits']//8
                    payload = raw[start:len(raw)-meta['trailer_bits']//8]
                    train.append((payload, dict(file=relative, sha256=sha, start=meta['start'], end=meta['end'])))
                    if i+1 >= (4096 + len(files)-1)//len(files) or len(train) >= 4096:
                        break
            for raw, meta in windows(right,config['mode'],source,relative,sha,minimum,audit,'test'):
                seen += 1
                if len(chosen) < config['count']:
                    chosen.append((raw,meta))
                else:
                    i = rng.randrange(seen)
                    if i < len(chosen):
                        chosen[i] = raw,meta
        if len(chosen) != config['count']:
            raise ValueError(f'{source} has {len(chosen)} eligible samples, requested {config["count"]}')
        rng.shuffle(chosen)
        testing.extend(chosen)
        training[source] = train
    return training, testing, audit
