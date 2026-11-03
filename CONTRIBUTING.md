# Contributing

Keep hardware evidence separate from assumptions. For protocol findings, include:

- Module model, firmware version if known, and MIDI interface.
- Exact UI setting, bank/input/kit, original and test values.
- Repeated baseline, changed, and restored capture hashes.
- Address differences and tests ruling out incidental/volatile changes.
- Whether the result has been independently reproduced.

Do not upload personal backups, commercial sound packs, vendor PDFs, or firmware
binaries without distribution permission. Prefer minimal synthetic fixtures and
an experiment report. Current tests run with Python's standard unittest module:

```sh
python -m unittest discover -s tests -v
```

Writes exist only for verified layouts (`KNOWN_REVISIONS` in `web/td12.js`).
To add another firmware revision: capture ALL TRIG BANKS, confirm the bank size
is 1,843 bytes, use `scripts/map_session.py` to repeat a few one-change-per-dump
experiments from docs/TRIGGER-MAP.md, and report the diffs. A revision is added
only after a send was verified by a fresh dump on that hardware.

Browser codec tests: `node web/td12.test.mjs [dump.syx ...]` (dumps are optional
and stay private; with them, the JS output is cross-checked against Python).
