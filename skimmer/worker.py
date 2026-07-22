import os
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path

import yt_dlp
from beets import context as beets_context
from beets.library import Item, Library
from beets.util import bytestring_path
from gi.repository import GLib, GObject

from skimmer import synccache
from skimmer.playlist import Playlist, PlaylistTrack, load_playlists, save_playlists, export_m3u8, parse_m3u8


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
        print(f"[skimmer] Queued task [{task.id}] {task_type}: {title}")
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
            print(f"[skimmer] Starting task [{task.id}] {task.type}: {task.title}")
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
                print(f"[skimmer] Task [{task.id}] completed: {task.title}")
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                task.emit("updated", task.status, task.progress, str(e))
                print(f"[skimmer] Task [{task.id}] FAILED: {task.title} — {e}")

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
        print(f"[skimmer] Downloading {album['artist']} - {album['title']} ({total} tracks) to {album_dir}")

        for i, track in enumerate(album["tracks"]):
            print(f"[skimmer]   Track {i+1}/{total}: {track.get('title', '?')} (videoId: {track.get('videoId', 'none')})")
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
                print(f"[skimmer]   WARNING: No videoId for track: {track.get('title', '?')}")

        print(f"[skimmer] Starting yt-dlp with {len(urls)} URLs, format={self.config['ytdlp_format']}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download(urls)
        print(f"[skimmer] Download complete: {album['artist']} - {album['title']}")
        print(f"[skimmer] Files saved to: {album_dir}")

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
        print(f"[skimmer] Importing {len(files)} files from {album_dir}")
        print(f"[skimmer]   music_dir = {music_dir}")
        print(f"[skimmer]   beets_lib = {beets_db}")
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
                    print(f"[skimmer]   Warning: could not tag {fname}: {e}")
            print(f"[skimmer] Tagged {tagged}/{len(files)} files with artist={artist}, album={album_title}")

        print(f"[skimmer] Copying files to music library...")
        task.emit("updated", task.status, 0.0, "Copying to music library...")

        album_dst = os.path.join(music_dir, artist, album_title) if artist and album_title else album_dir
        os.makedirs(album_dst, exist_ok=True)

        audio_exts = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.mp4', '.m4b', '.wav'}
        copied = []
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in audio_exts:
                continue
            src = os.path.join(album_dir, fname)
            dst = os.path.join(album_dst, fname)
            shutil.copy2(src, dst)
            copied.append(dst)
        print(f"[skimmer] Copied {len(copied)} audio files to {album_dst}")

        try:
            print(f"[skimmer] Opening beets library at {beets_db}")
            beets_context.set_music_dir(bytestring_path(music_dir))
            lib = Library(beets_db, directory=music_dir)

            items = []
            for fpath in copied:
                item = Item.from_path(fpath)
                item.add(lib)
                items.append(item)
            print(f"[skimmer] Added {len(items)} items to beets library")

            if items:
                album = lib.add_album(items)
                print(f"[skimmer] Created album '{album.album}' (id={album.id})")
                try:
                    from beetsplug.fetchart import FetchArtPlugin
                    fa = FetchArtPlugin()
                    fa.batch_fetch_art(lib, [album], force=False, quiet=True)
                except Exception as fe:
                    print(f"[skimmer] fetchart for new album failed: {fe}")

            task.progress = 1.0
            print(f"[skimmer] Import complete")

            beets_query = f"album:{album_title} artist:{artist}"
            found = list(lib.items(beets_query))
            if found:
                print(f"[skimmer] Verified: {len(found)} tracks in library for {artist} - {album_title}")
            else:
                print(f"[skimmer] WARNING: verification found no tracks for {artist} - {album_title}")
        except Exception as e:
            print(f"[skimmer] Beets import error: {e}")
            raise
        finally:
            shutil.rmtree(album_dir, ignore_errors=True)
            print(f"[skimmer] Cleaned up temp dir")

    def _do_sync(self, task):
        src = self.config["music_dir"]
        dst = os.path.join(self.config["mount_path"], "Music")
        if not os.path.isdir(src):
            raise FileNotFoundError(f"Source directory not found: {src}")
        os.makedirs(dst, exist_ok=True)

        cache_path = os.path.join(self.config["mount_path"], ".skimmer-cache.json")

        print(f"[skimmer] Sync: {src} -> {dst}")

        cached = synccache.load_cache(cache_path, src)
        if cached is not None:
            print(f"[skimmer] Sync: loaded cache from {cache_path} ({len(cached)} files)")
        else:
            print(f"[skimmer] Sync: no cache found at {cache_path}")

        GLib.idle_add(task.emit, "updated", task.status, 0.0, "Indexing files...")

        if cached is not None:
            added, modified, deleted = synccache.get_changes(src, cached)
            print(f"[skimmer] Sync: diff from cache — +{len(added)} ~{len(modified)} -{len(deleted)}")
            if modified:
                for p in sorted(modified)[:5]:
                    print(f"[skimmer] Sync:   modified: {p}")
            if not added and not modified and not deleted:
                print("[skimmer] Sync: no changes, skipping copy")
                GLib.idle_add(task.emit, "updated", task.status, 1.0, "Already up to date")
                task.progress = 1.0
                return

            for p in sorted(deleted):
                dst_path = os.path.join(dst, p)
                try:
                    if os.path.isfile(dst_path):
                        os.remove(dst_path)
                    elif os.path.isdir(dst_path):
                        shutil.rmtree(dst_path, ignore_errors=True)
                except OSError:
                    pass

            to_transfer = sorted(added) + sorted(modified)
            if not to_transfer:
                for p in sorted(deleted)[:10]:
                    print(f"[skimmer] Sync:   deleted: {p}")
                print("[skimmer] Sync: only deletions, skipping copy")
                GLib.idle_add(task.emit, "updated", task.status, 0.95, "Saving cache...")
                synccache.update_cache(cache_path, src)
                print(f"[skimmer] Sync: cache saved to {cache_path}")
                task.progress = 1.0
                GLib.idle_add(task.emit, "updated", task.status, 1.0, "Sync complete")
                print("[skimmer] Sync complete (deletions only)")
                return
        else:
            to_transfer = sorted(os.listdir(src))
            print(f"[skimmer] Sync: first sync — {len(to_transfer)} top-level items")

        total = len(to_transfer)
        print(f"[skimmer] Sync: copying {total} files")
        GLib.idle_add(task.emit, "updated", task.status, 0.0, f"Copying {total} files...")

        completed = 0
        last_tick = -1
        failed = []

        for p in to_transfer:
            src_path = os.path.join(src, p)
            dst_path = os.path.join(dst, p)
            try:
                if os.path.isdir(src_path):
                    os.makedirs(dst_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    shutil.copy2(src_path, dst_path)
            except Exception as e:
                print(f"[skimmer] Sync:   failed to copy {p}: {e}")
                failed.append(p)
            completed += 1
            pct = completed / total
            tick = int(pct * 50)
            if tick != last_tick:
                last_tick = tick
                task.progress = pct
                GLib.idle_add(task.emit, "updated", task.status, pct,
                              f"Copying... ({completed}/{total})")
            if completed <= 5 or completed % 50 == 0:
                print(f"[skimmer] Sync:   {completed}/{total}: {p[:120]}")

        if failed:
            print(f"[skimmer] Sync: {len(failed)} files failed: {failed[:5]}...")
            raise RuntimeError(f"Sync failed: {len(failed)} files could not be copied")

        print(f"[skimmer] Sync: copy finished ({completed} files)")
        GLib.idle_add(task.emit, "updated", task.status, 0.85, "Syncing playlists...")
        self._sync_playlists(task, dst)
        GLib.idle_add(task.emit, "updated", task.status, 0.95, "Saving cache...")
        synccache.update_cache(cache_path, src)
        print(f"[skimmer] Sync: cache saved to {cache_path}")
        task.progress = 1.0
        GLib.idle_add(task.emit, "updated", task.status, 1.0, "Sync complete")
        print(f"[skimmer] Sync complete ({completed} files)")

    def _sync_playlists(self, task, music_dst):
        mount_path = self.config.get("mount_path", "")
        if not mount_path:
            return
        device_root = os.path.realpath(mount_path)
        playlist_dir = os.path.join(device_root, "Playlists")
        os.makedirs(playlist_dir, exist_ok=True)

        app_playlists = load_playlists()
        app_by_name = {p.name: p for p in app_playlists}

        device_m3us = {}
        if os.path.isdir(playlist_dir):
            for fname in os.listdir(playlist_dir):
                if not (fname.endswith(".m3u8") or fname.endswith(".m3u")):
                    continue
                if fname.startswith("._"):
                    continue
                name = os.path.splitext(fname)[0]
                fpath = os.path.join(playlist_dir, fname)
                device_m3us[name] = (fpath, os.path.getmtime(fpath))

        changed = False
        for name, pl in list(app_by_name.items()):
            dev_info = device_m3us.pop(name, None)
            if dev_info:
                dev_path, dev_mtime = dev_info
                if dev_mtime > pl.last_modified:
                    print(f"[skimmer] Sync: playlist '{name}' newer on device — importing")
                    parsed = parse_m3u8(dev_path)
                    if parsed:
                        pl.tracks = parsed.tracks
                        pl.last_modified = dev_mtime
                        changed = True
                else:
                    print(f"[skimmer] Sync: playlist '{name}' newer in app — exporting")
                    export_m3u8(pl, device_root, dev_path)
                    pl.last_modified = time.time()
                    changed = True
            else:
                if pl.tracks:
                    out_path = os.path.join(playlist_dir, f"{name}.m3u8")
                    print(f"[skimmer] Sync: creating playlist '{name}' on device")
                    export_m3u8(pl, device_root, out_path)
                    pl.last_modified = time.time()
                    changed = True

        for name, (dev_path, _) in device_m3us.items():
            print(f"[skimmer] Sync: importing new playlist '{name}' from device")
            parsed = parse_m3u8(dev_path)
            if parsed:
                app_playlists.append(parsed)
                changed = True

        if changed:
            save_playlists(app_playlists)
            print(f"[skimmer] Sync: playlists synced ({len(app_playlists)} playlists)")
        else:
            print("[skimmer] Sync: playlists already up to date")
