import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

import gi
gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib

import yt_dlp
from beets import context as beets_context
from beets.library import Item, Library
from beets.util import bytestring_path
from spotify_scraper import SpotifyClient

from skimmer.config import resolve_path

import logging
log = logging.getLogger(__name__)


class SpotifyImportError(Exception):
    pass


class SpotifyImporter:
    def __init__(self, config):
        self.config = config
        self._cancelled = False
        self._on_status = None
        self._on_progress = None

    def set_callbacks(self, on_status=None, on_progress=None):
        self._on_status = on_status
        self._on_progress = on_progress

    def cancel(self):
        self._cancelled = True

    def _status(self, msg):
        if self._on_status:
            GLib.idle_add(self._on_status, msg)

    def _progress(self, current, total, msg=""):
        if self._on_progress:
            GLib.idle_add(self._on_progress, current, total, msg)

    def import_playlist(self, url):
        self._cancelled = False
        music_dir = resolve_path(self.config, "music_dir")
        beets_db = resolve_path(self.config, "beets_lib")

        log.info(f"[skimmer] Spotify import starting: url={url!r}")
        log.info(f"[skimmer]   music_dir={music_dir!r}, beets_db={beets_db!r}")

        self._status("Fetching playlist metadata...")
        try:
            with SpotifyClient() as client:
                playlist = client.get_playlist(url)
        except Exception as e:
            log.error(f"[skimmer] spotifyscraper get_playlist failed: {e}", exc_info=True)
            raise SpotifyImportError(f"Failed to fetch playlist: {e}")

        playlist_name = playlist.name or "Spotify Playlist"
        playlist_desc = playlist.description or ""
        playlist_image = playlist.images[0].url if playlist.images else ""
        raw_tracks = [pt.track for pt in playlist.tracks if pt.track]
        total = len(raw_tracks)

        log.info(f"[skimmer] Playlist: {playlist_name!r}, {total} tracks")
        self._status(f"Found playlist \"{playlist_name}\" — {total} tracks")

        lib = None
        if os.path.exists(beets_db):
            beets_context.set_music_dir(bytestring_path(music_dir))
            lib = Library(beets_db, directory=music_dir)

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
                    if found:
                        found_path = os.fsdecode(found[0].path)
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

    def _download_tracks(self, to_download, music_dir, lib, failed):
        temp_dir = tempfile.mkdtemp(prefix="skimmer-spotify-")
        log.info(f"[skimmer] Download temp dir: {temp_dir}")

        try:
            total = len(to_download)
            for idx, entry in enumerate(to_download):
                if self._cancelled:
                    raise SpotifyImportError("Cancelled")

                artist = entry["artist"]
                title = entry["title"]
                self._progress(idx + 1, total, f"Downloading ({idx+1}/{total}): {artist} - {title}")
                self._status(f"Downloading ({idx+1}/{total}): {artist} - {title}")

                search_query = f"ytsearch:{artist} - {title}"
                out_template = os.path.join(temp_dir, f"{idx:04d}-%(title)s.%(ext)s")

                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": out_template,
                    "default_search": "ytsearch",
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                    }],
                }

                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([search_query])

                    # Find the downloaded file
                    found_file = None
                    for fname in os.listdir(temp_dir):
                        if fname.startswith(f"{idx:04d}-"):
                            fpath = os.path.join(temp_dir, fname)
                            if os.path.isfile(fpath):
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
                    shutil.copy2(found_file, dest_path)
                    entry["file_path"] = dest_path

                    # Download album cover
                    cover_url = entry.get("cover_url", "")
                    if cover_url and not os.path.exists(os.path.join(dest_dir, "cover.jpg")):
                        try:
                            urllib.request.urlretrieve(cover_url, os.path.join(dest_dir, "cover.jpg"))
                            log.info(f"[skimmer] Saved cover: {dest_dir}/cover.jpg")
                        except Exception as ce:
                            log.warning(f"[skimmer] Failed to save cover: {ce}")

                    log.info(f"[skimmer] Copied to: {dest_path}")

                except Exception as e:
                    log.error(f"[skimmer] Download failed for {artist} - {title}: {e}", exc_info=True)
                    failed.append(f"{artist} - {title}")

            # Import new files into beets
            if lib:
                new_tracks = [(t["file_path"], t["artist"], t["album"])
                              for t in to_download if t["file_path"] and t["file_path"].startswith(music_dir)]
                if new_tracks:
                    self._status("Importing into beets library...")
                    try:
                        self._import_to_beets(lib, music_dir, new_tracks)
                    except Exception as e:
                        log.warning(f"[skimmer] Beets import error: {e}", exc_info=True)

        except SpotifyImportError:
            raise
        except Exception as e:
            log.error(f"[skimmer] Download phase failed: {e}", exc_info=True)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            log.info(f"[skimmer] Cleaned up temp dir {temp_dir}")

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

    def _import_to_beets(self, lib, music_dir, track_info_list):
        seen_albums = {}
        for fpath, artist, album in track_info_list:
            if not os.path.exists(fpath):
                continue
            try:
                item = Item.from_path(fpath)
                item.add(lib)
                key = (artist, album)
                if key not in seen_albums:
                    seen_albums[key] = []
                seen_albums[key].append(item)
            except Exception as e:
                log.warning(f"[skimmer] Failed to add {fpath}: {e}")

        for (artist, album_title), items in seen_albums.items():
            if items:
                try:
                    album_obj = lib.add_album(items)
                    album_obj.genre = "Spotify Import"
                    album_obj.store()
                    log.info(f"[skimmer] Created album '{album_obj.album}' (id={album_obj.id})")
                    try:
                        from beets.autotag.match import tag_album
                        album_items = list(album_obj.items())
                        _, _, proposal = tag_album(
                            album_items, search_artist=artist, search_name=album_title
                        )
                        if proposal and proposal.candidates:
                            match = proposal.candidates[0]
                            match.apply_metadata()
                            for item in match.mapping:
                                item.try_write()
                            album_obj.albumartist = match.info.artist
                            album_obj.album = match.info.album
                            album_obj.store()
                            log.info(f"[skimmer] Autotagged: {match.info.artist} - {match.info.album}")
                    except Exception as e:
                        log.warning(f"[skimmer] Autotag skipped: {e}")
                except Exception as e:
                    log.warning(f"[skimmer] Failed to create album {album_title}: {e}")

        if lib:
            try:
                lib.store()
            except Exception:
                pass
