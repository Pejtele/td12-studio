# TD-12 protocol research

## Established from Roland's MIDI Implementation v1.00 (2004-12-17)

Source: https://cdn.roland.com/assets/media/pdf/TD-12_MI.pdf

- pp.5,9: universal non-realtime identity request/reply; default device ID byte
  `10` (displayed 17). Family bytes `09 02`, family-number bytes `00 00`.
- pp.5,9: Roland manufacturer `41`, model ID `00 00 09`, command DT1 `12`.
- Four 7-bit address bytes, then data, Roland checksum, and F7.
- Checksum covers address plus data; their sum plus checksum is zero modulo 128.
- p.9: module-initiated bulk dump transmits DT1. Packets up to 128 data bytes,
  at least 40 ms between successive DT1 packets.
- p.10 bulk blocks: setup `70 00 00 00`; four trigger banks `71 bb 00 00`;
  50 kits `72 kk 00 00`; eight percussion sets `73 pp 00 00`;
  pattern information `74 00 00 00`; pattern data `75 00 00 00`.
- Setup dump excludes Device ID and LCD Contrast.

The public map has block starts only. There are no published individual
parameter offsets or block lengths. The receive section names identity query
and DT1, but does not specify RQ1 (`11`). Do not assume generic Roland RQ1 works
on this model, nor invent a block length. Absence of documentation does not
prove an undocumented facility is impossible.

## Local hardware evidence, 2026-10-31

- UM-ONE visible through Linux ALSA, independent of REAPER.
- The initial identity attempt without both MIDI directions connected received no reply.
- After the owner connected both DIN cables, identity was verified live:
  `F0 7E 10 06 02 41 09 02 00 00 00 02 00 00 F7`.
  Device ID is 17; revision bytes are recorded literally, not decoded as a
  guessed firmware version.
- The live test exposed a Python/RtMidi callback shutdown hang. The transport
  now uses RtMidi's native input queue with polling and explicit ALSA on Linux.
  A bounded identify/send/receive/close test then completed successfully.
- No setting writes or bulk restores have been attempted.
- First real ALL dump captured: 2,167 checksum-valid DT1 packets, 276,004 unique
  data bytes, all documented block categories present. See [capture analysis](FIRST-CAPTURE.md).
  Completeness has not yet been established by a repeated dump.

## Next experiments

1. Confirm the two physical MIDI directions, identify the module, preserve reply
   including raw revision bytes without guessing the firmware version encoding.
2. Capture a baseline SETUP and all trigger banks, plus the active kit. Capture
   each block twice without changes to identify volatile differences and stable
   lengths. Verify repeated block coverage, checksums, and device ID.
3. Select one reversible setting, preferably an unused trigger input, with the
   owner. Record its original value. Change one step, capture the same block,
   restore the value, and capture again. Compare A/B/A by address.
4. Repeat with multiple values and another input to establish encoding, bounds,
   and stride. Keep evidence and confidence alongside every map field.
5. Only after baseline backups and mapping evidence: construct an offline edit,
   validate that exactly the intended bytes changed, then test controlled
   restore/readback on an unused bank with explicit experiment scope.
6. If direct reads/writes are unsupported: analyse official firmware offline
   and/or existing software using appropriate tools (potentially REA). Do not
   flash modified firmware to investigate the protocol.

## Release criteria for actual parameter editing

- Named fields tied to repeated hardware experiments, not guessed offsets.
- Tested backup/restore and known complete block lengths for supported firmware.
- Original bytes preserved, minimal edits, visible diff and verified readback.
- Every supported field has a bounded range and explicit encoding.
- Failure and device disconnect do not silently claim success.

## Parallel hardware route

The existing TD-12/20 LCD emulator reads the two Epson SED1565 controllers' bus
using a logic analyser. It could restore visibility when MIDI cannot expose
settings. This needs a hardware connection inside the module and is distinct
from our USB-MIDI editor. No internal probing has been performed here.

## Second capture and bounded read experiment

The second, persistent ALL capture contains 2,408 checksum-valid TD1 packets
and 306,734 unique bytes. All 276,004 bytes shared with the initial capture are
identical; there are no addresses exclusive to the first. The second contains
30,730 additional contiguous pattern bytes, demonstrating that the first
capture did not include the whole pattern region. Setup, all four trigger
banks, all 50 kits and all eight percussion sets have identical coverage.
The first capture must not be described as a complete full backup.

One standard Roland RQ1 (`11`) request targeted bank 1 with the twice-observed
length 1,843 bytes. Only an exact echo of that request was received; no DT1
bank data arrived within four seconds. The echo's origin (module Soft Thru or
routing) is not established. This does not establish remote read support.
The separate script `scripts/probe_read_request.py` records the exact request
and response report; normal GUI/CLI operations still send only identity.

Next mapping experiment: rename only the first character of the current
trigger bank, then capture ALL TRIG BANKS. A name-only change avoids altering
pad response. Restore the original character and recapture to verify A/B/A.
