"""MIDI transport: receives any MIDI; sends only a universal identity request."""
from datetime import datetime
from pathlib import Path
import json
import threading
import sys
import rtmidi
import mido
from .protocol import IDENTITY_REQUEST, inspect_dump


def backend():
    # Mido's RtMidi default uses ALSA on Linux, CoreMIDI on macOS, WinMM on Windows.
    return mido.Backend('mido.backends.rtmidi/LINUX_ALSA' if sys.platform.startswith('linux') else 'mido.backends.rtmidi')


def ports():
    b = backend()
    return b.get_input_names(), b.get_output_names()


def choose_port(names, selector=None):
    if selector in names:
        return selector
    matches = [n for n in names if (selector or 'UM-ONE').casefold() in n.casefold()]
    if len(matches) != 1:
        raise ValueError('Select exactly one MIDI port; found: ' + ', '.join(matches or names))
    return matches[0]


class PollingInput:
    """Poll RtMidi's native queue; avoid native-to-Python callback shutdown races."""
    def __init__(self, name, callback):
        api = rtmidi.API_LINUX_ALSA if sys.platform.startswith('linux') else rtmidi.API_UNSPECIFIED
        self.rt = rtmidi.MidiIn(rtapi=api, name='TD-12 Studio', queue_size_limit=8192)
        names = self.rt.get_ports()
        if name not in names:
            self.rt.delete()
            raise ValueError('MIDI input disappeared: ' + name)
        self.rt.open_port(names.index(name))
        self.rt.ignore_types(sysex=False, timing=True, active_sense=True)
        self.error = None
        self.closed = False
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(callback,), daemon=True)
        self.thread.start()

    def _run(self, callback):
        try:
            while not self.stop.is_set():
                result = self.rt.get_message()
                if result:
                    callback(mido.Message.from_bytes(result[0]))
                else:
                    self.stop.wait(.005)
        except Exception as e:
            self.error = str(e)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        self.thread.join()
        self.rt.close_port()
        self.rt.delete()


class Connection:
    def __init__(self, input_name, on_message=lambda message: None):
        self._lock = threading.RLock()
        self._capture = None
        self.capture_path = None
        self.on_message = on_message
        self.error = None
        self.input = PollingInput(input_name, self._receive)

    def _receive(self, message):
        with self._lock:
            if message.type == 'sysex' and self._capture:
                try:
                    self._capture.write(bytes(message.bytes()))
                    self._capture.flush()
                except OSError as e:
                    self.error = str(e)
                    self._capture.close()
                    self._capture = None
        self.on_message(message)

    def identify(self, output_name):
        # Intentionally no generic send method and no DT1/RQ1 transmission.
        with backend().open_output(output_name) as output:
            output.send(mido.Message.from_bytes(IDENTITY_REQUEST))

    def start_capture(self, directory, label='capture'):
        with self._lock:
            if self._capture:
                raise ValueError('A capture is already running')
            directory = Path(directory).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            safe = ''.join(c if c.isalnum() or c in '-_' else '-' for c in label)[:60] or 'capture'
            self.capture_path = directory / f'{datetime.now():%Y%m%d-%H%M%S-%f}-{safe}.syx'
            self._capture = self.capture_path.open('xb')
            self.error = None
            return self.capture_path

    def stop_capture(self):
        with self._lock:
            if self._capture:
                self._capture.close()
                self._capture = None
            path = self.capture_path
            if path is None:
                return None
            report, _ = inspect_dump(path.read_bytes())
            if self.error:
                report['errors'].append(self.error)
                report['status'] = 'invalid'
            path.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
            self.capture_path = None
            return path, report

    def close(self):
        self.input.close()
        if self.input.error:
            self.error = self.input.error
        return self.stop_capture()
