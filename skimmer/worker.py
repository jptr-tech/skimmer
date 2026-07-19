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
        task._dl_progress = [0.0] * total
        vid_to_idx = {}
        for i, track in enumerate(album["tracks"]):
            vid = track.get("videoId")
            if vid:
                vid_to_idx[vid] = i
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
                lambda d: self._ytdlp_hook(d, task, total, vid_to_idx)
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

    def _ytdlp_hook(self, d, task, total, vid_to_idx):
        if d["status"] == "downloading":
            try:
                pct_str = d.get("_percent_str", "0%").strip().replace("%", "")
                track_pct = float(pct_str) / 100.0
            except (ValueError, KeyError):
                track_pct = 0.0
            info = d.get("info_dict", {})
            idx = vid_to_idx.get(info.get("id", ""), -1)
            if idx >= 0:
                task._dl_progress[idx] = track_pct
                overall = sum(task._dl_progress) / total
                if overall > task.progress:
                    task.progress = overall
                GLib.idle_add(
                    task.emit, "updated", task.status, task.progress,
                    f"Track {idx+1}/{total}: {info.get('title', '?')}"
                )
        elif d["status"] == "finished":
            info = d.get("info_dict", {})
            idx = vid_to_idx.get(info.get("id", ""), -1)
            if idx >= 0:
                task._dl_progress[idx] = 1.0
                overall = sum(task._dl_progress) / total
                task.progress = overall
                GLib.idle_add(
                    task.emit, "updated", task.status, task.progress,
                    f"Track {idx+1}/{total}: {info.get('title', '?')}"
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
        task.emit("updated", task.status, 0.0, "Tagging files...")

        artist = task.data.get("artist", "")
        album_title = task.data.get("title", "")
        if artist and album_title:
            from mutagen import File as MutagenFile
            from mutagen.id3 import ID3NoHeaderError
            from mutagen.easyid3 import EasyID3
            from mutagen.mp4 import MP4
            tagged = 0
            for fname in files:
                fpath = os.path.join(album_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                try:
                    if ext == ".mp3":
                        try:
                            audio = EasyID3(fpath)
                        except ID3NoHeaderError:
                            audio = MutagenFile(fpath, easy=True)
                            audio.add_tags()
                    elif ext in (".m4a", ".mp4", ".m4b"):
                        audio = MP4(fpath)
                    elif ext == ".flac":
                        from mutagen.flac import FLAC
                        audio = FLAC(fpath)
                    elif ext == ".opus":
                        from mutagen.oggopus import OggOpus
                        audio = OggOpus(fpath)
                    else:
                        continue
                    audio["artist"] = artist
                    audio["album"] = album_title
                    audio["albumartist"] = artist
                    audio.save()
                    tagged += 1
                except Exception as e:
                    print(f"[y1-skimmer]   Warning: could not tag {fname}: {e}")
            print(f"[y1-skimmer] Tagged {tagged}/{len(files)} files with artist={artist}, album={album_title}")

        print(f"[y1-skimmer] Running beet import...")
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

        task.progress = 1.0
        print(f"[y1-skimmer] Import complete")

        if "already in the library" in out or "Skipping" in out:
            try:
                folder_name = os.path.basename(album_dir)
                album_query = folder_name.split(" - ", 1)[-1] if " - " in folder_name else folder_name
                if artist and album_title:
                    beets_query = f"album:{album_title} artist:{artist}"
                else:
                    beets_query = f"album:{album_query}"
                result3 = subprocess.run(
                    [sys.executable, "-m", "beets", "list", "-ap",
                     beets_query],
                    capture_output=True, text=True, timeout=10,
                )
                if result3.stdout.strip():
                    print(f"[y1-skimmer] Imported files:\n{result3.stdout.strip()[:500]}")
                else:
                    print(f"[y1-skimmer] WARNING: no files found in library for this import")
                    print(f"[y1-skimmer]   Retrying import with duplicates forced...")
                    task.emit("updated", task.status, 0.0, "Retrying import (duplicates)...")
                    import tempfile
                    dup_config = tempfile.NamedTemporaryFile(
                        mode="w", suffix=".yaml", delete=False, prefix="beets-import-"
                    )
                    dup_config.write("import:\n  duplicate_action: keep\n")
                    dup_config.close()
                    try:
                        result4 = subprocess.run(
                            [sys.executable, "-m", "beets", "-c", dup_config.name,
                             "import", "-qC", "--quiet-fallback=asis", album_dir],
                            capture_output=True, text=True, timeout=300,
                        )
                    finally:
                        os.unlink(dup_config.name)
                    out4 = (result4.stdout or "") + (result4.stderr or "")
                    print(f"[y1-skimmer]   retry output:\n{out4[:1000]}")
                    result5 = subprocess.run(
                        [sys.executable, "-m", "beets", "list", "-ap",
                         beets_query],
                        capture_output=True, text=True, timeout=10,
                    )
                    if result5.stdout.strip():
                        print(f"[y1-skimmer] Imported on retry:\n{result5.stdout.strip()[:500]}")
                    else:
                        print(f"[y1-skimmer]   Still no files after retry — album may already exist under a different name")
            except Exception as e:
                print(f"[y1-skimmer] Verification query error: {e}")
            finally:
                shutil.rmtree(album_dir, ignore_errors=True)
                print(f"[y1-skimmer] Cleaned up temp dir")
        else:
            shutil.rmtree(album_dir, ignore_errors=True)
            print(f"[y1-skimmer] Cleaned up temp dir")

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

        GLib.idle_add(task.emit, "updated", task.status, 0.0, "Indexing files...")

        if cached is not None:
            added, modified, deleted = synccache.get_changes(src, cached)
            print(f"[y1-skimmer] Sync: diff from cache — +{len(added)} ~{len(modified)} -{len(deleted)}")
            if modified:
                for p in sorted(modified)[:5]:
                    print(f"[y1-skimmer] Sync:   modified: {p}")
            if not added and not modified and not deleted:
                print("[y1-skimmer] Sync: no changes, skipping rsync")
                GLib.idle_add(task.emit, "updated", task.status, 1.0, "Already up to date")
                task.progress = 1.0
                return

            to_transfer = sorted(added) + sorted(modified)
            for p in sorted(deleted):
                dst_path = os.path.join(dst, p)
                try:
                    if os.path.isfile(dst_path):
                        os.remove(dst_path)
                    elif os.path.isdir(dst_path):
                        shutil.rmtree(dst_path, ignore_errors=True)
                except OSError:
                    pass
            if to_transfer:
                total = len(to_transfer)
                GLib.idle_add(task.emit, "updated", task.status, 0.0,
                              f"Syncing {total} files...")
                import tempfile
                files_list = tempfile.NamedTemporaryFile(
                    mode="w", delete=False, suffix=".rsync", prefix="y1-skimmer-"
                )
                for p in to_transfer:
                    files_list.write(p + "\n")
                files_list.close()
                print(f"[y1-skimmer] Sync: starting rsync ({total} files)")
                proc = subprocess.Popen(
                    ["rsync", "-a", "--delete",
                     f"--files-from={files_list.name}",
                     "--out-format=%n", f"{src}/", f"{dst}/"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1,
                )
            else:
                for p in sorted(deleted)[:10]:
                    print(f"[y1-skimmer] Sync:   deleted: {p}")
                print("[y1-skimmer] Sync: only deletions, skipping rsync")
                GLib.idle_add(task.emit, "updated", task.status, 0.95, "Saving cache...")
                synccache.update_cache(cache_path, src)
                print(f"[y1-skimmer] Sync: cache saved to {cache_path}")
                task.progress = 1.0
                GLib.idle_add(task.emit, "updated", task.status, 1.0, "Sync complete")
                print("[y1-skimmer] Sync complete (deletions only)")
                return
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
                          f"Syncing {total} files..." if total else "Indexing...")
            if not total:
                print("[y1-skimmer] Sync: no changes, skipping rsync")
                GLib.idle_add(task.emit, "updated", task.status, 0.95, "Saving cache...")
                synccache.update_cache(cache_path, src)
                print(f"[y1-skimmer] Sync: cache saved to {cache_path}")
                task.progress = 1.0
                GLib.idle_add(task.emit, "updated", task.status, 1.0, "Sync complete")
                print("[y1-skimmer] Sync complete (no cache, nothing to sync)")
                return
            print(f"[y1-skimmer] Sync: starting rsync ({total} files)")
            proc = subprocess.Popen(
                ["rsync", "-a", "--delete", "--out-format=%n",
                 "--exclude=.musiclibrary.db", f"{src}/", f"{dst}/"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )

        completed = 0
        last_tick = -1
        files_list_path = None
        if cached is not None and to_transfer:
            files_list_path = files_list.name

        for line in proc.stdout:
            line = line.rstrip("\n\r")
            if not line:
                continue
            completed += 1
            pct = min(completed / total, 1.0) if total > 0 else 0.0
            tick = int(pct * 50)
            if tick != last_tick:
                last_tick = tick
                task.progress = pct
                GLib.idle_add(task.emit, "updated", task.status, pct,
                              f"Syncing... ({completed} files)")

            if completed <= 5 or completed % 100 == 0:
                print(f"[y1-skimmer] rsync: {line[:200]}")

        proc.wait()
        print(f"[y1-skimmer] Sync: rsync finished (rc={proc.returncode})")
        if files_list_path:
            try:
                os.unlink(files_list_path)
            except OSError:
                pass

        stderr = proc.stderr.read()
        if proc.returncode != 0:
            print(f"[y1-skimmer] Sync FAILED: {stderr[:500]}")
            raise RuntimeError(f"Sync failed (rc={proc.returncode}): {stderr[:500]}")

        GLib.idle_add(task.emit, "updated", task.status, 0.95, "Saving cache...")
        synccache.update_cache(cache_path, src)
        print(f"[y1-skimmer] Sync: cache saved to {cache_path}")
        task.progress = 1.0
        GLib.idle_add(task.emit, "updated", task.status, 1.0, "Sync complete")
        print(f"[y1-skimmer] Sync complete ({completed} files)")
