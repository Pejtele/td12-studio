from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import mido
from td12_studio.midi import Connection, choose_port
from td12_studio.protocol import MODEL, address_bytes, checksum


def fixture():
    payload = address_bytes(0x71 * 128**3) + bytes([3,4,5])
    return mido.Message.from_bytes(b'\xf0\x41\x10' + MODEL + b'\x12' + payload + bytes([checksum(payload),0xf7]))


class MidiTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch('td12_studio.midi.backend')
        self.backend = self.patch.start().return_value
        self.addCleanup(self.patch.stop)
        self.input_patch = patch('td12_studio.midi.PollingInput')
        self.fake_input = self.input_patch.start().return_value
        self.fake_input.error = None
        self.addCleanup(self.input_patch.stop)
        self.connection = Connection('test input')
        self.addCleanup(self.connection.close)

    def test_identity_is_the_only_transmitted_message(self):
        self.connection.identify('test output')
        sent = self.backend.open_output.return_value.__enter__.return_value.send.call_args.args[0]
        self.assertEqual(sent.hex(), 'F0 7E 7F 06 01 F7')
        self.backend.open_output.assert_called_once_with('test output')

    def test_capture_records_sysex_without_forwarding_or_notes(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.connection.start_capture(d, '../before')
            self.assertEqual(path.parent, Path(d))
            self.connection._receive(mido.Message('note_on',note=48))
            self.connection._receive(fixture())
            result_path, report = self.connection.stop_capture()
            self.assertEqual(path, result_path)
            self.assertEqual(path.read_bytes(), bytes(fixture().bytes()))
            self.assertEqual(report['status'], 'captured_data')
            self.assertEqual(report['unique_data_bytes'], 3)
            self.assertTrue(path.with_suffix('.json').is_file())
            self.backend.open_output.assert_not_called()

    def test_empty_capture_is_not_a_backup(self):
        with tempfile.TemporaryDirectory() as d:
            self.connection.start_capture(d)
            self.assertEqual(self.connection.stop_capture()[1]['status'], 'no_td12_data')

    def test_close_finalizes_capture(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.connection.start_capture(d)
            self.connection._receive(fixture())
            self.connection.close()
            self.assertTrue(path.with_suffix('.json').exists())

    def test_ambiguous_port_is_not_guessed(self):
        with self.assertRaises(ValueError): choose_port(['UM-ONE 1','UM-ONE 2'])
        self.assertEqual(choose_port(['TriggerIO','UM-ONE 1']), 'UM-ONE 1')


if __name__ == '__main__': unittest.main()
