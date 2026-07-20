from decimal import Decimal
import os

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from mpris_server import Metadata
from mpris_server.adapters import (
    MprisAdapter,
    PlayState,
)
from mpris_server.events import EventAdapter
from mpris_server.mpris.metadata import MetadataEntries
from mpris_server.server import Server

from .media_integration import MediaIntegration

URI = ["file"]
MIME_TYPES = [
    "audio/flac",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/aac",
    "audio/mp4",
]


class SkimmerAdapter(MprisAdapter):
    def __init__(self, player_bar):
        self._player = player_bar

    def metadata(self):
        idx = self._player._queue_index
        q = self._player._queue
        if not q or idx < 0 or idx >= len(q):
            meta = Metadata()
            meta[MetadataEntries.TRACK_ID] = "/"
            meta[MetadataEntries.TITLE] = ""
            return meta
        path, title, artist = q[idx]
        meta = Metadata()
        meta[MetadataEntries.TRACK_ID] = "/org/mpris/MediaPlayer2/skimmer/track/0"
        meta[MetadataEntries.TITLE] = title or ""
        meta[MetadataEntries.ARTISTS] = [artist] if artist else []
        meta[MetadataEntries.URL] = f"file://{path}"
        cover = self._player._current_cover_path
        if cover and os.path.exists(cover):
            meta[MetadataEntries.ART_URL] = f"file://{cover}"
        ok, dur = self._player._pipeline.query_duration(Gst.Format.TIME)
        if ok and dur > 0:
            meta[MetadataEntries.LENGTH] = dur // 1000
        return meta

    def get_current_position(self):
        ok, pos = self._player._pipeline.query_position(Gst.Format.TIME)
        return (pos // 1000) if ok else 0

    def get_playstate(self):
        if self._player._playing:
            ok, state, pending = self._player._pipeline.get_state(0)
            if state == Gst.State.PAUSED:
                return PlayState.PAUSED
            return PlayState.PLAYING
        return PlayState.PAUSED

    def get_volume(self):
        return Decimal(str(self._player._pipeline.get_property("volume") or 0.0))

    def is_mute(self):
        return self._player._pipeline.get_property("mute") or False

    def can_go_next(self):
        q = self._player._queue
        return bool(q) and self._player._queue_index < len(q) - 1

    def can_go_previous(self):
        q = self._player._queue
        return bool(q) and self._player._queue_index > 0

    def can_play(self):
        return True

    def can_pause(self):
        return True

    def can_seek(self):
        return True

    def can_control(self):
        return True

    def can_quit(self):
        return False

    def can_raise(self):
        return False

    def has_tracklist(self):
        return False

    def get_uri_schemes(self):
        return URI

    def get_mime_types(self):
        return MIME_TYPES

    def get_desktop_entry(self):
        return "skimmer"

    def get_rate(self):
        return Decimal("1.0")

    def get_minimum_rate(self):
        return Decimal("1.0")

    def get_maximum_rate(self):
        return Decimal("1.0")

    def get_shuffle(self):
        return False

    def is_repeating(self):
        return False

    def play(self):
        if not self._player._playing:
            self._player._on_play_pause(None)

    def pause(self):
        if self._player._playing:
            self._player._on_play_pause(None)

    def resume(self):
        if not self._player._playing:
            self._player._on_play_pause(None)

    def stop(self):
        if self._player._playing:
            self._player._on_play_pause(None)
        self._player._pipeline.set_state(Gst.State.NULL)

    def next(self):
        self._player._on_next(None)

    def previous(self):
        self._player._on_prev(None)

    def seek(self, position):
        if self._player._duration > 0:
            self._player._pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                position * 1000,
            )

    def open_uri(self, uri):
        pass


class SkimmerEventAdapter(EventAdapter):
    def on_playpause(self):
        self.emit_player_changes(["PlaybackStatus", "Metadata"])

    def on_title(self):
        self.emit_player_changes(["Metadata", "CanGoNext", "CanGoPrevious", "CanPlay"])

    def on_seek(self, position):
        self.player.Seeked(position)
        self.emit_player_changes(["Position"])


class LinuxMPRIS(MediaIntegration):
    def __init__(self, player_bar):
        super().__init__(player_bar)
        self._adapter = SkimmerAdapter(player_bar)
        self._server = Server("Skimmer", adapter=self._adapter)
        self._events = SkimmerEventAdapter(
            root=self._server.root,
            player=self._server.player,
        )
        self._server.set_event_adapter(self._events)
        self._published = False

        player_bar.set_track_change_cb(self._on_track_changed)
        player_bar._on_play_pause_orig = player_bar._on_play_pause
        player_bar._on_play_pause = self._wrap_play_pause(player_bar)

    def _wrap_play_pause(self, player_bar):
        orig = player_bar._on_play_pause

        def wrapped(btn):
            orig(btn)
            self._events.on_playpause()

        return wrapped

    def _on_track_changed(self, idx):
        if not self._published:
            self._server.publish()
            self._published = True
        self._events.on_playpause()
        self._events.on_title()

    def start(self):
        pass

    def stop(self):
        self._server.unpublish()
