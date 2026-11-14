"""Construct independent blocks and preserve record-level provenance."""
import hashlib
import random

from source_data import REGION, drone_records, paths, road_records

def windows(records, mode, source, relative, sha):
    for record in records:
        if not record.payload:
            continue
        header = record.header if mode == 'A' else b''
        trailer = record.trailer if mode == 'A' else b''
        raw = header + record.payload + trailer
        yield raw, dict(source=source, file=relative, sha256=sha,
                        start=record.start, end=record.end, record=record.number,
                        boundaries=record.fields, header_bits=len(header)*8,
                        trailer_bits=len(trailer)*8)


def select(root, config, announce):
    rng = random.Random(config['seed'])
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
                for i, (raw, meta) in enumerate(windows(left,config['mode'],source,relative,sha)):
                    start = meta['header_bits']//8
                    payload = raw[start:len(raw)-meta['trailer_bits']//8]
                    train.append((payload, dict(file=relative, sha256=sha, start=meta['start'], end=meta['end'])))
                    if i+1 >= (4096 + len(files)-1)//len(files) or len(train) >= 4096:
                        break
            for raw, meta in windows(right,config['mode'],source,relative,sha):
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
