import shutil
import time

import pytest
from gi.repository import GLib

from skimmer import synccache
from skimmer.playlist import Playlist, PlaylistTrack, export_m3u8
from skimmer.worker import ProcessingManager, Task, TaskCancelled

SAMPLE_CONFIG = {
    "temp_dir": "/tmp/skimmer",
    "music_dir": "/tmp/music",
    "ytdlp_format": "bestaudio/best",
    "ytdlp_audio_format": "mp3",
    "mount_path": "/tmp/mount",
    "beets_lib": "",
    "podcasts_dir": "/nonexistent-podcasts",
}


def pump_idle():
    """Run pending GLib idle callbacks."""
    ctx = GLib.main_context_default()
    while ctx.pending():
        ctx.iteration(False)


class TestTask:
    def test_initial_state(self):
        task = Task("download", "Test Album", {"key": "val"})
        assert task.type == "download"
        assert task.title == "Test Album"
        assert task.data == {"key": "val"}
        assert task.status == "pending"
        assert task.progress == 0.0
        assert task.error is None
        assert len(task.id) == 8

    def test_different_types(self):
        for t in ("download", "import", "sync"):
            task = Task(t, "title", {})
            assert task.type == t

    def test_updated_signal(self):
        task = Task("download", "t", {})
        results = []

        def handler(t, status, progress, message):
            results.append((status, progress, message))

        task.connect("updated", handler)
        task.emit("updated", "running", 0.5, "working")
        assert len(results) == 1
        assert results[0] == ("running", 0.5, "working")


class TestProcessingManager:
    def test_init(self):
        mgr = ProcessingManager(SAMPLE_CONFIG)
        assert mgr.tasks == []
        assert mgr.config == SAMPLE_CONFIG

    def test_add_task_returns_task(self):
        mgr = ProcessingManager(SAMPLE_CONFIG)
        task = mgr.add_task("download", "My Album", {"artist": "X"})
        assert isinstance(task, Task)
        assert task in mgr.tasks
        assert mgr.tasks == [task]

    def test_add_fires_signal(self):
        mgr = ProcessingManager(SAMPLE_CONFIG)
        fired = []
        mgr.connect("task-added", lambda m, t: fired.append(t))
        task = mgr.add_task("sync", "Sync", {})
        pump_idle()
        assert len(fired) == 1
        assert fired[0] is task

    def test_add_to_queue(self):
        """Task is consumed by worker thread immediately — check it was queued via task presence."""
        mgr = ProcessingManager(SAMPLE_CONFIG)
        task = mgr.add_task("download", "Q", {})
        assert task in mgr.tasks

    def test_remove_task(self):
        mgr = ProcessingManager(SAMPLE_CONFIG)
        task = mgr.add_task("download", "T", {"artist": "X"})
        mgr.remove_task(task)
        pump_idle()
        assert task not in mgr.tasks

    def test_remove_fires_signal(self):
        mgr = ProcessingManager(SAMPLE_CONFIG)
        task = mgr.add_task("download", "T", {"artist": "X"})
        pump_idle()
        fired = []
        mgr.connect("task-removed", lambda m, t: fired.append(t))
        mgr.remove_task(task)
        pump_idle()
        assert len(fired) == 1
        assert fired[0] is task


