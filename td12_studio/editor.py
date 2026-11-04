"""GTK4 trigger editor: read a bank by dump, edit mapped fields, send.

Writes go only to the bank shown, after a live read or after accepting the warning
about last known settings. A dump after a send is optional; when one arrives it is
checked against the send.
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
from .kit import HEAD, HIHAT_NOTES, KITS, RIM, Kit, kit_base, KIT_PACKED
from .trigger import Bank, FIELDS, INPUTS, TRIG_TYPES, CURVES, THREE_WAY, XSTICK, bank_base

CAPTURES = Path.home() / '.local/share/td12-studio/captures'
SETTINGS = Path.home() / '.local/share/td12-studio/settings.json'
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


def load_settings():
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    settings = load_settings()
    settings[key] = value
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(settings, indent=1))
    except OSError:
        pass        # the choice just won't survive a restart


EDGE_SLOT = INPUTS.index('EDGE')


class Table:
    """A filled grid (rows from 1) with titles that stay above the rows and left columns
    that stay beside them. The other parts scroll together."""

    def __init__(self, box, grid, titles, pin=1):
        spacing = grid.get_column_spacing()
        left = Gtk.Grid(column_spacing=spacing, row_spacing=grid.get_row_spacing())
        self.heads, self.rows = {}, {}
        cells, child = [], grid.get_first_child()
        while child:
            cells.append((child, *grid.query_child(child)[:2]))
            child = child.get_next_sibling()
        column_sizes = [Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL) for _ in titles]
        row_sizes = {}
        for widget, col, row in cells:
            if col < pin:
                grid.remove(widget)
                left.attach(widget, col, row, 1, 1)
            column_sizes[col].add_widget(widget)
            row_sizes.setdefault(row, Gtk.SizeGroup(mode=Gtk.SizeGroupMode.VERTICAL)).add_widget(widget)
            self.rows.setdefault(row - 1, []).append(widget)
        head_left, head_right = Gtk.Grid(column_spacing=spacing), Gtk.Grid(column_spacing=spacing)
        for col, title in enumerate(titles):
            label = Gtk.Label(label=title, xalign=0); label.add_css_class('heading')
            (head_left if col < pin else head_right).attach(label, col, 0, 1, 1)
            column_sizes[col].add_widget(label)
            self.heads[col] = label
        self.scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True); self.scroll.set_child(grid)
        head_scroll = Gtk.ScrolledWindow(hadjustment=self.scroll.get_hadjustment(), hexpand=True,
                                         hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                         vscrollbar_policy=Gtk.PolicyType.NEVER)
        head_scroll.set_child(head_right)
        left_scroll = Gtk.ScrolledWindow(vadjustment=self.scroll.get_vadjustment(), vexpand=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        left_scroll.set_child(left)
        outer = Gtk.Grid(column_spacing=spacing, vexpand=True)
        outer.attach(head_left, 0, 0, 1, 1)
        outer.attach(head_scroll, 1, 0, 1, 1)
        outer.attach(left_scroll, 0, 1, 1, 1)
        outer.attach(self.scroll, 1, 1, 1, 1)
        box.append(outer)

    def set_row_visible(self, row, visible):
        for widget in self.rows[row]:
            widget.set_visible(visible)


def pads_menu(table, names, key):
    """A button listing the table's rows; unticked rows are hidden and stay hidden next time.
    Hiding only changes what is shown: the module's settings for that row stay as they are."""
    hidden = set(load_settings().get(key, []))
    choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    def toggled(check, row):
        (hidden.discard if check.get_active() else hidden.add)(row)
        table.set_row_visible(row, check.get_active())
        save_setting(key, sorted(hidden))

    for row, name in enumerate(names):
        check = Gtk.CheckButton(label=name, active=row not in hidden)
        check.connect('toggled', toggled, row)
        choices.append(check)
        table.set_row_visible(row, row not in hidden)
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                  propagate_natural_width=True, max_content_height=500)
    scroller.set_child(choices)
    popover = Gtk.Popover(); popover.set_child(scroller)
    return Gtk.MenuButton(label='Show or hide pads', popover=popover)


