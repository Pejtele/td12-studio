// TD-12 trigger bank codec — JavaScript port of td12_studio/protocol.py and
// trigger.py. Hardware-verified on one unit; see docs/TRIGGER-MAP.md.

export const MODEL = [0x00, 0x00, 0x09];
export const IDENTITY_REQUEST = [0xf0, 0x7e, 0x7f, 0x06, 0x01, 0xf7];
export const KNOWN_REVISIONS = ['00 02 00 00'];   // layouts verified on real hardware

export const BANK_PACKED = 1843;
export const BANK_UNPACKED = 1612;
export const INPUT_BASE = 52, INPUT_STRIDE = 104;
export const NAME_LENGTH = 12;
export const XSTICK = 26;
const PACKET = 128;

export const INPUTS = ['KICK', 'SNARE', 'TOM 1', 'TOM 2', 'TOM 3', 'HI-HAT', 'CRASH 1',
  'CRASH 2', 'RIDE', 'EDGE', 'AUX 1', 'AUX 2'];
export const TRIG_TYPES = ('PD125 PD120 PD105 PD100 PD80R PD9 PD8 PD7 PD6 KD120 KD80 KD8 KD7 ' +
  'CY15R CY12RC CY14C CY8 CY6 CY12H VH12 VH11 PAD1 PAD2 RT7K RT5S RT3T').split(' ');
export const CURVES = 'LINEAR EXP1 EXP2 LOG1 LOG2 SPLINE LOUD1 LOUD2'.split(' ');

// offset is within the input block; display = (stored + shift) / scale
export const FIELDS = [
  { key: 'sens', label: 'Sensitivity', offset: 8, lo: 0, hi: 31, scale: 1, shift: 1 },
  { key: 'thresh', label: 'Threshold', offset: 10, lo: 0, hi: 31, scale: 1, shift: 0 },
  { key: 'curve', label: 'Curve', offset: 11, lo: 0, hi: CURVES.length - 1, scale: 1, shift: 0 },
  { key: 'scan', label: 'Scan Time', offset: 12, lo: 0, hi: 40, scale: 10, shift: 0, unit: 'ms' },
  { key: 'retrig', label: 'Retrig Cancel', offset: 15, lo: 0, hi: 15, scale: 1, shift: 1 },
  { key: 'mask', label: 'Mask Time', offset: 13, lo: 0, hi: 64, scale: 1, shift: 0, unit: 'ms' },
  { key: 'rimgain', label: 'Rim Gain', offset: 9, lo: 0, hi: 32, scale: 10, shift: 0 },
  { key: 'rimshot', label: 'RimShot Adj', offset: 14, lo: 0, hi: 80, scale: 10, shift: 0 },
  { key: 'xtalk', label: 'Xtalk Cancel', offset: 17, lo: 0, hi: 80, scale: 1, shift: 0, step: 5 },
];
export const display = (f, stored) => (stored + f.shift) / f.scale;
export function stored(f, shown) {
  const v = Math.round(shown * f.scale) - f.shift;
  if (!(v >= f.lo && v <= f.hi)) throw new Error(`${f.label} out of range`);
  return v;
}

// A trig type change leaves +16, Xtalk (+17, +18) and rim fields on inputs without them.
const KEEP_ON_TYPE_CHANGE = [16, 17, 18];
const KEEP_FOR_SLOT = { 0: [9, 14], 5: [14], 6: [14], 7: [14], 8: [14], 9: [14] };

export const checksum = data => (128 - data.reduce((a, b) => a + b, 0) % 128) % 128;
export const addressNumber = a => ((a[0] * 128 + a[1]) * 128 + a[2]) * 128 + a[3];
export const addressBytes = n => [21, 14, 7, 0].map(s => (n >> s) & 127);
export const bankBase = bank => addressNumber([0x71, bank - 1, 0, 0]);
export const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0').toUpperCase()).join(' ');
const packedIndex = u => Math.floor(u / 7) * 8 + 1 + u % 7;

/** Split a byte stream into complete SysEx frames, ignoring realtime bytes. */
export function splitSysex(bytes) {
  const frames = [], errors = [];
  let cur = null;
  for (const b of bytes) {
    if (b >= 0xf8) continue;
    if (b === 0xf0) { if (cur) errors.push('Truncated frame'); cur = [b]; }
    else if (b === 0xf7) { if (cur) { cur.push(b); frames.push(cur); cur = null; } else errors.push('Unexpected F7'); }
    else if (cur) { if (b >= 0x80) { errors.push('Status byte inside SysEx'); cur = null; } else cur.push(b); }
  }
  if (cur) errors.push('Truncated final frame');
  return { frames, errors };
}

