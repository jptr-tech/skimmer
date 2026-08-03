import os
import tempfile

import pytest
from beets.library import Item, Library

from skimmer.spotify_import import SpotifyImporter, SpotifyImportError


def _tmp_lib():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "b.db")
    music = os.path.join(tmp, "music")
    os.makedirs(music, exist_ok=True)
    lib = Library(db, directory=music)
    return tmp, music, lib


def _write_audio(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00" * 128)


def test_cleanup_orphans_removes_unindexed_files():
    _t, music, lib = _tmp_lib()
    orphan = os.path.join(music, "Orphan Artist", "Orphan Album", "orphan.mp3")
    _write_audio(orphan)
    importer = SpotifyImporter({})
    importer._cleanup_orphans(lib, music)
    assert not os.path.exists(orphan)


def test_cleanup_orphans_keeps_indexed_files():
    _t, music, lib = _tmp_lib()
    known = os.path.join(music, "Artist", "Album", "track.mp3")
    _write_audio(known)
    item = Item(path=os.fsencode(known))
    lib.add(item)
    importer = SpotifyImporter({})
    importer._cleanup_orphans(lib, music)
    assert os.path.exists(known)


def test_cleanup_orphans_ignores_partial_files():
    _t, music, lib = _tmp_lib()
    partial = os.path.join(music, ".track.mp3.partial")
    _write_audio(partial)
    importer = SpotifyImporter({})
    importer._cleanup_orphans(lib, music)
    assert os.path.exists(partial)


def test_download_tracks_aborts_without_lib():
    importer = SpotifyImporter({})
    with pytest.raises(SpotifyImportError):
        importer._download_tracks([{"artist": "A", "title": "T"}], "/tmp", None, [])
