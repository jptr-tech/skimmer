import gi
gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk, GLib


class SettingsPage(Gtk.Box):
    def __init__(self, config, on_save, scanner=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.config = config
        self.on_save_cb = on_save
        self._scanner = scanner
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)

        lbl = Gtk.Label(label="Settings", css_classes=["title"])
        lbl.set_halign(Gtk.Align.START)
        self.append(lbl)

        frame = Gtk.Frame(label="Paths")
        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(6)
        grid.set_margin_start(12)
        grid.set_margin_end(12)
        grid.set_margin_top(12)
        grid.set_margin_bottom(12)

        self.entries = {}
        row = 0
        for key, label_text, is_path in [
            ("music_dir", "Music Library", True),
            ("beets_lib", "Beets Database", True),
            ("temp_dir", "Temp Download Dir", True),
            ("mount_path", "Mount Path", True),
        ]:
            lbl_w = Gtk.Label(label=label_text, halign=Gtk.Align.START)
            grid.attach(lbl_w, 0, row, 1, 1)

            entry = Gtk.Entry()
            entry.set_text(self.config.get(key, ""))
            entry.set_hexpand(True)
            grid.attach(entry, 1, row, 1, 1)
            self.entries[key] = entry

            if is_path:
                browse_btn = Gtk.Button(label="Browse...")
                browse_btn.connect("clicked", self._on_browse, entry)
                grid.attach(browse_btn, 2, row, 1, 1)
            row += 1

        lbl_w = Gtk.Label(label="YT-DLP Format", halign=Gtk.Align.START)
        grid.attach(lbl_w, 0, row, 1, 1)

        self.format_combo = Gtk.ComboBoxText()
        formats = [
            "bestaudio/best",
            "bestaudio[ext=m4a]",
            "bestaudio[ext=webm]",
            "worstaudio",
        ]
        for f in formats:
            self.format_combo.append_text(f)
        current = self.config.get("ytdlp_format", "bestaudio/best")
        if current in formats:
            self.format_combo.set_active(formats.index(current))
        else:
            self.format_combo.set_active(0)
        grid.attach(self.format_combo, 1, row, 2, 1)
        self.entries["ytdlp_format"] = self.format_combo

        row += 1
        lbl_w = Gtk.Label(label="Audio Format", halign=Gtk.Align.START)
        grid.attach(lbl_w, 0, row, 1, 1)

        self.audio_combo = Gtk.ComboBoxText()
        for fmt in ["mp3", "m4a", "opus", "flac", "wav"]:
            self.audio_combo.append_text(fmt)
        current_audio = self.config.get("ytdlp_audio_format", "mp3")
        audio_formats = ["mp3", "m4a", "opus", "flac", "wav"]
        if current_audio in audio_formats:
            self.audio_combo.set_active(audio_formats.index(current_audio))
        else:
            self.audio_combo.set_active(0)
        grid.attach(self.audio_combo, 1, row, 2, 1)
        self.entries["ytdlp_audio_format"] = self.audio_combo

        frame.set_child(grid)
        self.append(frame)

        # Scanner section
        scan_frame = Gtk.Frame(label="Scanner")
        scan_grid = Gtk.Grid()
        scan_grid.set_column_spacing(12)
        scan_grid.set_row_spacing(6)
        scan_grid.set_margin_start(12)
        scan_grid.set_margin_end(12)
        scan_grid.set_margin_top(12)
        scan_grid.set_margin_bottom(12)

        srow = 0
        interval_lbl = Gtk.Label(label="Re-scan interval (minutes)", halign=Gtk.Align.START)
        scan_grid.attach(interval_lbl, 0, srow, 1, 1)

        self.scan_interval_btn = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(
                value=self.config.get("scan_interval", 600) / 60,
                lower=1, upper=1440, step_increment=1,
            ),
            climb_rate=1, digits=0,
        )
        self.scan_interval_btn.set_hexpand(True)
        scan_grid.attach(self.scan_interval_btn, 1, srow, 1, 1)
        self.entries["scan_interval"] = self.scan_interval_btn

        self.scan_now_btn = Gtk.Button(label="Scan Now")
        self.scan_now_btn.add_css_class("flat")
        self.scan_now_btn.connect("clicked", self._on_scan_now)
        scan_grid.attach(self.scan_now_btn, 2, srow, 1, 1)

        srow += 1
        self.scan_status_lbl = Gtk.Label(label="Status: Idle")
        self.scan_status_lbl.set_halign(Gtk.Align.START)
        scan_grid.attach(self.scan_status_lbl, 0, srow, 3, 1)

        srow += 1
        self.scan_progress = Gtk.ProgressBar()
        self.scan_progress.set_fraction(0.0)
        self.scan_progress.set_visible(False)
        scan_grid.attach(self.scan_progress, 0, srow, 3, 1)

        scan_frame.set_child(scan_grid)
        self.append(scan_frame)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_halign(Gtk.Align.END)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._save)
        btn_box.append(save_btn)

        self.append(btn_box)

        if self._scanner:
            self._scanner.set_callbacks(
                on_status=self._on_scanner_status,
                on_progress=self._on_scanner_progress,
                on_complete=self._on_scanner_complete,
            )

    def set_scanner(self, scanner):
        self._scanner = scanner
        self._scanner.set_callbacks(
            on_status=self._on_scanner_status,
            on_progress=self._on_scanner_progress,
            on_complete=self._on_scanner_complete,
        )

    def _on_scanner_status(self, msg):
        GLib.idle_add(self.scan_status_lbl.set_text, f"Status: {msg}")

    def _on_scanner_progress(self, current, total):
        def update():
            frac = current / total if total > 0 else 0.0
            self.scan_progress.set_fraction(frac)
            self.scan_progress.set_visible(frac < 1.0)
            self.scan_status_lbl.set_text(f"Scanning... {current}/{total}")
        GLib.idle_add(update)

    def _on_scanner_complete(self, changed):
        def update():
            self.scan_progress.set_fraction(1.0)
            self.scan_progress.set_visible(False)
            if changed > 0:
                self.scan_status_lbl.set_text(f"Status: Idle ({changed} changes)")
            else:
                self.scan_status_lbl.set_text("Status: Idle")
        GLib.idle_add(update)

    def _on_scan_now(self, btn):
        if self._scanner:
            self.scan_status_lbl.set_text("Status: Starting scan...")
            self.scan_now_btn.set_sensitive(False)
            self._scanner.scan_now()
            GLib.timeout_add_seconds(2, lambda: self.scan_now_btn.set_sensitive(True) or False)

    def _on_browse(self, btn, entry):
        dialog = Gtk.FileChooserDialog(
            title="Select Directory",
            transient_for=self.get_root() if self.get_root() else None,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Select", Gtk.ResponseType.ACCEPT)
        dialog.connect("response", self._on_dialog_response, entry)
        dialog.present()

    def _on_dialog_response(self, dialog, response, entry):
        if response == Gtk.ResponseType.ACCEPT:
            folder = dialog.get_file()
            if folder:
                entry.set_text(folder.get_path())
        dialog.destroy()

    def _save(self, *args):
        for key, widget in self.entries.items():
            if isinstance(widget, Gtk.ComboBoxText):
                self.config[key] = widget.get_active_text()
            elif isinstance(widget, Gtk.SpinButton):
                self.config[key] = int(widget.get_value() * 60)
            else:
                self.config[key] = widget.get_text()
        self.on_save_cb(self.config)
        root = self.get_root()
        if root and hasattr(root, "add_toast"):
            toast = Adw.Toast.new("Settings saved")
            root.add_toast(toast)
