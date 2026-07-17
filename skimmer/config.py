import getpass
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "y1-skimmer"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "music_dir": str(Path.home() / "Music"),
    "beets_lib": str(Path.home() / "Music" / ".musiclibrary.db"),
    "temp_dir": "/tmp/y1-skimmer",
    "y1_mount_path": f"/run/media/{getpass.getuser()}/disk",
    "ytdlp_format": "bestaudio/best",
    "ytdlp_audio_format": "mp3",
    "max_concurrent_downloads": 2,
}


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
