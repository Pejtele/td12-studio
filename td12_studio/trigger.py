"""TD-12 trigger bank codec, from hardware mapping (docs/TRIGGER-MAP.md).

Banks are edited in their packed form (as transmitted), so every byte that is
not a mapped field is preserved exactly. Packing: 8-byte groups; byte 0 holds
the high bits, bit k belonging to data byte k (consistent across all captures).
"""
from dataclasses import dataclass
from .protocol import MODEL, address_bytes, address_number, checksum

BANK_PACKED = 1843
BANK_UNPACKED = 1612
INPUT_BASE, INPUT_STRIDE = 52, 104   # 15 input blocks end exactly at 1612
NAME_LENGTH = 12
XSTICK = 26                          # bank-level XStick Thrshld
PACKET = 128

INPUTS = ['KICK', 'SNARE', 'TOM 1', 'TOM 2', 'TOM 3', 'HI-HAT', 'CRASH 1',
          'CRASH 2', 'RIDE', 'EDGE', 'AUX 1', 'AUX 2']   # slots 13-15 untouched

TRIG_TYPES = ('PD125 PD120 PD105 PD100 PD80R PD9 PD8 PD7 PD6 KD120 KD80 KD8 KD7 '
              'CY15R CY12RC CY14C CY8 CY6 CY12H VH12 VH11 PAD1 PAD2 RT7K RT5S RT3T').split()
CURVES = 'LINEAR EXP1 EXP2 LOG1 LOG2 SPLINE LOUD1 LOUD2'.split()


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    offset: int        # within the input block
    lo: int            # stored range
    hi: int
    scale: int = 1     # display = (stored + shift) / scale
    shift: int = 0
    unit: str = ''

    def display(self, stored):
        return (stored + self.shift) / self.scale

    def stored(self, shown):
        value = round(shown * self.scale) - self.shift
        if not self.lo <= value <= self.hi:
            raise ValueError(f'{self.label} out of range')
        return value


FIELDS = [
    Field('sens', 'Sensitivity', 8, 0, 31, shift=1),
    Field('thresh', 'Threshold', 10, 0, 31),
    Field('curve', 'Curve', 11, 0, len(CURVES) - 1),
    Field('scan', 'Scan Time', 12, 0, 40, scale=10, unit='ms'),
    Field('retrig', 'Retrig Cancel', 15, 0, 15, shift=1),
    Field('mask', 'Mask Time', 13, 0, 64, unit='ms'),
    Field('rimgain', 'Rim Gain', 9, 0, 32, scale=10),
    Field('rimshot', 'RimShot Adj', 14, 0, 80, scale=10),
    Field('xtalk', 'Xtalk Cancel', 17, 0, 80),
]
TRIG_TYPE_OFFSET = 0
# Bytes a trig type change leaves alone: 16 (unknown, per input) and Xtalk
# 17-18 (manual p.44). Templates were recorded on SNARE; factory banks show
# KICK has no Rim Gain/RimShot and HI-HAT..EDGE no RimShot, so keep those there.
KEEP_ON_TYPE_CHANGE = {16, 17, 18}
KEEP_FOR_SLOT = {0: {9, 14}, 5: {14}, 6: {14}, 7: {14}, 8: {14}, 9: {14}}


def packed_index(u):
    return (u // 7) * 8 + 1 + u % 7


def bank_base(bank):
    return address_number(bytes([0x71, bank - 1, 0, 0]))


class Bank:
    def __init__(self, number, packed):
        if len(packed) != BANK_PACKED:
            raise ValueError(f'Bank {number} has {len(packed)} bytes, expected {BANK_PACKED}')
        self.number = number
        self.original = bytes(packed)
        self.packed = bytearray(packed)

    @classmethod
    def from_memory(cls, memory, number):
        base = bank_base(number)
        try:
            return cls(number, bytes(memory[base + i] for i in range(BANK_PACKED)))
        except KeyError:
            raise ValueError(f'Trigger bank {number} is incomplete in this dump') from None

    def get(self, u):
        p = packed_index(u)
        high = self.packed[p - p % 8] >> (u % 7) & 1
        return self.packed[p] | high << 7

    def set(self, u, value):
        if not 0 <= value <= 255:
            raise ValueError('Value must fit in one byte')
        p = packed_index(u)
        bit = 1 << (u % 7)
        prefix = p - p % 8
        self.packed[prefix] = self.packed[prefix] & ~bit | (bit if value & 128 else 0)
        self.packed[p] = value & 127

    @property
    def name(self):
        return bytes(self.get(i) for i in range(NAME_LENGTH)).decode('ascii', 'replace').rstrip()

    def field(self, slot, field):
        return self.get(INPUT_BASE + INPUT_STRIDE * slot + field.offset)

    def set_field(self, slot, field, stored):
        if not field.lo <= stored <= field.hi:
            raise ValueError(f'{field.label} out of range')
        self.set(INPUT_BASE + INPUT_STRIDE * slot + field.offset, stored)

    def trig_type(self, slot):
        return self.get(INPUT_BASE + INPUT_STRIDE * slot + TRIG_TYPE_OFFSET)

    def apply_type_template(self, slot, template):
        """Copy a recorded type block (as the module writes it), keeping Xtalk."""
        if len(template) != INPUT_STRIDE:
            raise ValueError('Template must be one whole input block')
        start = INPUT_BASE + INPUT_STRIDE * slot
        keep = KEEP_ON_TYPE_CHANGE | KEEP_FOR_SLOT.get(slot, set())
        if not TRIG_TYPES[template[0]].startswith(('PD', 'PAD', 'RT')):
            keep = keep | {14}   # RimShot Adjust only applies to pads
        for i, value in enumerate(template):
            if i not in keep:
                self.set(start + i, value)

    def block(self, slot):
        start = INPUT_BASE + INPUT_STRIDE * slot
        return bytes(self.get(start + i) for i in range(INPUT_STRIDE))

    def changes(self):
        return [i for i in range(BANK_PACKED) if self.packed[i] != self.original[i]]

    def packets(self, device=0x10):
        """DT1 frames with the module's own chunking: 128 bytes per packet."""
        frames, base = [], bank_base(self.number)
        for start in range(0, BANK_PACKED, PACKET):
            payload = address_bytes(base + start) + bytes(self.packed[start:start + PACKET])
            frames.append(b'\xf0\x41' + bytes([device]) + MODEL + b'\x12' + payload
                          + bytes([checksum(payload), 0xf7]))
        return frames
