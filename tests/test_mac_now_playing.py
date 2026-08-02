import sys
import types

from skimmer.mac_now_playing import MacNowPlaying


class FakeCommand:
    def __init__(self):
        self.enabled = False
        self.handler = None

    def __call__(self):
        return self

    def setEnabled_(self, v):
        self.enabled = v

    def addTargetWithHandler_(self, h):
        self.handler = h
        return h


class FakeCommandCenter:
    _center = None

    def __init__(self):
        self.playCommand = FakeCommand()
        self.pauseCommand = FakeCommand()
        self.togglePlayPauseCommand = FakeCommand()
        self.nextTrackCommand = FakeCommand()
        self.previousTrackCommand = FakeCommand()
        self.changePlaybackPositionCommand = FakeCommand()

    @classmethod
    def sharedCommandCenter(cls):
        if cls._center is None:
            cls._center = FakeCommandCenter()
        return cls._center


class ExplodingCommandCenter:
    @classmethod
    def sharedCommandCenter(cls):
        raise RuntimeError("boom")


class FakeNowPlayingCenter:
    _center = None

    def __init__(self):
        self.info = "unset"

    @classmethod
    def defaultCenter(cls):
        if cls._center is None:
            cls._center = FakeNowPlayingCenter()
        return cls._center

    def setNowPlayingInfo_(self, info):
        self.info = info


class FakeArtwork:
    def __init__(self, image=None):
        self.image = image

    @classmethod
    def alloc(cls):
        return cls()

    def initWithImage_(self, image):
        self.image = image
        return self


class FakeNSImage:
    def __init__(self, path=None):
        self.path = path

    @classmethod
    def alloc(cls):
        return cls()

    def initWithContentsOfFile_(self, path):
        self.path = path
        return self


def make_fake_mp():
    mp = types.ModuleType("MediaPlayer")
    setattr(mp, "MPMediaItemPropertyTitle", "title")
    setattr(mp, "MPMediaItemPropertyArtist", "artist")
    setattr(mp, "MPMediaItemPropertyPlaybackDuration", "duration")
    setattr(mp, "MPMediaItemPropertyArtwork", "artwork")
    setattr(mp, "MPNowPlayingInfoPropertyElapsedPlaybackTime", "elapsed")
    setattr(mp, "MPNowPlayingInfoPropertyPlaybackRate", "rate")
    setattr(mp, "MPNowPlayingInfoPropertyMediaType", "mediatype")
    setattr(mp, "MPNowPlayingInfoMediaTypeAudio", 1)
    setattr(mp, "MPRemoteCommandHandlerStatusSuccess", 0)
    setattr(mp, "MPRemoteCommandHandlerStatusCommandFailed", 1)
    setattr(mp, "MPNowPlayingInfoCenter", FakeNowPlayingCenter)
    setattr(mp, "MPRemoteCommandCenter", FakeCommandCenter)
    setattr(mp, "MPMediaItemArtwork", FakeArtwork)
    return mp


def make_fake_appkit():
    appkit = types.ModuleType("AppKit")
    setattr(appkit, "NSImage", FakeNSImage)
    return appkit


class FakePipeline:
    def __init__(self, dur_ns=120_000_000_000, pos_ns=0):
        self._dur = dur_ns
        self._pos = pos_ns
        self.seeks = []

    def query_duration(self, fmt):
        return True, self._dur

    def query_position(self, fmt):
        return True, self._pos

    def seek_simple(self, fmt, flags, ns):
        self.seeks.append(ns)


class FakePlayer:
    def __init__(self):
        self._queue = []
        self._queue_index = -1
        self._playing = False
        self._current_cover_path: str | None = None
        self._duration = 0
        self._pipeline = FakePipeline()
        self._track_cbs = []
        self._state_cbs = []
        self._pos_cbs = []
        self.commands = []

    def set_track_change_cb(self, cb):
        self._track_cbs.append(cb)

    def set_state_change_cb(self, cb):
        self._state_cbs.append(cb)

    def set_position_cb(self, cb):
        self._pos_cbs.append(cb)

    def _on_play_pause(self, btn):
        self.commands.append("toggle")

    def _on_next(self, btn):
        self.commands.append("next")

    def _on_prev(self, btn):
        self.commands.append("prev")


def make_integration(player):
    integ = MacNowPlaying(player)
    integ._mp = make_fake_mp()
    integ._appkit = make_fake_appkit()
    return integ


