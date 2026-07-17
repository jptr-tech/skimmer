import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import yt_dlp
from gi.repository import GLib, GObject

from skimmer import synccache


class Task(GObject.Object):
    __gsignals__ = {
        "updated": (GObject.SignalFlags.RUN_FIRST, None, (str, float, str)),
    }

    def __init__(self, task_type, title, data):
        super().__init__()
        self.id = str(uuid.uuid4())[:8]
        self.type = task_type
        self.title = title
        self.data = data
        self.status = "pending"
        self.progress = 0.0
        self.error = None


class ProcessingManager(GObject.Object):
    __gsignals__ = {
        "task-added": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "task-removed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.tasks = []
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def add_task(self, task_type, title, data):
        task = Task(task_type, title, data)
        with self._lock:
            self.tasks.append(task)
        self._queue.put(task)
        print(f"[y1-skimmer] Queued task [{task.id}] {task_type}: {title}")
        GLib.idle_add(self.emit, "task-added", task)
        return task

    def remove_task(self, task):
        with self._lock:
            if task in self.tasks:
                self.tasks.remove(task)
        GLib.idle_add(self.emit, "task-removed", task)

    def _run(self):
        while True:
            task = self._queue.get()
            print(f"[y1-skimmer] Starting task [{task.id}] {task.type}: {task.title}")
            task.status = "running"
            task.emit("updated", task.status, task.progress, "")
            try:
                if task.type == "download":
                    self._do_download(task)
                elif task.type == "import":
                    self._do_import(task)
                elif task.type == "sync":
                    self._do_sync(task)
                task.status = "completed"
                task.progress = 1.0
                task.emit("updated", task.status, task.progress, "")
                print(f"[y1-skimmer] Task [{task.id}] completed: {task.title}")
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                task.emit("updated", task.status, task.progress, str(e))
                print(f"[y1-skimmer] Task [{task.id}] FAILED: {task.title} — {e}")

    def _do_download(self, task):
        album = task.data
        album_dir = os.path.join(
            self.config["temp_dir"],
            f"{album['artist']} - {album['title']}",
        )
        os.makedirs(album_dir, exist_ok=True)
        task.data["album_dir"] = album_dir

        total = len(album["tracks"])
        print(f"[y1-skimmer] Downloading {album['artist']} - {album['title']} ({total} tracks) to {album_dir}")

        for i, track in enumerate(album["tracks"]):
            print(f"[y1-skimmer]   Track {i+1}/{total}: {track.get('title', '?')} (videoId: {track.get('videoId', 'none')})")
        ydl_opts = {
            "format": self.config["ytdlp_format"],
            "outtmpl": os.path.join(album_dir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": self.config["ytdlp_audio_format"],
                }
            ],
            "progress_hooks": [
                lambda d: self._ytdlp_hook(d, task, total)
            ],
        }

        urls = []
        for track in album["tracks"]:
            vid = track.get("videoId")
            if vid:
                urls.append(f"https://music.youtube.com/watch?v={vid}")
            else:
                print(f"[y1-skimmer]   WARNING: No videoId for track: {track.get('title', '?')}")

        print(f"[y1-skimmer] Starting yt-dlp with {len(urls)} URLs, format={self.config['ytdlp_format']}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download(urls)
        print(f"[y1-skimmer] Download complete: {album['artist']} - {album['title']}")
        print(f"[y1-skimmer] Files saved to: {album_dir}")

    def _ytdlp_hook(self, d, task, total):
        if d["status"] == "downloading":
            try:
                pct_str = d.get("_percent_str", "0%").strip().replace("%", "")
                pct = float(pct_str) / 100.0
                task.progress = (task.progress + pct) / 2 if task.progress > 0 else pct
            except (ValueError, KeyError):
                pass
            GLib.idle_add(
                task.emit, "updated", task.status, task.progress,
                d.get("filename", "")
            )
        elif d["status"] == "finished":
            GLib.idle_add(
                task.emit, "updated", task.status, task.progress,
                f"Finished: {d.get('filename', '')}"
            )

    def _do_import(self, task):
        album_dir = task.data.get("album_dir")
        if not album_dir or not os.path.isdir(album_dir):
            raise FileNotFoundError(f"Album directory not found: {album_dir}")

        files = os.listdir(album_dir)
        music_dir = self.config.get("music_dir", "~")
        beets_db = self.config.get("beets_lib", "~")
        print(f"[y1-skimmer] Importing {len(files)} files from {album_dir}")
        print(f"[y1-skimmer]   music_dir = {music_dir}")
        print(f"[y1-skimmer]   beets_lib = {beets_db}")
        task.emit("updated", task.status, 0.0, "Running beet import...")

        result = subprocess.run(
            [sys.executable, "-m", "beets", "import", "-qC",
             "--quiet-fallback=asis", album_dir],
            capture_output=True,
            text=True,
            timeout=300,
        )
        out = (result.stdout or "") + (result.stderr or "")
        print(f"[y1-skimmer] beet import output:\n{out[:2000]}")

        if result.returncode != 0 or "No files imported" in out:
            msg = result.stderr[:500] if result.stderr else out[:500]
            print(f"[y1-skimmer] Beet import FAILED (rc={result.returncode})")
            print(f"[y1-skimmer]   {msg}")
            if result.returncode != 0:
                raise RuntimeError(f"Beet import failed: {msg}")
            print(f"[y1-skimmer]   (rc=0 but no files were imported — trying without quiet)")
            result2 = subprocess.run(
                [sys.executable, "-m", "beets", "import", "-C",
                 "--quiet-fallback=asis", album_dir],
                capture_output=True, text=True, timeout=300,
            )
            print(f"[y1-skimmer]   retry output:\n{(result2.stdout or '')[:1000]}")
            if "No files imported" in (result2.stdout or ""):
                raise RuntimeError(f"Beet import could not import files from {album_dir}")

        print(f"[y1-skimmer] Beet import succeeded, cleaning up temp dir")
        shutil.rmtree(album_dir, ignore_errors=True)
        task.progress = 1.0
        print(f"[y1-skimmer] Import complete")

        try:
            folder_name = os.path.basename(album_dir)
            result3 = subprocess.run(
                [sys.executable, "-m", "beets", "list", "-ap",
                 f"album:{folder_name.split(' - ', 1)[-1] if ' - ' in folder_name else folder_name}"],
                capture_output=True, text=True, timeout=10,
            )
            if result3.stdout.strip():
                print(f"[y1-skimmer] Imported files:\n{result3.stdout.strip()[:500]}")
            else:
                print(f"[y1-skimmer] WARNING: no files found in library for this import")
        except Exception as e:
            print(f"[y1-skimmer] Verification query error: {e}")

    def _do_sync(self, task):
        src = self.config["music_dir"]
        dst = os.path.join(self.config["y1_mount_path"], "Music")
        if not os.path.isdir(src):
            raise FileNotFoundError(f"Source directory not found: {src}")
        os.makedirs(dst, exist_ok=True)

        cache_path = os.path.join(self.config["y1_mount_path"], ".y1-skimmer-cache.json")

        print(f"[y1-skimmer] Sync: {src} -> {dst}")

        cached = synccache.load_cache(cache_path, src)
        if cached is not None:
            print(f"[y1-skimmer] Sync: loaded cache from {cache_path} ({len(cached)} files)")
        else:
            print(f"[y1-skimmer] Sync: no cache found at {cache_path}")

        GLib.idle_add(task.emit, "updated", task.status, 0.0, "Indexing...")

        if cached is not None:
            added, modified, deleted = synccache.get_changes(src, cached)
            print(f"[y1-skimmer] Sync: diff from cache — +{len(added)} ~{len(modified)} -{len(deleted)}")
            if not added and not modified and not deleted:
                print("[y1-skimmer] Sync: no changes, skipping rsync")
                GLib.idle_add(task.emit, "updated", task.status, 1.0, "Already up to date")
                task.progress = 1.0
                return
            total = len(added) + len(modified)
        else:
            total = 0
            try:
                r = subprocess.run(
                    ["rsync", "-ahn", "--delete", "--out-format=%n",
                     "--exclude=.musiclibrary.db",
                     f"{src}/", f"{dst}/"],
                    capture_output=True, text=True, timeout=60,
                )
                total = len([l for l in r.stdout.split("\n") if l.strip()])
                print(f"[y1-skimmer] Sync: dry-run — {total} items to transfer")
            except Exception as e:
                print(f"[y1-skimmer] Sync: dry-run failed: {e}")

        GLib.idle_add(task.emit, "updated", task.status, 0.0,
                      f"Found {total} files" if total else "Indexing...")

        print(f"[y1-skimmer] Sync: starting rsync ({total} files)")
        proc = subprocess.Popen(
            ["rsync", "-a", "--delete", "--out-format=%n",
             "--exclude=.musiclibrary.db", f"{src}/", f"{dst}/"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )

        completed = 0
        last_tick = -1

        for line in proc.stdout:
            line = line.rstrip("\n\r")
            if not line:
                continue
            completed += 1
            if total > 0:
                pct = min(completed / total, 1.0)
                tick = int(pct * 50)
                if tick != last_tick:
                    last_tick = tick
                    task.progress = pct
                    GLib.idle_add(task.emit, "updated", task.status, pct,
                                  f"{completed}/{total}")
            else:
                GLib.idle_add(task.emit, "updated", task.status, 0.0,
                              f"Syncing... {completed} files")

            if completed <= 5 or completed % 100 == 0:
                print(f"[y1-skimmer] rsync: {line[:200]}")

        proc.wait()
        print(f"[y1-skimmer] Sync: rsync finished (rc={proc.returncode})")

        stderr = proc.stderr.read()
        if proc.returncode != 0:
            print(f"[y1-skimmer] Sync FAILED: {stderr[:500]}")
            raise RuntimeError(f"Sync failed (rc={proc.returncode}): {stderr[:500]}")

        synccache.update_cache(cache_path, src)
        print(f"[y1-skimmer] Sync: cache saved to {cache_path}")
        task.progress = 1.0
        print(f"[y1-skimmer] Sync complete ({completed} files)")
