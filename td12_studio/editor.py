"""GTK4 trigger editor: read a bank by dump, edit mapped fields, send.

Writes go only to the bank shown and only after a live read in this session.
A dump after a send is optional; when one arrives it is checked against the send.
"""
from datetime import datetime
from pathlib import Path
import json
import queue
import time
import gi
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk, GLib, Gio
import mido
from .midi import PollingInput, backend, choose_port, ports
from .protocol import inspect_dump
from .trigger import Bank, FIELDS, INPUTS, TRIG_TYPES, CURVES, XSTICK, bank_base

CAPTURES = Path.home() / '.local/share/td12-studio/captures'
TEMPLATES = Path(__file__).with_name('trig_types.json')
MANUAL = Path(__file__).resolve().parents[1] / 'web' / 'manual.html'
IDLE = 1.5          # silence that ends a dump
GAP_MS = 60         # between DT1 packets (manual: at least 40 ms)
FULL_DUMP = 60      # ALL TRIG BANKS = 4 x 15 packets; a 15-packet echo of our send is ignored

CSS = b'.changed { background: alpha(@accent_bg_color, 0.35); border-radius: 6px; }'


def load_templates():
    try:
        return {int(k): bytes(v) for k, v in json.loads(TEMPLATES.read_text()).items()}
    except (OSError, ValueError):
        return {}


def guard_scroll(widget):
    """The wheel changes a field only once it is selected; otherwise it scrolls the page."""
    def on_scroll(controller, dx, dy):
        if widget.get_state_flags() & Gtk.StateFlags.FOCUS_WITHIN:
            return False
        scroll = widget.get_ancestor(Gtk.ScrolledWindow)
        if scroll:
            wheel = controller.get_unit() == Gdk.ScrollUnit.WHEEL
            for adjustment, delta in ((scroll.get_hadjustment(), dx), (scroll.get_vadjustment(), dy)):
                step = adjustment.get_page_size() ** (2 / 3) if wheel else 1
                adjustment.set_value(adjustment.get_value() + delta * step)
        return True
    controller = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
    controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    controller.connect('scroll', on_scroll)
    widget.add_controller(controller)


def guard_fields(widget):
    if isinstance(widget, (Gtk.SpinButton, Gtk.DropDown)):
        return guard_scroll(widget)
    child = widget.get_first_child()
    while child:
        guard_fields(child)
        child = child.get_next_sibling()


def add_table(box, grid, titles):
    """Show a filled grid (rows from 1) under column titles that stay put while the rows scroll."""
    scroll = Gtk.ScrolledWindow(vexpand=True); scroll.set_child(grid)
    header = Gtk.Grid(column_spacing=grid.get_column_spacing())
    # Shares the sideways scrolling, so the titles follow the rows.
    header_scroll = Gtk.ScrolledWindow(hadjustment=scroll.get_hadjustment(),
                                       hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                       vscrollbar_policy=Gtk.PolicyType.NEVER)
    header_scroll.set_child(header)
    box.append(header_scroll)
    box.append(scroll)
    sizes = []
    for col, title in enumerate(titles):
        label = Gtk.Label(label=title, xalign=0); label.add_css_class('heading')
        header.attach(label, col, 0, 1, 1)
        sizes.append(Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL))
        sizes[col].add_widget(label)
    child = grid.get_first_child()
    while child:
        sizes[grid.query_child(child)[0]].add_widget(child)
        child = child.get_next_sibling()
    return header


