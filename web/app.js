// TD-12 Trigger Editor — page logic. Codec lives in td12.js.
import {
  IDENTITY_REQUEST, KNOWN_REVISIONS, INPUTS, TRIG_TYPES, CURVES, FIELDS, XSTICK,
  Bank, readDump, splitSysex, parseIdentity, display, stored, hex,
} from './td12.js';

// Set to a Ko-fi / Gumroad / Buy Me a Coffee URL to show the support section.
const SUPPORT_URL = '';

const IDLE_MS = 1500;     // silence that ends a dump
const GAP_MS = 60;        // between DT1 packets (Roland: at least 40 ms)
const $ = id => document.getElementById(id);

const s = {
  midi: null, input: null, output: null,
  identity: null,           // {device, revision}
  dump: null, dumpBytes: null, bank: null, templates: null,
  state: 'idle',            // idle | sending | verify
  sent: [],                 // banks awaiting verification
  backedUp: false,
  buffer: [], timer: null,
};

// ---------- MIDI ----------

$('connect').onclick = async () => {
  if (!navigator.requestMIDIAccess) {
    return say('This browser has no Web MIDI. Use Chrome or Edge.', 'bad');
  }
  try {
    s.midi = await navigator.requestMIDIAccess({ sysex: true });
  } catch {
    return say('MIDI access was blocked. Allow MIDI (with SysEx) for this page and try again.', 'bad');
  }
  s.midi.onstatechange = fillPorts;
  fillPorts();
  $('connect').disabled = true;
};

function fillPorts() {
  for (const [id, ports] of [['input', s.midi.inputs], ['output', s.midi.outputs]]) {
    const sel = $(id), keep = sel.value;
    sel.replaceChildren(...[...ports.values()].map(p => new Option(p.name, p.id)));
    const guess = [...ports.values()].find(p => /um-one|td-12|roland/i.test(p.name));
    sel.value = [...ports.values()].some(p => p.id === keep) ? keep : (guess || [...ports.values()][0] || {}).id || '';
    sel.disabled = false;
  }
  usePorts();
}

function usePorts() {
  if (s.input) s.input.onmidimessage = null;
  s.input = s.midi.inputs.get($('input').value) || null;
  s.output = s.midi.outputs.get($('output').value) || null;
  if (s.input) s.input.onmidimessage = onMessage;
  $('identify').disabled = !s.output;
  s.identity = null;
  if (s.output) identify();
  else $('identity').textContent = 'No MIDI output found. Is the interface plugged in?';
  if (!s.dump) say('Waiting for a dump from the TD-12.');
}
$('input').onchange = $('output').onchange = usePorts;
$('identify').onclick = identify;

function identify() {
  $('identity').textContent = 'Asking the module who it is…';
  $('identity').className = 'muted';
  s.identity = null;
  s.output.send(IDENTITY_REQUEST);
  setTimeout(() => {
    if (!s.identity) {
      $('identity').textContent = 'No answer from a TD-12. Reading still works with one cable; ' +
        'sending needs both cables (interface OUT → TD-12 MIDI IN).';
      $('identity').className = 'bad';
      refresh();
    }
  }, 2500);
}

function onMessage(e) {
  const d = e.data;
  if (d[0] >= 0xf8) return;                     // clock, active sensing
  if (d[0] !== 0xf0 && !(s.buffer.length && d[0] < 0x80)) return;   // notes etc.
  const id = d[0] === 0xf0 && d[d.length - 1] === 0xf7 ? parseIdentity([...d]) : null;
  if (id) return onIdentity(id);
  s.buffer.push(...d);
  clearTimeout(s.timer);
  s.timer = setTimeout(endOfDump, IDLE_MS);
}

function onIdentity(id) {
  s.identity = id;
  const known = KNOWN_REVISIONS.includes(id.revision);
  $('identity').textContent = `Roland TD-12 found · device ID ${id.device + 1} · revision ${id.revision}` +
    (known ? ' · verified version, editing allowed' :
      ' · this version is not verified yet: read-only. Please report it so it can be added.');
  $('identity').className = known ? 'ok' : 'bad';
  refresh();
}

const canWrite = () => !!(s.output && s.identity && KNOWN_REVISIONS.includes(s.identity.revision) &&
  s.dump && s.dump.device === s.identity.device);

// ---------- receiving ----------

function describe(dump) {
  const kinds = new Set();
  for (const a of dump.memory.keys()) kinds.add(Math.floor(a / 128 ** 3));
  const names = { 0x70: 'SETUP', 0x71: 'trigger banks', 0x72: 'drum kits', 0x73: 'percussion sets', 0x74: 'patterns', 0x75: 'patterns' };
  return [...new Set([...kinds].map(k => names[k] || 'unknown data'))].join(' + ') || 'no TD-12 data';
}