export function parseIdentity(f) {
  if (f.length === 15 && f[1] === 0x7e && f[3] === 0x06 && f[4] === 0x02 && f[5] === 0x41 &&
      f[6] === 0x09 && f[7] === 0x02 && f[8] === 0 && f[9] === 0)
    return { device: f[2], revision: hex(f.slice(10, 14)) };
  return null;
}

export function parseDT1(f) {
  if (f.length < 14 || f[1] !== 0x41 || f[3] !== 0 || f[4] !== 0 || f[5] !== 9 || f[6] !== 0x12) return null;
  const body = f.slice(7, -2);
  if (checksum(body) !== f[f.length - 2]) throw new Error('Roland checksum mismatch');
  return { device: f[2], address: addressNumber(body.slice(0, 4)), data: body.slice(4) };
}

/** Collect DT1 packets into address -> byte; report what was found. */
export function readDump(frames) {
  const memory = new Map(), devices = new Set(), errors = [];
  let packets = 0;
  for (const f of frames) {
    let p;
    try { p = parseDT1(f); } catch (e) { errors.push(e.message); continue; }
    if (!p) continue;
    packets++; devices.add(p.device);
    p.data.forEach((v, i) => {
      const a = p.address + i;
      if (memory.has(a) && memory.get(a) !== v) errors.push('Conflicting data');
      memory.set(a, v);
    });
  }
  if (devices.size > 1) errors.push('Dump mixes device IDs');
  const banks = [1, 2, 3, 4].filter(n => {
    const base = bankBase(n);
    for (let i = 0; i < BANK_PACKED; i++) if (!memory.has(base + i)) return false;
    return !memory.has(base + BANK_PACKED);   // a longer bank means an unknown layout
  });
  return { memory, packets, banks, device: [...devices][0], errors };
}

export class Bank {
  constructor(number, packed) {
    if (packed.length !== BANK_PACKED) throw new Error(`Bank ${number}: unexpected size ${packed.length}`);
    this.number = number;
    this.original = Uint8Array.from(packed);
    this.packed = Uint8Array.from(packed);
  }
  static fromMemory(memory, number) {
    const base = bankBase(number), out = [];
    for (let i = 0; i < BANK_PACKED; i++) {
      if (!memory.has(base + i)) throw new Error(`Trigger bank ${number} is incomplete`);
      out.push(memory.get(base + i));
    }
    return new Bank(number, out);
  }
  get(u) {
    const p = packedIndex(u);
    return this.packed[p] | ((this.packed[p - p % 8] >> (u % 7)) & 1) << 7;
  }
  set(u, value) {
    if (!(value >= 0 && value <= 255)) throw new Error('Value must fit in one byte');
    const p = packedIndex(u), bit = 1 << (u % 7), prefix = p - p % 8;
    this.packed[prefix] = (this.packed[prefix] & ~bit) | (value & 128 ? bit : 0);
    this.packed[p] = value & 127;
  }
  get name() {
    let s = '';
    for (let i = 0; i < NAME_LENGTH; i++) s += String.fromCharCode(this.get(i));
    return s.trimEnd();
  }
  start(slot) { return INPUT_BASE + INPUT_STRIDE * slot; }
  field(slot, f) { return this.get(this.start(slot) + f.offset); }
  setField(slot, f, v) {
    if (!(v >= f.lo && v <= f.hi)) throw new Error(`${f.label} out of range`);
    this.set(this.start(slot) + f.offset, v);
  }
  trigType(slot) { return this.get(this.start(slot)); }
  block(slot) { return Array.from({ length: INPUT_STRIDE }, (_, i) => this.get(this.start(slot) + i)); }
  applyTypeTemplate(slot, template) {
    if (template.length !== INPUT_STRIDE) throw new Error('Template must be one whole input block');
    const keep = new Set([...KEEP_ON_TYPE_CHANGE, ...(KEEP_FOR_SLOT[slot] || [])]);
    if (!/^(PD|PAD|RT)/.test(TRIG_TYPES[template[0]])) keep.add(14);  // RimShot only on pads
    template.forEach((v, i) => { if (!keep.has(i)) this.set(this.start(slot) + i, v); });
  }
  changed() { return this.packed.some((v, i) => v !== this.original[i]); }
  /** DT1 frames with the module's own chunking (128 data bytes per packet). */
  packets(device = 0x10) {
    const frames = [], base = bankBase(this.number);
    for (let s = 0; s < BANK_PACKED; s += PACKET) {
      const payload = [...addressBytes(base + s), ...this.packed.slice(s, s + PACKET)];
      frames.push([0xf0, 0x41, device, ...MODEL, 0x12, ...payload, checksum(payload), 0xf7]);
    }
    return frames;
  }
}