def last_state():
    """What the module last held according to saved reads and sends, oldest first so later ones win."""
    memory, when = {}, None
    for path in sorted(CAPTURES.glob('*.syx')):
        if not any(mark in path.name for mark in ('editor-read', 'editor-sent', 'all-drum-kits')):
            continue
        report, found = inspect_dump(path.read_bytes())
        if not report['errors']:
            memory.update(found)
            when = path.name[:15]
    return memory, when


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
        self.note_widgets = {}
        self.hihat_widgets = {}
        self.after_last_state = []  # called when last known settings are accepted
        self.kit_memory = None     # last dump holding drum kits: address -> byte
        self.kit = None
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
        self.three_way = Gtk.CheckButton(label='3-way ride')
        self.three_way.connect('toggled', self.on_three_way)
        top.append(self.three_way)
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
        self.open_last = Gtk.CheckButton(label='Offer last settings on opening',
                                         active=load_settings().get('open_last', True))
        self.open_last.connect('toggled', lambda b: save_setting('open_last', b.get_active()))
        top.append(self.open_last)

        kit_bar = Gtk.Box(spacing=10); root.append(kit_bar)
        kit_bar.append(Gtk.Label(label='Notes for'))
        self.kit_model = Gtk.StringList.new([f'Kit {n}' for n in range(1, KITS + 1)])
        self.kit_pick = Gtk.DropDown(model=self.kit_model, sensitive=False)
        self.kit_pick.connect('notify::selected', lambda *_: self.loading or self.show_kit())
        kit_bar.append(self.kit_pick)
        self.kit_revert_button = Gtk.Button(label='Revert notes', sensitive=False)
        self.kit_revert_button.connect('clicked', lambda *_: self.show_kit())
        kit_bar.append(self.kit_revert_button)
        self.kit_send_button = Gtk.Button(label='Send notes to TD-12', sensitive=False)
        self.kit_send_button.add_css_class('suggested-action')
        self.kit_send_button.connect('clicked', self.confirm_kit_send)
        kit_bar.append(self.kit_send_button)
        self.kit_changes = Gtk.Label(xalign=0, wrap=True, selectable=True,
                                     label='To edit notes: dump ALL DRUM KITS or 1 DRUM KIT from the module.')
        kit_bar.append(self.kit_changes)
        hihat_bar = Gtk.Box(spacing=10); root.append(hihat_bar)
        for title, offset in HIHAT_NOTES:
            hihat_bar.append(Gtk.Label(label=title))
            note = Gtk.SpinButton.new_with_range(0, 127, 1)
            note.set_sensitive(False)
            note.connect('value-changed', self.on_hihat_note, offset)
            hihat_bar.append(note)
            self.hihat_widgets[offset] = note

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
            for col, hit in ((2, HEAD), (3, RIM)):
                note = Gtk.SpinButton.new_with_range(0, 127, 1)
                note.set_sensitive(False)
                note.connect('value-changed', self.on_note, slot, hit)
                grid.attach(note, col, slot + 1, 1, 1)
                self.note_widgets[slot, hit] = note
            for col, field in enumerate(FIELDS, start=4):
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
        self.table = Table(root, grid, ['Input', 'Trig Type', 'Head note', 'Rim note'] + [f.label for f in FIELDS])
        top.append(pads_menu(self.table, INPUTS, 'hidden_td12'))

        self.changes = Gtk.Label(xalign=0, wrap=True, selectable=True)
        root.append(self.changes)
        root.append(Gtk.Label(xalign=0, wrap=True, label=(
            'To read or verify: on the TD-12 press SETUP → F1 (MIDI) → F5 (BULK), choose ALL TRIG BANKS, '
            'press F5 (EXECUTE). Every dump is saved in ~/.local/share/td12-studio/captures.')))

        guard_fields(root)
        # GTK selects the first field on opening; start with none selected.
        self.connect('map', lambda *_: GLib.idle_add(self.set_focus, None))
        self.connect('map', lambda *_: GLib.idle_add(self.offer_last_state))
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
        self.three_way.set_sensitive(on)
        self.lock_edge_type()

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
        self.three_way.set_active(self.bank.get(THREE_WAY) == 1)
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
        if original.get(THREE_WAY) != self.bank.get(THREE_WAY):
            lines.append(f'3-way ride {"on" if self.bank.get(THREE_WAY) else "off"}')
        self.mark(self.three_way, original.get(THREE_WAY) != self.bank.get(THREE_WAY))
        self.lock_edge_type()
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

    def on_three_way(self, button):
        if not self.loading and self.bank:
            self.bank.set(THREE_WAY, 1 if button.get_active() else 0)
            self.refresh_changes()

    def lock_edge_type(self):
        # With 3-way on the module shows RD CTRL for EDGE and doesn't allow another type.
        if self.bank and self.bank.get(THREE_WAY) == 1:
            self.widgets[EDGE_SLOT, 'type'].set_sensitive(False)
        elif self.bank and self.state == 'idle' and self.templates:
            self.widgets[EDGE_SLOT, 'type'].set_sensitive(True)

    def on_xstick(self, *_):
        if not self.loading and self.bank:
            self.bank.set(XSTICK, int(self.xstick.get_value()))
            self.refresh_changes()

    # ---------- drum kit notes ----------

    def show_kit(self):
        if self.kit_memory is None or self.state != 'idle':
            return
        number = self.kit_pick.get_selected() + 1
        try:
            self.kit = Kit.from_memory(self.kit_memory, number)
        except ValueError:
            self.kit = None
        self.loading = True
        for (slot, hit), w in self.note_widgets.items():
            w.set_sensitive(self.kit is not None)
            if self.kit:
                w.set_value(self.kit.note(slot, hit))
        for offset, w in self.hihat_widgets.items():
            w.set_sensitive(self.kit is not None)
            if self.kit:
                w.set_value(self.kit.get(offset))
        self.loading = False
        self.refresh_kit()

    def on_hihat_note(self, widget, offset):
        if not self.loading and self.kit:
            self.kit.set(offset, int(widget.get_value()))
            self.refresh_kit()

    def on_note(self, widget, slot, hit):
        if not self.loading and self.kit:
            self.kit.set_note(slot, hit, int(widget.get_value()))
            self.refresh_kit()

    def refresh_kit(self):
        lines = []
        if self.kit:
            original = Kit(self.kit.number, self.kit.original)
            for (slot, hit), w in self.note_widgets.items():
                a, b = original.note(slot, hit), self.kit.note(slot, hit)
                self.mark(w, a != b)
                if a != b:
                    lines.append(f'{INPUTS[slot]} {"rim" if hit else "head"} note {a} → {b}')
            for title, offset in HIHAT_NOTES:
                a, b = original.get(offset), self.kit.get(offset)
                self.mark(self.hihat_widgets[offset], a != b)
                if a != b:
                    lines.append(f'{title} {a} → {b}')
        if self.kit:
            self.kit_changes.set_text('Unsent note changes: ' + ', '.join(lines) if lines else f'Kit {self.kit.number} “{self.kit.name}”.')
        elif self.kit_memory is not None:
            self.kit_changes.set_text('This kit wasn’t in the last dump. Dump it, or ALL DRUM KITS.')
        busy = self.state != 'idle'
        self.kit_send_button.set_sensitive(bool(lines) and not busy)
        self.kit_revert_button.set_sensitive(bool(lines) and not busy)
        self.kit_pick.set_sensitive(self.kit_memory is not None and not lines and not busy)

    def confirm_kit_send(self, *_):
        dialog = Gtk.AlertDialog(message=f'Send Kit {self.kit.number} “{self.kit.name}” to the TD-12?',
                                 detail=self.kit_changes.get_text() + '\nOnly the notes change. Select the kit again on the module if the new notes don’t apply.',
                                 buttons=['Cancel', 'Send'], cancel_button=0, default_button=1)
        dialog.choose(self, None, self.on_kit_confirm)

    def on_kit_confirm(self, dialog, result):
        try:
            if dialog.choose_finish(result) != 1:
                return
        except GLib.Error:
            return
        kit, packets = self.kit, self.kit.packets()
        total = len(packets)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        CAPTURES.mkdir(parents=True, exist_ok=True)
        (CAPTURES / f'{stamp}-editor-sent-kit{kit.number}.syx').write_bytes(b''.join(packets))
        self.state = 'sending'
        self.refresh_kit(); self.refresh_bank_buttons()
        try:
            output = backend().open_output(self.out_name)
        except Exception as e:
            return self.fail(f'Could not open MIDI output: {e}')

        def step():
            try:
                output.send(mido.Message.from_bytes(packets.pop(0)))
            except Exception as e:
                output.close()
                self.fail(f'Sending stopped: {e}. Kit {kit.number} may be partly written — send again.')
                self.refresh_kit()
                return False
            self.say(f'Sending… {total - len(packets)}/{total}')
            if packets:
                return True
            output.close()
            self.state = 'idle'
            base = kit_base(kit.number)
            self.kit_memory.update({base + i: value for i, value in enumerate(kit.packed)})
            self.show_kit(); self.refresh_bank_buttons()
            self.say(f'Sent Kit {kit.number} notes at {datetime.now():%H:%M:%S}. Keep editing.')
            return False
        GLib.timeout_add(GAP_MS, step)

    def refresh_bank_buttons(self):
        if self.bank:
            self.refresh_changes()

    def on_kit_dump(self, data, memory):
        """Keep kits from a dump. Our own sends echo back and change nothing here."""
        known = self.kit_memory or {}
        if all(known.get(address) == value for address, value in memory.items()):
            return
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        CAPTURES.mkdir(parents=True, exist_ok=True)
        (CAPTURES / f'{stamp}-editor-read-kits.syx').write_bytes(data)
        pending = self.kit and self.kit.changes()
        self.set_kits({**known, **memory})
        if pending:
            return self.say('Kits arrived, but you have unsent note edits. Click "Revert notes" to load them.')
        self.show_kit()
        self.say(f'Read kits from the TD-12 at {datetime.now():%H:%M:%S}.')

    def set_kits(self, memory):
        self.kit_memory = memory
        selected = self.kit_pick.get_selected()
        self.loading = True
        for number in range(1, KITS + 1):
            try:
                self.kit_model.splice(number - 1, 1, [f'Kit {number}  “{Kit.from_memory(self.kit_memory, number).name}”'])
            except ValueError:
                pass
        self.kit_pick.set_selected(selected)
        self.loading = False

    # ---------- last known settings ----------

    def offer_last_state(self):
        if not self.open_last.get_active() or self.memory is not None:
            return False
        memory, when = last_state()
        try:
            for number in range(1, 5):
                Bank.from_memory(memory, number)
        except ValueError:
            return False
        shown = f'{when[:4]}-{when[4:6]}-{when[6:8]} {when[9:11]}:{when[11:13]}'
        dialog = Gtk.AlertDialog(
            message='Open with the last known settings?',
            detail=f'These are from {shown}. They were not read from the module just now. If anything '
                   'changed on the module since then, they are out of date, and sending replaces the '
                   'whole bank or kit with what is shown here.\n\nCancel waits for a dump instead.',
            buttons=['Cancel', 'OK'], cancel_button=0, default_button=1)
        dialog.choose(self, None, self.on_last_state_answer, memory, shown)
        return False

    def on_last_state_answer(self, dialog, result, memory, shown):
        try:
            if dialog.choose_finish(result) != 1:
                return
        except GLib.Error:
            return
        if self.memory is not None:
            return      # a real dump arrived while the question was open
        self.load_memory({a: v for a, v in memory.items() if a < kit_base(1)})
        kits = {a: v for a, v in memory.items() if a >= kit_base(1)}
        if kits and self.kit_memory is None:
            self.set_kits(kits)
            self.show_kit()
        for callback in self.after_last_state:
            callback()
        self.say(f'Showing last known settings from {shown}, not read from the module. Dump to check them.')

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
        kits = {a: v for a, v in memory.items() if kit_base(1) <= a <= kit_base(KITS) + KIT_PACKED - 1}
        if kits and not report['errors']:
            self.on_kit_dump(data, kits)
            if not any(name.startswith('Trigger bank') for name in report['blocks']):
                return
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
        self.load_memory(memory)
        self.say(f'Read from TD-12 at {datetime.now():%H:%M:%S}. Edit, then send.' + note)

    def load_memory(self, memory):
        self.memory = memory
        self.loading = True
        for n in range(4):
            self.bank_model.splice(n, 1, [f'Bank {n + 1}  “{Bank.from_memory(memory, n + 1).name}”'])
        self.loading = False
        self.bank_pick.set_sensitive(True)
        self.set_rows_sensitive(True)
        self.show_bank()

    def on_close(self, *_):
        if self.input:
            self.input.close()
        return False


def run():
    app = Gtk.Application(application_id='org.td12studio.TriggerEditor')
    app.connect('activate', lambda a: (a.get_active_window() or Editor(a)).present())
    return app.run(None)