def set_queue(player, path="/m/a.mp3", title="Title", artist="Artist"):
    player._queue = [(path, title, artist)]
    player._queue_index = 0


def test_start_registers_commands_and_hooks(monkeypatch):
    monkeypatch.setitem(sys.modules, "MediaPlayer", make_fake_mp())
    monkeypatch.setitem(sys.modules, "AppKit", make_fake_appkit())
    player = FakePlayer()
    integ = MacNowPlaying(player)
    integ.start()

    center = FakeCommandCenter.sharedCommandCenter()
    for name in (
        "playCommand",
        "pauseCommand",
        "togglePlayPauseCommand",
        "nextTrackCommand",
        "previousTrackCommand",
        "changePlaybackPositionCommand",
    ):
        cmd = getattr(center, name)()
        assert cmd.enabled
        assert cmd.handler is not None
    assert len(player._track_cbs) == 1
    assert len(player._state_cbs) == 1
    assert len(player._pos_cbs) == 1


def test_start_non_fatal_when_command_center_raises(monkeypatch):
    mp = types.ModuleType("MediaPlayer")
    setattr(mp, "MPRemoteCommandCenter", ExplodingCommandCenter)
    monkeypatch.setitem(sys.modules, "MediaPlayer", mp)
    monkeypatch.setitem(sys.modules, "AppKit", make_fake_appkit())
    player = FakePlayer()
    integ = MacNowPlaying(player)
    integ.start()
    assert len(player._track_cbs) == 0
    assert len(player._state_cbs) == 0
    assert len(player._pos_cbs) == 0


def test_handle_command_play():
    player = FakePlayer()
    integ = make_integration(player)
    player._playing = False
    integ._handle_command("playCommand")
    assert player.commands == ["toggle"]
    player.commands.clear()
    player._playing = True
    integ._handle_command("playCommand")
    assert player.commands == []


def test_handle_command_pause():
    player = FakePlayer()
    integ = make_integration(player)
    player._playing = True
    integ._handle_command("pauseCommand")
    assert player.commands == ["toggle"]
    player.commands.clear()
    player._playing = False
    integ._handle_command("pauseCommand")
    assert player.commands == []


def test_handle_command_toggle_next_prev():
    player = FakePlayer()
    integ = make_integration(player)
    integ._handle_command("togglePlayPauseCommand")
    integ._handle_command("nextTrackCommand")
    integ._handle_command("previousTrackCommand")
    assert player.commands == ["toggle", "next", "prev"]


def test_on_change_position_seeks():
    player = FakePlayer()
    integ = make_integration(player)
    player._duration = 120_000_000_000

    class Event:
        positionTime = 30.0

    assert integ._on_change_position(Event()) == 0
    assert player._pipeline.seeks == [30_000_000_000]


def test_build_base():
    player = FakePlayer()
    set_queue(player)
    integ = make_integration(player)
    info = integ._build_base()
    assert info["title"] == "Title"
    assert info["artist"] == "Artist"
    assert info["mediatype"] == 1
    assert info["duration"] == 120.0


def test_build_base_with_artwork(tmp_path):
    player = FakePlayer()
    set_queue(player)
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"jpg")
    player._current_cover_path = str(cover)
    integ = make_integration(player)
    info = integ._build_base()
    assert isinstance(info["artwork"], FakeArtwork)


def test_on_state_changed():
    player = FakePlayer()
    set_queue(player)
    integ = make_integration(player)
    integ._on_state_changed(True)
    center = FakeNowPlayingCenter.defaultCenter()
    assert center.info["rate"] == 1.0
    assert center.info["elapsed"] == 0.0
    integ._on_state_changed(False)
    assert center.info["rate"] == 0.0


def test_on_position():
    player = FakePlayer()
    set_queue(player)
    player._playing = True
    integ = make_integration(player)
    integ._on_position(5_000_000_000, 120_000_000_000)
    center = FakeNowPlayingCenter.defaultCenter()
    assert center.info["elapsed"] == 5.0
    assert center.info["rate"] == 1.0
    assert center.info["duration"] == 120.0


def test_stop_clears_info():
    player = FakePlayer()
    integ = make_integration(player)
    center = FakeNowPlayingCenter.defaultCenter()
    integ._push({"a": 1})
    assert center.info == {"a": 1}
    integ.stop()
    assert center.info is None
