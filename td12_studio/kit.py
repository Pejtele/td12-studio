"""Drum kit data: read and change the MIDI note of each pad, keeping every other byte.

Kit `72 kk 00 00` (kk = kit - 1), 2,035 packed bytes, same packing as a trigger bank.
Note of input n: unpacked 172 + 68 x (2n + hit), hit 0 = head, 1 = rim. Write and
note change confirmed on hardware 2026-11-04 (Kit 50 KICK head).
"""
from .protocol import address_bytes, address_number, checksum
from .trigger import Bank, MODEL, PACKET

KITS = 50
KIT_PACKED = 2035
NOTE_BASE = 172
NOTE_STRIDE = 68
HEAD, RIM = 0, 1
# Unpacked offsets. Pedal confirmed on hardware. The closed notes are sent only with
# HH Note# Border on, so those two are placed by their factory values (42, 22) alone.
HIHAT_NOTES = (('Hi-hat pedal note', 24), ('Closed bow note', 25), ('Closed edge note', 26))


def kit_base(number):
    return address_number(bytes([0x72, number - 1, 0, 0]))


class Kit(Bank):
    def __init__(self, number, packed):
        if len(packed) != KIT_PACKED:
            raise ValueError(f'Kit {number} has {len(packed)} bytes, expected {KIT_PACKED}')
        self.number = number
        self.original = bytes(packed)
        self.packed = bytearray(packed)

    @classmethod
    def from_memory(cls, memory, number):
        base = kit_base(number)
        try:
            return cls(number, bytes(memory[base + i] for i in range(KIT_PACKED)))
        except KeyError:
            raise ValueError(f'Kit {number} is incomplete in this dump') from None

    def changes(self):
        return [i for i in range(KIT_PACKED) if self.packed[i] != self.original[i]]

    def note(self, slot, hit):
        return self.get(NOTE_BASE + NOTE_STRIDE * (2 * slot + hit))

    def set_note(self, slot, hit, note):
        if not 0 <= note <= 127:
            raise ValueError('Note out of range')
        self.set(NOTE_BASE + NOTE_STRIDE * (2 * slot + hit), note)

    def packets(self, device=0x10):
        """DT1 frames with the module's own chunking: 128 bytes per packet."""
        frames, base = [], kit_base(self.number)
        for start in range(0, KIT_PACKED, PACKET):
            payload = address_bytes(base + start) + bytes(self.packed[start:start + PACKET])
            frames.append(b'\xf0\x41' + bytes([device]) + MODEL + b'\x12' + payload
                          + bytes([checksum(payload), 0xf7]))
        return frames
