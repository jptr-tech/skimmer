import os
import threading
from pathlib import Path

import pytest

from skimmer.scanner import BackgroundScanner


SAMPLE_CONFIG = {
    "music_dir": "",
    "mount_path": "",
    "scan_interval": 600,
}


def test_initial_state():
    scanner = BackgroundScanner(SAMPLE_CONFIG)
    assert scanner._local_cache is None
    assert scanner._thread is None
    assert scanner.get_local_cache() is None


def test_double_start_noop():
    scanner = BackgroundScanner(SAMPLE_CONFIG)
    scanner.start()
    thread = scanner._thread
    scanner.start()
    assert scanner._thread is thread
    scanner.stop()


def test_stop_without_start():
    scanner = BackgroundScanner(SAMPLE_CONFIG)
    scanner.stop()  # no crash


def test_scan_now_without_start():
    scanner = BackgroundScanner(SAMPLE_CONFIG)
    scanner.scan_now()  # no crash


def test_scan_creates_cache(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "song1.flac").write_bytes(b"data1")
    (music_dir / "song2.flac").write_bytes(b"data2")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir)}
    scanner = BackgroundScanner(config)
    scanner._local_cache_path = str(tmp_path / "local-cache.json")

    scanner._scan()

    cache = scanner.get_local_cache()
    assert cache is not None
    assert "song1.flac" in cache
    assert "song2.flac" in cache
    assert len(cache) == 2
    assert len(cache["song1.flac"]) == 3  # (mtime, size, hash)
    assert cache["song1.flac"][2] is not None


def test_scan_caches_are_persistent(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "a.flac").write_bytes(b"data")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir)}
    cache_path = str(tmp_path / "local-cache.json")

    scanner = BackgroundScanner(config)
    scanner._local_cache_path = cache_path
    scanner._scan()

    first = scanner.get_local_cache()

    scanner._scan()

    second = scanner.get_local_cache()
    assert second["a.flac"][2] == first["a.flac"][2]


def test_scan_detects_new_file(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "a.flac").write_bytes(b"data")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir)}
    scanner = BackgroundScanner(config)
    scanner._local_cache_path = str(tmp_path / "local-cache.json")
    scanner._scan()

    (music_dir / "b.flac").write_bytes(b"new data")
    changed = []
    scanner.set_callbacks(on_complete=lambda n: changed.append(n))
    scanner._scan()

    assert len(scanner.get_local_cache()) == 2
    assert changed == [1]


def test_scan_detects_modified_file(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    fpath = music_dir / "a.flac"
    fpath.write_bytes(b"data")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir)}
    scanner = BackgroundScanner(config)
    scanner._local_cache_path = str(tmp_path / "local-cache.json")
    scanner._scan()
    first_hash = scanner.get_local_cache()["a.flac"][2]

    fpath.write_bytes(b"modified data")
    changed = []
    scanner.set_callbacks(on_complete=lambda n: changed.append(n))
    scanner._scan()

    assert changed == [1]
    assert scanner.get_local_cache()["a.flac"][2] != first_hash


def test_scan_detects_deleted_file(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "a.flac").write_bytes(b"data")
    (music_dir / "b.flac").write_bytes(b"data2")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir)}
    scanner = BackgroundScanner(config)
    scanner._local_cache_path = str(tmp_path / "local-cache.json")
    scanner._scan()
    assert len(scanner.get_local_cache()) == 2

    os.remove(music_dir / "a.flac")
    changed = []
    scanner.set_callbacks(on_complete=lambda n: changed.append(n))
    scanner._scan()

    assert len(scanner.get_local_cache()) == 1
    assert changed == [1]


def test_callbacks_fired(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "a.flac").write_bytes(b"data")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir)}
    scanner = BackgroundScanner(config)
    scanner._local_cache_path = str(tmp_path / "local-cache.json")

    statuses = []
    progress_vals = []
    completes = []

    scanner.set_callbacks(
        on_status=lambda s: statuses.append(s),
        on_progress=lambda c, t: progress_vals.append((c, t)),
        on_complete=lambda n: completes.append(n),
    )
    scanner._scan()

    assert "Scanning local library..." in statuses
    assert any("Idle" in s for s in statuses)
    assert len(progress_vals) >= 1
    assert len(completes) == 1
    assert completes[0] == 1


def test_missing_music_dir_no_crash(tmp_path):
    config = {**SAMPLE_CONFIG, "music_dir": str(tmp_path / "nonexistent")}
    scanner = BackgroundScanner(config)
    scanner._scan()  # no crash
    assert scanner.get_local_cache() is None


def test_cache_file_written(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "x.flac").write_bytes(b"data")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir)}
    scanner = BackgroundScanner(config)
    cache_file = tmp_path / "local-cache.json"
    scanner._local_cache_path = str(cache_file)
    scanner._scan()

    assert cache_file.exists()
    import json
    data = json.loads(cache_file.read_text())
    assert data["music_dir"] == str(music_dir)
    assert "x.flac" in data["files"]


def test_device_scan_updates_device_cache(tmp_path):
    mount = tmp_path / "mount"
    music_dir = tmp_path / "music"
    mount_music = mount / "Music"
    mount.mkdir()
    mount_music.mkdir()
    music_dir.mkdir()

    (music_dir / "local.flac").write_bytes(b"local")
    (mount_music / "device.flac").write_bytes(b"device data")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir), "mount_path": str(mount)}
    scanner = BackgroundScanner(config)
    scanner._local_cache_path = str(tmp_path / "local-cache.json")

    scanner._scan()

    device_cache = mount / ".skimmer-cache.json"
    assert device_cache.exists()
    import json
    data = json.loads(device_cache.read_text())
    assert "device.flac" in data["files"]


def test_device_scan_detects_deletion(tmp_path):
    mount = tmp_path / "mount"
    mount_music = mount / "Music"
    mount.mkdir()
    mount_music.mkdir()
    (mount_music / "gone.flac").write_bytes(b"gone")

    config = {**SAMPLE_CONFIG, "music_dir": str(tmp_path / "music"), "mount_path": str(mount)}
    scanner = BackgroundScanner(config)
    scanner._local_cache_path = str(tmp_path / "local-cache.json")

    scanner._scan()

    # delete file directly on device
    os.remove(mount_music / "gone.flac")
    changed = []
    scanner.set_callbacks(on_complete=lambda n: changed.append(n))
    scanner._scan()

    device_cache = mount / ".skimmer-cache.json"
    import json
    data = json.loads(device_cache.read_text())
    assert "gone.flac" not in data["files"]
    assert changed[0] == 1  # device scan detects the deletion


def test_device_skipped_when_not_mounted(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "a.flac").write_bytes(b"data")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir), "mount_path": ""}
    scanner = BackgroundScanner(config)
    scanner._local_cache_path = str(tmp_path / "local-cache.json")
    scanner._scan()  # no crash, device scan skipped

    assert scanner.get_local_cache() is not None
    assert len(scanner.get_local_cache()) == 1


def test_lifecycle_start_stop(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "a.flac").write_bytes(b"data")

    config = {**SAMPLE_CONFIG, "music_dir": str(music_dir)}
    scanner = BackgroundScanner(config)

    done = threading.Event()
    scanner.set_callbacks(on_complete=lambda n: done.set())

    scanner.start()
    assert scanner._thread is not None
    assert scanner._thread.is_alive()

    assert done.wait(timeout=5), "Initial scan did not complete"

    cache = scanner.get_local_cache()
    assert cache is not None
    assert "a.flac" in cache

    scanner.stop()
    scanner._thread.join(timeout=2)
    assert not scanner._thread.is_alive()
