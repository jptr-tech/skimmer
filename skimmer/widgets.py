import json
import os
import threading
import urllib.parse
import urllib.request

import gi
gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gtk, Gdk, GLib, Pango, GdkPixbuf


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

    def __init__(self, artist, album, year, cover_path=None, cover_url=None, data=None, size=COVER_SIZE):
        super().__init__()
        self.artist = artist
        self.album = album
        self.year = year
        self.cover_path = cover_path
        self.cover_url = cover_url
        self.data = data

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

        self._load_cover()
        self._apply_size()

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

        print(f"[skimmer] Opening cover search dialog for {artist} - {album}")

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
        print(f"[skimmer] Cover search: '{term}'")
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
            print(f"[skimmer] iTunes search error: {e}")

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
            print(f"[skimmer] Deezer search error: {e}")

        GLib.idle_add(self._show_results, list(combined.values()))

    def _show_error(self, msg):
        print(f"[skimmer] Cover search error displayed: {msg}")
        self.status_lbl.set_text(msg)

    def _show_results(self, results):
        if not results:
            self.status_lbl.set_text("No results found")
            print("[skimmer] Cover search: 0 results")
            return
        source_counts = {}
        for _, _, _, _, src in results:
            source_counts[src] = source_counts.get(src, 0) + 1
        summary = ", ".join(f"{src}: {n}" for src, n in source_counts.items())
        self.status_lbl.set_text(f"{len(results)} results ({summary})")
        print(f"[skimmer] Cover search: {len(results)} results ({summary})")
        for title, artist, thumb_url, full_url, source in results:
            label = f"[{source}] {title}"
            result_widget = CoverSearchResult(
                label, artist, thumb_url, full_url,
                on_select=self._on_result_selected,
            )
            self.flowbox.append(result_widget)

    def _on_result_selected(self, result):
        url = result.get_full_url()
        print(f"[skimmer] Cover selected: {url}")
        self.status_lbl.set_text("Downloading cover...")
        threading.Thread(target=self._download_thread, args=(url,), daemon=True).start()

    def _download_thread(self, url):
        print(f"[skimmer] Downloading cover from {url}")
        try:
            data = urllib.request.urlopen(url, timeout=15).read()
            print(f"[skimmer] Downloaded {len(data)} bytes")
        except Exception as e:
            print(f"[skimmer] Download failed: {e}")
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
                print(f"[skimmer] Invalid image data")
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
                 player_bar=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.config = config
        self.album_path = album_path
        self._on_set_cover = on_set_cover
        self._cover_path = cover_path
        self._artist = artist
        self._album = album
        self._on_back_cb = on_back
        self._player_bar = player_bar
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
            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            btn_row.set_margin_top(4)
            set_cover_btn = Gtk.Button(label="Set Album Art...")
            set_cover_btn.connect("clicked", self._on_set_cover_clicked)
            btn_row.append(set_cover_btn)

            search_btn = Gtk.Button(label="Search Online...")
            search_btn.connect("clicked", lambda b: CoverSearchDialog(
                self.get_root(), self._artist, self._album,
                self.album_path, self.cover_img, self._on_set_cover,
            ))
            btn_row.append(search_btn)
            meta_box.append(btn_row)

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

            row.set_child(hbox)
            self._track_list.append(row)

        wrapper.append(self._track_list)

        if self._player_bar:
            self._player_bar.set_track_change_cb(self._on_current_track_changed)

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
                    print(f"[skimmer] Failed to set cover: {e}")
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
