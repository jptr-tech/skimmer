import json
import os
import threading
import time
import urllib.parse
import urllib.request

import gi
gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gtk, Gdk, GLib, Pango, GdkPixbuf

from skimmer.playlist import Playlist, PlaylistTrack, load_playlists, save_playlists

from beets import context as beets_context
from beets.util import bytestring_path

from gi.repository import Gio

import logging
log = logging.getLogger(__name__)

COVER_SIZE = 150


def find_cover(album_path):
    if not album_path:
        return None
    for name in ("cover.jpg", "cover.png", "front.jpg", "folder.jpg", "Cover.jpg"):
        path = os.path.join(album_path, name)
        if os.path.exists(path):
            return path
    return None


def _make_placeholder_pixbuf(size=COVER_SIZE):
    pixbuf = GdkPixbuf.Pixbuf.new(
        GdkPixbuf.Colorspace.RGB, False, 8, size, size
    )
    pixbuf.fill(0x44444400)
    return pixbuf


class AlbumCover(Gtk.FlowBoxChild):
    ITEM_EXTRA = 12

    def __init__(self, artist, album, year, cover_path=None, cover_url=None, data=None, size=COVER_SIZE, on_delete=None):
        super().__init__()
        self.artist = artist
        self.album = album
        self.year = year
        self.cover_path = cover_path
        self.cover_url = cover_url
        self.data = data
        self._on_delete = on_delete

        self._cover_size = size
        self._visible = True
        self.set_margin_start(4)
        self.set_margin_end(4)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.image = Gtk.Image()
        self.image.set_pixel_size(size)
        self.image.set_halign(Gtk.Align.CENTER)
        box.append(self.image)

        self.album_lbl = Gtk.Label(label=album)
        self.album_lbl.set_halign(Gtk.Align.CENTER)
        self.album_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.album_lbl.set_single_line_mode(True)
        self.album_lbl.add_css_class("body")
        box.append(self.album_lbl)

        self.artist_lbl = Gtk.Label(label=artist)
        self.artist_lbl.set_halign(Gtk.Align.CENTER)
        self.artist_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.artist_lbl.set_single_line_mode(True)
        self.artist_lbl.add_css_class("caption")
        box.append(self.artist_lbl)

        self.set_child(box)

        if on_delete:
            gesture = Gtk.GestureClick()
            gesture.set_button(3)
            gesture.connect("pressed", self._on_right_click)
            self.add_controller(gesture)

        self._load_cover()
        self._apply_size()

    def _on_right_click(self, gesture, n_press, x, y):
        menu = Gtk.PopoverMenu.new_from_model(self._build_delete_menu())
        menu.set_parent(self)
        menu.set_position(Gtk.PositionType.BOTTOM)
        menu.popup()

    def _build_delete_menu(self):
        model = Gio.Menu.new()
        model.append("Delete from Library", "cover.delete")
        action_group = Gio.SimpleActionGroup.new()
        delete_action = Gio.SimpleAction.new("delete", None)
        delete_action.connect("activate", lambda a, p: self._on_delete(self.data))
        action_group.add_action(delete_action)
        self.insert_action_group("cover", action_group)
        return model

    def _apply_size(self):
        s = self._cover_size
        self.set_size_request(s + self.ITEM_EXTRA, -1)
        self.image.set_pixel_size(s)
        max_chars = max(8, s // 8)
        self.album_lbl.set_max_width_chars(max_chars)
        self.artist_lbl.set_max_width_chars(max_chars)

    def set_cover_size(self, size):
        self._cover_size = size
        self._apply_size()

    def _load_cover(self):
        if self.cover_path and os.path.exists(self.cover_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    self.cover_path, self._cover_size, self._cover_size, True
                )
                self.image.set_from_pixbuf(pixbuf)
                return
            except Exception:
                pass
        elif self.cover_url:
            threading.Thread(target=self._load_url_cover, daemon=True).start()
            return
        self._set_placeholder()

    def _load_url_cover(self):
        try:
            data = urllib.request.urlopen(self.cover_url, timeout=10).read()
        except Exception:
            GLib.idle_add(self._set_placeholder)
            return
        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except Exception:
            try:
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
            except Exception:
                GLib.idle_add(self._set_placeholder)
                return
        scaled = pixbuf.scale_simple(
            self._cover_size, self._cover_size,
            GdkPixbuf.InterpType.BILINEAR
        )
        GLib.idle_add(self.image.set_from_pixbuf, scaled)

    def _set_placeholder(self):
        try:
            pixbuf = _make_placeholder_pixbuf(self._cover_size)
            self.image.set_from_pixbuf(pixbuf)
        except Exception:
            pass



class CoverSearchResult(Gtk.Box):
    def __init__(self, title, artist, thumb_url, full_url, on_select):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.full_url = full_url
        self.set_margin_start(4)
        self.set_margin_end(4)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        self.thumb = Gtk.Image()
        self.thumb.set_pixel_size(48)
        self.append(self.thumb)
        self._load_thumb(thumb_url)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_lbl = Gtk.Label(label=title)
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        title_lbl.set_single_line_mode(True)
        title_lbl.add_css_class("body")
        text_box.append(title_lbl)

        artist_lbl = Gtk.Label(label=artist)
        artist_lbl.set_halign(Gtk.Align.START)
        artist_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        artist_lbl.set_single_line_mode(True)
        artist_lbl.add_css_class("caption")
        text_box.append(artist_lbl)

        self.append(text_box)

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", lambda g, n, x, y: on_select(self))
        self.add_controller(gesture)

    def _load_thumb(self, url):
        threading.Thread(target=self._load_thumb_thread, args=(url,), daemon=True).start()

    def _load_thumb_thread(self, url):
        try:
            data = urllib.request.urlopen(url, timeout=10).read()
            loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except Exception:
            try:
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
            except Exception:
                return
        scaled = pixbuf.scale_simple(48, 48, GdkPixbuf.InterpType.BILINEAR)
        GLib.idle_add(self.thumb.set_from_pixbuf, scaled)

    def get_full_url(self):
        return self.full_url


class CoverSearchDialog(Gtk.Window):
    def __init__(self, parent, artist, album, album_path, cover_pic, on_set_cover):
        super().__init__(title="Search Album Art", transient_for=parent, modal=True)
        self.set_default_size(450, 400)
        self.album_path = album_path
        self.cover_pic = cover_pic
        self._on_set_cover = on_set_cover

        log.info(f"[skimmer] Opening cover search dialog for {artist} - {album}")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        self.set_child(vbox)

        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_entry = Gtk.Entry()
        self.search_entry.set_text(f"{artist} {album}")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self._do_search)
        search_box.append(self.search_entry)

        search_btn = Gtk.Button(label="Search")
        search_btn.connect("clicked", self._do_search)
        search_box.append(search_btn)
        vbox.append(search_box)

        self.status_lbl = Gtk.Label(label="")
        self.status_lbl.set_halign(Gtk.Align.START)
        self.status_lbl.add_css_class("dim-label")
        vbox.append(self.status_lbl)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_homogeneous(False)
        self.flowbox.set_max_children_per_line(1)
        self.flowbox.set_min_children_per_line(1)
        self.flowbox.set_valign(Gtk.Align.START)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.flowbox)
        scroll.set_vexpand(True)
        vbox.append(scroll)

        self.present()
        self._do_search()

    def _do_search(self, *args):
        term = self.search_entry.get_text().strip()
        if not term:
            return
        self.flowbox.remove_all()
        self.status_lbl.set_text("Searching iTunes + Deezer...")
        log.info(f"[skimmer] Cover search: '{term}'")
        threading.Thread(target=self._search_thread, args=(term,), daemon=True).start()

    def _search_thread(self, term):
        combined = {}
        encoded = urllib.parse.quote(term)

        # iTunes
        try:
            url = f"https://itunes.apple.com/search?term={encoded}&entity=album&limit=15"
            resp = urllib.request.urlopen(url, timeout=15).read()
            data = json.loads(resp)
            for r in data.get("results", []):
                key = (r.get("artistName", ""), r.get("collectionName", ""))
                thumb = r.get("artworkUrl60", "")
                full = r.get("artworkUrl100", "").replace("100x100bb", "600x600bb").replace("100x100", "600x600")
                if full and key not in combined:
                    combined[key] = (r.get("collectionName", "?"), r.get("artistName", "?"), thumb, full, "iTunes")
        except Exception as e:
            log.warning(f"[skimmer] iTunes search error: {e}")

        # Deezer
        try:
            url = f"https://api.deezer.com/search/album?q={encoded}&limit=15"
            resp = urllib.request.urlopen(url, timeout=15).read()
            data = json.loads(resp)
            for r in data.get("data", []):
                key = (r.get("artist", {}).get("name", ""), r.get("title", ""))
                thumb = r.get("cover_small", "")
                full = r.get("cover_big", "")
                if full and key not in combined:
                    combined[key] = (r.get("title", "?"), r.get("artist", {}).get("name", "?"), thumb, full, "Deezer")
        except Exception as e:
            log.warning(f"[skimmer] Deezer search error: {e}")

        GLib.idle_add(self._show_results, list(combined.values()))

    def _show_error(self, msg):
        log.warning(f"[skimmer] Cover search error displayed: {msg}")
        self.status_lbl.set_text(msg)

    def _show_results(self, results):
        if not results:
            self.status_lbl.set_text("No results found")
            log.info("[skimmer] Cover search: 0 results")
            return
        source_counts = {}
        for _, _, _, _, src in results:
            source_counts[src] = source_counts.get(src, 0) + 1
        summary = ", ".join(f"{src}: {n}" for src, n in source_counts.items())
        self.status_lbl.set_text(f"{len(results)} results ({summary})")
        log.info(f"[skimmer] Cover search: {len(results)} results ({summary})")
        for title, artist, thumb_url, full_url, source in results:
            label = f"[{source}] {title}"
            result_widget = CoverSearchResult(
                label, artist, thumb_url, full_url,
                on_select=self._on_result_selected,
            )
            self.flowbox.append(result_widget)

    def _on_result_selected(self, result):
        url = result.get_full_url()
        log.info(f"[skimmer] Cover selected: {url}")
        self.status_lbl.set_text("Downloading cover...")
        threading.Thread(target=self._download_thread, args=(url,), daemon=True).start()

    def _download_thread(self, url):
        log.info(f"[skimmer] Downloading cover from {url}")
        try:
            data = urllib.request.urlopen(url, timeout=15).read()
            log.info(f"[skimmer] Downloaded {len(data)} bytes")
        except Exception as e:
            log.warning(f"[skimmer] Download failed: {e}")
            GLib.idle_add(self.status_lbl.set_text, f"Download failed: {e}")
            return

        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
            loader.write(data)
            loader.close()
            loader.get_pixbuf()
        except Exception:
            try:
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(data)
                loader.close()
                loader.get_pixbuf()
            except Exception:
                log.info(f"[skimmer] Invalid image data")
                GLib.idle_add(self.status_lbl.set_text, "Invalid image data")
                return

        dst = os.path.join(self.album_path, "cover.jpg")
        try:
            with open(dst, "wb") as f:
                f.write(data)
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                dst, 200, 200, True
            )
            GLib.idle_add(self.cover_pic.set_from_pixbuf, pixbuf)
            if self._on_set_cover:
                GLib.idle_add(self._on_set_cover, dst)
            GLib.idle_add(self.close)
        except Exception as e:
            GLib.idle_add(self.status_lbl.set_text, f"Save failed: {e}")


