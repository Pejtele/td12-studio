"""Mapping session recorder: listen to the TD-12 until Ctrl+C, save each bulk
dump as it arrives, and print what changed since the previous dump.

Receive-only: sends nothing to the module. Dumps are split by 1.5 s of silence.
Offsets are shown per trigger bank, both packed (as sent) and unpacked
(high-bit bytes removed; every 8th packed byte is a high-bit byte).
"""
from pathlib import Path
import argparse
import json
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from td12_studio.protocol import inspect_dump, address_bytes, address_number, block_name
from td12_studio.midi import PollingInput, choose_port, ports

IDLE = 1.5


def bank_offset(address):
    a, b, _, _ = address_bytes(address)
    return address - address_number(bytes([a, b, 0, 0]))


def describe(address, before, after):
    packed = bank_offset(address)
    group, pos = divmod(packed, 8)
    where = f'high-bit byte of group {group}' if pos == 0 else f'unpacked {group * 7 + pos - 1}'
    return f'{block_name(address)}: packed {packed} ({where}): {before} -> {after}'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('baseline', type=Path, help='last known dump to compare the first one with')
    args = ap.parse_args()
    folder = Path(__file__).resolve().parents[1] / 'research' / f'map-{time.strftime("%Y%m%d-%H%M%S")}'
    folder.mkdir(parents=True)
    _, previous = inspect_dump(args.baseline.read_bytes())
    pending, last = [], [0.0]

    def on_message(message):
        if message.type == 'sysex':
            pending.append(bytes(message.bytes()))
            last[0] = time.monotonic()

    inputs, _ = ports()
    midi = PollingInput(choose_port(inputs), on_message)
    log = (folder / 'log.txt').open('a')

    def say(text):
        print(text, flush=True)
        log.write(text + '\n'); log.flush()

    say(f'Recording to {folder}\nBaseline: {args.baseline.name}\nWaiting for dump 1 ...')
    number = 0
    try:
        while True:
            time.sleep(.1)
            if not pending or time.monotonic() - last[0] < IDLE:
                continue
            frames = pending[:]
            del pending[:len(frames)]
            number += 1
            data = b''.join(frames)
            (folder / f'dump-{number:02d}.syx').write_bytes(data)
            report, memory = inspect_dump(data)
            say(f'\n=== Dump {number} at {time.strftime("%H:%M:%S")}: {report["valid_td12_packets"]} packets, '
                f'{report["unique_data_bytes"]} bytes, errors: {report["errors"] or "none"}')
            if report['errors'] or not memory:
                say('    !! Not usable - please dump again (nothing compared)')
                continue
            changes = [(n, previous[n], memory[n]) for n in sorted(memory.keys() & previous.keys())
                       if previous[n] != memory[n]]
            say(f'    {len(changes)} byte(s) changed since previous dump')
            for n, a, b in changes:
                say('    ' + describe(n, a, b))
            (folder / f'dump-{number:02d}.json').write_text(json.dumps(
                [{'address': n, 'before': a, 'after': b} for n, a, b in changes]) + '\n')
            previous = memory
            say(f'Waiting for dump {number + 1} ...')
    except KeyboardInterrupt:
        say('\nStopped.')
    finally:
        midi.close()
        log.close()


if __name__ == '__main__':
    main()
