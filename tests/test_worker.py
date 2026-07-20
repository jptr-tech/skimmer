import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import GLib

import pytest

from skimmer.worker import Task, ProcessingManager


SAMPLE_CONFIG = {
    "temp_dir": "/tmp/skimmer",
    "music_dir": "/tmp/music",
    "ytdlp_format": "bestaudio/best",
    "ytdlp_audio_format": "mp3",
    "mount_path": "/tmp/mount",
    "beets_lib": "",
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
