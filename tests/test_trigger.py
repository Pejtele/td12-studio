import json
import unittest
from pathlib import Path
from td12_studio.protocol import inspect_dump
from td12_studio.trigger import *

TEMPLATES = json.loads((Path(__file__).parents[1] / 'td12_studio' / 'trig_types.json').read_text())


class TriggerTests(unittest.TestCase):
    """Synthetic fixtures only; never transmitted to hardware."""

    def test_high_bit_lives_in_group_prefix(self):
        b = Bank(1, bytes(BANK_PACKED))
        b.set(20, 200)
        self.assertEqual(b.get(20), 200)
        self.assertEqual(b.packed[packed_index(20) - packed_index(20) % 8], 1 << (20 % 7))
        b.set(20, 5)
        self.assertEqual(b.get(20), 5)
        self.assertEqual(b.packed[16], 0)   # high bit cleared again

    def test_packets_round_trip_through_parser(self):
        b = Bank(3, bytes(BANK_PACKED))
        b.set_field(1, FIELDS[0], 12)
        _, memory = inspect_dump(b''.join(b.packets()))
        again = Bank.from_memory(memory, 3)
        self.assertEqual(again.field(1, FIELDS[0]), 12)
        self.assertEqual(len(b.packets()), 15)

    def test_field_display_encodings(self):
        sens, scan = FIELDS[0], next(f for f in FIELDS if f.key == 'scan')
        self.assertEqual(sens.display(6), 7)
        self.assertEqual(scan.stored(2.1), 21)
        with self.assertRaises(ValueError):
            sens.stored(33)

    def test_type_change_keeps_xtalk_and_kick_rim_fields(self):
        b = Bank(1, bytes(BANK_PACKED))
        start = INPUT_BASE
        for offset, value in ((9, 7), (14, 8), (17, 25), (18, 2)):
            b.set(start + offset, value)
        b.apply_type_template(0, bytes(TEMPLATES['9']))   # KD120 on KICK
        self.assertEqual(b.trig_type(0), 9)
        self.assertEqual([b.get(start + o) for o in (9, 14, 17, 18)], [7, 8, 25, 2])

    def test_all_types_recorded(self):
        self.assertEqual(sorted(map(int, TEMPLATES)), list(range(len(TRIG_TYPES))))
        self.assertTrue(all(v[0] == int(k) and len(v) == INPUT_STRIDE for k, v in TEMPLATES.items()))


if __name__ == '__main__':
    unittest.main()
