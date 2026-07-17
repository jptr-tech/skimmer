import json
import os
import time


def _walk(music_dir):
    result = {}
    for root, dirs, files in os.walk(music_dir):
        for f in files:
            path = os.path.join(root, f)
            rel = os.path.relpath(path, music_dir)
            try:
                st = os.stat(path)
                result[rel] = (int(st.st_mtime), st.st_size)
            except OSError:
                pass
    return result


def load_cache(cache_path, music_dir):
    try:
        with open(cache_path) as f:
            data = json.load(f)
        if data.get("music_dir") == music_dir:
            files = data["files"]
            for k, v in files.items():
                files[k] = (int(v[0]), v[1])
            return files
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None


def save_cache(cache_path, music_dir, files):
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    data = {
        "music_dir": music_dir,
        "files": files,
        "last_synced": time.time(),
    }
    tmp = cache_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, cache_path)


def get_changes(music_dir, cached_files):
    current = _walk(music_dir)
    cached = set(cached_files.keys())
    current_set = set(current.keys())

    added = current_set - cached
    deleted = cached - current_set
    modified = {
        p for p in cached & current_set
        if current[p] != cached_files[p]
    }
    return added, modified, deleted


def update_cache(cache_path, music_dir):
    files = _walk(music_dir)
    save_cache(cache_path, music_dir, files)
    return files