class AlbumDetail(Gtk.Box):
    def __init__(self, config, artist, album, year, tracks,
                 cover_path=None, cover_url=None,
                 on_back=None, on_download=None,
                 album_path=None, on_set_cover=None,
                 player_bar=None, beets_lib=None,
                 album_obj=None, on_reimport=None, on_delete=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.config = config
        self.album_path = album_path
        self._on_set_cover = on_set_cover
        self._cover_path = cover_path
        self._artist = artist
        self._album = album
        self._on_back_cb = on_back
        self._player_bar = player_bar
        self._beets_lib = beets_lib
        self._album_obj = album_obj
        self._reimport_complete_cb = on_reimport
        self._delete_cb = on_delete
        self._tracks = sorted(
            tracks,
            key=lambda t: (
                int(t.get("track", t.get("trackNumber", 0)) or 0),
                int(t.get("trackNumber", 0) or 0),
            ),
        )

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        self.append(scroll)

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

        if on_back:
            back_btn = Gtk.Button(label="\u2190 Back")
            back_btn.set_halign(Gtk.Align.START)
            back_btn.connect("clicked", lambda b: on_back())
            wrapper.append(back_btn)

        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        self.cover_img = Gtk.Image()
        self.cover_img.set_pixel_size(200)
        self.cover_img.set_halign(Gtk.Align.START)
        self.cover_img.set_valign(Gtk.Align.START)
        info_box.append(self.cover_img)
        self._load_detail_cover(cover_path, cover_url)

        meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        meta_box.set_valign(Gtk.Align.START)

        title_lbl = Gtk.Label(label=album, css_classes=["title-2"])
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_wrap(True)
        meta_box.append(title_lbl)

        artist_lbl = Gtk.Label(label=artist, css_classes=["title-4"])
        artist_lbl.set_halign(Gtk.Align.START)
        meta_box.append(artist_lbl)

        year_lbl = Gtk.Label(label=str(year) if year else "")
        year_lbl.set_halign(Gtk.Align.START)
        year_lbl.add_css_class("dim-label")
        meta_box.append(year_lbl)

        if album_path and on_set_cover is not None:
            btn_grid = Gtk.Grid()
            btn_grid.set_column_spacing(6)
            btn_grid.set_row_spacing(6)
            btn_grid.set_margin_top(4)

            set_cover_btn = Gtk.Button(label="Set Album Art...")
            set_cover_btn.connect("clicked", self._on_set_cover_clicked)
            btn_grid.attach(set_cover_btn, 0, 0, 1, 1)

            search_btn = Gtk.Button(label="Search Online...")
            search_btn.connect("clicked", lambda b: CoverSearchDialog(
                self.get_root(), self._artist, self._album,
                self.album_path, self.cover_img, self._on_set_cover,
            ))
            btn_grid.attach(search_btn, 1, 0, 1, 1)

            if self._beets_lib and self._album_obj:
                reimport_btn = Gtk.Button(label="Re-import...")
                reimport_btn.connect("clicked", self._on_reimport)
                btn_grid.attach(reimport_btn, 0, 1, 1, 1)

                delete_btn = Gtk.Button(label="Delete Album")
                delete_btn.add_css_class("destructive-action")
                delete_btn.connect("clicked", lambda b: self._delete_cb(self._album_obj))
                btn_grid.attach(delete_btn, 1, 1, 1, 1)

            meta_box.append(btn_grid)

        if on_download:
            self.dl_btn = Gtk.Button(label="Download Album")
            self.dl_btn.add_css_class("suggested-action")
            self.dl_btn.set_halign(Gtk.Align.START)
            self.dl_btn.set_margin_top(8)
            self.dl_btn.connect("clicked", lambda b: on_download(self.dl_btn))
            meta_box.append(self.dl_btn)

        info_box.append(meta_box)
        wrapper.append(info_box)

        self._track_list = Gtk.ListBox()
        self._track_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._track_list.connect("row-activated", self._on_track_activated)
        for t in self._tracks:
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.set_margin_start(8)
            hbox.set_margin_end(8)
            hbox.set_margin_top(4)
            hbox.set_margin_bottom(4)

            num = t.get("track", t.get("trackNumber", ""))
            num_lbl = Gtk.Label(label=str(num))
            num_lbl.set_width_chars(2)
            num_lbl.set_xalign(1.0)
            num_lbl.add_css_class("dim-label")
            hbox.append(num_lbl)

            title_lbl = Gtk.Label(label=t.get("title", "?"))
            title_lbl.set_halign(Gtk.Align.START)
            title_lbl.set_hexpand(True)
            title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            title_lbl.set_single_line_mode(True)
            hbox.append(title_lbl)

            artist = t.get("artist", "")
            if artist:
                art_lbl = Gtk.Label(label=artist)
                art_lbl.set_halign(Gtk.Align.END)
                art_lbl.add_css_class("dim-label")
                art_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                art_lbl.set_single_line_mode(True)
                hbox.append(art_lbl)

            playlist_btn = Gtk.MenuButton()
            playlist_btn.set_icon_name("list-add-symbolic")
            playlist_btn.set_tooltip_text("Add to playlist")
            playlist_btn.set_has_frame(False)
            playlist_btn.set_popover(self._build_playlist_popover(t))
            hbox.append(playlist_btn)

            row.set_child(hbox)
            self._track_list.append(row)

        wrapper.append(self._track_list)

        if self._player_bar:
            self._player_bar.set_track_change_cb(self._on_current_track_changed)

    def _build_playlist_popover(self, track):
        file_path = track.get("file_path")
        track_title = track.get("title", "")
        track_artist = track.get("artist", "")

        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        popover.set_child(box)

        def populate():
            while box.get_first_child():
                box.remove(box.get_first_child())

            playlists = load_playlists()

            for pl in playlists:
                already = any(t.file_path == file_path for t in pl.tracks)
                prefix = "✔ " if already else "  "
                btn = Gtk.Button(label=f"{prefix}{pl.name}")
                btn.set_halign(Gtk.Align.FILL)
                btn.add_css_class("flat")

                def toggle(b, pl=pl):
                    log.info(f"[skimmer] toggle {pl.name}: fp={file_path!r}")
                    if not file_path:
                        log.info("[skimmer] toggle: no file_path, skipping")
                        return
                    existing = [t for t in pl.tracks if t.file_path == file_path]
                    if existing:
                        log.info(f"[skimmer] toggle: removing track from '{pl.name}'")
                        pl.tracks[:] = [t for t in pl.tracks if t.file_path != file_path]
                    else:
                        log.info(f"[skimmer] toggle: adding track to '{pl.name}'")
                        pl.tracks.append(PlaylistTrack(
                            file_path=file_path, title=track_title,
                            artist=track_artist, album=self._album,
                        ))
                    pl.last_modified = time.time()
                    save_playlists(playlists)
                    popover.popdown()

                btn.connect("clicked", toggle)
                box.append(btn)

            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            separator.set_margin_top(4)
            separator.set_margin_bottom(4)
            box.append(separator)

            new_btn = Gtk.Button(label="+ New Playlist...")
            new_btn.set_halign(Gtk.Align.FILL)
            new_btn.add_css_class("flat")

            def new_playlist(b):
                parent = self.get_root() if self.get_root() else None
                win = Gtk.Window(title="New Playlist", transient_for=parent, modal=True)
                win.set_default_size(300, 100)
                vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                vbox.set_margin_start(12)
                vbox.set_margin_end(12)
                vbox.set_margin_top(12)
                vbox.set_margin_bottom(12)
                win.set_child(vbox)
                lbl = Gtk.Label(label="Playlist name:")
                vbox.append(lbl)
                entry = Gtk.Entry()
                entry.set_placeholder_text("e.g. Favorites")
                vbox.append(entry)

                def do_create(*a):
                    name = entry.get_text().strip()
                    if not name:
                        return
                    pl = Playlist(name=name)
                    if file_path:
                        pl.tracks.append(PlaylistTrack(
                            file_path=file_path, title=track_title,
                            artist=track_artist, album=self._album,
                        ))
                    pl.last_modified = time.time()
                    playlists.append(pl)
                    save_playlists(playlists)
                    win.close()

                entry.connect("activate", do_create)
                btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                btn_box.set_halign(Gtk.Align.END)
                create_btn = Gtk.Button(label="Create & Add")
                create_btn.add_css_class("suggested-action")
                create_btn.connect("clicked", do_create)
                btn_box.append(create_btn)
                cancel = Gtk.Button(label="Cancel")
                cancel.connect("clicked", lambda w: win.close())
                btn_box.append(cancel)
                vbox.append(btn_box)
                win.present()

            new_btn.connect("clicked", new_playlist)
            box.append(new_btn)

        popover.connect("show", lambda p: populate())
        return popover

    def _on_track_activated(self, listbox, row):
        if not self._player_bar:
            return
        idx = row.get_index()
        if idx < 0 or idx >= len(self._tracks):
            return
        self._clear_track_highlight()
        self._play_track(idx)

    def _play_track(self, idx):
        t = self._tracks[idx]
        file_path = t.get("file_path")
        if not file_path or not os.path.exists(file_path):
            return
        tracks_tuple = [
            (t2.get("file_path", ""), t2.get("title", "?"), t2.get("artist", ""))
            for t2 in self._tracks
        ]
        self._player_bar.play_file(
            file_path,
            title=t.get("title", "?"),
            artist=t.get("artist", ""),
            track_idx=idx,
            tracks=tracks_tuple,
            cover_path=self._cover_path,
        )

    def _on_current_track_changed(self, idx):
        self._track_list.unselect_all()
        if 0 <= idx < len(self._tracks):
            row = self._track_list.get_row_at_index(idx)
            if row:
                self._track_list.select_row(row)

    def _clear_track_highlight(self):
        self._track_list.unselect_all()

    def _on_reimport(self, btn):
        if not self._beets_lib or not self._album_obj:
            return
        file_paths = [
            os.fsdecode(item.path)
            for item in self._album_obj.items()
            if item.path
        ]
        parent = self.get_root() if self.get_root() else None
        dialog = AlbumImportDialog(
            parent=parent,
            config=self.config,
            file_paths=file_paths,
            beets_lib=self._beets_lib,
            album=self._album_obj,
            on_complete=self._reimport_complete_cb,
        )
        dialog.present()

    def _on_set_cover_clicked(self, btn):
        parent = self.get_root() if self.get_root() else None
        dialog = Gtk.FileChooserDialog(
            title="Select Cover Image",
            transient_for=parent,
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

        dialog.connect("response", self._on_cover_dialog_response)
        dialog.present()

    def _on_cover_dialog_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            gf = dialog.get_file()
            dialog.destroy()
            if gf and self.album_path:
                src = gf.get_path()
                dst = os.path.join(self.album_path, "cover.jpg")
                try:
                    import shutil
                    shutil.copy2(src, dst)
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        dst, 200, 200, True
                    )
                    self.cover_img.set_from_pixbuf(pixbuf)
                    self._cover_path = dst
                    if self._on_set_cover:
                        self._on_set_cover(dst)
                except Exception as e:
                    log.info(f"[skimmer] Failed to set cover: {e}")
            return
        dialog.destroy()

    def _load_detail_cover(self, cover_path, cover_url):
        if cover_path and os.path.exists(cover_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    cover_path, 200, 200, True
                )
                self.cover_img.set_from_pixbuf(pixbuf)
                return
            except Exception:
                pass
        if cover_url:
            threading.Thread(
                target=self._load_url, args=(cover_url,), daemon=True
            ).start()
            return
        self._set_img_placeholder()

    def _load_url(self, url):
        try:
            data = urllib.request.urlopen(url, timeout=10).read()
            loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except Exception:
            try:
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(data)
                loader.close()
                pixbuf = loader.get_pixbuf()
            except Exception:
                GLib.idle_add(self._set_img_placeholder)
                return
        scaled = pixbuf.scale_simple(200, 200, GdkPixbuf.InterpType.BILINEAR)
        GLib.idle_add(self.cover_img.set_from_pixbuf, scaled)

    def _set_img_placeholder(self):
        try:
            pixbuf = _make_placeholder_pixbuf(200)
            self.cover_img.set_from_pixbuf(pixbuf)
        except Exception:
            pass


class AlbumImportDialog(Gtk.Window):
    def __init__(self, parent, config, file_paths, beets_lib, album, on_complete=None):
        super().__init__(title="Re-import: Match MusicBrainz", transient_for=parent, modal=True)
        self.set_default_size(600, 500)
        self._config = config
        self._file_paths = [p for p in file_paths if os.path.isfile(p)]
        self._beets_lib = beets_lib
        self._album = album
        self._on_complete = on_complete
        self._candidates = []
        self._selected_match = None

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        self.set_child(vbox)

        header_lbl = Gtk.Label(label="Match album metadata from MusicBrainz", css_classes=["title-2"])
        header_lbl.set_halign(Gtk.Align.START)
        vbox.append(header_lbl)

        count_lbl = Gtk.Label(label=f"{len(self._file_paths)} files in album")
        count_lbl.set_halign(Gtk.Align.START)
        count_lbl.add_css_class("dim-label")
        vbox.append(count_lbl)

        file_scroll = Gtk.ScrolledWindow()
        file_scroll.set_max_content_height(120)
        file_list = Gtk.ListBox()
        file_list.set_selection_mode(Gtk.SelectionMode.NONE)
        for fp in self._file_paths:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label(label=os.path.basename(fp), xalign=0)
            lbl.set_margin_start(6)
            lbl.set_margin_end(6)
            lbl.set_margin_top(2)
            lbl.set_margin_bottom(2)
            lbl.add_css_class("dim-label")
            row.set_child(lbl)
            file_list.append(row)
        file_scroll.set_child(file_list)
        vbox.append(file_scroll)

        self._match_btn = Gtk.Button(label="Find MusicBrainz Matches")
        self._match_btn.add_css_class("suggested-action")
        self._match_btn.set_halign(Gtk.Align.START)
        self._match_btn.connect("clicked", self._on_find_matches)
        vbox.append(self._match_btn)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        vbox.append(self._spinner)

        self._status_lbl = Gtk.Label(label="")
        self._status_lbl.set_halign(Gtk.Align.START)
        self._status_lbl.add_css_class("dim-label")
        vbox.append(self._status_lbl)

        candidates_lbl = Gtk.Label(label="Matches:", css_classes=["heading"])
        candidates_lbl.set_halign(Gtk.Align.START)
        candidates_lbl.set_visible(False)
        vbox.append(candidates_lbl)
        self._candidates_lbl = candidates_lbl

        self._candidates_list = Gtk.ListBox()
        self._candidates_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._candidates_list.connect("selected-rows-changed", self._on_selection_changed)
        cand_scroll = Gtk.ScrolledWindow()
        cand_scroll.set_vexpand(True)
        cand_scroll.set_child(self._candidates_list)
        cand_scroll.set_visible(False)
        vbox.append(cand_scroll)
        self._candidates_scroll = cand_scroll

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_halign(Gtk.Align.END)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda b: self.close())
        btn_box.append(cancel_btn)

        self._import_btn = Gtk.Button(label="Re-tag & Save")
        self._import_btn.add_css_class("suggested-action")
        self._import_btn.set_sensitive(False)
        self._import_btn.connect("clicked", self._on_import)
        btn_box.append(self._import_btn)
        vbox.append(btn_box)

        self.present()

    def _on_find_matches(self, btn):
        btn.set_sensitive(False)
        self._spinner.set_visible(True)
        self._spinner.start()
        self._status_lbl.set_text("Reading files and searching MusicBrainz...")
        self._candidates_list.remove_all()
        self._candidates = []
        self._selected_match = None
        self._import_btn.set_sensitive(False)
        threading.Thread(target=self._search_thread, daemon=True).start()

    def _search_thread(self):
        try:
            import beets.plugins
            beets.plugins.load_plugins()
            from beets import library as beets_lib_mod
            from beets.autotag.match import tag_album
            beets_context.set_music_dir(bytestring_path(
                os.path.expanduser(self._config.get("music_dir", "~/Music"))
            ))
            items = [beets_lib_mod.Item.from_path(p) for p in self._file_paths]
            album_artist = getattr(self._album, "albumartist", None) or ""
            album_name = getattr(self._album, "album", None) or ""
            log.info(f"[skimmer] reimport: searching MusicBrainz for {album_artist!r} - {album_name!r}")
            _, _, proposal = tag_album(items, search_artist=album_artist, search_name=album_name)
            count = len(proposal.candidates) if proposal else 0
            log.info(f"[skimmer] reimport: found {count} candidates")
            GLib.idle_add(self._on_candidates, proposal.candidates if proposal else [])
        except Exception as e:
            log.warning(f"[skimmer] reimport: search error: {e}")
            GLib.idle_add(self._on_search_error, str(e))

    def _on_search_error(self, msg):
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._match_btn.set_sensitive(True)
        self._status_lbl.set_text(f"Search failed: {msg}")

    def _on_candidates(self, candidates):
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._match_btn.set_sensitive(True)
        if not candidates:
            self._status_lbl.set_text("No MusicBrainz matches found. Try selecting different files.")
            return
        self._candidates = list(candidates)
        self._candidates_lbl.set_visible(True)
        self._candidates_scroll.set_visible(True)
        self._candidates_list.remove_all()
        first_radio = None
        for i, match in enumerate(candidates):
            info = match.info
            artist = getattr(info, "artist", "?") or "?"
            album = getattr(info, "album", "?") or "?"
            year = getattr(info, "year", "")
            ntracks = len(getattr(info, "tracks", []) or [])
            year_str = f" ({year})" if year else ""
            label_text = f"{artist}  —  {album}{year_str}  ({ntracks} tracks)"
            row = Gtk.ListBoxRow()
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            hbox.set_margin_start(6)
            hbox.set_margin_end(6)
            hbox.set_margin_top(6)
            hbox.set_margin_bottom(6)
            radio = Gtk.CheckButton()
            if first_radio:
                radio.set_group(first_radio)
            else:
                first_radio = radio
            radio.set_active(i == 0)
            hbox.append(radio)
            lbl = Gtk.Label(label=label_text, xalign=0)
            lbl.set_hexpand(True)
            hbox.append(lbl)
            row.set_child(hbox)
            row._radio = radio
            self._candidates_list.append(row)
        self._candidates_list.select_row(self._candidates_list.get_row_at_index(0))
        self._selected_match = candidates[0]
        self._import_btn.set_sensitive(True)
        self._status_lbl.set_text(f"Found {len(candidates)} candidate{'' if len(candidates) == 1 else 's'}")

    def _on_selection_changed(self, listbox):
        rows = listbox.get_selected_rows()
        if not rows:
            return
        row = rows[0]
        idx = row.get_index()
        if 0 <= idx < len(self._candidates):
            self._selected_match = self._candidates[idx]
            for i in range(len(self._candidates)):
                r = listbox.get_row_at_index(i)
                if hasattr(r, "_radio"):
                    r._radio.set_active(r == row)
            self._import_btn.set_sensitive(True)

    def _on_import(self, btn):
        if not self._selected_match:
            return
        btn.set_sensitive(False)
        self._match_btn.set_sensitive(False)
        self._status_lbl.set_text("Applying metadata and updating beets...")
        self._spinner.set_visible(True)
        self._spinner.start()
        threading.Thread(target=self._import_thread, daemon=True).start()

    def _import_thread(self):
        try:
            match = self._selected_match
            match.apply_metadata()
            for item in match.mapping:
                item.try_write()
            music_dir = os.path.expanduser(self._config.get("music_dir", "~/Music"))
            for db_item in self._album.items():
                try:
                    fpath = os.fsdecode(db_item.path)
                    if not os.path.isabs(fpath):
                        fpath = os.path.join(music_dir, fpath)
                    db_item.read(fpath)
                    db_item.store()
                except Exception as e:
                    log.warning(f"[skimmer] reimport: error updating item: {e}")
            try:
                self._album.albumartist = match.info.artist
                self._album.album = match.info.album
                self._album.store()
            except Exception:
                pass
            GLib.idle_add(self._on_import_done, None)
        except Exception as e:
            GLib.idle_add(self._on_import_done, str(e))

    def _on_import_done(self, error):
        self._spinner.stop()
        self._spinner.set_visible(False)
        if error:
            self._status_lbl.set_text(f"Import failed: {error}")
            self._match_btn.set_sensitive(True)
            return
        self._status_lbl.set_text("Metadata updated successfully!")
        cb = self._on_complete
        self._on_complete = None
        if cb:
            cb()
        GLib.timeout_add(800, self.close)
