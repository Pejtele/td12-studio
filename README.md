# TD-12 Studio

An open-source project toward a screen-independent Roland TD-12 editor, for
owners whose module LCD is fading or unusable.

**Current release: read-only research prototype. It is not a working parameter
editor or a replacement display yet.** No module settings are written. The normal GUI/CLI sends only the standard universal identity query. A separate
research script probes one standard Roland RQ1 read request; TD-12 support is
unverified, and it does not send parameter data.

## What works

- MIDI input/output selection and module identity query.
- Live note number, velocity, and channel display.
- Capture module-initiated SysEx dumps to standard `.syx` files.
- Recognize TD-12 bulk blocks and validate Roland checksums.
- Report corrupt, truncated, conflicting, and empty captures.
- Compare bytes by address even when packet boundaries differ.
- Keep missing regions separate from changed values.
- A GTK4 Linux desktop UI and a portable Python command-line core.

A checksum-valid capture is **not evidence of a complete backup**. Published
block lengths are unavailable, so the program explicitly reports completeness
as unknown. It does not implement restore or transmission of captured data.

## Install on Arch / Omarchy

System dependencies: Python, GTK4, PyGObject, ALSA, plus a working MIDI interface.
The GUI uses the system GTK/PyGObject packages. Source builds of python-rtmidi may
need a C++ compiler and ALSA development files; use binary wheels where available.

```sh
python -m venv --system-site-packages .venv
.venv/bin/pip install -e .
.venv/bin/python -m td12_studio gui
```

The core CLI is intended to work on Linux, macOS, and Windows via RtMidi;
only Linux has been exercised so far. GTK4 packaging for other platforms is
not provided. There is no background service or MIDI forwarding.

## Connections

Use both directions for identification: MIDI interface OUT → TD-12 MIDI IN;
TD-12 MIDI OUT → interface IN. On the UM-ONE, follow the **CONNECT TO MIDI IN/OUT**
labels: those labels describe the module socket, not the interface direction.
Capturing a module-initiated dump only needs the TD-12 OUT connection.
Opening our input port adds a listener; it does not remove REAPER's MIDI routes.

## Capture and compare

1. Connect in the UI and give the capture a descriptive name.
2. Click **Start capture**.
3. On the TD-12, choose **SETUP → F1 MIDI → F5 BULK**, select the desired
   block, and press **F5 EXECUTE**. Do not use factory reset or bulk receive.
4. After transmission finishes, click **Stop and inspect**.
5. Captures and JSON reports are saved in `~/.local/share/td12-studio/captures`.
6. Use **Compare two captures** to compare a baseline and a second capture.

The need to initiate bulk dumps on the module is a current limitation for
owners with completely dead screens. We have not established remote dump
requests or virtual front-panel commands.

Research experiments should change only one known setting at a time and return
it to its recorded starting value. Never infer a parameter's meaning from one
byte difference alone. See [research plan](docs/RESEARCH.md).

```sh
.venv/bin/python -m td12_studio ports
.venv/bin/python -m td12_studio identify
.venv/bin/python -m td12_studio capture --label trigger-before
# Capture stays armed until Ctrl+C; use --seconds N only for an explicit limit.
.venv/bin/python -m td12_studio inspect capture.syx
.venv/bin/python -m td12_studio diff before.syx after.syx
.venv/bin/python -m unittest discover -s tests -v
```

## Standing on existing work

- [Roland TD-12 MIDI Implementation](https://cdn.roland.com/assets/media/pdf/TD-12_MI.pdf):
  official identity message, DT1 framing, checksum, and bulk address blocks.
- [Roland TD-12 support](https://www.roland.com/us/support/by_product/td-12/owners_manuals/):
  owner's manual and model documentation.
- [Erriez MIDI SYSEX-IO](https://github.com/Erriez/midi-sysex-io):
  existing open-source Windows/Linux backup utility originally used with TD-12.
- [TD-12/20 LCD emulator](https://github.com/Aleksa-Stojano/ROLAND_TD_12_20_LCD_EMULATOR):
  existing GPLv3 LCD bus decoder. It requires internal hardware probing;
  it does not obtain the display through MIDI. It remains a separate project;
  none of its code is copied here.
- [Mido](https://github.com/mido/mido) and
  [python-rtmidi](https://github.com/SpotlightKid/python-rtmidi): MIDI transport.
- [REA](https://rea.tools/): a possible later analysis frontend for firmware or
  existing software. It is not a substitute for a verified device protocol.

Roland's document is linked, not redistributed with the source. Real personal
backups, firmware, and downloaded reference materials are excluded from Git.
No firmware modification or update is part of this prototype.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Contributions most needed: documented
capture experiments, parameter maps with evidence, and testing on additional
firmware revisions. TD-20 compatibility is not claimed.

MIT license for this project's code. Independent project, not affiliated with Roland.
