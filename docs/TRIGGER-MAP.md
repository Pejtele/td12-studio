# TD-12 trigger bank map (hardware-verified 2026-11-03)

Source: mapping session `research/map-20261103-201243/` on bank 4, one setting
changed per dump; every step changed exactly one byte. This unit, revision
bytes `00 02 00 00`.

## Bank layout

- Bank `71 bb 00 00` (bb = bank − 1), 1,843 packed bytes = 1,612 unpacked.
- Packing: 8-byte groups, first byte holds high bits (all 0 seen so far), then
  7 data bytes. Unpacked offset u ↔ packed offset (u // 7) * 8 + 1 + u % 7.
- Unpacked 0–11: bank name, ASCII, space padded.
- Unpacked 26: XStick Thrshld (changed when the SNARE value was edited; may be
  snare-only. Not repeated per input).
- Unpacked 20: 3Way Trigger, 0 = OFF, 1 = ON (toggled on the module, 2026-11-04; changed exactly
  this byte). The EDGE Trig Type byte stays as it was; the module just shows RD CTRL.
- Writes: send the whole bank as DT1, ≤128 bytes per packet, 60 ms gap
  (`scripts/restore_test.py`). Accepted without any extra internal checksum.
- The active trigger bank number is not in the trigger banks (bank select changed nothing).

## Trig Type (verified)

Each input block starts at unpacked 52 + 104 × (n − 1); Sensitivity is block +8.
Block +0 is Trig Type, index in manual order (PD125 = 0 … RT3T = 25), confirmed
by stepping through all 26 types on the module and by every factory bank.
Choosing a type loads defaults for the whole block except +16, Xtalk (+17, +18),
and rim fields on inputs without them. Defaults per type are in
`research/trig-type-templates.json`, recorded on SNARE; re-applying them to
factory banks 2–4 changes zero bytes (`td12_studio/trigger.py`).

Packing: bit k of each group's first byte is the high bit of data byte k.
Block +20 holds 128 on all real inputs.

## Per-input block (offsets below are from Sensitivity, i.e. block +8)

Sensitivity of input n sits at unpacked 60 + 104 × (n − 1). Verified for
KICK (60), SNARE (164), TOM 1 (268). Fields relative to that offset:

Encodings confirmed against the display (bank 4 SNARE) unless marked.

| +  | Field           | Stored → display          | Example (stored → shown) |
|----|-----------------|---------------------------|--------------------------|
| 0  | Sensitivity     | stored + 1 (1–32)         | 6 → 7                    |
| 1  | Rim Gain        | stored / 10 (0–3.2)       | 22 → 2.2                 |
| 2  | Threshold       | as stored (0–31)          | 2 → 2                    |
| 3  | Curve           | 0 = LINEAR; 1 = next (EXP1?, unconfirmed) | 0 → LINEAR |
| 4  | Scan Time       | stored / 10 ms (0–4.0)    | 20 → 2.0                 |
| 5  | Mask Time       | ms as stored (0–64)       | 6 → 6 ms                 |
| 6  | RimShot Adjust  | stored / 10 (0–8.0)       | 30 → 3.0                 |
| 7  | Retrig Cancel   | stored + 1 (1–16)         | 3 → 4                    |
| 8  | unknown         | values 0–2 across inputs  | —                        |
| 9  | Xtalk Cancel    | as stored, steps of 5; 0 = OFF? | 10 → 10            |
| 10 | Xtalk Group     | index (display unconfirmed) | —                      |

XStick Thrshld (unpacked 26): as stored, 90 → 90.
Input order beyond 1–3 assumed to follow TRIGGER INPUT numbering; not yet verified.
Trig Type, hi-hat and 3-way settings are not mapped yet.

## Owner's live bank 1 SNARE (2026-11-03 backup), decoded

Sensitivity 3, Threshold 2, LINEAR, Scan 2.0, Mask 6, Rim Gain 1.5, RimShot 2.7,
Retrig 4, Xtalk 25. (Display not yet compared.)
