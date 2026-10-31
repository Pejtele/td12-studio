"""TD-12 identification and bulk-dump inspection. No parameter-write API."""
from dataclasses import dataclass
import hashlib

MODEL = bytes([0, 0, 9])
IDENTITY_REQUEST = bytes.fromhex('f0 7e 7f 06 01 f7')


def checksum(data):
    return (-sum(data)) & 0x7f


def address_number(data):
    if len(data) != 4 or any(x > 127 for x in data):
        raise ValueError('Address must contain four 7-bit bytes')
    n = 0
    for x in data:
        n = n * 128 + x
    return n


def address_bytes(n):
    if not 0 <= n < 128**4:
        raise ValueError('Address outside 28-bit range')
    return bytes((n >> shift) & 127 for shift in (21, 14, 7, 0))


def address_text(n):
    return address_bytes(n).hex(' ').upper()


def block_name(address):
    a, b, _, _ = address_bytes(address)
    if a == 0x70 and b == 0:
        return 'Setup'
    if a == 0x71 and b < 4:
        return f'Trigger bank {b+1}'
    if a == 0x72 and b < 50:
        return f'Drum kit {b+1}'
    if a == 0x73 and b < 8:
        return f'Percussion set {b+1}'
    return {0x74: 'Pattern information', 0x75: 'Pattern data'}.get(a, 'Unknown block')


@dataclass(frozen=True)
class Packet:
    raw: bytes
    kind: str
    device_id: int | None = None
    address: int | None = None
    data: bytes = b''
    description: str = ''


def parse_packet(raw):
    raw = bytes(raw)
    if len(raw) < 3 or raw[0] != 0xf0 or raw[-1] != 0xf7:
        raise ValueError('Incomplete SysEx frame')
    if any(x > 127 for x in raw[1:-1]):
        raise ValueError('SysEx payload contains a status byte')
    if raw[1] == 0x7e and len(raw) >= 6 and raw[3:5] == b'\x06\x02':
        if len(raw) != 15 or raw[5] != 0x41:
            return Packet(raw, 'other_identity', raw[2], description='Other device identity')
        td12 = raw[6:10] == bytes.fromhex('09 02 00 00')
        revision = raw[10:14].hex(' ').upper()
        return Packet(raw, 'td12_identity' if td12 else 'other_identity', raw[2],
                      description=f'{"Roland TD-12" if td12 else "Other Roland device"}; device ID {raw[2]+1}; revision bytes {revision}')
    if raw[1] == 0x41 and len(raw) >= 8 and raw[3:6] == MODEL:
        if raw[6] != 0x12:
            return Packet(raw, 'unknown_td12', raw[2], description='Unsupported TD-12 command')
        if len(raw) < 14:
            raise ValueError('TD-12 DT1 packet has no data')
        if checksum(raw[7:-2]) != raw[-2]:
            raise ValueError('Roland checksum mismatch')
        address = address_number(raw[7:11])
        data = raw[11:-2]
        if address + len(data) > 128**4:
            raise ValueError('DT1 payload exceeds address space')
        return Packet(raw, 'td12_data', raw[2], address, data, block_name(address))
    return Packet(raw, 'other', description='Other SysEx message')


def split_sysex(data):
    """Read a raw .syx stream; tolerate interleaved MIDI realtime only."""
    frames, errors, current = [], [], None
    for offset, value in enumerate(data):
        if value >= 0xf8:
            continue
        if value == 0xf0:
            if current is not None:
                errors.append(f'Truncated frame before byte {offset}')
            current = bytearray([value])
        elif value == 0xf7:
            if current is None:
                errors.append(f'Unexpected F7 at byte {offset}')
            else:
                current.append(value)
                frames.append(bytes(current))
                current = None
        elif current is not None:
            if value >= 0x80:
                errors.append(f'Invalid status inside SysEx at byte {offset}')
                current = None
            else:
                current.append(value)
        else:
            errors.append(f'Unexpected byte outside SysEx at {offset}')
    if current is not None:
        errors.append('Truncated final SysEx frame')
    return frames, errors


def inspect_dump(data):
    frames, errors = split_sysex(data)
    memory, blocks, devices, packets = {}, {}, set(), []
    for index, raw in enumerate(frames):
        try:
            packet = parse_packet(raw)
        except ValueError as e:
            errors.append(f'Packet {index+1}: {e}')
            continue
        packets.append(packet)
        if packet.kind != 'td12_data':
            continue
        devices.add(packet.device_id)
        for offset, value in enumerate(packet.data):
            address = packet.address + offset
            if address in memory and memory[address] != value:
                errors.append(f'Conflicting data at {address_text(address)}')
            memory[address] = value
    if len(devices) > 1:
        errors.append('Dump combines multiple device IDs')
    for address in memory:
        name = block_name(address)
        blocks[name] = blocks.get(name, 0) + 1
    report = {
        'sha256': hashlib.sha256(data).hexdigest(),
        'sysex_frames': len(frames),
        'valid_td12_packets': sum(p.kind == 'td12_data' for p in packets),
        'unique_data_bytes': len(memory), 'device_ids': [x+1 for x in sorted(devices)],
        'blocks': blocks, 'errors': errors,
        'status': 'invalid' if errors else ('captured_data' if memory else 'no_td12_data'),
        'completeness': 'Not established: published block sizes are unavailable',
    }
    return report, memory


def compare_dumps(before, after):
    a, am = inspect_dump(before)
    b, bm = inspect_dump(after)
    if a['errors'] or b['errors']:
        raise ValueError('Cannot compare invalid dumps: ' + '; '.join(a['errors'] + b['errors']))
    if not am or not bm:
        raise ValueError('Both files must contain TD-12 bulk data')
    if a['device_ids'] != b['device_ids']:
        raise ValueError('Dumps have different device IDs')
    changes = [{'address': address_text(n), 'block': block_name(n),
                'before': am[n], 'after': bm[n]} for n in sorted(am.keys() & bm.keys()) if am[n] != bm[n]]
    return {'before_sha256': a['sha256'], 'after_sha256': b['sha256'],
            'changed_bytes': changes,
            'only_before': [address_text(n) for n in sorted(am.keys() - bm.keys())],
            'only_after': [address_text(n) for n in sorted(bm.keys() - am.keys())],
            'interpretation': 'Byte differences only; parameter meanings remain unverified'}
