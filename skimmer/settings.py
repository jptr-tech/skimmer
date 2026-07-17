import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Adw


class SettingsPage(Gtk.Box):
    def __init__(self, config, on_save):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.config = config
        self.on_save_cb = on_save
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
            ("y1_mount_path", "Y1 Mount Path", True),
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

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_halign(Gtk.Align.END)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._save)
        btn_box.append(save_btn)

        self.append(btn_box)

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
            else:
                self.config[key] = widget.get_text()
        self.on_save_cb(self.config)
        root = self.get_root()
        if root and hasattr(root, "add_toast"):
            toast = Adw.Toast.new("Settings saved")
            root.add_toast(toast)
