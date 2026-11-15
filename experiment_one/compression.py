"""Bit ordering, codec adapters, and self-contained block framing."""
import struct
import time
import zlib

import zstandard as zstd

ALGORITHMS = {'zlib-1': 'zlib level 1', 'zlib-6': 'zlib level 6',
              'zstd-1': 'Zstandard level 1', 'zstd-dict': 'Zstandard level 1 + dictionary'}


def bits(data, length=None):
    value = ''.join(f'{byte:08b}' for byte in data)
    if length is not None and not 0 <= length <= len(value):
        raise ValueError('Invalid effective bit length')
    return value[:length]


def pack(value):
    if not value:
        return b''
    return int(value.ljust((len(value)+7)//8*8,'0'),2).to_bytes((len(value)+7)//8,'big')


def positions(ranges, length):
    previous = 0
    for start, count in ranges:
        if start < previous or count <= 0 or start+count > length:
            raise ValueError('Invalid protected bit range')
        previous = start+count


def split_bits(raw, ranges, length=3000):
    positions(ranges,length)
    value = bits(raw,length)
    protected, payload = [], []
    last = 0
    for start, count in ranges:
        payload.append(value[last:start])
        protected.append(value[start:start+count])
        last = start+count
    payload.append(value[last:])
    return ''.join(protected), pack(''.join(payload))


def join_bits(protected, payload, ranges, length=3000):
    positions(ranges,length)
    plain = bits(payload,length-len(protected))
    joined = []
    used = end = 0
    for start,count in ranges:
        joined.append(plain[end-used:start-used])
        joined.append(protected[used:used+count])
        used += count
        end = start+count
    joined.append(plain[end-used:])
    return pack(''.join(joined))


def codec(name, dictionary=None):
    if name in ('zlib-1','zlib-6'):
        level = int(name[-1])
        return lambda data: zlib.compress(data,level), zlib.decompress
    if name not in ALGORITHMS:
        raise ValueError('Unknown codec')
    shared = zstd.ZstdCompressionDict(dictionary) if dictionary is not None else None
    enc = zstd.ZstdCompressor(level=1, dict_data=shared, threads=0, write_checksum=True)
    dec = zstd.ZstdDecompressor(dict_data=shared)
    return enc.compress, dec.decompress


def frame_overhead(name, compressed):
    if not compressed:
        return 0
    if name.startswith('zlib'):
        return 48  # RFC 1950 two-byte header plus four-byte Adler-32.
    params = zstd.get_frame_parameters(compressed)
    return 8*(zstd.frame_header_size(compressed)+4*params.has_checksum)


def compress_sample(raw, header_bits, trailer_bits, encode, decode):
    start = header_bits//8
    end = len(raw)-trailer_bits//8
    header, payload, trailer = raw[:start], raw[start:end], raw[end:]
    clock = time.perf_counter_ns()
    compressed = encode(payload)
    encode_ns = time.perf_counter_ns()-clock
    fallback = len(compressed) >= len(payload)
    wire = b'\x00'+raw if fallback else b'\x01'+header+compressed+trailer
    recovered, decoded, error = b'', b'', ''
    decode_ns = None
    try:
        # The benchmark supplies block extent and protected byte counts out of band.
        encoded = compressed if fallback else wire[1+start:len(wire)-len(trailer)]
        clock = time.perf_counter_ns()
        decoded = decode(encoded)
        decode_ns = time.perf_counter_ns()-clock
        if decoded != payload:
            error = 'Codec payload round trip mismatch'
        recovered = wire[1:] if fallback else wire[1:1+start]+decoded+wire[len(wire)-len(trailer):]
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    return dict(compressed=compressed,recovered=recovered,decoded=decoded,wire=wire,
                fallback=fallback,encode_ns=encode_ns,decode_ns=decode_ns,mode_bits=8,error=error)
