import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

CONFIG_DIR = Path(platformdirs.user_config_dir("skimmer", ensure_exists=True))
PLAYLIST_FILE = CONFIG_DIR / "playlists.json"
COVERS_DIR = CONFIG_DIR / "covers"


@dataclass
class PlaylistTrack:
    file_path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: int = 0


@dataclass
class Playlist:
    name: str
    tracks: list[PlaylistTrack] = field(default_factory=list)
    last_modified: float = 0.0
    cover_path: str = ""
    uuid: str = ""


def _serialize(playlists: list[Playlist]) -> dict:
    return {
        "playlists": [
            {
                "name": p.name,
                "uuid": p.uuid,
                "cover_path": p.cover_path,
                "last_modified": p.last_modified,
                "tracks": [
                    {
                        "file_path": t.file_path,
                        "title": t.title,
                        "artist": t.artist,
                        "album": t.album,
                        "duration": t.duration,
                    }
                    for t in p.tracks
                ],
            }
            for p in playlists
        ]
    }


def _deserialize(data: dict) -> list[Playlist]:
    return [
        Playlist(
            name=entry["name"],
            uuid=entry.get("uuid", uuid.uuid4().hex),
            cover_path=entry.get("cover_path", ""),
            last_modified=entry.get("last_modified", 0.0),
            tracks=[
                PlaylistTrack(
                    file_path=t["file_path"],
                    title=t.get("title", ""),
                    artist=t.get("artist", ""),
                    album=t.get("album", ""),
                    duration=t.get("duration", 0),
                )
                for t in entry.get("tracks", [])
            ],
        )
        for entry in data.get("playlists", [])
    ]


def load_playlists() -> list[Playlist]:
    if PLAYLIST_FILE.exists():
        try:
            with open(PLAYLIST_FILE) as f:
                return _deserialize(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_playlists(playlists: list[Playlist]):
    PLAYLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PLAYLIST_FILE, "w") as f:
        json.dump(_serialize(playlists), f, indent=2)


def resolve_cover(playlist: Playlist) -> str | None:
    if playlist.cover_path and os.path.exists(playlist.cover_path):
        return playlist.cover_path
    for track in playlist.tracks:
        if track.file_path and os.path.exists(track.file_path):
            album_dir = os.path.dirname(track.file_path)
            for name in ("cover.jpg", "cover.png", "front.jpg", "folder.jpg", "Cover.jpg"):
                path = os.path.join(album_dir, name)
                if os.path.exists(path):
                    return path
    return None


def _find_album_cover(track: PlaylistTrack) -> str | None:
    if not track.file_path:
        return None
    album_dir = os.path.dirname(track.file_path)
    if not os.path.isdir(album_dir):
        return None
    for name in ("cover.jpg", "cover.png", "front.jpg", "folder.jpg", "Cover.jpg"):
        path = os.path.join(album_dir, name)
        if os.path.exists(path):
            return path
    return None


def generate_playlist_cover(playlist: Playlist, music_dir: str) -> str | None:
    """Generate cover from track album covers:
    - 1-3 tracks: first track's cover (256x256)
    - 4+ tracks: 2x2 grid of first 4 covers (512x512)
    Returns path to saved cover in COVERS_DIR/{uuid}.jpg, or None if no covers found.
    """
    if not playlist.tracks:
        return None

    covers = []
    for track in playlist.tracks[:4]:
        cover_path = _find_album_cover(track)
        if cover_path:
            covers.append(cover_path)

    if not covers:
        return None

    try:
        from PIL import Image
    except ImportError:
        return None

    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = COVERS_DIR / f"{playlist.uuid}.jpg"

    if len(covers) == 1:
        img = Image.open(covers[0]).convert("RGB")
        img = img.resize((256, 256), Image.Resampling.LANCZOS)
    else:
        grid_size = 512
        tile_size = 256
        img = Image.new("RGB", (grid_size, grid_size), (0x22, 0x22, 0x22))
        for i, cp in enumerate(covers[:4]):
            try:
                tile = Image.open(cp).convert("RGB")
                tile = tile.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
            except Exception:
                continue
            x = (i % 2) * tile_size
            y = (i // 2) * tile_size
            img.paste(tile, (x, y))

    img.save(out_path, "JPEG", quality=85)
    return str(out_path)


def export_m3u8(playlist: Playlist, out_path: str, paths: list[str] | None = None):
    """Write an .m3u8 file.

    Entries are written relative to the .m3u8's own directory so they resolve on
    Rockbox. When ``paths`` is given it supplies the target path for each track
    (e.g. paths on the device); otherwise each track's ``file_path`` is used.
    """
    playlist_dir = os.path.dirname(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for i, track in enumerate(playlist.tracks):
            target = paths[i] if paths is not None else track.file_path
            if target and os.path.isabs(target):
                rel = os.path.relpath(target, playlist_dir)
            else:
                rel = target
            duration_str = str(int(track.duration)) if track.duration else "-1"
            title_str = (
                f"{track.artist} - {track.title}"
                if track.artist and track.title
                else (track.title or track.artist or "Unknown")
            )
            f.write(f"#EXTINF:{duration_str},{title_str}\n")
            f.write(f"{rel}\n")


def parse_m3u8(file_path: str) -> Playlist | None:
    name = os.path.splitext(os.path.basename(file_path))[0]
    mtime = os.path.getmtime(file_path)
    tracks = []
    extinf = None
    try:
        with open(file_path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#EXTM3U"):
                    continue
                if line.startswith("#EXTINF:"):
                    extinf = line
                    continue
                if extinf is not None:
                    duration = 0
                    title = ""
                    artist = ""
                    if extinf.startswith("#EXTINF:"):
                        rest = extinf[len("#EXTINF:") :]
                        parts = rest.split(",", 1)
                        try:
                            duration = int(parts[0])
                        except ValueError:
                            duration = 0
                        if len(parts) > 1:
                            title_str = parts[1].strip()
                            if " - " in title_str:
                                artist, title = title_str.split(" - ", 1)
                            else:
                                title = title_str
                    tracks.append(
                        PlaylistTrack(
                            file_path=line,
                            title=title,
                            artist=artist,
                            duration=duration,
                        )
                    )
                    extinf = None
    except (OSError, UnicodeDecodeError):
        return None
    return Playlist(name=name, tracks=tracks, last_modified=mtime, uuid=uuid.uuid4().hex)
