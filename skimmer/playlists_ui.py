import os
import shutil
import threading
import time

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gtk, GLib, Gdk, GdkPixbuf, Gio, Pango

from beets import context as beets_context
from beets.library import Library
from beets.util import bytestring_path

from skimmer.playlist import (
    Playlist,
    PlaylistTrack,
    load_playlists,
    save_playlists,
    export_m3u8,
    parse_m3u8,
    resolve_cover,
    COVERS_DIR,
)


def _beets_search(query: str, config: dict) -> list[PlaylistTrack]:
    from skimmer.config import resolve_path

    music_dir = resolve_path(config, "music_dir")
    beets_db = resolve_path(config, "beets_lib")
    if not os.path.exists(beets_db):
        return []
    beets_context.set_music_dir(bytestring_path(music_dir))
    lib = Library(beets_db, directory=music_dir)
    results = []
    for item in lib.items(query):
        results.append(
            PlaylistTrack(
                file_path=os.fsdecode(item.path),
                title=item.title or "",
                artist=item.artist or "",
                album=item.album or "",
                duration=int(item.length or 0),
            )
        )
    return results


class AddTracksDialog(Gtk.Window):
    def __init__(self, parent, config, on_add):
        super().__init__(title="Add Tracks", transient_for=parent, modal=True)
        self._config = config
        self._on_add = on_add
        self._results = []
        self._selected = set()
        self.set_default_size(500, 400)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        self.set_child(vbox)

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Search beets library...")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._do_search)
        search_box.append(self.entry)
        btn = Gtk.Button(label="Search")
        btn.connect("clicked", self._do_search)
        search_box.append(btn)
        vbox.append(search_box)

        self.status = Gtk.Label(label="")
        self.status.set_halign(Gtk.Align.START)
        self.status.add_css_class("dim-label")
        vbox.append(self.status)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        self.listbox.connect("selected-rows-changed", self._on_selection_changed)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.listbox)
        scroll.set_vexpand(True)
        vbox.append(scroll)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_halign(Gtk.Align.END)
        self.add_btn = Gtk.Button(label="Add Selected")
        self.add_btn.add_css_class("suggested-action")
        self.add_btn.set_sensitive(False)
        self.add_btn.connect("clicked", self._do_add)
        btn_box.append(self.add_btn)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda b: self.close())
        btn_box.append(cancel)
        vbox.append(btn_box)

        self.present()

    def _do_search(self, *args):
        q = self.entry.get_text().strip()
        if not q:
            return
        self.status.set_text("Searching...")
        self.listbox.remove_all()
        self._results = []
        self._selected = set()
        threading.Thread(target=self._search_thread, args=(q,), daemon=True).start()

    def _search_thread(self, q):
        results = _beets_search(q, self._config)
        GLib.idle_add(self._show_results, results)

    def _show_results(self, results):
        self._results = results
        self.listbox.remove_all()
        for i, track in enumerate(results):
            lbl = f"{track.artist} — {track.title}  ({track.album})"
            row = Gtk.ListBoxRow()
            row.set_child(
                Gtk.Label(
                    label=lbl,
                    xalign=0.0,
                    margin_start=6,
                    margin_end=6,
                    margin_top=3,
                    margin_bottom=3,
                )
            )
            self.listbox.append(row)
        self.status.set_text(f"{len(results)} results")

    def _on_selection_changed(self, listbox):
        self._selected = {row.get_index() for row in listbox.get_selected_rows()}
        self.add_btn.set_sensitive(len(self._selected) > 0)

    def _do_add(self, *args):
        tracks = [self._results[i] for i in sorted(self._selected)]
        self._on_add(tracks)
        self.close()


COVER_SIZE = 150


def _make_placeholder(size=COVER_SIZE):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, size, size)
    pixbuf.fill(0x44444400)
    return pixbuf


