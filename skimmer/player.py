import os

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gtk, GLib, Gst, GdkPixbuf

Gst.init(None)


class PlayerBar(Gtk.Box):
    def __init__(self, config):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.config = config
        self.add_css_class("toolbar")
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        self._pipeline = Gst.ElementFactory.make("playbin", "player")
        self._bus = self._pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_bus_message)
        self._playing = False
        self._duration = 0
        self._position_timer = 0
        self._queue = []
        self._queue_index = -1
        self._track_change_cbs = []
        self._show_album_cb = None
        self._current_cover_path = None

        center_box = Gtk.CenterBox()
        center_box.set_hexpand(True)
        self.append(center_box)

        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        info_box.set_margin_start(4)
        info_box.add_css_class("cursor-pointer")

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", lambda g, n, x, y: self._on_info_clicked())
        info_box.add_controller(gesture)

        self.cover_thumb = Gtk.Image()
        self.cover_thumb.set_pixel_size(28)
        info_box.append(self.cover_thumb)

        song_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.song_label = Gtk.Label(label="")
        self.song_label.set_halign(Gtk.Align.START)
        self.song_label.set_ellipsize(3)
        self.song_label.set_single_line_mode(True)
        self.song_label.add_css_class("caption")
        song_box.append(self.song_label)

        self.artist_label = Gtk.Label(label="")
        self.artist_label.set_halign(Gtk.Align.START)
        self.artist_label.set_ellipsize(3)
        self.artist_label.set_single_line_mode(True)
        self.artist_label.add_css_class("dim-label")
        song_box.append(self.artist_label)

        info_box.append(song_box)
        info_box.set_size_request(180, -1)
        center_box.set_start_widget(info_box)

        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        mid.set_hexpand(True)

        self.prev_btn = Gtk.Button()
        self.prev_btn.set_icon_name("media-skip-backward-symbolic")
        self.prev_btn.add_css_class("flat")
        self.prev_btn.set_tooltip_text("Previous")
        self.prev_btn.connect("clicked", self._on_prev)
        mid.append(self.prev_btn)

        self.play_btn = Gtk.Button()
        self.play_btn.set_icon_name("media-playback-start-symbolic")
        self.play_btn.add_css_class("flat")
        self.play_btn.set_tooltip_text("Play")
        self.play_btn.connect("clicked", self._on_play_pause)
        mid.append(self.play_btn)

        self.next_btn = Gtk.Button()
        self.next_btn.set_icon_name("media-skip-forward-symbolic")
        self.next_btn.add_css_class("flat")
        self.next_btn.set_tooltip_text("Next")
        self.next_btn.connect("clicked", self._on_next)
        mid.append(self.next_btn)

        self.time_current = Gtk.Label(label="0:00")
        self.time_current.add_css_class("dim-label")
        self.time_current.set_width_chars(5)
        mid.append(self.time_current)

        self.progress = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.progress.set_draw_value(False)
        self.progress.set_hexpand(True)
        self.progress.set_size_request(200, -1)
        self.progress.connect("change-value", self._on_seek)
        self.progress.set_sensitive(False)
        mid.append(self.progress)

        self.time_total = Gtk.Label(label="0:00")
        self.time_total.add_css_class("dim-label")
        self.time_total.set_width_chars(5)
        mid.append(self.time_total)

        clamp = Adw.Clamp()
        clamp.set_child(mid)
        clamp.set_maximum_size(600)
        clamp.set_tightening_threshold(400)
        clamp.set_hexpand(True)
        center_box.set_center_widget(clamp)

        end = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        end.set_margin_end(4)

        vol_icon = Gtk.Image.new_from_icon_name("audio-volume-medium-symbolic")
        vol_icon.set_pixel_size(14)
        end.append(vol_icon)

        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_value(50)
        self.volume.set_draw_value(False)
        self.volume.set_size_request(100, -1)
        self.volume.connect("change-value", self._on_volume)
        end.append(self.volume)

        center_box.set_end_widget(end)

        self.set_visible(False)

    def set_track_change_cb(self, cb):
        self._track_change_cbs.append(cb)

    def set_show_album_cb(self, cb):
        self._show_album_cb = cb

    def _on_info_clicked(self):
        if self._show_album_cb:
            self._show_album_cb()

    def _load_cover_image(self, cover_path):
        self._current_cover_path = cover_path
        if cover_path and os.path.exists(cover_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    cover_path, 28, 28, True
                )
                self.cover_thumb.set_from_pixbuf(pixbuf)
                return
            except Exception:
                pass
        self.cover_thumb.set_from_icon_name("audio-x-generic-symbolic")

    def play_file(
        self, path, title=None, artist=None, track_idx=0, tracks=None, cover_path=None
    ):
        if not os.path.exists(path):
            return

        if tracks:
            self._queue = [(p or "", t or "", a or "") for p, t, a in tracks]
            self._queue_index = track_idx
        else:
            self._queue = [(path, title or os.path.basename(path), artist or "")]
            self._queue_index = 0

        uri = f"file://{path}"
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline.set_property("uri", uri)
        self._pipeline.set_state(Gst.State.PLAYING)
        self._playing = True
        self.play_btn.set_icon_name("media-playback-pause-symbolic")
        self.progress.set_sensitive(True)

        self.song_label.set_text(title or os.path.basename(path))
        self.artist_label.set_text(artist or "")

        self._load_cover_image(cover_path)

        if self._position_timer:
            GLib.source_remove(self._position_timer)
        self._position_timer = GLib.timeout_add(500, self._update_position)

        self._notify_track_change()
        self.set_visible(True)

    def _notify_track_change(self):
        for cb in self._track_change_cbs:
            cb(self._queue_index)

    def _on_play_pause(self, btn):
        if not self._playing:
            self._pipeline.set_state(Gst.State.PLAYING)
            self._playing = True
            self.play_btn.set_icon_name("media-playback-pause-symbolic")
            if self._position_timer:
                GLib.source_remove(self._position_timer)
            self._position_timer = GLib.timeout_add(500, self._update_position)
        else:
            self._pipeline.set_state(Gst.State.PAUSED)
            self._playing = False
            self.play_btn.set_icon_name("media-playback-start-symbolic")
            if self._position_timer:
                GLib.source_remove(self._position_timer)
                self._position_timer = 0

    def _on_next(self, btn):
        self._play_queue_index(self._queue_index + 1)

    def _on_prev(self, btn):
        self._play_queue_index(self._queue_index - 1)

    def _play_queue_index(self, idx):
        if not self._queue or idx < 0 or idx >= len(self._queue):
            self._pipeline.set_state(Gst.State.NULL)
            self._playing = False
            self.play_btn.set_icon_name("media-playback-start-symbolic")
            self.progress.set_sensitive(False)
            self.progress.set_value(0)
            self.time_current.set_text("0:00")
            self.time_total.set_text("0:00")
            self.song_label.set_text("")
            self.artist_label.set_text("")
            self.cover_thumb.set_from_icon_name("audio-x-generic-symbolic")
            self.set_visible(False)
            return
        self._queue_index = idx
        path, title, artist = self._queue[self._queue_index]
        self.play_file(
            path,
            title,
            artist,
            track_idx=idx,
            tracks=list(self._queue),
            cover_path=self._current_cover_path,
        )

    def _on_seek(self, scale, scroll, value):
        if self._duration > 0:
            seek_ns = int(value / 100 * self._duration)
            self._pipeline.seek_simple(
                Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, seek_ns
            )

    def _on_volume(self, scale, scroll, value):
        self._pipeline.set_property("volume", value / 100)

    def _update_position(self):
        if not self._playing:
            return GLib.SOURCE_CONTINUE

        ok, state, pending = self._pipeline.get_state(0)
        if state != Gst.State.PLAYING:
            return GLib.SOURCE_CONTINUE

        ok, pos = self._pipeline.query_position(Gst.Format.TIME)
        if ok:
            ok, dur = self._pipeline.query_duration(Gst.Format.TIME)
            if ok:
                self._duration = dur
                percent = (pos / dur) * 100 if dur > 0 else 0
                self.progress.set_value(percent)
                self.time_current.set_text(self._format_ns(pos))
                self.time_total.set_text(self._format_ns(dur))

        return GLib.SOURCE_CONTINUE

    def _on_bus_message(self, bus, msg):
        if msg.type == Gst.MessageType.EOS:
            self._on_next(None)
        elif msg.type == Gst.MessageType.ERROR:
            err, debug = msg.parse_error()
            print(f"[player] Error: {err}")
            self._on_next(None)

    @staticmethod
    def _format_ns(ns):
        total_sec = ns // 1000000000
        m = total_sec // 60
        s = total_sec % 60
        return f"{m}:{s:02d}"
