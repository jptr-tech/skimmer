import json
import os
import tempfile
from pathlib import Path

import platformdirs

CONFIG_DIR = Path(platformdirs.user_config_dir("skimmer", ensure_exists=True))
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "music_dir": "",
    "beets_lib": "",
    "temp_dir": os.path.join(tempfile.gettempdir(), "skimmer"),
    "mount_path": "",
    "ytdlp_format": "bestaudio/best",
    "ytdlp_audio_format": "mp3",
    "max_concurrent_downloads": 2,
    "scan_interval": 1800,
}

_BEETS_FALLBACKS = {
    "music_dir": "~/Music",
    "beets_lib": "~/Music/.musiclibrary.db",
}


def _beets_config_value(skimmer_key):
    """Read a value from beets' own config. Returns None on failure."""
    beets_key = "directory" if skimmer_key == "music_dir" else "library"
    try:
        from beets import config as beets_config
        return (
            beets_config[beets_key].as_filename()
            if beets_key == "library"
            else beets_config[beets_key].get()
        )
    except Exception:
        return None


def resolve_path(config, skimmer_key):
    """Priority: skimmer override → beets config → hardcoded fallback."""
    stored = config.get(skimmer_key, "")
    if stored:
        return os.path.expanduser(stored)
    from_beets = _beets_config_value(skimmer_key)
    if from_beets:
        return os.path.expanduser(from_beets)
    return os.path.expanduser(_BEETS_FALLBACKS[skimmer_key])


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
                config.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    save_config(config)
    return config


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
