import json
import os
from pathlib import Path
from unittest import mock

import pytest

from skimmer.config import (
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    load_config,
    save_config,
    DEFAULT_CONFIG,
)


def test_default_config_keys():
    expected_keys = {
        "music_dir", "beets_lib", "temp_dir", "mount_path",
        "ytdlp_format", "ytdlp_audio_format", "max_concurrent_downloads",
        "scan_interval",
    }
    assert set(DEFAULT_CONFIG) == expected_keys


def test_default_config_values():
    assert DEFAULT_CONFIG["music_dir"] == str(Path.home() / "Music")
    assert DEFAULT_CONFIG["temp_dir"] == "/tmp/skimmer"
    assert DEFAULT_CONFIG["ytdlp_format"] == "bestaudio/best"
    assert DEFAULT_CONFIG["ytdlp_audio_format"] == "mp3"
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
