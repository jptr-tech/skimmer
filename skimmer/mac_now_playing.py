import logging
import os
from typing import Any

from skimmer.gst import Gst

from .media_integration import MediaIntegration

log = logging.getLogger(__name__)

_COMMAND_NAMES = (
    "playCommand",
    "pauseCommand",
    "togglePlayPauseCommand",
    "nextTrackCommand",
    "previousTrackCommand",
)


class MacNowPlaying(MediaIntegration):
    """macOS Now Playing / media keys via MPNowPlayingInfoCenter + MPRemoteCommandCenter."""

    def __init__(self, player_bar):
        super().__init__(player_bar)
        self._mp: Any = None
        self._appkit: Any = None
        self._handlers = []
        self._artwork = None
        self._artwork_path = None

    def start(self):
        try:
            import AppKit  # pyright: ignore[reportMissingImports]
            import MediaPlayer  # pyright: ignore[reportMissingImports]
        except ImportError:
            log.warning("pyobjc MediaPlayer not available — Now Playing disabled")
            return
        self._mp = MediaPlayer
        self._appkit = AppKit
        try:
            self._register_commands()
            player = self._player
            player.set_track_change_cb(self._on_track_changed)
            player.set_state_change_cb(self._on_state_changed)
            player.set_position_cb(self._on_position)
        except Exception:
            log.warning("Failed to start Now Playing", exc_info=True)
            self._mp = None
            self._appkit = None
            return
        log.info("macOS Now Playing integration started")

    def stop(self):
        mp = self._mp
        if mp is None:
            return
        try:
            mp.MPNowPlayingInfoCenter.defaultCenter().setNowPlayingInfo_(None)
        except Exception as e:
            log.warning("Failed to clear Now Playing: %s", e)

    def _register_commands(self):
        mp = self._mp
        cc = mp.MPRemoteCommandCenter.sharedCommandCenter()
        for name in _COMMAND_NAMES:
            cmd = getattr(cc, name)()
            cmd.setEnabled_(True)
            token = cmd.addTargetWithHandler_(self._make_handler(name))
            self._handlers.append(token)
        change = cc.changePlaybackPositionCommand()
        change.setEnabled_(True)
        token = change.addTargetWithHandler_(self._on_change_position)
        self._handlers.append(token)

    def _make_handler(self, name):
        def handler(event):
            self._handle_command(name)
            return self._mp.MPRemoteCommandHandlerStatusSuccess

        return handler

    def _handle_command(self, name):
        player = self._player
        if name == "playCommand":
            if not player._playing:
                player._on_play_pause(None)
        elif name == "pauseCommand":
            if player._playing:
                player._on_play_pause(None)
        elif name == "togglePlayPauseCommand":
            player._on_play_pause(None)
        elif name == "nextTrackCommand":
            player._on_next(None)
        elif name == "previousTrackCommand":
            player._on_prev(None)

    def _on_change_position(self, event):
        mp = self._mp
        player = self._player
        try:
            seconds = float(event.positionTime)
        except Exception:
            return mp.MPRemoteCommandHandlerStatusCommandFailed
        if player._duration > 0:
            player._pipeline.seek_simple(
                Gst.Format.TIME,
                Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                int(seconds * 1e9),
            )
        return mp.MPRemoteCommandHandlerStatusSuccess

    def _current(self):
        player = self._player
        idx = player._queue_index
        q = player._queue
        if not q or idx < 0 or idx >= len(q):
            return None
        return q[idx]

    def _make_artwork(self):
        player = self._player
        cover = player._current_cover_path
        if not cover or not os.path.exists(cover):
            return None
        if self._artwork is not None and self._artwork_path == cover:
            return self._artwork
        try:
            image = self._appkit.NSImage.alloc().initWithContentsOfFile_(cover)
            if image:
                self._artwork = self._mp.MPMediaItemArtwork.alloc().initWithImage_(image)
                self._artwork_path = cover
                return self._artwork
        except Exception as e:
            log.warning("Failed to load artwork %s: %s", cover, e)
        self._artwork = None
        self._artwork_path = None
        return None

    def _build_base(self):
        mp = self._mp
        player = self._player
        cur = self._current()
        if cur is None:
            return None
        path, title, artist = cur
        info = {
            mp.MPMediaItemPropertyTitle: title or os.path.basename(path),
            mp.MPMediaItemPropertyArtist: artist or "",
            mp.MPNowPlayingInfoPropertyMediaType: mp.MPNowPlayingInfoMediaTypeAudio,
        }
        ok, dur = player._pipeline.query_duration(Gst.Format.TIME)
        if ok and dur > 0:
            info[mp.MPMediaItemPropertyPlaybackDuration] = dur / 1e9
        artwork = self._make_artwork()
        if artwork is not None:
            info[mp.MPMediaItemPropertyArtwork] = artwork
        return info

    def _push(self, info):
        try:
            self._mp.MPNowPlayingInfoCenter.defaultCenter().setNowPlayingInfo_(info)
        except Exception as e:
            log.warning("Failed to update Now Playing: %s", e)

    def _on_track_changed(self, idx):
        if self._mp is None:
            return
        self._push(self._build_base())

    def _on_state_changed(self, playing):
        mp = self._mp
        if mp is None:
            return
        info = self._build_base()
        if info is None:
            return
        info[mp.MPNowPlayingInfoPropertyPlaybackRate] = 1.0 if playing else 0.0
        ok, pos = self._player._pipeline.query_position(Gst.Format.TIME)
        if ok:
            info[mp.MPNowPlayingInfoPropertyElapsedPlaybackTime] = pos / 1e9
        self._push(info)

    def _on_position(self, pos_ns, dur_ns):
        mp = self._mp
        if mp is None or not self._player._playing:
            return
        info = self._build_base()
        if info is None:
            return
        info[mp.MPNowPlayingInfoPropertyPlaybackRate] = 1.0
        info[mp.MPNowPlayingInfoPropertyElapsedPlaybackTime] = pos_ns / 1e9
        if dur_ns > 0:
            info[mp.MPMediaItemPropertyPlaybackDuration] = dur_ns / 1e9
        self._push(info)