class PlaylistCover(Gtk.FlowBoxChild):
    def __init__(self, playlist, cover_path, size=COVER_SIZE, on_delete=None):
        super().__init__()
        self.playlist = playlist
        self._cover_path = cover_path
        self._size = size
        self._on_delete = on_delete
        self.set_margin_start(4)
        self.set_margin_end(4)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.image = Gtk.Image()
        self.image.set_pixel_size(size)
        self.image.set_halign(Gtk.Align.CENTER)
        box.append(self.image)

        name_lbl = Gtk.Label(label=playlist.name)
        name_lbl.set_halign(Gtk.Align.CENTER)
        name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        name_lbl.set_single_line_mode(True)
        name_lbl.add_css_class("body")
        box.append(name_lbl)

        count_lbl = Gtk.Label(label=f"{len(playlist.tracks)} tracks")
        count_lbl.set_halign(Gtk.Align.CENTER)
        count_lbl.add_css_class("caption")
        box.append(count_lbl)

        self.set_child(box)
        self.set_size_request(size + 12, -1)
        self._load()

        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.connect("pressed", self._on_right_click)
        self.add_controller(gesture)

    def _on_right_click(self, gesture, n_press, x, y):
        if not self._on_delete:
            return
        menu = Gtk.PopoverMenu.new_from_model(self._build_delete_menu())
        menu.set_parent(self)
        menu.set_position(Gtk.PositionType.BOTTOM)
        menu.popup()

    def _build_delete_menu(self):
        model = Gio.Menu.new()
        model.append("Delete", "playlist.delete")
        action_group = Gio.SimpleActionGroup.new()
        delete_action = Gio.SimpleAction.new("delete", None)
        delete_action.connect("activate", lambda a, p: self._on_delete(self.playlist))
        action_group.add_action(delete_action)
        self.insert_action_group("playlist", action_group)
        return model

    def _load(self):
        if self._cover_path and os.path.exists(self._cover_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    self._cover_path, self._size, self._size, True
                )
                self.image.set_from_pixbuf(pixbuf)
                return
            except Exception:
                pass
        self.image.set_from_pixbuf(_make_placeholder(self._size))


