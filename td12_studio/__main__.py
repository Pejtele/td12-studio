import argparse
import json
import math
from pathlib import Path
import queue
import time
from .protocol import parse_packet, inspect_dump, compare_dumps


def main():
    parser = argparse.ArgumentParser(description='TD-12 Studio — read-only MIDI research tools')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('gui')
    sub.add_parser('ports')
    p = sub.add_parser('identify'); p.add_argument('--input'); p.add_argument('--output')
    p = sub.add_parser('capture'); p.add_argument('--input'); p.add_argument('--seconds', type=float, default=0, help='Capture duration; 0 listens until Ctrl+C (default)')
    p.add_argument('--label', default='capture'); p.add_argument('--directory', default='~/.local/share/td12-studio/captures')
    p = sub.add_parser('inspect'); p.add_argument('file', type=Path)
    p = sub.add_parser('diff'); p.add_argument('before', type=Path); p.add_argument('after', type=Path)
    args = parser.parse_args()
    if args.command == 'gui':
        from .gui import run
        return run()
    if args.command == 'inspect':
        report, _ = inspect_dump(args.file.read_bytes()); print(json.dumps(report, indent=2))
        return 1 if report['errors'] or report['status'] == 'no_td12_data' else 0
    if args.command == 'diff':
        print(json.dumps(compare_dumps(args.before.read_bytes(), args.after.read_bytes()), indent=2)); return 0
    from .midi import Connection, ports, choose_port
    inputs, outputs = ports()
    if args.command == 'ports':
        print(json.dumps({'inputs': inputs, 'outputs': outputs}, indent=2)); return 0
    events = queue.Queue()
    conn = Connection(choose_port(inputs, args.input), events.put if args.command == 'identify' else lambda message: None)
    try:
        if args.command == 'identify':
            conn.identify(choose_port(outputs, args.output))
            end = time.monotonic() + 4
            while time.monotonic() < end:
                try: msg = events.get(timeout=.1)
                except queue.Empty: continue
                if msg.type == 'sysex':
                    pkt = parse_packet(msg.bytes())
                    if pkt.kind.endswith('identity'):
                        print(json.dumps({'kind': pkt.kind, 'description': pkt.description, 'raw': msg.hex()}, indent=2))
                        return 0 if pkt.kind == 'td12_identity' else 1
            print('No identity reply. Check both DIN connections and module power. No settings were sent.')
            return 2
        if not math.isfinite(args.seconds) or args.seconds < 0:
            raise ValueError('--seconds must be finite and nonnegative; 0 means until stopped')
        path = conn.start_capture(args.directory, args.label)
        print(f'Listening: {path}\nInitiate BULK DUMP on the module; this command cannot request it remotely.', flush=True)
        end = time.monotonic() + args.seconds if args.seconds else None
        try:
            while end is None or time.monotonic() < end:
                if conn.input.error:
                    raise RuntimeError('MIDI input failed: ' + conn.input.error)
                time.sleep(.1)
        except KeyboardInterrupt:
            pass
        result = conn.stop_capture()
        print(json.dumps(result[1], indent=2))
        return 0 if result[1]['status'] == 'captured_data' else 1
    finally:
        conn.close()


if __name__ == '__main__':
    try: raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as e:
        raise SystemExit(str(e))
