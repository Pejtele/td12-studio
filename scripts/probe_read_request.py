"""One bounded, experimental Roland RQ1 read; never sends a DT1 write.

TD-12 RQ1 support is undocumented. The target and length come from two real
captures. Do not interpret no response as proof of universal lack of support.
"""
from pathlib import Path
import json
import time
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mido
from td12_studio.protocol import MODEL, address_bytes, checksum
from td12_studio.midi import Connection, backend, choose_port, ports


def main():
    inputs, outputs = ports()
    # Bank 1, exact contiguous length observed twice: 1843 packed bytes.
    address = bytes.fromhex('71 00 00 00')
    size = address_bytes(1843)
    payload = address + size
    request = b'\xf0\x41\x10' + MODEL + b'\x11' + payload + bytes([checksum(payload), 0xf7])
    assert len(request) == 17 and request[6] == 0x11
    root = Path(__file__).resolve().parents[1] / 'research'
    root.mkdir(exist_ok=True)
    conn = Connection(choose_port(inputs))
    try:
        path = conn.start_capture(root, 'experimental-read-bank1')
        print('Experimental read request only:', request.hex(' ').upper(), flush=True)
        with backend().open_output(choose_port(outputs)) as out:
            out.send(mido.Message.from_bytes(request))
        time.sleep(4)
        _, report = conn.stop_capture()
        print(json.dumps(report, indent=2))
        (root/'read-request-result.json').write_text(json.dumps({'request': request.hex(' '), 'capture': str(path), 'result': report}, indent=2)+'\n')
    finally:
        conn.close()


if __name__ == '__main__': main()
