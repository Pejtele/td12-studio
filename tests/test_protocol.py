import unittest
from td12_studio.protocol import *


def packet(address, data, device=0x10):
    """Synthetic DT1 fixture, never transmitted to hardware."""
    payload = address_bytes(address) + bytes(data)
    return b'\xf0\x41' + bytes([device]) + MODEL + b'\x12' + payload + bytes([checksum(payload), 0xf7])


class ProtocolTests(unittest.TestCase):
    def test_identity_from_roland_document(self):
        p = parse_packet(bytes.fromhex('f0 7e 10 06 02 41 09 02 00 00 00 02 00 00 f7'))
        self.assertEqual(p.kind, 'td12_identity'); self.assertEqual(p.device_id, 16)

    def test_wrong_family_is_not_identified_as_td12(self):
        p = parse_packet(bytes.fromhex('f0 7e 10 06 02 41 0a 02 00 00 00 02 00 00 f7'))
        self.assertEqual(p.kind, 'other_identity')

    def test_checksum_error_is_not_a_backup(self):
        raw = bytearray(packet(0x71*128**3, [3,4])); raw[-2] ^= 1
        report, memory = inspect_dump(raw)
        self.assertEqual(report['status'], 'invalid'); self.assertFalse(memory)

    def test_realtime_inside_frame_and_truncated_tail(self):
        raw = packet(0, [1]); mixed = raw[:5] + b'\xf8\xfe' + raw[5:]
        frames, errors = split_sysex(mixed)
        self.assertEqual(frames, [raw]); self.assertFalse(errors)
        self.assertTrue(inspect_dump(raw[:-1])[0]['errors'])

    def test_address_carry_is_base128(self):
        start = address_number(bytes([0x71,0,0,127]))
        report, memory = inspect_dump(packet(start, [4,5]))
        self.assertEqual(address_text(max(memory)), '71 00 01 00')
        self.assertEqual(report['blocks'], {'Trigger bank 1': 2})

    def test_compare_independent_of_packet_boundaries(self):
        start = address_number(bytes.fromhex('71 00 00 00'))
        before = packet(start, [1,2,3])
        after = packet(start, [1]) + packet(start+1, [9,3])
        diff = compare_dumps(before, after)
        self.assertEqual(diff['changed_bytes'], [{'address': '71 00 00 01', 'block': 'Trigger bank 1', 'before': 2, 'after': 9}])
        self.assertFalse(diff['only_before']); self.assertFalse(diff['only_after'])

    def test_missing_regions_are_not_reported_as_changed_settings(self):
        result = compare_dumps(packet(0,[1,2]), packet(0,[1]))
        self.assertFalse(result['changed_bytes']); self.assertEqual(result['only_before'], ['00 00 00 01'])

    def test_conflicting_overlaps_and_mixed_devices_rejected(self):
        for raw in [packet(0,[1])+packet(0,[2]), packet(0,[1])+packet(1,[2],17)]:
            with self.assertRaises(ValueError): compare_dumps(raw, packet(0,[1]))

    def test_empty_and_identity_only_are_not_backups(self):
        for raw in [b'', bytes.fromhex('f0 7e 10 06 02 41 09 02 00 00 00 02 00 00 f7')]:
            self.assertEqual(inspect_dump(raw)[0]['status'], 'no_td12_data')
            with self.assertRaises(ValueError): compare_dumps(raw, packet(0,[1]))


if __name__ == '__main__': unittest.main()
