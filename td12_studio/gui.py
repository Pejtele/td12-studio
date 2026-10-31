"""GTK4 desktop front end. Hardware operations remain read-only."""
from pathlib import Path
import json
import queue
import time
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gio
from .midi import Connection, ports
from .protocol import parse_packet, inspect_dump, compare_dumps

CAPTURES = Path.home() / '.local/share/td12-studio/captures'


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='TD-12 Studio — connection and backup research')
        self.set_default_size(940, 760)
        self.conn = None
        self.events = queue.Queue()
        self.identify_deadline = None
        self.frames = self.data_bytes = self.invalid = 0
        self.capture_active = False
        self.dialogs = []
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for name in ('top', 'bottom', 'start', 'end'): getattr(root, 'set_margin_'+name)(20)
        outer = Gtk.ScrolledWindow()
        outer.set_child(root)
        self.set_child(outer)
        title = Gtk.Label(label='TD-12 Studio', xalign=0)
        title.add_css_class('title-1'); root.append(title)
        root.append(self.label('Read-only prototype • Identify your module, capture backups, compare settings.'))
        root.append(self.label('This version cannot change module settings or replace the LCD yet.'))
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        root.append(grid)
        self.input_model = Gtk.StringList.new([]); self.output_model = Gtk.StringList.new([])
        self.input = Gtk.DropDown(model=self.input_model); self.input.set_hexpand(True)
        self.output = Gtk.DropDown(model=self.output_model); self.output.set_hexpand(True)
        grid.attach(self.label('MIDI input (from TD-12)'), 0, 0, 1, 1); grid.attach(self.input, 1, 0, 1, 1)
        grid.attach(self.label('MIDI output (to TD-12)'), 0, 1, 1, 1); grid.attach(self.output, 1, 1, 1, 1)
        row = Gtk.Box(spacing=8); root.append(row)
        self.refresh_button = self.button(row, 'Refresh ports', self.refresh)
        self.connect_button = self.button(row, 'Connect', self.toggle_connect)
        self.identify_button = self.button(row, 'Identify module', self.identify)
        self.identify_button.set_sensitive(False)
        self.status = self.label('Choose the UM-ONE ports, then Connect.'); root.append(self.status)
        self.identity = self.label('Module identity: not queried'); root.append(self.identity)
        self.notes = self.label('Last pad hit: waiting'); root.append(self.notes)
        root.append(Gtk.Separator())
        self.capture_label = Gtk.Entry(placeholder_text='Capture name, e.g. trigger-bank-before')
        root.append(self.capture_label)
        row = Gtk.Box(spacing=8); root.append(row)
        self.start_button = self.button(row, 'Start capture', self.start_capture); self.start_button.set_sensitive(False)
        self.stop_button = self.button(row, 'Stop and inspect', self.stop_capture); self.stop_button.set_sensitive(False)
        self.button(row, 'Open captures folder', self.open_folder)
        root.append(self.label('Capture procedure: SETUP → F1 MIDI → F5 BULK → choose data → F5 EXECUTE.\nStart capture here first. Stop after the module finishes sending.'))
        self.progress = self.label('No capture running.'); root.append(self.progress)
        row = Gtk.Box(spacing=8); root.append(row)
        self.button(row, 'Inspect .syx file…', lambda *_: self.pick_file(self.inspect_file))
        self.button(row, 'Compare two captures…', self.pick_before)
        self.buffer = Gtk.TextBuffer()
        text = Gtk.TextView(buffer=self.buffer, editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow(vexpand=True); scroll.set_min_content_height(180); scroll.set_child(text); root.append(scroll)
        self.report('The editor sends only the standard identity query when you click Identify.\nA capture listens only; it does not request a bulk dump.\nIndividual parameter offsets still need to be established from real dumps.')
        self.refresh()
        GLib.timeout_add(100, self.poll)
        self.connect('close-request', self.on_close)

    @staticmethod
    def label(text):
        w = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
        return w

    @staticmethod
    def button(parent, text, callback):
        b = Gtk.Button(label=text); b.connect('clicked', callback); parent.append(b); return b

    def report(self, text):
        self.buffer.set_text(text)

    def refresh(self, *_):
        try:
            inputs, outputs = ports()
            for model, dropdown, names in [(self.input_model,self.input,inputs), (self.output_model,self.output,outputs)]:
                model.splice(0, model.get_n_items(), names)
                choice = next((i for i,n in enumerate(names) if 'UM-ONE' in n), 0)
                dropdown.set_selected(choice)
            self.status.set_text('Ports refreshed. Select UM-ONE for the TD-12.')
        except Exception as e:
            self.status.set_text(str(e))

    @staticmethod
    def selected(model, dropdown):
        value = model.get_string(dropdown.get_selected())
        if not value: raise ValueError('No MIDI port selected')
        return value

    def toggle_connect(self, *_):
        try:
            if self.conn:
                if self.capture_active: self.stop_capture()
                self.conn.close(); self.conn = None
                self.connect_button.set_label('Connect'); self.status.set_text('Disconnected.')
            else:
                name = self.selected(self.input_model, self.input)
                self.conn = Connection(name, self.events.put)
                self.connect_button.set_label('Disconnect'); self.status.set_text('Listening to ' + name)
                self.identity.set_text('Module identity: not queried')
            connected = self.conn is not None
            self.identify_deadline = None
            for w in [self.input,self.output,self.refresh_button]: w.set_sensitive(not connected)
            self.identify_button.set_sensitive(connected); self.start_button.set_sensitive(connected)
        except Exception as e:
            self.status.set_text(str(e))

    def identify(self, *_):
        try:
            self.conn.identify(self.selected(self.output_model,self.output))
            self.identity.set_text('Waiting for identity reply…')
            self.identify_deadline = time.monotonic() + 4
        except Exception as e: self.status.set_text(str(e))

    def start_capture(self, *_):
        try:
            path = self.conn.start_capture(CAPTURES, self.capture_label.get_text() or 'capture')
            self.frames = self.data_bytes = self.invalid = 0; self.capture_active = True
            self.start_button.set_sensitive(False); self.stop_button.set_sensitive(True)
            self.progress.set_text('Listening — now initiate the module’s bulk dump.')
            self.report('Capturing to:\n' + str(path))
        except Exception as e: self.status.set_text(str(e))

    def stop_capture(self, *_):
        try:
            result = self.conn.stop_capture(); self.capture_active = False
            self.start_button.set_sensitive(True); self.stop_button.set_sensitive(False)
            if result:
                path, report = result
                self.progress.set_text(f'Captured {report["valid_td12_packets"]} valid TD-12 packets. Full-backup completeness is not verified.')
                self.report(str(path) + '\n\n' + json.dumps(report, indent=2))
        except Exception as e: self.status.set_text(str(e))

    def poll(self):
        latest_note = None
        # Bound processing per tick so a large dump cannot freeze the UI.
        for _ in range(500):
            try: message = self.events.get_nowait()
            except queue.Empty: break
            if message.type == 'note_on' and message.velocity:
                latest_note = f'Last pad hit: channel {message.channel+1}, note {message.note}, velocity {message.velocity}'
            elif message.type == 'sysex':
                try:
                    packet = parse_packet(message.bytes())
                    if packet.kind.endswith('identity'):
                        self.identity.set_text(packet.description); self.identify_deadline = None
                    if self.capture_active:
                        self.frames += 1
                        if packet.kind == 'td12_data': self.data_bytes += len(packet.data)
                except ValueError as e:
                    if self.capture_active: self.invalid += 1
                    self.status.set_text(str(e))
        if latest_note: self.notes.set_text(latest_note)
        if self.capture_active:
            self.progress.set_text(f'Receiving: {self.frames} SysEx frames • {self.data_bytes} TD-12 data bytes • {self.invalid} invalid frames')
            if self.conn.error: self.status.set_text('Capture error: ' + self.conn.error)
        if self.identify_deadline and time.monotonic() >= self.identify_deadline:
            self.identity.set_text('No identity reply. Check TD-12 power and BOTH DIN connections. Pad notes can still work with only MIDI OUT connected.')
            self.identify_deadline = None
        return True

    def open_folder(self, *_):
        CAPTURES.mkdir(parents=True, exist_ok=True)
        Gio.AppInfo.launch_default_for_uri(CAPTURES.as_uri(), None)

    def pick_file(self, callback):
        dialog = Gtk.FileChooserNative(title='Choose a raw SysEx (.syx) capture', transient_for=self,
                                       action=Gtk.FileChooserAction.OPEN, accept_label='Open', cancel_label='Cancel')
        f = Gtk.FileFilter(); f.set_name('SysEx captures'); f.add_pattern('*.syx'); dialog.add_filter(f)
        self.dialogs.append(dialog)
        def response(d, result):
            try:
                if result == Gtk.ResponseType.ACCEPT:
                    callback(Path(d.get_file().get_path()))
            except Exception as e: self.report(str(e))
            finally: d.destroy(); self.dialogs.remove(d)
        dialog.connect('response', response); dialog.show()

    def inspect_file(self, path):
        report, _ = inspect_dump(path.read_bytes()); self.report(str(path) + '\n\n' + json.dumps(report, indent=2))

    def pick_before(self, *_):
        def before(path):
            self.pick_file(lambda after: self.report(json.dumps(compare_dumps(path.read_bytes(), after.read_bytes()), indent=2)))
        self.pick_file(before)

    def on_close(self, *_):
        if self.conn: self.conn.close()
        return False


def run():
    app = Gtk.Application(application_id='org.td12studio.Research')
    def activate(a):
        window = a.get_active_window() or Window(a); window.present()
    app.connect('activate', activate)
    return app.run(None)