class TestExportM3u8:
    def test_writes_relative_paths(self, tmp_path):
        out = tmp_path / "Playlists" / "P.m3u8"
        out.parent.mkdir()
        pl = Playlist(
            name="P",
            tracks=[
                PlaylistTrack(
                    file_path=str(tmp_path / "Music" / "Artist" / "Album" / "a.mp3"),
                    title="A",
                    artist="Artist",
                    duration=180,
                ),
                PlaylistTrack(
                    file_path=str(tmp_path / "Music" / ".Spotify" / "Sp" / "Alb" / "s.mp3"),
                    title="S",
                    artist="Sp",
                    duration=90,
                ),
            ],
        )
        export_m3u8(pl, str(out), paths=[t.file_path for t in pl.tracks])
        content = out.read_text()
        assert "../Music/Artist/Album/a.mp3" in content
        assert "../Music/.Spotify/Sp/Alb/s.mp3" in content
        assert "#EXTINF:180,Artist - A" in content

    def test_keeps_relative_entries_untouched(self, tmp_path):
        out = tmp_path / "P.m3u8"
        pl = Playlist(
            name="P",
            tracks=[PlaylistTrack(file_path="../Music/Artist/a.mp3", title="A")],
        )
        export_m3u8(pl, str(out))
        assert "../Music/Artist/a.mp3" in out.read_text()

    def test_defaults_to_file_path(self, tmp_path):
        out = tmp_path / "P.m3u8"
        pl = Playlist(
            name="P",
            tracks=[PlaylistTrack(file_path=str(tmp_path / "Music" / "A" / "t.mp3"), title="T")],
        )
        export_m3u8(pl, str(out))
        assert "Music/A/t.mp3" in out.read_text()


class TestSyncSpotifyIgnore:
    def _make_mgr(self, tmp_path, monkeypatch, spotify_paths):
        music = tmp_path / "Music"
        mount = tmp_path / "mount"
        (music / "NormalA" / "Album").mkdir(parents=True)
        (music / "SpotA" / "Alb").mkdir(parents=True)
        open(music / "NormalA" / "Album" / "file.mp3", "wb").write(b"n")
        open(music / "SpotA" / "Alb" / "s.mp3", "wb").write(b"s")

        stale = mount / "Music" / "SpotA" / "Alb" / "s.mp3"
        stale.parent.mkdir(parents=True)
        open(stale, "wb").write(b"stale")

        config = dict(SAMPLE_CONFIG)
        config["music_dir"] = str(music)
        config["mount_path"] = str(mount)
        config["podcasts_dir"] = str(tmp_path / "no-podcasts")

        synccache.save_cache(str(mount / ".skimmer-cache.json"), str(music), {})

        monkeypatch.setattr("skimmer.worker.load_playlists", lambda: [])
        monkeypatch.setattr("skimmer.worker.save_playlists", lambda playlists: None)

        mgr = ProcessingManager(config)
        mgr._spotify_paths = lambda: {str(music / p) for p in spotify_paths}
        return mgr, music, mount

    def test_spotify_imports_go_to_ignored_folder(self, tmp_path, monkeypatch):
        mgr, music, mount = self._make_mgr(tmp_path, monkeypatch, ["SpotA/Alb/s.mp3"])
        task = Task("sync", "Sync", {})
        mgr._do_sync(task)

        assert (mount / "Music" / "NormalA" / "Album" / "file.mp3").is_file()
        assert (mount / "Music" / ".Spotify" / "SpotA" / "Alb" / "s.mp3").is_file()
        assert (mount / "Music" / ".Spotify" / "database.ignore").is_file()
        assert not (mount / "Music" / "SpotA" / "Alb" / "s.mp3").exists()
        assert task.error is None

    def test_non_spotify_files_unaffected(self, tmp_path, monkeypatch):
        mgr, music, mount = self._make_mgr(tmp_path, monkeypatch, [])
        task = Task("sync", "Sync", {})
        mgr._do_sync(task)

        assert (mount / "Music" / "NormalA" / "Album" / "file.mp3").is_file()
        assert (mount / "Music" / "SpotA" / "Alb" / "s.mp3").is_file()
        assert not (mount / "Music" / ".Spotify").exists()

    def test_device_paths_mapping(self, tmp_path, monkeypatch):
        mgr, music, mount = self._make_mgr(tmp_path, monkeypatch, ["SpotA/Alb/s.mp3"])
        pl = Playlist(
            name="P",
            tracks=[
                PlaylistTrack(file_path=str(music / "NormalA" / "Album" / "file.mp3")),
                PlaylistTrack(file_path=str(music / "SpotA" / "Alb" / "s.mp3")),
            ],
        )
        paths = mgr._device_paths(pl, {"SpotA/Alb/s.mp3"})
        assert paths[0] == str(mount / "Music" / "NormalA" / "Album" / "file.mp3")
        assert paths[1] == str(mount / "Music" / ".Spotify" / "SpotA" / "Alb" / "s.mp3")

    def test_first_sync_copies_nested_dirs(self, tmp_path, monkeypatch):
        music = tmp_path / "Music"
        mount = tmp_path / "mount"
        (music / "Artist" / "Album").mkdir(parents=True)
        (music / "Artist" / "Another").mkdir(parents=True)
        open(music / "Artist" / "Album" / "t1.mp3", "wb").write(b"a")
        open(music / "Artist" / "Album" / "t2.flac", "wb").write(b"b")
        open(music / "Artist" / "Another" / "t3.mp3", "wb").write(b"c")

        config = dict(SAMPLE_CONFIG)
        config["music_dir"] = str(music)
        config["mount_path"] = str(mount)
        config["podcasts_dir"] = str(tmp_path / "no-podcasts")

        monkeypatch.setattr("skimmer.worker.load_playlists", lambda: [])
        monkeypatch.setattr("skimmer.worker.save_playlists", lambda playlists: None)

        mgr = ProcessingManager(config)
        mgr._spotify_paths = lambda: set()

        task = Task("sync", "Sync", {})
        mgr._do_sync(task)

        assert (mount / "Music" / "Artist" / "Album" / "t1.mp3").is_file()
        assert (mount / "Music" / "Artist" / "Album" / "t2.flac").is_file()
        assert (mount / "Music" / "Artist" / "Another" / "t3.mp3").is_file()
        assert (mount / ".skimmer-cache.json").is_file()
        assert task.error is None

    def test_sync_uses_scanner_written_device_cache(self, tmp_path, monkeypatch):
        music = tmp_path / "Music"
        mount = tmp_path / "mount"
        (music / "A").mkdir(parents=True)
        open(music / "A" / "t.mp3", "wb").write(b"x")

        config = dict(SAMPLE_CONFIG)
        config["music_dir"] = str(music)
        config["mount_path"] = str(mount)
        config["podcasts_dir"] = str(tmp_path / "no-podcasts")

        synccache.save_cache(
            str(mount / ".skimmer-cache.json"),
            str(mount / "Music"),
            {"gone.flac": (0, 0, None)},
        )

        monkeypatch.setattr("skimmer.worker.load_playlists", lambda: [])
        monkeypatch.setattr("skimmer.worker.save_playlists", lambda playlists: None)

        mgr = ProcessingManager(config)
        mgr._spotify_paths = lambda: set()

        task = Task("sync", "Sync", {})
        mgr._do_sync(task)

        assert (mount / "Music" / "A" / "t.mp3").is_file()
        assert not (mount / "Music" / "gone.flac").exists()
        import json

        data = json.loads((mount / ".skimmer-cache.json").read_text())
        assert data["music_dir"] == str(music)
        assert "A/t.mp3" in data["files"]
        assert task.error is None


