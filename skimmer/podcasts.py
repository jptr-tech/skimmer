import glob
import logging
import os
import urllib.request

import yt_dlp
from gi.repository import GLib

from skimmer.config import resolve_podcasts_dir

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".opus", ".flac", ".wav", ".ogg", ".mp4"}


class PodcastError(Exception):
    pass


class PodcastDownloader:
    def __init__(self, config):
        self.config = config
        self._on_status = None
        self._on_progress = None

    def set_callbacks(self, on_status=None, on_progress=None):
        self._on_status = on_status
        self._on_progress = on_progress

    def _status(self, msg):
        if self._on_status:
            GLib.idle_add(self._on_status, msg)

    def _progress(self, fraction, msg=""):
        if self._on_progress:
            GLib.idle_add(self._on_progress, fraction, msg)

    def download(self, url):
        podcasts_dir = resolve_podcasts_dir(self.config)
        os.makedirs(podcasts_dir, exist_ok=True)
        log.info(f"[skimmer] Podcast download starting: {url!r} -> {podcasts_dir}")

        self._status("Fetching video info...")
        ydl_opts = {
            "format": self.config.get("ytdlp_format", "bestaudio/best"),
            "outtmpl": os.path.join(podcasts_dir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": self.config.get("ytdlp_audio_format", "mp3"),
                }
            ],
            "progress_hooks": [self._hook],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as e:
            log.error(f"[skimmer] Podcast download failed: {e}")
            raise PodcastError(f"Download failed: {e}")

        file_path = self._find_output(info, podcasts_dir)
        title = info.get("title") or os.path.splitext(os.path.basename(file_path))[0]
        stem = os.path.splitext(os.path.basename(file_path))[0]

        thumb_path = ""
        thumbnail = info.get("thumbnail")
        if thumbnail:
            thumb_path = os.path.join(podcasts_dir, f"{stem}.jpg")
            try:
                urllib.request.urlretrieve(thumbnail, thumb_path)
            except Exception as e:
                log.warning(f"[skimmer] Podcast thumbnail failed: {e}")
                thumb_path = ""

        log.info(f"[skimmer] Podcast saved: {file_path} (thumb: {thumb_path or 'none'})")
        return {
            "title": title,
            "file_path": file_path,
            "thumb_path": thumb_path,
            "url": url,
        }

    def _hook(self, d):
        if d["status"] == "downloading":
            try:
                pct_str = d.get("_percent_str", "0%").strip().replace("%", "")
                frac = float(pct_str) / 100.0
            except (ValueError, KeyError):
                frac = 0.0
            self._progress(frac, f"Downloading... {d.get('_percent_str', '').strip()}")
        elif d["status"] == "finished":
            self._progress(1.0, "Processing audio...")

    @staticmethod
    def _find_output(info, podcasts_dir):
        try:
            downloads = info.get("requested_downloads") or []
            if (
                downloads
                and downloads[0].get("filepath")
                and os.path.exists(downloads[0]["filepath"])
            ):
                return downloads[0]["filepath"]
        except Exception:
            pass
        candidates = []
        for ext in AUDIO_EXTS:
            candidates.extend(glob.glob(os.path.join(podcasts_dir, f"*{ext}")))
        if candidates:
            return max(candidates, key=os.path.getmtime)
        raise PodcastError("Download finished but no audio file was found")