function endOfDump() {
  const bytes = s.buffer; s.buffer = [];
  const { frames } = splitSysex(bytes);
  const dump = readDump(frames);
  if (dump.banks.length < 4 || dump.errors.length) {
    if (s.state === 'sending' || dump.packets === 15 * s.sent.length) return;   // echo of our own send
    const what = dump.errors.length ? `damaged data (${dump.errors[0]})` : describe(dump);
    return say(`Received ${what}, not all four trigger banks. Choose ALL TRIG BANKS and dump again.`, 'bad');
  }
  if (s.state === 'verify') return verify(dump, bytes);
  if (s.bank && s.bank.changed()) {
    s.pending = { dump, bytes };
    return say('A new dump arrived, but you have unsent edits. "Revert edits" loads the new dump.');
  }
  load(dump, bytes);
  say(`Read all four trigger banks at ${new Date().toLocaleTimeString()}.` +
    (canWrite() ? ' Edit, then send.' : ' Read-only (see step 1).'), 'ok');
}

function load(dump, bytes) {
  const first = !s.dump;
  s.dump = dump; s.dumpBytes = bytes; s.backedUp = false; s.pending = null;
  const sel = $('bank'), keep = sel.value || '1';
  sel.replaceChildren(...[1, 2, 3, 4].map(n =>
    new Option(`${n}  “${Bank.fromMemory(dump.memory, n).name}”`, n)));
  sel.value = keep;
  if (first) buildGrid();
  showBank();
}

function verify(dump, bytes) {
  const bad = s.sent.filter(b => {
    const got = Bank.fromMemory(dump.memory, b.number);
    return got.original.some((v, i) => v !== b.packed[i]);
  });
  s.state = 'idle';
  if (bad.length) {
    say(`✗ Bank ${bad.map(b => b.number).join(', ')} on the TD-12 does not match what was sent. ` +
      'Your edits are still here; send again. A poor USB-MIDI cable can garble SysEx.', 'bad');
    return refresh();
  }
  const numbers = s.sent.map(b => b.number);
  s.sent = [];
  load(dump, bytes);
  say(numbers.length > 1 ? `✓ Verified: banks ${numbers.join(', ')} on the TD-12 now match exactly.`
    : `✓ Verified: bank ${numbers[0]} on the TD-12 now matches exactly.`, 'ok');
}

// ---------- grid ----------

function buildGrid() {
  const head = ['Input', 'Trig Type', ...FIELDS.map(f => f.label + (f.unit ? ` (${f.unit})` : ''))];
  const rows = [`<tr>${head.map(h => `<th>${h}</th>`).join('')}</tr>`];
  INPUTS.forEach((name, slot) => {
    const cells = [`<td>${slot + 1} ${name}</td>`,
      `<td><select data-slot="${slot}" data-key="type">${TRIG_TYPES.map((t, i) => `<option value="${i}">${t}</option>`).join('')}</select></td>`];
    for (const f of FIELDS) {
      cells.push(f.key === 'curve'
        ? `<td><select data-slot="${slot}" data-key="curve">${CURVES.map((c, i) => `<option value="${i}">${c}</option>`).join('')}</select></td>`
        : `<td><input type="number" data-slot="${slot}" data-key="${f.key}" min="${display(f, f.lo)}" ` +
          `max="${display(f, f.hi)}" step="${f.step || 1 / f.scale}"></td>`);
    }
    rows.push(`<tr>${cells.join('')}</tr>`);
  });
  $('grid').innerHTML = rows.join('');
  $('grid').onchange = onEdit;
}

function showBank() {
  s.bank = Bank.fromMemory(s.dump.memory, +$('bank').value);
  showValues();
}

function showValues() {
  for (const el of $('grid').querySelectorAll('[data-key]')) {
    const slot = +el.dataset.slot, key = el.dataset.key;
    if (key === 'type') el.value = s.bank.trigType(slot);
    else {
      const f = FIELDS.find(x => x.key === key);
      el.value = key === 'curve' ? s.bank.field(slot, f) : display(f, s.bank.field(slot, f));
    }
  }
  $('xstick').value = s.bank.get(XSTICK);
  refresh();
}

function onEdit(e) {
  const el = e.target, slot = +el.dataset.slot, key = el.dataset.key;
  try {
    if (key === 'type') {
      const t = s.templates && s.templates[el.value];
      if (!t) throw new Error('Trig type defaults are not available');
      s.bank.applyTypeTemplate(slot, t);
      return showValues();                       // a type loads its defaults
    }
    const f = FIELDS.find(x => x.key === key);
    s.bank.setField(slot, f, key === 'curve' ? +el.value : stored(f, +el.value));
  } catch (err) {
    say(err.message, 'bad');
    return showValues();
  }
  refresh();
}

$('xstick').onchange = () => {
  const v = +$('xstick').value;
  if (Number.isInteger(v) && v >= 0 && v <= 127) s.bank.set(XSTICK, v);
  showValues();
};
$('bank').onchange = showBank;
$('revert').onclick = () => {
  if (s.pending) load(s.pending.dump, s.pending.bytes);
  else showBank();
};

