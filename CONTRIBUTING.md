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

The current transport must remain read-only except for identity queries. Add
parameter transmission only alongside a separately reviewed hardware test plan,
verified mappings, backup handling, and readback evidence.