class Editor(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='TD-12 Trigger Editor')
        self.set_default_size(1250, 720)
        self.memory = None          # last complete dump: address -> byte
        self.bank = None
        self.state = 'idle'         # idle | sending | verify
        self.sent = None
        self.frames, self.last_frame = [], 0.0
        self.events = queue.Queue()
        self.loading = False
        self.widgets = {}
        self.templates = load_templates()

        provider = Gtk.CssProvider(); provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), provider, 800)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(root, 'set_margin_' + side)(16)
        self.set_child(root)

        top = Gtk.Box(spacing=10); root.append(top)
        top.append(Gtk.Label(label='Bank'))
        self.bank_model = Gtk.StringList.new([f'Bank {n}' for n in range(1, 5)])
        self.bank_pick = Gtk.DropDown(model=self.bank_model, sensitive=False)
        self.bank_pick.connect('notify::selected', self.on_bank)
        top.append(self.bank_pick)
        top.append(Gtk.Label(label='XStick Thrshld'))
        self.xstick = Gtk.SpinButton.new_with_range(0, 127, 1)
        self.xstick.connect('value-changed', self.on_xstick)
        top.append(self.xstick)
        self.revert_button = Gtk.Button(label='Revert edits', sensitive=False)
        self.revert_button.connect('clicked', lambda *_: self.show_bank())
        top.append(self.revert_button)
        self.send_button = Gtk.Button(label='Send to TD-12', sensitive=False)
        self.send_button.add_css_class('suggested-action')
        self.send_button.connect('clicked', self.confirm_send)
        top.append(self.send_button)
        manual_button = Gtk.Button(label='Settings manual')
        manual_button.connect('clicked', lambda *_: Gio.AppInfo.launch_default_for_uri(MANUAL.as_uri(), None))
        top.append(manual_button)

        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.add_css_class('title-4')
        root.append(self.status)

        grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        for slot, name in enumerate(INPUTS):
            grid.attach(Gtk.Label(label=f'{slot + 1} {name}', xalign=0), 0, slot + 1, 1, 1)
            types = Gtk.DropDown(model=Gtk.StringList.new(TRIG_TYPES))
            types.connect('notify::selected', self.on_type, slot)
            grid.attach(types, 1, slot + 1, 1, 1)
            self.widgets[slot, 'type'] = types
            for col, field in enumerate(FIELDS, start=2):
                if field.key == 'curve':
                    w = Gtk.DropDown(model=Gtk.StringList.new(CURVES))
                    w.connect('notify::selected', self.on_field, slot, field)
                else:
                    step = 5 if field.key == 'xtalk' else 1 / field.scale
                    w = Gtk.SpinButton.new_with_range(field.display(field.lo), field.display(field.hi), step)
                    w.set_digits(1 if field.scale == 10 else 0)
                    w.set_snap_to_ticks(True)
                    w.connect('value-changed', self.on_field, slot, field)
                grid.attach(w, col, slot + 1, 1, 1)
                self.widgets[slot, field.key] = w
        self.header = add_table(root, grid, ['Input', 'Trig Type'] + [f.label for f in FIELDS])

        self.changes = Gtk.Label(xalign=0, wrap=True, selectable=True)
        root.append(self.changes)
        root.append(Gtk.Label(xalign=0, wrap=True, label=(
            'To read or verify: on the TD-12 press SETUP → F1 (MIDI) → F5 (BULK), choose ALL TRIG BANKS, '
            'press F5 (EXECUTE). Every dump is saved in ~/.local/share/td12-studio/captures.')))

        guard_fields(root)
        # GTK selects the first field on opening; start with none selected.
        self.connect('map', lambda *_: GLib.idle_add(self.set_focus, None))
        self.set_rows_sensitive(False)
        try:
            inputs, outputs = ports()
            self.in_name, self.out_name = choose_port(inputs), choose_port(outputs)
            self.input = PollingInput(self.in_name, self.events.put)
            self.say('Waiting for a dump from the TD-12 (ALL TRIG BANKS). Nothing can be sent before that.')
        except Exception as e:
            self.input = None
            self.say(f'MIDI problem: {e}. Connect the UM-ONE and restart the editor.')
        GLib.timeout_add(100, self.poll)
        self.connect('close-request', self.on_close)

    # ---------- display ----------

    def say(self, text):
        self.status.set_text(text)

    def set_rows_sensitive(self, on):
        for (slot, key), w in self.widgets.items():
            w.set_sensitive(on and (key != 'type' or bool(self.templates)))
        self.xstick.set_sensitive(on)

    def show_bank(self, *_):
        if self.memory is None:
            return
        number = self.bank_pick.get_selected() + 1
        self.bank = Bank.from_memory(self.memory, number)
        self.loading = True
        for slot in range(len(INPUTS)):
            self.widgets[slot, 'type'].set_selected(self.bank.trig_type(slot))
            for field in FIELDS:
                w, value = self.widgets[slot, field.key], self.bank.field(slot, field)
                if field.key == 'curve':
                    w.set_selected(value)
                else:
                    w.set_value(field.display(value))
        self.xstick.set_value(self.bank.get(XSTICK))
        self.loading = False
        self.refresh_changes()

    def refresh_changes(self):
        original = Bank(self.bank.number, self.bank.original)
        lines = []
        for slot, name in enumerate(INPUTS):
            a, b = original.trig_type(slot), self.bank.trig_type(slot)
            mark = a != b
            if mark:
                lines.append(f'{name} Trig Type {TRIG_TYPES[a]} → {TRIG_TYPES[b]} (loads that type’s defaults)')
            self.mark(self.widgets[slot, 'type'], mark)
            for field in FIELDS:
                a, b = original.field(slot, field), self.bank.field(slot, field)
                self.mark(self.widgets[slot, field.key], a != b)
                if a != b:
                    show = (lambda v: CURVES[v]) if field.key == 'curve' else (lambda v: f'{field.display(v):g}')
                    lines.append(f'{name} {field.label} {show(a)} → {show(b)}')
        if original.get(XSTICK) != self.bank.get(XSTICK):
            lines.append(f'XStick Thrshld {original.get(XSTICK)} → {self.bank.get(XSTICK)}')
        self.mark(self.xstick, original.get(XSTICK) != self.bank.get(XSTICK))
        self.changes.set_text('Unsent changes:\n' + '\n'.join(lines) if lines else 'No unsent changes.')
        busy = self.state != 'idle'
        self.send_button.set_sensitive(bool(lines) and not busy)
        self.send_button.set_label(f'Send bank {self.bank.number} to TD-12')
        self.revert_button.set_sensitive(bool(lines) and not busy)
        self.bank_pick.set_sensitive(not lines and not busy)

    @staticmethod
    def mark(widget, on):
        (widget.add_css_class if on else widget.remove_css_class)('changed')

    # ---------- edits ----------

    def on_bank(self, *_):
        self.show_bank()

    def on_field(self, widget, *args):
        slot, field = args[-2], args[-1]
        if self.loading or not self.bank:
            return
        value = widget.get_selected() if field.key == 'curve' else field.stored(widget.get_value())
        self.bank.set_field(slot, field, value)
        self.refresh_changes()

    def on_type(self, widget, _param, slot):
        if self.loading or not self.bank:
            return
        kind = widget.get_selected()
        if kind == self.bank.trig_type(slot):
            return
        template = self.templates.get(kind)
        if template is None:
            self.loading = True; widget.set_selected(self.bank.trig_type(slot)); self.loading = False
            self.say(f'No recorded defaults for {TRIG_TYPES[kind]} yet, so it cannot be set from the PC.')
            return
        self.bank.apply_type_template(slot, template)
        self.show_values_keep_edits()

    def show_values_keep_edits(self):
        self.loading = True
        for slot in range(len(INPUTS)):
            self.widgets[slot, 'type'].set_selected(self.bank.trig_type(slot))
            for field in FIELDS:
                w, value = self.widgets[slot, field.key], self.bank.field(slot, field)
                w.set_selected(value) if field.key == 'curve' else w.set_value(field.display(value))
        self.loading = False
        self.refresh_changes()

    def on_xstick(self, *_):
        if not self.loading and self.bank:
            self.bank.set(XSTICK, int(self.xstick.get_value()))
            self.refresh_changes()

    # ---------- send and verify ----------

    def confirm_send(self, *_):
        dialog = Gtk.AlertDialog(message=f'Send bank {self.bank.number} “{self.bank.name}” to the TD-12?',
                                 detail=self.changes.get_text(),
                                 buttons=['Cancel', 'Send'], cancel_button=0, default_button=1)
        dialog.choose(self, None, self.on_confirm)

    def on_confirm(self, dialog, result):
        try:
            if dialog.choose_finish(result) != 1:
                return
        except GLib.Error:
            return
        self.sent = self.bank
        packets = self.bank.packets()
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        CAPTURES.mkdir(parents=True, exist_ok=True)
        (CAPTURES / f'{stamp}-editor-sent-bank{self.bank.number}.syx').write_bytes(b''.join(packets))
        self.state = 'sending'
        self.refresh_changes(); self.set_rows_sensitive(False)
        try:
            self.output = backend().open_output(self.out_name)
        except Exception as e:
            return self.fail(f'Could not open MIDI output: {e}')
        self.queue_packets(packets)

    def queue_packets(self, packets):
        def step():
            try:
                self.output.send(mido.Message.from_bytes(packets.pop(0)))
            except Exception as e:
                self.fail(f'Sending stopped: {e}. Bank {self.sent.number} may be partly written — send again.')
                return False
            self.say(f'Sending… {15 - len(packets)}/15')
            if packets:
                return True
            self.output.close()
            self.state = 'idle'
            # What was sent is the new starting point for further edits.
            base = bank_base(self.sent.number)
            self.memory.update({base + i: value for i, value in enumerate(self.sent.packed)})
            self.show_bank()
            self.set_rows_sensitive(True)
            self.say(f'Sent bank {self.sent.number} at {datetime.now():%H:%M:%S}. Keep editing.')
            return False
        GLib.timeout_add(GAP_MS, step)

    def fail(self, text):
        self.state = 'idle'
        self.say(text)
        self.set_rows_sensitive(self.memory is not None)
        if self.bank:
            self.refresh_changes()

    # ---------- receiving ----------

    def poll(self):
        while True:
            try:
                message = self.events.get_nowait()
            except queue.Empty:
                break
            if message.type == 'sysex':
                self.frames.append(bytes(message.bytes()))
                self.last_frame = time.monotonic()
        if self.frames and time.monotonic() - self.last_frame >= IDLE:
            frames, self.frames = self.frames, []
            self.on_dump(frames)
        return True

    def on_dump(self, frames):
        data = b''.join(frames)
        report, memory = inspect_dump(data)
        banks = [n for n in range(1, 5) if f'Trigger bank {n}' in report['blocks']]
        if report['valid_td12_packets'] < FULL_DUMP or report['errors'] or len(banks) < 4:
            # Multiples of 15 are the module echoing our own sends back (MIDI soft thru).
            if self.state != 'sending' and report['valid_td12_packets'] % 15:
                self.say(f'Ignored incomplete data ({report["valid_td12_packets"]} packets). '
                         'Dump ALL TRIG BANKS (not ALL, not one bank).')
            return
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        CAPTURES.mkdir(parents=True, exist_ok=True)
        (CAPTURES / f'{stamp}-editor-read.syx').write_bytes(data)
        note = ''
        if self.sent:
            same = Bank.from_memory(memory, self.sent.number).original == bytes(self.sent.packed)
            note = (f' ✓ Bank {self.sent.number} matches the last send.' if same else
                    f' ✗ Bank {self.sent.number} does NOT match the last send; values shown are from the module.')
            self.sent = None
        if self.bank and self.bank.changes():
            self.say('A dump arrived, but you have unsent edits. Click "Revert edits" to load the new dump.' + note)
            self.memory = memory
            return
        self.memory = memory
        self.loading = True
        for n in range(4):
            self.bank_model.splice(n, 1, [f'Bank {n + 1}  “{Bank.from_memory(memory, n + 1).name}”'])
        self.loading = False
        self.bank_pick.set_sensitive(True)
        self.set_rows_sensitive(True)
        self.show_bank()
        self.say(f'Read from TD-12 at {datetime.now():%H:%M:%S}. Edit, then send.' + note)

    def on_close(self, *_):
        if self.input:
            self.input.close()
        return False


def run():
    app = Gtk.Application(application_id='org.td12studio.TriggerEditor')
    app.connect('activate', lambda a: (a.get_active_window() or Editor(a)).present())
    return app.run(None)
