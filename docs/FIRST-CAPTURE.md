# First hardware bulk capture

Hardware: Roland TD-12; identity device ID 17; revision bytes `00 02 00 00`.

Captured 2167 DT1 packets with valid checksums and 276004 unique address bytes. No conflicting overlaps or framing errors were found. All documented block categories were present.

Observed contiguous block lengths (single capture; not yet a universal format specification):

| Block | Captured bytes |
|---|---:|
| Setup | 1230 |
| Trigger bank 1 | 1843 |
| Drum kit 1 | 2035 |
| Percussion set 1 | 2391 |
| Pattern information | 14172 |
| Pattern data | 132352 |

All four trigger banks have 1,843 bytes each. All 50 drum kits have 2,035 bytes each; all eight percussion sets have 2,391 bytes each. Trigger-bank coverage is contiguous from `71 bb 00 00` through `71 bb 0E 32`.

## Encoding hypothesis

The data appears to use groups of one high-bit prefix followed by seven low-seven-bit bytes. Removing every eighth byte produces readable trigger-bank names and decoded lengths divisible by four (trigger banks 1,612; kits 1,780; setup 1,076). This is evidence of packing, but high-bit order and field offsets are not established. No parameter write or restore is implemented.

Next: repeat the unchanged dump to check stability and coverage, then map one reversible setting with before/changed/restored captures. Personal capture binaries remain local and excluded from Git.