class TestCancel:
    def test_cancel_task_sets_flag_and_invokes_cb(self):
        mgr = ProcessingManager(SAMPLE_CONFIG)
        task = Task("sync", "Sync", {})
        called = []
        task.cancel_cb = lambda: called.append(True)
        mgr.cancel_task(task)
        assert task.cancelled
        assert called == [True]

    def test_cancel_before_start(self, tmp_path):
        config = dict(SAMPLE_CONFIG)
        config["music_dir"] = str(tmp_path / "missing-music")
        mgr = ProcessingManager(config)
        task = mgr.add_task("sync", "Sync music to device", {})
        mgr.cancel_task(task)
        deadline = time.time() + 5
        while task.status in ("pending", "running") and time.time() < deadline:
            time.sleep(0.01)
        assert task.status == "cancelled"

    def test_ytdlp_hook_raises_when_cancelled(self):
        mgr = ProcessingManager(SAMPLE_CONFIG)
        task = Task("download", "Album", {})
        task.cancelled = True
        with pytest.raises(TaskCancelled):
            mgr._ytdlp_hook({"status": "downloading"}, task, 2, {})

    def test_download_cleanup_on_cancel(self, tmp_path, monkeypatch):
        class FakeYDL:
            def __init__(self, opts=None):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def download(self, urls):
                raise TaskCancelled("Download cancelled")

        monkeypatch.setattr("skimmer.worker.yt_dlp.YoutubeDL", FakeYDL)

        config = dict(SAMPLE_CONFIG)
        config["temp_dir"] = str(tmp_path / "tmp")
        mgr = ProcessingManager(config)
        task = Task(
            "download",
            "Album",
            {
                "artist": "Artist",
                "title": "Album",
                "tracks": [{"videoId": "abc", "title": "T1"}],
            },
        )
        album_dir = tmp_path / "tmp" / "Artist - Album"
        with pytest.raises(TaskCancelled):
            mgr._do_download(task)
        assert not album_dir.exists()

    def test_import_cancel_cleans_temp_dir(self, tmp_path):
        album_dir = tmp_path / "album"
        album_dir.mkdir()
        open(album_dir / "a.mp3", "wb").write(b"x")
        config = dict(SAMPLE_CONFIG)
        config["music_dir"] = str(tmp_path / "music")
        mgr = ProcessingManager(config)
        task = Task("import", "Import", {"album_dir": str(album_dir)})
        task.cancelled = True
        with pytest.raises(TaskCancelled):
            mgr._do_import(task)
        assert not album_dir.exists()

    def test_sync_cancel_persists_partial_cache(self, tmp_path, monkeypatch):
        music = tmp_path / "Music"
        mount = tmp_path / "mount"
        music.mkdir()
        open(music / "a.mp3", "wb").write(b"aaa")
        open(music / "b.mp3", "wb").write(b"bbb")
        open(music / "c.mp3", "wb").write(b"ccc")

        config = dict(SAMPLE_CONFIG)
        config["music_dir"] = str(music)
        config["mount_path"] = str(mount)
        config["podcasts_dir"] = str(tmp_path / "no-podcasts")

        monkeypatch.setattr("skimmer.worker.load_playlists", lambda: [])
        monkeypatch.setattr("skimmer.worker.save_playlists", lambda playlists: None)

        mgr = ProcessingManager(config)
        mgr._spotify_paths = lambda: set()

        task = Task("sync", "Sync", {})
        real_copy = shutil.copy2

        def fake_copy(src, dst, *a, **k):
            real_copy(src, dst, *a, **k)
            task.cancelled = True

        monkeypatch.setattr(shutil, "copy2", fake_copy)

        with pytest.raises(TaskCancelled):
            mgr._do_sync(task)

        assert (mount / "Music" / "a.mp3").is_file()
        assert not (mount / "Music" / "b.mp3").exists()
        assert not (mount / "Music" / "c.mp3").exists()

        import json

        data = json.loads((mount / ".skimmer-cache.json").read_text())
        assert data["music_dir"] == str(music)
        assert list(data["files"]) == ["a.mp3"]

    def test_sync_cancel_removes_partial_file_on_failure(self, tmp_path, monkeypatch):
        music = tmp_path / "Music"
        mount = tmp_path / "mount"
        music.mkdir()
        open(music / "a.mp3", "wb").write(b"aaa")

        config = dict(SAMPLE_CONFIG)
        config["music_dir"] = str(music)
        config["mount_path"] = str(mount)
        config["podcasts_dir"] = str(tmp_path / "no-podcasts")

        monkeypatch.setattr("skimmer.worker.load_playlists", lambda: [])
        monkeypatch.setattr("skimmer.worker.save_playlists", lambda playlists: None)

        mgr = ProcessingManager(config)
        mgr._spotify_paths = lambda: set()

        task = Task("sync", "Sync", {})

        def failing_copy(src, dst, *a, **k):
            open(dst, "wb").write(b"partial")
            raise OSError("disk full")

        monkeypatch.setattr(shutil, "copy2", failing_copy)

        with pytest.raises(RuntimeError):
            mgr._do_sync(task)

        assert not (mount / "Music" / "a.mp3").exists()
