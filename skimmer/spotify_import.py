import logging
import os
import shutil
import tempfile
import time
import urllib.request

import yt_dlp
from beets import context as beets_context
from beets.library import Item, Library
from beets.util import bytestring_path
from gi.repository import GLib
from spotify_scraper import SpotifyClient

from skimmer.config import resolve_path

log = logging.getLogger(__name__)


def _is_bot_check(exc):
    """True when yt-dlp hit YouTube's 'Sign in to confirm you're not a bot' block."""
    return "sign in to confirm" in str(exc).lower()


class SpotifyImportError(Exception):
    pass


class SpotifyImporter:
    def __init__(self, config):
        self.config = config
        self._cancelled = False
        self._on_status = None
        self._on_progress = None

    def set_callbacks(self, on_status=None, on_progress=None, on_waiting=None):
        self._on_status = on_status
        self._on_progress = on_progress
        self._on_waiting = on_waiting

    def cancel(self):
        self._cancelled = True

    def _status(self, msg):
        if self._on_status:
            GLib.idle_add(self._on_status, msg)

    def _progress(self, current, total, msg=""):
        if self._on_progress:
            GLib.idle_add(self._on_progress, current, total, msg)

    def _waiting(self, active, seconds):
        if self._on_waiting:
            GLib.idle_add(self._on_waiting, active, seconds)

    def _wait_cancellable(self, seconds):
        remaining = int(seconds)
        while remaining > 0:
            if self._cancelled:
                raise SpotifyImportError("Cancelled")
            time.sleep(1)
            remaining -= 1

    def import_playlist(self, url):
        self._cancelled = False
        music_dir = resolve_path(self.config, "music_dir")
        beets_db = resolve_path(self.config, "beets_lib")

        log.info(f"[skimmer] Spotify import starting: url={url!r}")
        log.info(f"[skimmer]   music_dir={music_dir!r}, beets_db={beets_db!r}")

        self._status("Fetching playlist metadata...")
        try:
            with SpotifyClient() as client:
                playlist = client.get_playlist(url, max_tracks=None)
        except Exception as e:
            log.error(f"[skimmer] spotifyscraper get_playlist failed: {e}", exc_info=True)
            raise SpotifyImportError(f"Failed to fetch playlist: {e}")

        playlist_name = playlist.name or "Spotify Playlist"
        playlist_desc = playlist.description or ""
        playlist_image = playlist.images[0].url if playlist.images else ""
        raw_tracks = [pt.track for pt in playlist.tracks if pt.track]
        total = len(raw_tracks)

        log.info(f"[skimmer] Playlist: {playlist_name!r}, {total} tracks")
        self._status(f'Found playlist "{playlist_name}" — {total} tracks')

        lib = None
        try:
            os.makedirs(music_dir, exist_ok=True)
            beets_context.set_music_dir(bytestring_path(music_dir))
            lib = Library(beets_db, directory=music_dir)
        except Exception as e:
            log.error(f"[skimmer] Failed to open beets library {beets_db}: {e}")
            lib = None

        if lib:
            self._cleanup_orphans(lib, music_dir)

        matched = 0
        to_download = []
        playlist_tracks = []
        failed = []

        for t in raw_tracks:
            if self._cancelled:
                raise SpotifyImportError("Cancelled")

            title = t.name
            artist = t.artists[0].name if t.artists else ""
            album_name = t.album.name if t.album else ""
            duration = t.duration_ms // 1000 if t.duration_ms else 0

            found_path = None
            if lib:
                safe_artist = artist.replace("'", "")
                safe_title = title.replace("'", "")
                try:
                    found = list(lib.items(f"artist:{safe_artist} title:{safe_title}"))
                    for f in found:
                        path = os.fsdecode(f.path)
                        if os.path.isfile(path) and os.path.getsize(path) > 0:
                            found_path = path
                            break
                    if found_path:
                        matched += 1
                except Exception:
                    pass

            cover_url = t.album.images[0].url if t.album and t.album.images else ""
            track_entry = {
                "file_path": found_path or "",
                "title": title,
                "artist": artist,
                "album": album_name,
                "duration": duration,
                "cover_url": cover_url,
            }
            playlist_tracks.append(track_entry)

            if found_path:
                log.info(f"[skimmer]  ✓ Already in library: {artist} — {title}")
            else:
                to_download.append(track_entry)
                log.info(f"[skimmer]  ⬇ Need download: {artist} — {title}")

        self._status(f"{matched} already in library, {len(to_download)} need download")

        if to_download:
            self._download_tracks(to_download, music_dir, lib, failed)

        result = {
            "name": playlist_name,
            "description": playlist_desc,
            "cover_url": playlist_image,
            "tracks": playlist_tracks,
            "failed": failed,
            "total": total,
            "matched": matched,
            "downloaded": len(to_download) - len(failed),
        }

        log.info(
            f"[skimmer] Import result: {matched} matched, "
            f"{len(to_download) - len(failed)} downloaded, "
            f"{len(failed)} failed, {total} total"
        )
        self._status("Import complete")
        return result

    def _cleanup_orphans(self, lib, music_dir):
        """Delete audio files under music_dir that are not indexed in beets."""
        try:
            known = {os.fsdecode(i.path) for i in lib.items() if i.path}
        except Exception as e:
            log.warning(f"[skimmer] Could not read beets items during cleanup: {e}")
            return

        removed = 0
        for root, _dirs, files in os.walk(music_dir):
            for fname in files:
                if fname.startswith(".") and fname.endswith(".partial"):
                    continue
                if not fname.lower().endswith((".mp3", ".m4a", ".flac", ".opus")):
                    continue
                fpath = os.path.join(root, fname)
                if fpath in known or os.path.realpath(fpath) in known:
                    continue
                try:
                    os.remove(fpath)
                    removed += 1
                    log.info(f"[skimmer] Removed orphan: {fpath}")
                except OSError as e:
                    log.warning(f"[skimmer] Failed to remove orphan {fpath}: {e}")
        if removed:
            log.info(f"[skimmer] Cleanup removed {removed} orphan audio file(s)")

    def _download_tracks(self, to_download, music_dir, lib, failed):
        if lib is None:
            raise SpotifyImportError(
                "Beets library is unavailable; refusing to import so files are not orphaned."
            )

        temp_dir = tempfile.mkdtemp(prefix="skimmer-spotify-")
        log.info(f"[skimmer] Download temp dir: {temp_dir}")

        try:
            total = len(to_download)
            for idx, entry in enumerate(to_download):
                if self._cancelled:
                    raise SpotifyImportError("Cancelled")

                artist = entry["artist"]
                title = entry["title"]
                self._progress(
                    idx + 1, total, f"Downloading ({idx + 1}/{total}): {artist} - {title}"
                )
                self._status(f"Downloading ({idx + 1}/{total}): {artist} - {title}")

                search_query = f"ytsearch:{artist} - {title}"
                out_template = os.path.join(temp_dir, f"{idx:04d}-%(title)s.%(ext)s")

                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": out_template,
                    "default_search": "ytsearch",
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                    "progress_hooks": [self._download_hook],
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                        }
                    ],
                }

                max_retries = int(self.config.get("ytdlp_max_retries", 4))
                base_wait = int(self.config.get("ytdlp_retry_wait", 60))

                try:
                    attempt = 0
                    while True:
                        try:
                            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                                ydl.download([search_query])
                            break
                        except Exception as e:
                            if _is_bot_check(e) and attempt < max_retries:
                                wait = base_wait * (2**attempt)
                                log.warning(
                                    f"[skimmer] YouTube bot-check on '{artist} - {title}', "
                                    f"waiting {wait}s then retrying"
                                )
                                self._waiting(True, wait)
                                self._wait_cancellable(wait)
                                self._waiting(False, 0)
                                attempt += 1
                            elif _is_bot_check(e):
                                raise SpotifyImportError(
                                    "YouTube is temporarily blocking downloads (bot check). "
                                    "Wait a while and try again."
                                )
                            else:
                                raise

                    # Find the downloaded file
                    found_file = None
                    for fname in os.listdir(temp_dir):
                        if fname.startswith(f"{idx:04d}-"):
                            fpath = os.path.join(temp_dir, fname)
                            if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                                found_file = fpath
                                break

                    if not found_file:
                        log.warning(f"[skimmer] No output file found for {artist} - {title}")
                        failed.append(f"{artist} - {title}")
                        continue

                    log.info(f"[skimmer] Downloaded: {os.path.basename(found_file)}")

                    # Tag the file
                    try:
                        self._tag_file(found_file, entry)
                    except Exception as e:
                        log.warning(f"[skimmer] Tagging failed: {e}")

                    # Copy to music library
                    artist_dir = artist or "Unknown Artist"
                    album_dir_name = entry["album"] or title or "Unknown Album"
                    dest_dir = os.path.join(music_dir, artist_dir, album_dir_name)
                    os.makedirs(dest_dir, exist_ok=True)

                    dest_path = os.path.join(dest_dir, os.path.basename(found_file))
                    partial_path = os.path.join(
                        dest_dir, f".{os.path.basename(found_file)}.partial"
                    )
                    shutil.copy2(found_file, partial_path)
                    os.replace(partial_path, dest_path)
                    entry["file_path"] = dest_path

                    # Download album cover
                    cover_url = entry.get("cover_url", "")
                    if cover_url and not os.path.exists(os.path.join(dest_dir, "cover.jpg")):
                        try:
                            urllib.request.urlretrieve(
                                cover_url, os.path.join(dest_dir, "cover.jpg")
                            )
                            log.info(f"[skimmer] Saved cover: {dest_dir}/cover.jpg")
                        except Exception as ce:
                            log.warning(f"[skimmer] Failed to save cover: {ce}")

                    log.info(f"[skimmer] Copied to: {dest_path}")

                    # Index immediately so a mid-import abort never leaves an orphan file.
                    try:
                        self._import_one_track(lib, music_dir, entry, dest_path)
                    except Exception as e:
                        log.warning(f"[skimmer] Beets index failed for {dest_path}: {e}")
                        try:
                            os.remove(dest_path)
                        except OSError:
                            pass
                        failed.append(f"{artist} - {title}")
                        entry["file_path"] = ""
                        continue

                except SpotifyImportError:
                    raise
                except Exception as e:
                    if self._cancelled:
                        raise SpotifyImportError("Cancelled")
                    log.warning(f"[skimmer] Download failed for {artist} - {title}: {e}")
                    failed.append(f"{artist} - {title}")

        except SpotifyImportError:
            raise
        except Exception as e:
            log.error(f"[skimmer] Download phase failed: {e}", exc_info=True)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            log.info(f"[skimmer] Cleaned up temp dir {temp_dir}")

    def _download_hook(self, d):
        if self._cancelled:
            raise SpotifyImportError("Cancelled")

    def _tag_file(self, fpath, entry):
        ext = os.path.splitext(fpath)[1].lower()
        from mutagen import File as MutagenFile

        try:
            if ext == ".mp3":
                from mutagen.easyid3 import EasyID3

                try:
                    audio = EasyID3(fpath)
                except Exception:
                    audio = MutagenFile(fpath, easy=True)
                    audio.add_tags()
            elif ext in (".m4a", ".mp4", ".m4b"):
                from mutagen.mp4 import MP4

                audio = MP4(fpath)
            elif ext == ".flac":
                from mutagen.flac import FLAC

                audio = FLAC(fpath)
            elif ext == ".opus":
                from mutagen.oggopus import OggOpus

                audio = OggOpus(fpath)
            else:
                return
            audio["artist"] = entry["artist"]
            audio["album"] = entry["album"] or entry["title"]
            audio["albumartist"] = entry["artist"]
            audio["title"] = entry["title"]
            audio.save()
        except Exception as e:
            log.warning(f"[skimmer] Failed to tag {fpath}: {e}")

    def _import_one_track(self, lib, music_dir, entry, fpath):
        artist = entry["artist"]
        album_title = entry["album"] or entry["title"]
        item = Item.from_path(fpath)
        item.add(lib)
        album_obj = lib.add_album([item])
        album_obj.genre = "Spotify Import"
        album_obj.store()
        log.info(
            f"[skimmer] Indexed '{item.title}' -> album '{album_obj.album}' (id={album_obj.id})"
        )
        try:
            from beets.autotag.match import tag_album

            album_items = list(album_obj.items())
            _, _, proposal = tag_album(album_items, search_artist=artist, search_name=album_title)
            if proposal and proposal.candidates:
                match = proposal.candidates[0]
                match.apply_metadata()
                for item in match.mapping:  # pyright: ignore[reportAttributeAccessIssue]
                    item.try_write()
                album_obj.albumartist = match.info.artist
                album_obj.album = match.info.album
                album_obj.store()
                log.info(f"[skimmer] Autotagged: {match.info.artist} - {match.info.album}")
        except Exception as e:
            log.warning(f"[skimmer] Autotag skipped: {e}")
