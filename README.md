# TD-12 Trigger Editor

Edit the trigger settings of a **Roland TD-12** from your computer, for owners
whose module screen is fading or dead. It works in the browser, with nothing to
install.

**→ Open the editor: `https://<your-github-name>.github.io/td12-studio/`**
*(Chrome or Edge on Windows, macOS, Linux or ChromeOS. Safari has no Web MIDI.)*

Independent project, not affiliated with or endorsed by Roland. "Roland" and
"TD-12" are trademarks of Roland Corporation, used only to say what this works with.

## What you can edit

For each of the 12 trigger inputs (KICK, SNARE, TOM 1–3, HI-HAT, CRASH 1–2,
RIDE, EDGE, AUX 1–2), in any of the 4 trigger banks:

- **Trig Type** (PD125 … RT3T). Choosing a type loads that pad's defaults,
  exactly as the module does.
- Sensitivity, Threshold, Curve, Scan Time, Retrig Cancel, Mask Time,
  Rim Gain, RimShot Adjust, Xtalk Cancel.
- XStick Threshold.

Not yet: hi-hat pedal settings, 3-way ride, drum kits, patterns.

## What you need

- A USB-MIDI interface. A Roland UM-ONE works. No-name cables often garble SysEx;
  the editor detects that and refuses to report success.
- **Both** MIDI cables: interface OUT → TD-12 MIDI IN, and TD-12 MIDI OUT/THRU →
  interface IN. On the UM-ONE, the plug labels name the TD-12 socket they go to.

## How it works

The TD-12 has no "send me your settings" command, so each read starts on the module:

1. In the editor click **Connect MIDI** and allow MIDI access.
2. On the TD-12: **SETUP → F1 (MIDI) → F5 (BULK)**, select **ALL TRIG BANKS**, press **F5 (EXECUTE)**.
3. Edit in the browser, click **Send**. A backup of what you read downloads first.
4. Dump **ALL TRIG BANKS** once more. The editor compares every byte and shows
   **✓ Verified** only if the module now holds exactly what was sent.

### With an unreadable screen

After **SETUP → F1 → F5**: turn the dial **left 20–30 clicks** (you land on the
first option, ALL), then **right exactly 4 clicks** (SETUP, ALL DRUM KITS,
1 DRUM KIT, ALL TRIG BANKS), then **F5**. If something else arrives, the editor
tells you what it was. A dump only sends data; it never changes the module.

## Safety

- Only the bank you edit is sent. Every byte of it that you did not change is
  sent back exactly as read, including data this project does not understand.
- **Restore a backup…** puts all four banks back from a downloaded `.syx` file.
- Writing is enabled only for firmware already verified on real hardware
  (identity revision `00 02 00 00`). Other versions open **read-only**: please
  report them (see below) so they can be checked and added.
- These are settings in the module's memory, not firmware. A bad send can scramble
  trigger settings, which a backup or the module's factory reset fixes.

Use at your own risk; see the license.

## Status and evidence

Verified on one TD-12 (revision `00 02 00 00`) on 2026-11-03:
restore of an unchanged bank, a one-byte rename, every field mapped by
one-change-per-dump experiments and read back from the display, all 26 trig
types recorded from the module, and editor sends verified by fresh dumps.
Details: [docs/TRIGGER-MAP.md](docs/TRIGGER-MAP.md).

**Wanted: reports from other TD-12s.** Open an issue with the revision shown
after "Check module" and whether a read worked. To help map a new firmware,
see [CONTRIBUTING.md](CONTRIBUTING.md).

## For developers

| Path | What |
|---|---|
| `web/` | The browser editor (`td12.js` codec, `app.js` page logic). Plain ES modules, no build step. |
| `td12_studio/` | Python reference: codec (`trigger.py`, `protocol.py`), GTK editor, CLI. |
| `td12_studio/trig_types.json` | Trig type defaults recorded from the module. |
| `scripts/` | Hardware research: `map_session.py` (records dumps and diffs), `restore_test.py`. |
| `docs/` | Protocol research and the trigger map. |

```sh
# browser editor, locally (Web MIDI needs localhost or https)
python3 -m http.server 8123      # then open http://localhost:8123/web/
node web/td12.test.mjs [your-dump.syx ...]

# Python tools (Linux tested; GTK4 for the desktop editor)
python -m venv --system-site-packages .venv && .venv/bin/pip install -e .
.venv/bin/python -m td12_studio editor
.venv/bin/python -m unittest discover -s tests -v
```

Built on Roland's public
[TD-12 MIDI Implementation](https://cdn.roland.com/assets/media/pdf/TD-12_MI.pdf)
(framing, checksum, bulk block addresses) plus hardware experiments for
everything inside the blocks. Related: the
[TD-12/20 LCD emulator](https://github.com/Aleksa-Stojano/ROLAND_TD_12_20_LCD_EMULATOR)
(a hardware route; none of its code is used here) and
[Erriez MIDI SYSEX-IO](https://github.com/Erriez/midi-sysex-io) for plain backups.

MIT license.
