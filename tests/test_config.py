import json
import os
import tempfile
from unittest import mock

from skimmer.config import (
    DEFAULT_CONFIG,
    load_config,
    save_config,
)


def test_default_config_keys():
    expected_keys = {
        "music_dir",
        "beets_lib",
        "temp_dir",
        "mount_path",
        "podcasts_dir",
        "ytdlp_format",
        "ytdlp_audio_format",
        "ytdlp_retry_wait",
        "ytdlp_max_retries",
        "max_concurrent_downloads",
        "scan_interval",
    }
    assert set(DEFAULT_CONFIG) == expected_keys


def test_default_config_values():
    assert DEFAULT_CONFIG["music_dir"] == ""
    assert DEFAULT_CONFIG["beets_lib"] == ""
    assert DEFAULT_CONFIG["temp_dir"] == os.path.join(tempfile.gettempdir(), "skimmer")
    assert DEFAULT_CONFIG["mount_path"] == ""
    assert DEFAULT_CONFIG["podcasts_dir"] == ""
    assert DEFAULT_CONFIG["ytdlp_format"] == "bestaudio/best"
    assert DEFAULT_CONFIG["ytdlp_audio_format"] == "mp3"
    assert DEFAULT_CONFIG["ytdlp_retry_wait"] == 60
    assert DEFAULT_CONFIG["ytdlp_max_retries"] == 4
    assert DEFAULT_CONFIG["max_concurrent_downloads"] == 2
    assert DEFAULT_CONFIG["scan_interval"] == 1800


class TestLoadConfig:
    def test_no_existing_file_creates_default(self, tmp_path):
        with mock.patch("skimmer.config.CONFIG_DIR", tmp_path):
            with mock.patch("skimmer.config.CONFIG_FILE", tmp_path / "config.json"):
                config = load_config()
                assert config["music_dir"] == DEFAULT_CONFIG["music_dir"]
                assert (tmp_path / "config.json").exists()

    def test_loads_saved_values(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        saved = {"music_dir": "/custom/music", "ytdlp_format": "worstaudio"}
        cfg_file.write_text(json.dumps(saved))
        with mock.patch("skimmer.config.CONFIG_DIR", tmp_path):
            with mock.patch("skimmer.config.CONFIG_FILE", cfg_file):
                config = load_config()
                assert config["music_dir"] == "/custom/music"
                assert config["ytdlp_format"] == "worstaudio"
                assert config["beets_lib"] == DEFAULT_CONFIG["beets_lib"]

    def test_corrupted_file_falls_back_to_defaults(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{{{ not json }}}")
        with mock.patch("skimmer.config.CONFIG_DIR", tmp_path):
            with mock.patch("skimmer.config.CONFIG_FILE", cfg_file):
                config = load_config()
                for k in DEFAULT_CONFIG:
                    assert config[k] == DEFAULT_CONFIG[k]

    def test_unknown_keys_are_preserved(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"custom_key": "custom_val"}))
        with mock.patch("skimmer.config.CONFIG_DIR", tmp_path):
            with mock.patch("skimmer.config.CONFIG_FILE", cfg_file):
                config = load_config()
                assert config["custom_key"] == "custom_val"


class TestSaveConfig:
    def test_saves_to_file(self, tmp_path):
        cfg_file = tmp_path / "config.json"
        with mock.patch("skimmer.config.CONFIG_DIR", tmp_path):
            with mock.patch("skimmer.config.CONFIG_FILE", cfg_file):
                test_cfg = {"key": "val", "num": 42}
                save_config(test_cfg)
                assert cfg_file.exists()
                loaded = json.loads(cfg_file.read_text())
                assert loaded == test_cfg

    def test_creates_directory(self, tmp_path):
        nested = tmp_path / "a" / "b"
        cfg_file = nested / "config.json"
        with mock.patch("skimmer.config.CONFIG_DIR", nested):
            with mock.patch("skimmer.config.CONFIG_FILE", cfg_file):
                save_config({"x": 1})
                assert cfg_file.exists()
                assert json.loads(cfg_file.read_text()) == {"x": 1}
