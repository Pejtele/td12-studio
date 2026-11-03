// node web/td12.test.mjs [capture.syx ...] — checks the JS codec against real
// captures and the Python reference. Captures are personal and not in the repo.
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { splitSysex, readDump, parseDT1, Bank, FIELDS, TRIG_TYPES, stored, display, hex } from './td12.js';

const root = new URL('..', import.meta.url).pathname;
const templates = JSON.parse(readFileSync(root + 'td12_studio/trig_types.json'));

// Synthetic: checksum example from Roland's documentation style
const bank = new Bank(4, new Array(1843).fill(0));
bank.set(20, 200); assert.equal(bank.get(20), 200);
bank.set(20, 5); assert.equal(bank.get(20), 5);
const f = FIELDS[0]; assert.equal(display(f, stored(f, 7)), 7);
for (const fr of bank.packets()) assert.ok(parseDT1(fr));
console.log('synthetic ok');

for (const file of process.argv.slice(2)) {
  const bytes = readFileSync(file);
  const { frames, errors } = splitSysex(bytes);
  assert.deepEqual(errors, []);
  const dump = readDump(frames);
  for (const n of dump.banks) {
    const b = Bank.fromMemory(dump.memory, n);
    const own = frames.filter(fr => fr[7] === 0x71 && fr[8] === n - 1);
    assert.deepEqual(b.packets(dump.device), own, `packets differ: bank ${n}`);
    // Re-applying each input's own type must not change the bank, as in Python
    if (n > 1) {
      for (let s = 0; s < 12; s++) b.applyTypeTemplate(s, templates[b.trigType(s)]);
      assert.ok(!b.changed(), `template changed bank ${n}`);
    }
  }
  // Same edit in JS and Python must give identical packets
  const b = Bank.fromMemory(dump.memory, 4);
  b.setField(1, FIELDS[0], 20); b.applyTypeTemplate(2, templates[3]); b.set(26, 77);
  const py = execFileSync(root + '.venv/bin/python', ['-I', '-c', `
import sys; sys.path.insert(0, ${JSON.stringify(root)})
from pathlib import Path
from td12_studio.protocol import inspect_dump
from td12_studio.trigger import Bank, FIELDS
import json
b = Bank.from_memory(inspect_dump(Path(${JSON.stringify(file)}).read_bytes())[1], 4)
t = json.loads(Path(${JSON.stringify(root)} + 'td12_studio/trig_types.json').read_text())
b.set_field(1, FIELDS[0], 20); b.apply_type_template(2, bytes(t['3'])); b.set(26, 77)
print(json.dumps([list(p) for p in b.packets()]))`]).toString();
  assert.deepEqual(b.packets(), JSON.parse(py));
  console.log(file.split('/').pop(), 'banks', dump.banks.join(','), 'ok; bank 4 snare', TRIG_TYPES[b.trigType(1)]);
}
console.log('ALL OK');