class PlaylistsPage(Gtk.Box):
    def __init__(self, config, player_bar=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.config = config
        self._player_bar = player_bar
        self._playlists: list[Playlist] = []
        self._detail_playlist_index = -1
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)
        self.append(self.stack)

        self._build_grid()
        self._build_detail()
        self._load()

    def _build_grid(self):
        grid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        new_btn = Gtk.Button(label="New Playlist")
        new_btn.connect("clicked", self._on_new)
        toolbar.append(new_btn)
        import_btn = Gtk.Button(label="Import M3U")
        import_btn.connect("clicked", self._on_import)
        toolbar.append(import_btn)
        grid_box.append(toolbar)

        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text("Filter playlists...")
        search_entry.set_hexpand(True)
        search_entry.connect("search-changed", self._on_search)
        grid_box.append(search_entry)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_homogeneous(True)
        self.flowbox.set_column_spacing(8)
        self.flowbox.set_row_spacing(12)
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.connect("child-activated", self._on_cover_activated)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.flowbox)
        scroll.set_vexpand(True)
        grid_box.append(scroll)

        self._cover_widgets: list[PlaylistCover] = []
        self.stack.add_named(grid_box, "grid")

    def _build_detail(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.detail_stack_child = scroll

        clamp = Adw.Clamp()
        clamp.set_maximum_size(700)
        clamp.set_tightening_threshold(500)
        scroll.set_child(clamp)

        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        wrapper.set_margin_start(12)
        wrapper.set_margin_end(12)
        wrapper.set_margin_top(12)
        wrapper.set_margin_bottom(24)
        clamp.set_child(wrapper)

        back_btn = Gtk.Button(label="\u2190 Back")
        back_btn.set_halign(Gtk.Align.START)
        back_btn.connect("clicked", lambda b: self.stack.set_visible_child_name("grid"))
        wrapper.append(back_btn)

        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        self.detail_cover = Gtk.Image()
        self.detail_cover.set_pixel_size(200)
        self.detail_cover.set_halign(Gtk.Align.START)
        self.detail_cover.set_valign(Gtk.Align.START)
        info_box.append(self.detail_cover)

        meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        meta_box.set_valign(Gtk.Align.START)

        self.detail_name = Gtk.Label(label="", css_classes=["title-2"])
        self.detail_name.set_halign(Gtk.Align.START)
        self.detail_name.set_wrap(True)
        meta_box.append(self.detail_name)

        self.detail_count = Gtk.Label(label="")
        self.detail_count.set_halign(Gtk.Align.START)
        self.detail_count.add_css_class("dim-label")
        meta_box.append(self.detail_count)

        meta_box.append(Gtk.Label(label=""))

        btn_grid = Gtk.Grid()
        btn_grid.set_column_spacing(6)
        btn_grid.set_row_spacing(6)

        set_cover_btn = Gtk.Button(label="Set Cover Art...")
        set_cover_btn.connect("clicked", self._on_set_cover)
        btn_grid.attach(set_cover_btn, 0, 0, 1, 1)

        add_tracks_btn = Gtk.Button(label="Add Tracks...")
        add_tracks_btn.connect("clicked", self._on_add_tracks)
        btn_grid.attach(add_tracks_btn, 1, 0, 1, 1)

        export_btn = Gtk.Button(label="Export M3U...")
        export_btn.connect("clicked", self._on_export)
        btn_grid.attach(export_btn, 0, 1, 1, 1)

        delete_btn = Gtk.Button(label="Delete Playlist")
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect("clicked", self._on_delete)
        btn_grid.attach(delete_btn, 1, 1, 1, 1)

        meta_box.append(btn_grid)

        info_box.append(meta_box)
        wrapper.append(info_box)

        self.detail_track_list = Gtk.ListBox()
        self.detail_track_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.detail_track_list.connect("row-activated", self._on_detail_track_activated)
        wrapper.append(self.detail_track_list)

        self.stack.add_named(scroll, "detail")

    def _load(self):
        self._playlists = load_playlists()
        self._rebuild_grid()

    def _save(self):
        save_playlists(self._playlists)

    def _rebuild_grid(self, filter_text=""):
        self.flowbox.remove_all()
        self._cover_widgets = []
        q = filter_text.lower()
        for pl in self._playlists:
            if q and q not in pl.name.lower():
                continue
            cover = resolve_cover(pl)
            widget = PlaylistCover(pl, cover, on_delete=self._on_grid_delete)
            self._cover_widgets.append(widget)
            self.flowbox.append(widget)

    def _build_detail_for(self, index):
        if index < 0 or index >= len(self._playlists):
            return
        self._detail_playlist_index = index
        pl = self._playlists[index]
        self.detail_name.set_text(pl.name)
        self.detail_count.set_text(f"{len(pl.tracks)} tracks")

        cover = resolve_cover(pl)
        if cover and os.path.exists(cover):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(cover, 200, 200, True)
                self.detail_cover.set_from_pixbuf(pixbuf)
            except Exception:
                self.detail_cover.set_from_pixbuf(_make_placeholder(200))
        else:
            self.detail_cover.set_from_pixbuf(_make_placeholder(200))

        self.detail_track_list.remove_all()
        for i, t in enumerate(pl.tracks):
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(8)
            hbox.set_margin_end(8)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)

            num_lbl = Gtk.Label(label=str(i + 1))
            num_lbl.set_width_chars(2)
            num_lbl.set_xalign(1.0)
            num_lbl.add_css_class("dim-label")
            hbox.append(num_lbl)

            title_lbl = Gtk.Label(label=t.title or "?")
            title_lbl.set_halign(Gtk.Align.START)
            title_lbl.set_hexpand(True)
            title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            title_lbl.set_single_line_mode(True)
            hbox.append(title_lbl)

            if t.artist:
                art_lbl = Gtk.Label(label=t.artist)
                art_lbl.set_halign(Gtk.Align.END)
                art_lbl.add_css_class("dim-label")
                art_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                art_lbl.set_single_line_mode(True)
                hbox.append(art_lbl)

            rem_btn = Gtk.Button()
            rem_btn.set_icon_name("list-remove-symbolic")
            rem_btn.set_tooltip_text("Remove from playlist")
            rem_btn.set_has_frame(False)

            def remove_cb(b, idx=i):
                self._remove_track_at(idx)

            rem_btn.connect("clicked", remove_cb)
            hbox.append(rem_btn)

            row.set_child(hbox)
            self.detail_track_list.append(row)

    def _remove_track_at(self, idx):
        if self._detail_playlist_index < 0:
            return
        pl = self._playlists[self._detail_playlist_index]
        if idx < 0 or idx >= len(pl.tracks):
            return
        del pl.tracks[idx]
        pl.last_modified = time.time()
        self._save()
        self._build_detail_for(self._detail_playlist_index)
        self._rebuild_grid()

    def _on_search(self, entry):
        self._rebuild_grid(entry.get_text())

    def _on_cover_activated(self, flowbox, child):
        idx = child.get_index()
        self._build_detail_for(idx)
        self.stack.set_visible_child_name("detail")

    def _on_detail_track_activated(self, listbox, row):
        if self._player_bar is None or self._detail_playlist_index < 0:
            return
        pl = self._playlists[self._detail_playlist_index]
        idx = row.get_index()
        if idx < 0 or idx >= len(pl.tracks):
            return
        track = pl.tracks[idx]
        if not os.path.exists(track.file_path):
            return
        tracks_tuple = [(t.file_path, t.title, t.artist) for t in pl.tracks]
        self._player_bar.play_file(
            track.file_path,
            title=track.title,
            artist=track.artist,
            track_idx=idx,
            tracks=tracks_tuple,
        )

    def _on_new(self, *args):
        dialog = Gtk.Window(
            title="New Playlist", transient_for=self.get_root(), modal=True
        )
        dialog.set_default_size(300, 100)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        dialog.set_child(vbox)

        lbl = Gtk.Label(label="Playlist name:")
        vbox.append(lbl)
        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g. Favorites")
        entry.set_hexpand(True)
        vbox.append(entry)

        def do_create(*a):
            name = entry.get_text().strip()
            if not name:
                return
            pl = Playlist(name=name)
            pl.last_modified = time.time()
            self._playlists.append(pl)
            self._save()
            self._rebuild_grid()
            dialog.close()

        entry.connect("activate", do_create)
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_halign(Gtk.Align.END)
        create_btn = Gtk.Button(label="Create")
        create_btn.add_css_class("suggested-action")
        create_btn.connect("clicked", do_create)
        btn_box.append(create_btn)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda b: dialog.close())
        btn_box.append(cancel)
        vbox.append(btn_box)
        dialog.present()

    def _on_grid_delete(self, playlist):
        idx = next((i for i, p in enumerate(self._playlists) if p is playlist), -1)
        if idx < 0:
            return
        self._confirm_delete(idx)

    def _on_delete(self, *args):
        idx = self._detail_playlist_index
        if idx < 0 or idx >= len(self._playlists):
            return
        self._confirm_delete(idx)

    def _confirm_delete(self, idx):
        pl = self._playlists[idx]
        dialog = Gtk.MessageDialog(
            transient_for=self.get_root(),
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f'Delete playlist "{pl.name}"?',
        )
        dialog.connect("response", lambda d, r: self._do_delete(d, r, idx))
        dialog.present()

    def _do_delete(self, dialog, response, idx):
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        pl = self._playlists[idx]
        if pl.cover_path and os.path.exists(pl.cover_path):
            try:
                os.remove(pl.cover_path)
            except OSError:
                pass
        del self._playlists[idx]
        self._save()
        if self.stack.get_visible_child_name() == "detail":
            self.stack.set_visible_child_name("grid")
        self._rebuild_grid()

    def _on_add_tracks(self, *args):
        if self._detail_playlist_index < 0:
            return

        def add_tracks(tracks):
            pl = self._playlists[self._detail_playlist_index]
            for t in tracks:
                pl.tracks.append(t)
            pl.last_modified = time.time()
            self._save()
            self._build_detail_for(self._detail_playlist_index)
            self._rebuild_grid()

        AddTracksDialog(self.get_root(), self.config, add_tracks)

    def _on_remove_tracks(self, *args):
        if self._detail_playlist_index < 0:
            return
        row = self.detail_track_list.get_selected_row()
        if row is None:
            return
        idx = row.get_index()
        self._remove_track_at(idx)

    def _on_set_cover(self, *args):
        if self._detail_playlist_index < 0:
            return
        dialog = Gtk.FileChooserDialog(
            title="Select Cover Image",
            transient_for=self.get_root(),
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Select", Gtk.ResponseType.ACCEPT)
        filter_img = Gtk.FileFilter()
        filter_img.set_name("Images")
        filter_img.add_mime_type("image/jpeg")
        filter_img.add_mime_type("image/png")
        filter_img.add_mime_type("image/webp")
        dialog.add_filter(filter_img)

        def on_response(d, response):
            gf = d.get_file()
            d.destroy()
            if response != Gtk.ResponseType.ACCEPT or not gf:
                return
            src = gf.get_path()
            if not src:
                return
            pl = self._playlists[self._detail_playlist_index]
            COVERS_DIR.mkdir(parents=True, exist_ok=True)
            dst = str(COVERS_DIR / f"{pl.name}.jpg")
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(src, 200, 200, True)
                pixbuf.savev(dst, "jpeg", [], [])
                pl.cover_path = dst
                self._save()
                self._build_detail_for(self._detail_playlist_index)
                self._rebuild_grid()
            except Exception as e:
                print(f"[skimmer] Failed to set cover: {e}")

        dialog.connect("response", on_response)
        dialog.present()

    def _on_import(self, *args):
        dialog = Gtk.FileChooserDialog(
            title="Import M3U Playlist",
            transient_for=self.get_root(),
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Import", Gtk.ResponseType.ACCEPT)
        filter_m3u = Gtk.FileFilter()
        filter_m3u.set_name("M3U playlists")
        filter_m3u.add_pattern("*.m3u")
        filter_m3u.add_pattern("*.m3u8")
        dialog.add_filter(filter_m3u)
        dialog.connect("response", self._on_import_response)
        dialog.present()

    def _on_import_response(self, dialog, response):
        gf = dialog.get_file()
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT or not gf:
            return
        path = gf.get_path()
        if not path:
            return

        def do_import():
            pl = parse_m3u8(path)
            if pl is None:
                return
            GLib.idle_add(self._add_imported, pl)

        threading.Thread(target=do_import, daemon=True).start()

    def _add_imported(self, pl):
        existing = [p for p in self._playlists if p.name == pl.name]
        if existing:
            existing[0].tracks = pl.tracks
            existing[0].last_modified = time.time()
        else:
            self._playlists.append(pl)
        self._save()
        self._rebuild_grid()

    def _on_export(self, *args):
        if self._detail_playlist_index < 0:
            return
        pl = self._playlists[self._detail_playlist_index]
        dialog = Gtk.FileChooserDialog(
            title=f"Export {pl.name}.m3u8",
            transient_for=self.get_root(),
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Export", Gtk.ResponseType.ACCEPT)
        dialog.set_current_name(f"{pl.name}.m3u8")
        filter_m3u = Gtk.FileFilter()
        filter_m3u.set_name("M3U8 playlist")
        filter_m3u.add_pattern("*.m3u8")
        dialog.add_filter(filter_m3u)
        dialog.connect("response", self._on_export_response)
        dialog.present()

    def _on_export_response(self, dialog, response):
        gf = dialog.get_file()
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT or not gf:
            return
        path = gf.get_path()
        if not path:
            return
        pl = self._playlists[self._detail_playlist_index]
        export_m3u8(pl, self.config.get("music_dir", "~/Music"), path)

    def _refresh(self):
        self._load()

    def get_playlists(self) -> list[Playlist]:
        return self._playlists

    def set_playlists(self, playlists: list[Playlist]):
        self._playlists = playlists
        self._rebuild_grid()