function changeList() {
  const before = new Bank(s.bank.number, s.bank.original), lines = [];
  INPUTS.forEach((name, slot) => {
    const a = before.trigType(slot), b = s.bank.trigType(slot);
    if (a !== b) lines.push(`${name} Trig Type ${TRIG_TYPES[a]} → ${TRIG_TYPES[b]} (loads its defaults)`);
    for (const f of FIELDS) {
      const x = before.field(slot, f), y = s.bank.field(slot, f);
      const show = v => f.key === 'curve' ? CURVES[v] : display(f, v);
      if (x !== y) lines.push(`${name} ${f.label} ${show(x)} → ${show(y)}`);
    }
  });
  if (before.get(XSTICK) !== s.bank.get(XSTICK)) lines.push(`XStick Thrshld ${before.get(XSTICK)} → ${s.bank.get(XSTICK)}`);
  return lines;
}

function refresh() {
  const busy = s.state !== 'idle', ready = !!s.bank;
  const lines = ready ? changeList() : [];
  if (ready) {
    const before = new Bank(s.bank.number, s.bank.original);
    for (const el of $('grid').querySelectorAll('[data-key]')) {
      const slot = +el.dataset.slot, key = el.dataset.key;
      const f = FIELDS.find(x => x.key === key);
      const diff = key === 'type' ? before.trigType(slot) !== s.bank.trigType(slot)
        : before.field(slot, f) !== s.bank.field(slot, f);
      el.classList.toggle('changed', diff);
      el.disabled = busy || (key === 'type' && !s.templates);
    }
    $('xstick').classList.toggle('changed', before.get(XSTICK) !== s.bank.get(XSTICK));
    $('changes').textContent = lines.length ? 'Unsent changes:\n' + lines.join('\n') : 'No unsent changes.';
    $('send').textContent = `Send bank ${s.bank.number} to TD-12`;
  }
  $('xstick').disabled = !ready || busy;
  $('bank').disabled = !ready || busy || lines.length > 0;
  $('revert').disabled = busy || !(lines.length || s.pending);
  $('send').disabled = busy || !lines.length || !canWrite();
  $('send').title = canWrite() ? '' : 'Sending needs a verified TD-12 answering on both cables';
  $('backup').disabled = !s.dumpBytes;
  $('restore-pick').disabled = busy || !canWrite();
}

// ---------- sending ----------

function download(bytes, label) {
  const d = new Date(), p = n => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([Uint8Array.from(bytes)], { type: 'application/octet-stream' }));
  a.download = `td12-${label}-${stamp}.syx`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
$('backup').onclick = () => download(s.dumpBytes, 'trigger-banks');

function send(banks) {
  if (!s.backedUp) { download(s.dumpBytes, 'backup-before-send'); s.backedUp = true; }
  const frames = banks.flatMap(b => b.packets(s.dump.device));
  s.sent = banks; s.state = 'sending'; refresh();
  const t0 = performance.now() + 50;
  try {
    frames.forEach((f, i) => s.output.send(f, t0 + i * GAP_MS));
  } catch (err) {
    s.state = 'idle'; s.sent = [];
    say(`Sending failed: ${err.message}. Bank may be partly written — send again.`, 'bad');
    return refresh();
  }
  say(`Sending ${frames.length} packets…`);
  setTimeout(() => {
    s.state = 'verify'; refresh();
    say('Sent. Now dump ALL TRIG BANKS on the TD-12 to verify.');
  }, frames.length * GAP_MS + 300);
}

$('send').onclick = () => {
  const lines = changeList();
  if (confirm(`Send bank ${s.bank.number} “${s.bank.name}” to the TD-12?\n\n${lines.join('\n')}\n\n` +
      'A backup of the current read downloads first. Afterwards, dump once to verify.')) send([s.bank]);
};

$('restore-pick').onclick = () => $('restore-file').click();
$('restore-file').onchange = async e => {
  const file = e.target.files[0]; e.target.value = '';
  if (!file) return;
  const dump = readDump(splitSysex(new Uint8Array(await file.arrayBuffer())).frames);
  if (dump.banks.length < 4 || dump.errors.length) return say('That file does not contain four complete trigger banks.', 'bad');
  if (dump.device !== s.identity.device) return say('That backup is from a module with a different device ID.', 'bad');
  const banks = [1, 2, 3, 4].map(n => Bank.fromMemory(dump.memory, n));
  if (confirm(`Restore all four trigger banks from ${file.name}?\n` +
      banks.map(b => `Bank ${b.number} “${b.name}”`).join('\n') +
      '\n\nThis replaces every trigger setting on the TD-12. A backup of the current read downloads first.')) {
    // compare against the restored bytes, not the file’s “original”
    banks.forEach(b => { b.original = Uint8Array.from(b.packed); });
    send(banks);
  }
};

// ---------- misc ----------

function say(text, kind = '') {
  $('status').textContent = text;
  $('status').className = kind;
}

if (SUPPORT_URL) { $('support-link').href = SUPPORT_URL; $('support').hidden = false; }

fetch(new URL('../td12_studio/trig_types.json', import.meta.url))
  .then(r => r.ok ? r.json() : null)
  .then(t => { s.templates = t; if (s.bank) refresh(); })
  .catch(() => {});
