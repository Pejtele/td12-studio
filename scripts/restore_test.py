"""Restore test: send ONE trigger bank back to the TD-12, byte-for-byte as the
module itself transmitted it in a capture. No re-encoding, no edits.

Dry run by default (validates and prints only). Transmits only with --send.
Afterwards, dump ALL TRIG BANKS from the module and diff against the capture.
"""
from pathlib import Path
import argparse
import json
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mido
from td12_studio.protocol import split_sysex, parse_packet, address_text, checksum
from td12_studio.midi import backend, choose_port, ports

BANK_LENGTH = 1843  # observed in every capture of this unit, not published by Roland
GAP = 0.060         # manual: at least 40 ms between DT1 packets; too slow -> Receive Time Out


def bank_packets(capture, bank):
    frames, errors = split_sysex(capture.read_bytes())
    if errors:
        raise ValueError('Capture has framing errors: ' + '; '.join(errors))
    name = f'Trigger bank {bank}'
    packets = []
    for raw in frames:
        p = parse_packet(raw)  # raises on checksum errors
        if p.kind == 'td12_data' and p.description == name:
            packets.append(p)
    if not packets:
        raise ValueError(f'{name} not in capture')
    # Must be exactly one copy, contiguous from offset 0, exact observed length.
    devices = {p.device_id for p in packets}
    if devices != {0x10}:
        raise ValueError(f'Unexpected device IDs {devices}; module uses 17')
    base = packets[0].address
    if address_text(base) != f'71 {bank-1:02X} 00 00':
        raise ValueError('Bank does not start at its block address')
    expected = base
    for p in packets:
        if p.address != expected:
            raise ValueError(f'Gap or duplicate at {address_text(p.address)}')
        expected = p.address + len(p.data)
    if expected - base != BANK_LENGTH:
        raise ValueError(f'Bank length {expected - base}, expected {BANK_LENGTH}')
    return packets


def with_name(packet, name):
    """Rename test: replace the 12-char bank name in the first packet only.

    Packed layout seen in captures: 8-byte groups, a high-bit byte then 7 data
    bytes. Name char i sits at (i // 7) * 8 + 1 + i % 7. ASCII has no high bit,
    so the group's high-bit byte must already be 0 and stays 0.
    """
    if len(name) > 12 or any(not 0x20 <= ord(c) <= 0x7e for c in name):
        raise ValueError('Name must be at most 12 plain ASCII characters')
    data = bytearray(packet.data)
    if data[0] != 0 or data[8] != 0:
        raise ValueError('Name groups have high bits set; layout not as expected')
    for i, c in enumerate(name.ljust(12)):
        data[(i // 7) * 8 + 1 + i % 7] = ord(c)
    raw = packet.raw[:11] + bytes(data)
    raw += bytes([checksum(raw[7:]), 0xf7])
    parse_packet(raw)  # re-validate framing and checksum
    return raw


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('capture', type=Path, help='fresh .syx dump that contains the bank')
    ap.add_argument('--bank', type=int, required=True, choices=[1, 2, 3, 4])
    ap.add_argument('--send', action='store_true', help='actually transmit (default: dry run)')
    ap.add_argument('--set-name', help='rename test: change only the bank name')
    args = ap.parse_args()

    packets = bank_packets(args.capture, args.bank)
    raw = b''.join(p.data for p in packets)
    name = bytes(b for i, b in enumerate(raw) if i % 8)[:12].decode('ascii', 'replace')
    print(f'Trigger bank {args.bank} "{name.strip()}": {len(packets)} packets, '
          f'{sum(len(p.data) for p in packets)} bytes, from {args.capture.name}')
    if args.set_name is not None:
        edited = parse_packet(with_name(packets[0], args.set_name))
        changed = [i for i, (a, b) in enumerate(zip(packets[0].data, edited.data)) if a != b]
        print(f'Renaming to "{args.set_name}": data bytes changed at offsets {changed}, '
              f'checksum {packets[0].raw[-2]:02X} -> {edited.raw[-2]:02X}')
        packets[0] = edited
    if not args.send:
        print('Dry run: nothing sent. Add --send to transmit.')
        return 0

    inputs, outputs = ports()
    out_name = choose_port(outputs)
    log = {'capture': str(args.capture), 'bank': args.bank, 'output': out_name,
           'gap_seconds': GAP, 'packets': [p.raw.hex(' ') for p in packets]}
    with backend().open_output(out_name) as out:
        for i, p in enumerate(packets):
            out.send(mido.Message.from_bytes(p.raw))
            print(f'  sent {i+1}/{len(packets)} {address_text(p.address)}', flush=True)
            time.sleep(GAP)
    log['sent_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    path = Path(__file__).resolve().parents[1] / 'research' / f'restore-test-bank{args.bank}-{time.strftime("%Y%m%d-%H%M%S")}.json'
    path.write_text(json.dumps(log, indent=2) + '\n')
    print('Done. Log:', path)
    print('Now check the TD-12 screen for an error, then dump ALL TRIG BANKS and diff.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as e:
        raise SystemExit('ERROR: ' + str(e))
