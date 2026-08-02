import logging
import os
import queue
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Callable

import yt_dlp
from beets import context as beets_context
from beets.library import Item, Library
from beets.util import bytestring_path
from gi.repository import GLib, GObject

from skimmer import synccache
from skimmer.playlist import (
    export_m3u8,
    load_playlists,
    parse_m3u8,
    save_playlists,
)
from skimmer.podcasts import PodcastDownloader
from skimmer.spotify_import import SpotifyImporter

log = logging.getLogger(__name__)


class TaskCancelled(Exception):
    pass


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
        self.cancelled = False
        self.cancel_cb: Callable[[], None] | None = None


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
        log.info(f"[skimmer] Queued task [{task.id}] {task_type}: {title}")
        GLib.idle_add(self.emit, "task-added", task)
        return task

    def remove_task(self, task):
        with self._lock:
            if task in self.tasks:
                self.tasks.remove(task)
        GLib.idle_add(self.emit, "task-removed", task)

    def cancel_task(self, task):
        task.cancelled = True
        cb = task.cancel_cb
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _run(self):
        while True:
            task = self._queue.get()
            log.info(f"[skimmer] Starting task [{task.id}] {task.type}: {task.title}")
            if task.cancelled:
                task.status = "cancelled"
                task.emit("updated", task.status, task.progress, "Cancelled")
                log.info(f"[skimmer] Task [{task.id}] cancelled before start")
                continue
            task.status = "running"
            task.emit("updated", task.status, task.progress, "")
            try:
                if task.type == "download":
                    self._do_download(task)
                elif task.type == "import":
                    self._do_import(task)
                elif task.type == "sync":
                    self._do_sync(task)
                elif task.type == "spotify_import":
                    self._do_spotify_import(task)
                elif task.type == "podcast":
                    self._do_podcast(task)
                task.status = "completed"
                task.progress = 1.0
                task.emit("updated", task.status, task.progress, "")
                log.info(f"[skimmer] Task [{task.id}] completed: {task.title}")
            except TaskCancelled:
                task.status = "cancelled"
                task.emit("updated", task.status, task.progress, "Cancelled")
                log.info(f"[skimmer] Task [{task.id}] cancelled: {task.title}")
            except Exception as e:
                if task.cancelled:
                    task.status = "cancelled"
                    task.emit("updated", task.status, task.progress, "Cancelled")
                    log.info(f"[skimmer] Task [{task.id}] cancelled: {task.title}")
                else:
                    task.status = "failed"
                    task.error = str(e)
                    task.emit("updated", task.status, task.progress, str(e))
                    log.error(f"[skimmer] Task [{task.id}] FAILED: {task.title} — {e}")

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
        log.info(
            f"[skimmer] Downloading {album['artist']} - {album['title']} ({total} tracks) to {album_dir}"
        )

        for i, track in enumerate(album["tracks"]):
            if task.cancelled:
                raise TaskCancelled("Download cancelled")
            log.info(
                f"[skimmer]   Track {i + 1}/{total}: {track.get('title', '?')} (videoId: {track.get('videoId', 'none')})"
            )

        ffmpeg_location_dir = None
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            ffmpeg_path = shutil.which("ffmpeg") or os.path.join(meipass, "ffmpeg")
            if os.path.exists(ffmpeg_path):
                log.info(f"[skimmer] Using bundled ffmpeg at {ffmpeg_path}")
                ffmpeg_location_dir = os.path.dirname(ffmpeg_path)

        ydl_opts = {"format": self.config["ytdlp_format"]}
        if ffmpeg_location_dir:
            ydl_opts["ffmpeg_location"] = ffmpeg_location_dir
        ydl_opts.update(
            {
                "outtmpl": os.path.join(album_dir, "%(autonumber)02d - %(title)s.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": self.config["ytdlp_audio_format"],
                    }
                ],
                "progress_hooks": [lambda d: self._ytdlp_hook(d, task, total, vid_to_idx)],
            }
        )

        urls = []
        for track in album["tracks"]:
            vid = track.get("videoId")
            if vid:
                urls.append(f"https://music.youtube.com/watch?v={vid}")
            else:
                log.warning(f"[skimmer]   WARNING: No videoId for track: {track.get('title', '?')}")

        log.info(
            f"[skimmer] Starting yt-dlp with {len(urls)} URLs, format={self.config['ytdlp_format']}"
        )
        if task.cancelled:
            raise TaskCancelled("Download cancelled")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download(urls)
        except TaskCancelled:
            shutil.rmtree(album_dir, ignore_errors=True)
            log.info(f"[skimmer] Download cancelled, cleaned up {album_dir}")
            raise
        log.info(f"[skimmer] Download complete: {album['artist']} - {album['title']}")
        log.info(f"[skimmer] Files saved to: {album_dir}")

    def _ytdlp_hook(self, d, task, total, vid_to_idx):
        if task.cancelled:
            raise TaskCancelled("Download cancelled")
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
                    task.emit,
                    "updated",
                    task.status,
                    task.progress,
                    f"Track {idx + 1}/{total}: {info.get('title', '?')}",
                )
        elif d["status"] == "finished":
            info = d.get("info_dict", {})
            idx = vid_to_idx.get(info.get("id", ""), -1)
            if idx >= 0:
                task._dl_progress[idx] = 1.0
                overall = sum(task._dl_progress) / total
                task.progress = overall
                GLib.idle_add(
                    task.emit,
                    "updated",
                    task.status,
                    task.progress,
                    f"Track {idx + 1}/{total}: {info.get('title', '?')}",
                )

    def _do_import(self, task):
        album_dir = task.data.get("album_dir")
        if not album_dir or not os.path.isdir(album_dir):
            raise FileNotFoundError(f"Album directory not found: {album_dir}")

        from skimmer.config import resolve_path

        files = sorted(os.listdir(album_dir))
        music_dir = resolve_path(self.config, "music_dir")
        beets_db = resolve_path(self.config, "beets_lib")
        log.info(f"[skimmer] Importing {len(files)} files from {album_dir}")
        log.info(f"[skimmer]   music_dir = {music_dir}")
        log.info(f"[skimmer]   beets_lib = {beets_db}")
        task.emit("updated", task.status, 0.0, "Tagging files...")

        artist = task.data.get("artist", "")
        album_title = task.data.get("title", "")
        if artist and album_title:
            from mutagen import File as MutagenFile
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError
            from mutagen.mp4 import MP4

            tagged = 0
            try:
                for fname in files:
                    if task.cancelled:
                        raise TaskCancelled("Import cancelled")
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
                        log.warning(f"[skimmer]   Warning: could not tag {fname}: {e}")
            except TaskCancelled:
                shutil.rmtree(album_dir, ignore_errors=True)
                log.info("[skimmer] Import cancelled, cleaned up temp dir")
                raise
            log.info(
                f"[skimmer] Tagged {tagged}/{len(files)} files with artist={artist}, album={album_title}"
            )

        log.info("[skimmer] Copying files to music library...")
        task.emit("updated", task.status, 0.0, "Copying to music library...")

        album_dst = (
            os.path.join(music_dir, artist, album_title) if artist and album_title else album_dir
        )
        os.makedirs(album_dst, exist_ok=True)

        audio_exts = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".mp4", ".m4b", ".wav"}
        copied = []
        try:
            for fname in files:
                if task.cancelled:
                    raise TaskCancelled("Import cancelled")
                ext = os.path.splitext(fname)[1].lower()
                if ext not in audio_exts:
                    continue
                src = os.path.join(album_dir, fname)
                dst = os.path.join(album_dst, fname)
                shutil.copy2(src, dst)
                copied.append(dst)
        except TaskCancelled:
            shutil.rmtree(album_dir, ignore_errors=True)
            log.info("[skimmer] Import cancelled, cleaned up temp dir")
            raise
        log.info(f"[skimmer] Copied {len(copied)} audio files to {album_dst}")

        try:
            log.info(f"[skimmer] Opening beets library at {beets_db}")
            beets_context.set_music_dir(bytestring_path(music_dir))
            lib = Library(beets_db, directory=music_dir)

            items = []
            for fpath in copied:
                if task.cancelled:
                    raise TaskCancelled("Import cancelled")
                item = Item.from_path(fpath)
                item.add(lib)
                items.append(item)
            log.info(f"[skimmer] Added {len(items)} items to beets library")

            if items:
                album = lib.add_album(items)
                log.info(f"[skimmer] Created album '{album.album}' (id={album.id})")

                # Try beets autotag with MusicBrainz, fall back to track metadata
                try:
                    from beets.autotag.match import tag_album

                    album_items = list(album.items())
                    _, _, proposal = tag_album(
                        album_items, search_artist=artist, search_name=album_title
                    )
                    if proposal and proposal.candidates:
                        match = proposal.candidates[0]
                        match.apply_metadata()
                        for item in match.mapping:  # pyright: ignore[reportAttributeAccessIssue]
                            item.try_write()
                            item.store()
                        album.albumartist = match.info.artist
                        album.album = match.info.album
                        album.store()
                        log.info(
                            f"[skimmer] Autotag matched: {match.info.artist} - {match.info.album}"
                        )
                    else:
                        raise ValueError("No MusicBrainz candidates")
                except Exception as autotag_err:
                    log.warning(f"[skimmer] Autotag failed, using track metadata: {autotag_err}")
                    tracks = task.data.get("tracks", [])
                    if tracks:
                        for i, item in enumerate(album.items()):
                            if i < len(tracks):
                                track = tracks[i]
                                item.title = track.get("title", f"Track {i + 1}")
                                item.track = i + 1
                                item.tracktotal = len(tracks)
                                item.store()
                                try:
                                    item.try_write()
                                except Exception:
                                    pass
                        log.info(
                            f"[skimmer] Set track metadata from album data ({len(tracks)} tracks)"
                        )

                try:
                    from beetsplug.fetchart import FetchArtPlugin

                    fa = FetchArtPlugin()
                    fa.batch_fetch_art(lib, [album], force=False, quiet=True)
                except Exception as fe:
                    log.warning(f"[skimmer] fetchart for new album failed: {fe}")

            task.progress = 1.0
            log.info("[skimmer] Import complete")

            beets_query = f"album:{album_title} artist:{artist}"
            found = list(lib.items(beets_query))
            if found:
                log.info(
                    f"[skimmer] Verified: {len(found)} tracks in library for {artist} - {album_title}"
                )
            else:
                log.warning(
                    f"[skimmer] WARNING: verification found no tracks for {artist} - {album_title}"
                )
        except Exception as e:
            log.error(f"[skimmer] Beets import error: {e}")
            raise
        finally:
            shutil.rmtree(album_dir, ignore_errors=True)
            log.info("[skimmer] Cleaned up temp dir")

    def _spotify_paths(self):
        """Absolute local paths of tracks whose album is tagged 'Spotify Import'."""
        from skimmer.config import resolve_path

        beets_db = resolve_path(self.config, "beets_lib")
        if not beets_db or not os.path.exists(beets_db):
            return set()
        try:
            music_dir = resolve_path(self.config, "music_dir")
            beets_context.set_music_dir(bytestring_path(music_dir))
            lib = Library(beets_db, directory=music_dir)
            paths = set()
            for album in lib.albums():
                if getattr(album, "genre", None) != "Spotify Import":
                    continue
                for item in album.items():
                    if getattr(item, "path", None):
                        paths.add(os.fsdecode(item.path))
            return paths
        except Exception as e:
            log.warning(f"[skimmer] Sync: could not determine Spotify imports: {e}")
            return set()

    def _rel_paths_under(self, paths, root):
        root_abs = os.path.abspath(root)
        rels = set()
        for p in paths:
            p_abs = os.path.abspath(p)
            if p_abs == root_abs or p_abs.startswith(root_abs + os.sep):
                rels.add(os.path.relpath(p_abs, root_abs))
        return rels

    @staticmethod
    def _is_spotify_rel(rel, spotify_rels):
        if rel in spotify_rels:
            return True
        return any(s.startswith(rel + os.sep) for s in spotify_rels)

    def _device_locations(self, dst, rel, spotify_rels):
        """Device destination(s) for a music-relative path. Spotify imports are
        relocated under a .Spotify folder that Rockbox's database ignores."""
        if self._is_spotify_rel(rel, spotify_rels):
            return [os.path.join(dst, ".Spotify", rel)]
        return [os.path.join(dst, rel)]

    @staticmethod
    def _remove_if_exists(path):
        try:
            if os.path.isfile(path):
                os.remove(path)
                return True
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                return True
        except OSError:
            pass
        return False

    def _device_paths(self, pl, spotify_rels):
        """Map each playlist track to its absolute path on the device mount."""
        mount_path = self.config.get("mount_path", "")
        music_dir = os.path.abspath(self.config["music_dir"])
        paths = []
        for trk in pl.tracks:
            fp = trk.file_path
            if fp and os.path.isabs(fp) and os.path.abspath(fp).startswith(music_dir + os.sep):
                rel = os.path.relpath(fp, music_dir)
                sub = ".Spotify" if rel in spotify_rels else ""
                paths.append(os.path.join(mount_path, "Music", sub, rel))
            else:
                paths.append(fp)
        return paths

    def _do_sync(self, task):
        src = self.config["music_dir"]
        dst = os.path.join(self.config["mount_path"], "Music")
        if not os.path.isdir(src):
            raise FileNotFoundError(f"Source directory not found: {src}")
        os.makedirs(dst, exist_ok=True)

        spotify_rels = self._rel_paths_under(self._spotify_paths(), src)
        spotify_dir = os.path.join(dst, ".Spotify")
        if spotify_rels:
            os.makedirs(spotify_dir, exist_ok=True)
            ignore_file = os.path.join(spotify_dir, "database.ignore")
            if not os.path.exists(ignore_file):
                with open(ignore_file, "w", encoding="utf-8"):
                    pass
                log.info(f"[skimmer] Sync: wrote database.ignore in {spotify_dir}")

        cache_path = os.path.join(self.config["mount_path"], ".skimmer-cache.json")

        log.info(f"[skimmer] Sync: {src} -> {dst}")

        cached = synccache.load_cache(cache_path, None)
        if cached is not None:
            log.info(f"[skimmer] Sync: loaded cache from {cache_path} ({len(cached)} files)")
        else:
            log.info(f"[skimmer] Sync: no cache found at {cache_path}")

        GLib.idle_add(task.emit, "updated", task.status, 0.0, "Indexing files...")

        if cached is not None:
            added, modified, deleted = synccache.get_changes(src, cached)
            log.info(
                f"[skimmer] Sync: diff from cache — +{len(added)} ~{len(modified)} -{len(deleted)}"
            )
            if modified:
                for p in sorted(modified)[:5]:
                    log.info(f"[skimmer] Sync:   modified: {p}")

            for rel in sorted(spotify_rels):
                stale = os.path.join(dst, rel)
                if os.path.lexists(stale):
                    log.info(f"[skimmer] Sync: removing stale Spotify copy at {stale}")
                    self._remove_if_exists(stale)

            if not added and not modified and not deleted:
                log.info("[skimmer] Sync: no changes, skipping copy")
                GLib.idle_add(task.emit, "updated", task.status, 1.0, "Already up to date")
                task.progress = 1.0
                return

            removed_rels = set()
            for p in sorted(deleted):
                if task.cancelled:
                    log.info("[skimmer] Sync: cancelled during deletions")
                    self._persist_sync_cache(cache_path, src, cached, set(), removed_rels)
                    raise TaskCancelled("Sync cancelled")
                for dst_path in self._device_locations(dst, p, spotify_rels):
                    if self._remove_if_exists(dst_path):
                        removed_rels.add(p)

            to_transfer = sorted(added) + sorted(modified)
            if not to_transfer:
                for p in sorted(deleted)[:10]:
                    log.info(f"[skimmer] Sync:   deleted: {p}")
                log.info("[skimmer] Sync: only deletions, skipping copy")
                GLib.idle_add(task.emit, "updated", task.status, 0.95, "Saving cache...")
                synccache.update_cache(cache_path, src)
                log.info(f"[skimmer] Sync: cache saved to {cache_path}")
                task.progress = 1.0
                GLib.idle_add(task.emit, "updated", task.status, 1.0, "Sync complete")
                log.info("[skimmer] Sync complete (deletions only)")
                return
        else:
            to_transfer = sorted(synccache._walk(src))
            log.info(f"[skimmer] Sync: first sync — {len(to_transfer)} files")
            cached = None
            removed_rels = set()

        total = len(to_transfer)
        log.info(f"[skimmer] Sync: copying {total} files")
        GLib.idle_add(task.emit, "updated", task.status, 0.0, f"Copying {total} files...")

        completed = 0
        last_tick = -1
        failed = []
        copied_rels = set()

        for p in to_transfer:
            if task.cancelled:
                log.info(f"[skimmer] Sync: cancelled after {completed} files")
                self._persist_sync_cache(cache_path, src, cached, copied_rels, removed_rels)
                raise TaskCancelled("Sync cancelled")
            src_path = os.path.join(src, p)
            dst_path = self._device_locations(dst, p, spotify_rels)[0]
            try:
                if os.path.isdir(src_path):
                    os.makedirs(dst_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    try:
                        shutil.copy2(src_path, dst_path)
                    except Exception:
                        self._remove_if_exists(dst_path)
                        raise
                    copied_rels.add(p)
            except Exception as e:
                log.warning(f"[skimmer] Sync:   failed to copy {p}: {e}")
                failed.append(p)
            completed += 1
            pct = completed / total
            tick = int(pct * 50)
            if tick != last_tick:
                last_tick = tick
                task.progress = pct
                GLib.idle_add(
                    task.emit, "updated", task.status, pct, f"Copying... ({completed}/{total})"
                )
            if completed <= 5 or completed % 50 == 0:
                log.info(f"[skimmer] Sync:   {completed}/{total}: {p[:120]}")

        if failed:
            log.warning(f"[skimmer] Sync: {len(failed)} files failed: {failed[:5]}...")
            raise RuntimeError(f"Sync failed: {len(failed)} files could not be copied")

        log.info(f"[skimmer] Sync: copy finished ({completed} files)")
        GLib.idle_add(task.emit, "updated", task.status, 0.85, "Syncing playlists...")
        self._sync_playlists(task, dst, spotify_rels)
        if task.cancelled:
            log.info("[skimmer] Sync: cancelled during playlist sync")
            synccache.update_cache(cache_path, src)
            raise TaskCancelled("Sync cancelled")
        self._sync_podcasts(task)
        if task.cancelled:
            log.info("[skimmer] Sync: cancelled during podcast sync")
            synccache.update_cache(cache_path, src)
            raise TaskCancelled("Sync cancelled")
        GLib.idle_add(task.emit, "updated", task.status, 0.95, "Saving cache...")
        synccache.update_cache(cache_path, src)
        log.info(f"[skimmer] Sync: cache saved to {cache_path}")
        task.progress = 1.0
        GLib.idle_add(task.emit, "updated", task.status, 1.0, "Sync complete")
        log.info(f"[skimmer] Sync complete ({completed} files)")

    def _persist_sync_cache(self, cache_path, src, cached, copied_rels, removed_rels):
        files = {}
        if cached:
            files = {k: v for k, v in cached.items() if k not in removed_rels}
        for rel in copied_rels:
            path = os.path.join(src, rel)
            try:
                st = os.stat(path)
                files[rel] = (int(st.st_mtime), st.st_size, synccache._quick_hash(path))
            except OSError:
                pass
        try:
            synccache.save_cache(cache_path, src, files)
            log.info(
                f"[skimmer] Sync: persisted partial cache ({len(files)} files) to {cache_path}"
            )
        except Exception as e:
            log.warning(f"[skimmer] Sync: failed to persist cache on cancel: {e}")

    def _sync_playlists(self, task, music_dst, spotify_rels=None):
        mount_path = self.config.get("mount_path", "")
        if not mount_path:
            return
        device_root = os.path.realpath(mount_path)
        playlist_dir = os.path.join(device_root, "Playlists")
        os.makedirs(playlist_dir, exist_ok=True)
        if spotify_rels is None:
            spotify_rels = set()

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
            if task.cancelled:
                log.info("[skimmer] Sync: playlist sync cancelled")
                return
            dev_info = device_m3us.pop(name, None)
            if dev_info:
                dev_path, dev_mtime = dev_info
                if dev_mtime > pl.last_modified:
                    log.info(f"[skimmer] Sync: playlist '{name}' newer on device — importing")
                    parsed = parse_m3u8(dev_path)
                    if parsed:
                        pl.tracks = parsed.tracks
                        pl.last_modified = dev_mtime
                        changed = True
                else:
                    log.info(f"[skimmer] Sync: playlist '{name}' newer in app — exporting")
                    export_m3u8(pl, dev_path, paths=self._device_paths(pl, spotify_rels))
                    pl.last_modified = time.time()
                    changed = True
            else:
                if pl.tracks:
                    out_path = os.path.join(playlist_dir, f"{name}.m3u8")
                    log.info(f"[skimmer] Sync: creating playlist '{name}' on device")
                    export_m3u8(pl, out_path, paths=self._device_paths(pl, spotify_rels))
                    pl.last_modified = time.time()
                    changed = True

        for name, (dev_path, _) in device_m3us.items():
            if task.cancelled:
                log.info("[skimmer] Sync: playlist sync cancelled")
                return
            log.info(f"[skimmer] Sync: importing new playlist '{name}' from device")
            parsed = parse_m3u8(dev_path)
            if parsed:
                app_playlists.append(parsed)
                changed = True

        if changed:
            save_playlists(app_playlists)
            log.info(f"[skimmer] Sync: playlists synced ({len(app_playlists)} playlists)")
        else:
            log.info("[skimmer] Sync: playlists already up to date")

    def _sync_podcasts(self, task):
        mount_path = self.config.get("mount_path", "")
        if not mount_path:
            return
        from skimmer.config import resolve_podcasts_dir

        podcasts_dir = resolve_podcasts_dir(self.config)
        if not os.path.isdir(podcasts_dir):
            log.info("[skimmer] Sync: no podcasts dir, skipping podcast sync")
            return
        pod_dst = os.path.join(mount_path, "Podcasts")
        os.makedirs(pod_dst, exist_ok=True)
        shutil.copytree(podcasts_dir, pod_dst, dirs_exist_ok=True)
        log.info(f"[skimmer] Sync: podcasts copied to {pod_dst}")

    def _do_spotify_import(self, task):
        url = task.data.get("url", "")
        if not url:
            raise ValueError("No URL provided for Spotify import")

        log.info(f"[skimmer] Starting Spotify import: {url}")

        importer = SpotifyImporter(self.config)
        task.cancel_cb = importer.cancel

        def emit_status(msg):
            GLib.idle_add(task.emit, "updated", task.status, task.progress, msg)

        def emit_progress(current, total, msg):
            frac = current / total if total > 0 else 0
            task.progress = frac
            GLib.idle_add(task.emit, "updated", task.status, frac, msg)

        def emit_waiting(active, seconds):
            status = "waiting" if active else "running"
            msg = f"Waiting for rate limit to disperse ({seconds}s)..." if active else ""
            GLib.idle_add(task.emit, "updated", status, task.progress, msg)

        importer.set_callbacks(
            on_status=emit_status,
            on_progress=emit_progress,
            on_waiting=emit_waiting,
        )

        result = importer.import_playlist(url)
        task.data["result"] = result

        log.info(
            f"[skimmer] Spotify import complete: {result['name']}, {len(result['tracks'])} tracks"
        )

    def _do_podcast(self, task):
        url = task.data.get("url", "")
        if not url:
            raise ValueError("No URL provided for podcast download")

        downloader = PodcastDownloader(self.config)
        task.cancel_cb = downloader.cancel

        def emit_status(msg):
            GLib.idle_add(task.emit, "updated", task.status, task.progress, msg)

        def emit_progress(fraction, msg):
            task.progress = fraction
            GLib.idle_add(task.emit, "updated", task.status, fraction, msg)

        downloader.set_callbacks(on_status=emit_status, on_progress=emit_progress)

        result = downloader.download(url)
        task.data["result"] = result

        log.info(f"[skimmer] Podcast download complete: {result['title']}")
