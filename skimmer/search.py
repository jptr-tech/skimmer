import threading

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk, GLib

from ytmusicapi import YTMusic

from skimmer.widgets import AlbumCover, AlbumDetail, find_cover


class SearchPage(Gtk.Box):
    def __init__(self, config, processing_manager, player_bar=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.config = config
        self.proc_mgr = processing_manager
        self._player_bar = player_bar
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.yt = YTMusic()
        self._current_album_data = None

        self._clamp = Adw.Clamp()
        self._clamp.set_maximum_size(600)
        self._clamp.set_tightening_threshold(400)
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text(
            "Search YouTube Music: Artist or Artist - Album..."
        )
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self._do_search)
        search_btn = Gtk.Button(label="Search")
        search_btn.connect("clicked", self._do_search)
        search_row.append(self.search_entry)
        search_row.append(search_btn)
        self._clamp.set_child(search_row)
        self.append(self._clamp)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)
        self.append(self.stack)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_homogeneous(True)
        self.flowbox.set_column_spacing(8)
        self.flowbox.set_row_spacing(12)
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flowbox.set_activate_on_single_click(True)
        self.flowbox.connect("child-activated", self._on_result_activated)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.flowbox)
        scroll.set_vexpand(True)
        self.stack.add_named(scroll, "results")

        self._search_results = []

    def _do_search(self, *args):
        query = self.search_entry.get_text().strip()
        if not query:
            return
        self.flowbox.remove_all()
        self._search_results = []
        self._current_album_data = None
        self.status_label = Gtk.Label(label="Searching...")
        self.stack.add_named(self.status_label, "status")

        threading.Thread(target=self._search_thread, args=(query,), daemon=True).start()

    def _search_thread(self, query):
        try:
            results = self.yt.search(query, filter="albums")
            GLib.idle_add(self._populate_results, results)
        except Exception as e:
            GLib.idle_add(self._show_error, f"Search error: {e}")

    def _show_error(self, msg):
        lbl = Gtk.Label(label=msg)
        self.stack.add_named(lbl, "status")
        self.stack.set_visible_child_name("status")

    def _populate_results(self, results):
        seen = set()
        for r in results:
            artist_names = [a.get("name", "?") for a in r.get("artists", [])]
            artist_str = ", ".join(artist_names)
            title = r.get("title", "?")
            key = (artist_str, title)
            if key in seen:
                continue
            seen.add(key)

            thumbnails = r.get("thumbnails", [])
            cover_url = thumbnails[-1]["url"] if thumbnails else None
            year = r.get("year", "")
            browse_id = r.get("browseId", "")

            result_data = {
                "artist": artist_str,
                "album": title,
                "year": year,
                "cover_url": cover_url,
                "browseId": browse_id,
            }
            cover = AlbumCover(
                artist=artist_str,
                album=title,
                year=year,
                cover_url=cover_url,
                data=result_data,
            )
            self.flowbox.append(cover)
            self._search_results.append(result_data)

        if self.stack.get_child_by_name("status"):
            self.stack.remove(self.stack.get_child_by_name("status"))

    def _on_result_activated(self, flowbox, child):
        result = child.data
        if not result:
            return
        self._current_album_data = result
        self._show_album_detail(result)

    def _show_album_detail(self, result):
        self._current_album_data = result
        self.status_label = Gtk.Label(
            label=f"Loading {result['artist']} - {result['album']}..."
        )
        self.stack.add_named(self.status_label, "status")
        self.stack.set_visible_child_name("status")

        threading.Thread(
            target=self._load_album_thread,
            args=(result,),
            daemon=True,
        ).start()

    def _load_album_thread(self, result):
        try:
            browse_id = result.get("browseId")
            if not browse_id:
                GLib.idle_add(self._show_error, "No album ID found")
                return
            album_data = self.yt.get_album(browse_id)
            tracks = album_data.get("tracks", [])
            track_list = []
            for t in tracks:
                track_list.append(
                    {
                        "track": str(t.get("trackNumber", "")),
                        "title": t.get("title", "?"),
                        "artist": ", ".join(
                            a.get("name", "") for a in t.get("artists", [])
                        )
                        or result["artist"],
                    }
                )

            GLib.idle_add(self._show_detail_view, result, album_data, track_list)
        except Exception as e:
            GLib.idle_add(self._show_error, f"Error: {e}")

    def _show_detail_view(self, result, album_data, track_list):
        if self.stack.get_child_by_name("status"):
            self.stack.remove(self.stack.get_child_by_name("status"))

        detail_name = "detail-search"

        existing = self.stack.get_child_by_name(detail_name)
        if existing:
            self.stack.remove(existing)

        # Store full album info for download
        artists = album_data.get("artists", [])
        artist_names = [a.get("name", "") for a in artists]

        full_album = {
            "title": album_data.get("title", result["album"]),
            "artist": ", ".join(artist_names) or result["artist"],
            "year": album_data.get("year", result.get("year", "")),
            "tracks": [
                {
                    "title": t.get("title", "?"),
                    "videoId": t.get("videoId", ""),
                    "trackNumber": t.get("trackNumber", 0),
                    "duration": t.get("duration", ""),
                    "artists": [a.get("name", "") for a in t.get("artists", [])],
                }
                for t in album_data.get("tracks", [])
            ],
            "browseId": result.get("browseId", ""),
        }

        detail = AlbumDetail(
            config=self.config,
            artist=full_album["artist"],
            album=full_album["title"],
            year=full_album["year"],
            tracks=track_list,
            cover_url=result.get("cover_url"),
            on_back=lambda: self.stack.set_visible_child_name("results"),
            on_download=lambda btn: self._do_download(full_album, btn),
            player_bar=self._player_bar,
        )
        detail.set_vexpand(True)
        self.stack.add_named(detail, detail_name)
        self.stack.set_visible_child(detail)

    def _do_download(self, album, button):
        button.set_sensitive(False)
        button.set_label("Downloading...")
        button.remove_css_class("suggested-action")
        button.add_css_class("opaque")
        task = self.proc_mgr.add_task(
            "download",
            f"{album['artist']} - {album['title']}",
            dict(album),
        )
        task.connect("updated", self._on_dl_updated, button)

    def _on_dl_updated(self, task, status, progress, message, button):
        if status == "completed":
            button.set_label("Downloaded")
            album_dir = task.data.get("album_dir")
            if album_dir:
                self.proc_mgr.add_task(
                    "import",
                    f"Import: {task.title}",
                    {"album_dir": album_dir},
                )
            GLib.timeout_add(2000, lambda: self._reset_button(button))
        elif status == "failed":
            button.set_sensitive(True)
            button.set_label("Download Album")
            button.add_css_class("suggested-action")
            button.remove_css_class("opaque")

    def _reset_button(self, button):
        button.set_label("Download Album")
        button.add_css_class("suggested-action")
        button.remove_css_class("opaque")
        return GLib.SOURCE_REMOVE
