"""Read real record boundaries without changing the source files."""
from dataclasses import dataclass
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / 'ICD_Bitstream_Data/03_intermediate'
SOURCES = {'ROAD': 'ROAD CAN', 'MAVLink': 'DroneKit MAVLink', 'DataFlash': 'DroneKit DataFlash'}
REGION = 1 << 20
CANDUMP = re.compile(rb'\(\d+\.\d+\)\s+\S+\s+([0-9A-Fa-f]{1,8})#([0-9A-Fa-f]{16})\s*')


@dataclass(frozen=True)
class Record:
    number: int
    start: int
    end: int
    header: bytes
    payload: bytes
    trailer: bytes
    fields: dict


def paths(root, source):
    if source == 'ROAD':
        return sorted((root / 'ROAD/road').rglob('*.log'))
    name = 'flight.tlog' if source == 'MAVLink' else 'log171.bin'
    return [root / 'DroneKit/dronekit-la-testdata-master' / name]


def road_records(path, start, stop):
    with path.open('rb') as handle:
        if start:
            handle.seek(start - 1)
            if handle.read(1) != b'\n':
                handle.readline()
        number = 0
        while (offset := handle.tell()) < stop:
            line = handle.readline()
            end = handle.tell()
            if not line or end > stop:
                break
            match = CANDUMP.fullmatch(line)
            if not match:
                raise ValueError(f'{path.name}@{offset}: unsupported candump record')
            number += 1
            header = int(match[1], 16).to_bytes(4, 'big')
            yield Record(number, offset, end, header, bytes.fromhex(match[2].decode()), b'', {
                'convention': 'logical CAN ID uint32 big-endian; no physical CAN header',
                'record_number_basis': '1-based within selected range; byte offset identifies source record',
                'logical_header_source': [offset+match.start(1), offset+match.end(1)],
                'payload': [offset+match.start(2), offset+match.end(2)],
            })


def drone_records(path, source, audit):
    data = path.read_bytes()
    offset, number = 0, 0
    lengths = {0x80: 89}
    while offset < len(data):
        number += 1
        if source == 'MAVLink':
            first = offset + 8
            if len(data) - first < 8:
                audit.append([path.name, offset, len(data), 'incomplete MAVLink tail'])
                return
            if data[first] != 0xFE:
                raise ValueError(f'{path.name}@{first}: expected MAVLink v1')
            payload_start = first + 6
            payload_end = payload_start + data[first+1]
            end = payload_end + 2
        else:
            if len(data)-offset < 4096 and not any(data[offset:]):
                audit.append([path.name, offset, len(data), 'terminal zero fill'])
                return
            first = offset
            if len(data) - offset < 3 or data[offset:offset+2] != b'\xa3\x95':
                raise ValueError(f'{path.name}@{offset}: expected DataFlash sync')
            kind = data[offset+2]
            end = offset + lengths[kind]
            payload_start, payload_end = offset + 3, end
        if end > len(data):
            audit.append([path.name, offset, len(data), 'incomplete terminal record'])
            return
        if source == 'DataFlash' and kind == 0x80:
            lengths[data[offset+3]] = data[offset+4]
            if lengths[data[offset+3]] < 3:
                raise ValueError(f'{path.name}@{offset}: invalid FMT record length')
        else:
            yield Record(number, offset, end, data[first:payload_start],
                         data[payload_start:payload_end], data[payload_end:end], {
                'convention': 'native frame header' if source == 'MAVLink' else 'native DataFlash record header',
                'header': [first,payload_start], 'payload': [payload_start,payload_end],
                'trailer': [payload_end,end],
                'log_metadata': [offset,first] if offset != first else None,
            })
        offset = end
