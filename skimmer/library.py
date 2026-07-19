import os
import subprocess
import sys
import threading

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gtk, GLib, Gdk

from beets.library import Library

from skimmer.widgets import AlbumCover, AlbumDetail, find_cover, COVER_SIZE


class LibraryPage(Gtk.Box):
    def __init__(self, config, player_bar=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.config = config
        self._player_bar = player_bar
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.status_label = Gtk.Label(label="")
        self.status_label.set_halign(Gtk.Align.END)
        self.status_label.set_hexpand(True)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", self._refresh)
        header.append(self.status_label)
        header.append(refresh_btn)
        self.append(header)

        self._clamp = Adw.Clamp()
        self._clamp.set_maximum_size(600)
        self._clamp.set_tightening_threshold(400)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.set_placeholder_text("Filter library...")
        self.search_entry.connect("search-changed", self._on_search)
        self._clamp.set_child(self.search_entry)
        self.append(self._clamp)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)
        self.stack.connect("notify::visible-child", self._on_stack_page_changed)
        self.append(self.stack)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_homogeneous(True)
        self.flowbox.set_column_spacing(8)
        self.flowbox.set_row_spacing(12)
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.connect("child-activated", self._on_album_activated)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.flowbox)
        scroll.set_vexpand(True)
        self.stack.add_named(scroll, "grid")

        self._cover_size = COVER_SIZE
        self._cover_widgets = []
        self._beets_lib = None
        self._all_albums = []
        self._init_beets()
        self._refresh()
        GLib.timeout_add(500, self._lazy_fetch_covers)

        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(ctrl)

    def _lazy_fetch_covers(self):
        threading.Thread(target=self._fetch_missing_covers, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _fetch_missing_covers(self):
        if not self._beets_lib:
            return
        cmd = [sys.executable, "-m", "beets", "fetchart"]
        print(f"[y1-skimmer] Starting: {' '.join(cmd)}")
        GLib.idle_add(self.status_label.set_text, "Fetching missing album art...")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            count = 0
            for line in proc.stdout:
                line = line.strip()
                if line:
                    count += 1
                    print(f"[fetchart] {line}")
                    if (
                        count % 5 == 0
                        or "has album art" in line
                        or "fetching" in line.lower()
                    ):
                        short = line[:70]
                        GLib.idle_add(self.status_label.set_text, f"fetchart: {short}")
            proc.wait(timeout=120)
            print(f"[y1-skimmer] fetchart done: checked {count} albums")
            GLib.idle_add(
                self.status_label.set_text, f"fetchart: checked {count} albums"
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            print("[y1-skimmer] fetchart timed out after 120s")
            GLib.idle_add(self.status_label.set_text, "fetchart timed out")
        except Exception as e:
            print(f"[y1-skimmer] fetchart error: {e}")
            GLib.idle_add(self.status_label.set_text, f"fetchart: {e}")
        GLib.idle_add(self._refresh)

    def _init_beets(self):
        try:
            lib_path = os.path.expanduser(
                self.config.get("beets_lib", "~/Music/.musiclibrary.db")
            )
            if os.path.exists(lib_path):
                self._beets_lib = Library(lib_path)
        except Exception:
            pass

    def _refresh(self, *args):
        self.flowbox.remove_all()
        self._cover_widgets = []
        self._all_albums = []
        if not self._beets_lib:
            self._init_beets()
        if not self._beets_lib:
            self.status_label.set_text("Beets library not found")
            return
        try:
            for album in self._beets_lib.albums():
                try:
                    artist = album.albumartist or "Unknown"
                except AttributeError:
                    artist = "Unknown"
                try:
                    title = album.album or "Unknown"
                except AttributeError:
                    title = "Unknown"
                year = album.year or 0
                if artist == "Unknown" and title == "Unknown" and year == 0:
                    continue
                cover_path = None
                try:
                    album_path = os.fsdecode(album.path) if album.path else None
                    if album_path:
                        cover_path = find_cover(album_path)
                except Exception:
                    pass
                self._all_albums.append((artist, title, year, cover_path, album))
            self._build_covers()
            self._filter_and_reflow(self.search_entry.get_text())
        except Exception as e:
            self.status_label.set_text(f"Error: {e}")

    def _build_covers(self):
        self.flowbox.remove_all()
        self._cover_widgets = []
        for artist, title, year, cover_path, album in self._all_albums:
            cover = AlbumCover(
                artist,
                title,
                year,
                cover_path=cover_path,
                data=album,
                size=self._cover_size,
            )
            self._cover_widgets.append(cover)

    def _on_search(self, entry):
        self._filter_and_reflow(entry.get_text())

    def _on_stack_page_changed(self, stack, pspec):
        is_grid = stack.get_visible_child_name() == "grid"
        self._clamp.set_visible(is_grid)

    def _filter_and_reflow(self, query):
        self.flowbox.remove_all()
        q = query.lower()
        visible_count = 0
        for cover in self._cover_widgets:
            visible = not q or q in cover.artist.lower() or q in cover.album.lower()
            cover._visible = visible
            if visible:
                self.flowbox.append(cover)
                visible_count += 1
        self.status_label.set_text(f"{visible_count} albums")

    def _refresh_covers(self):
        for i, (artist, title, year, _, album) in enumerate(self._all_albums):
            cover_path = None
            try:
                album_path_fs = os.fsdecode(album.path) if album.path else None
                if album_path_fs:
                    cover_path = find_cover(album_path_fs)
            except Exception:
                pass
            self._all_albums[i] = (artist, title, year, cover_path, album)
        self._build_covers()
        self._filter_and_reflow(self.search_entry.get_text())

    def _on_album_activated(self, flowbox, child):
        cover = child
        album_obj = cover.data
        tracks = []
        try:
            query = [
                f"albumartist:{cover.artist}",
                f"album:{cover.album}",
            ]
            for item in self._beets_lib.items(query):
                tracks.append(
                    {
                        "track": str(item.track or ""),
                        "title": item.title or "?",
                        "artist": item.artist or cover.artist,
                        "file_path": os.fsdecode(item.path) if item.path else None,
                    }
                )
        except Exception:
            tracks = []

        album_path = (
            os.fsdecode(album_obj.path) if album_obj and album_obj.path else None
        )

        detail = AlbumDetail(
            config=self.config,
            artist=cover.artist,
            album=cover.album,
            year=cover.year,
            tracks=tracks,
            cover_path=cover.cover_path,
            on_back=lambda: self.stack.set_visible_child_name("grid"),
            album_path=album_path,
            on_set_cover=lambda _path: self._refresh_covers(),
            player_bar=self._player_bar,
        )
        detail.set_vexpand(True)
        name = f"detail-{cover.artist}-{cover.album}"
        if self.stack.get_child_by_name(name):
            self.stack.remove(self.stack.get_child_by_name(name))
        self.stack.add_named(detail, name)
        self.stack.set_visible_child(detail)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if not ctrl:
            return False
        step = 20
        old_size = self._cover_size
        if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._cover_size = min(300, self._cover_size + step)
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self._cover_size = max(60, self._cover_size - step)
        elif keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            self._cover_size = COVER_SIZE
        else:
            return False
        if self._cover_size == old_size:
            return True
        for cover in self._cover_widgets:
            cover.set_cover_size(self._cover_size)
        self._filter_and_reflow(self.search_entry.get_text())
        return True
