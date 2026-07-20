import os
import threading

from skimmer import synccache


# This class exists to keep the indexes are in sync by periodically walking
# both locally & on the connected device.
class BackgroundScanner:
    def __init__(self, config):
        self.config = config
        self._local_cache = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread = None
        self._local_cache_path = None
        self._on_status = None
        self._on_progress = None
        self._on_complete = None

    def set_callbacks(self, *, on_status=None, on_progress=None, on_complete=None):
        self._on_status = on_status
        self._on_progress = on_progress
        self._on_complete = on_complete

    def start(self):
        if self._thread is not None:
            return
        from skimmer.config import CONFIG_DIR

        self._local_cache_path = str(CONFIG_DIR / "local-cache.json")
        self._load_local_cache()
        loaded = self._local_cache is not None
        nfiles = len(self._local_cache) if self._local_cache else 0
        print(
            f"[skimmer] Scanner: local cache at {self._local_cache_path}"
            f"{' loaded (' + str(nfiles) + ' files)' if loaded else ' — no existing cache'}"
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[skimmer] Scanner: thread started")

    def stop(self):
        print(f"[skimmer] Scanner: stopping")
        self._stop_event.set()
        self._wake_event.set()

    def scan_now(self):
        print(f"[skimmer] Scanner: manual scan triggered")
        self._wake_event.set()

    def get_local_cache(self):
        with self._lock:
            return dict(self._local_cache) if self._local_cache else None

    def _load_local_cache(self):
        cached = synccache.load_cache(self._local_cache_path, self.config["music_dir"])
        with self._lock:
            self._local_cache = cached

    def _run(self):
        print(f"[skimmer] Scanner: initial scan starting")
        self._scan()
        while not self._stop_event.is_set():
            interval = max(10, self.config.get("scan_interval", 1800))
            print(f"[skimmer] Scanner: next scan in {interval}s")
            self._wake_event.wait(timeout=interval)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            print(f"[skimmer] Scanner: periodic scan starting")
            self._scan()

    def _scan(self):
        local_changed = self._scan_local()
        device_changed = self._scan_device()
        total_changed = local_changed + device_changed

        if self._on_complete:
            self._on_complete(total_changed)

        if self._on_status:
            self._on_status(
                "Idle" if total_changed == 0 else f"Idle ({total_changed} changes)"
            )

    def _scan_local(self):
        music_dir = self.config["music_dir"]
        if not os.path.isdir(music_dir):
            print(f"[skimmer] Scanner: local music_dir not found: {music_dir}")
            return 0

        print(f"[skimmer] Scanner: walking local {music_dir}")
        if self._on_status:
            self._on_status("Scanning local library...")

        current = synccache._walk(music_dir)
        total = len(current)
        print(f"[skimmer] Scanner: local — found {total} files")

        with self._lock:
            old_cache = dict(self._local_cache) if self._local_cache else {}

        new_cache = {}
        changed = 0
        rehashed = 0
        kept = 0
        processed = 0
        for p, (mtime, size) in current.items():
            h = None
            if p in old_cache:
                old_mtime, old_size, old_hash = old_cache[p]
                if old_mtime == mtime and old_size == size:
                    h = old_hash
                    kept += 1
                else:
                    h = synccache._quick_hash(os.path.join(music_dir, p))
                    rehashed += 1
                    if h != old_hash:
                        changed += 1
            else:
                h = synccache._quick_hash(os.path.join(music_dir, p))
                rehashed += 1
                changed += 1
            new_cache[p] = (mtime, size, h)
            processed += 1
            if self._on_progress and (processed % 100 == 0 or processed == total):
                self._on_progress(processed, total)

        deleted = len(old_cache) - len(new_cache)
        changed += max(0, deleted)

        print(
            f"[skimmer] Scanner: local — {total} files, "
            f"{kept} unchanged, {rehashed} rehashed, "
            f"{changed} changes ({max(0, deleted)} deleted)"
        )

        with self._lock:
            self._local_cache = new_cache

        synccache.save_cache(self._local_cache_path, music_dir, new_cache)
        print(f"[skimmer] Scanner: local cache saved to {self._local_cache_path}")

        if self._on_progress:
            self._on_progress(total, total)

        return changed

    def _scan_device(self):
        mount_path = self.config.get("mount_path", "")
        if not mount_path or not os.path.isdir(mount_path):
            print(f"[skimmer] Scanner: device not mounted, skipping")
            return 0

        device_music = os.path.join(mount_path, "Music")
        if not os.path.isdir(device_music):
            print(
                f"[skimmer] Scanner: device Music dir not found: {device_music}, skipping"
            )
            return 0

        device_cache = os.path.join(mount_path, ".skimmer-cache.json")
        print(f"[skimmer] Scanner: walking device {device_music}")
        if self._on_status:
            self._on_status("Scanning device...")

        current = synccache._walk(device_music)
        total = len(current)
        print(f"[skimmer] Scanner: device — found {total} files")

        old_cache = synccache.load_cache(device_cache, device_music) or {}
        print(
            f"[skimmer] Scanner: device — loaded cache ({len(old_cache)} files)"
            if old_cache
            else "[skimmer] Scanner: device — no existing cache"
        )

        new_cache = {}
        changed = 0
        rehashed = 0
        kept = 0
        processed = 0
        for p, (mtime, size) in current.items():
            h = None
            if p in old_cache:
                old_mtime, old_size, old_hash = old_cache[p]
                if old_mtime == mtime and old_size == size:
                    h = old_hash
                    kept += 1
                else:
                    h = synccache._quick_hash(os.path.join(device_music, p))
                    rehashed += 1
                    if h != old_hash:
                        changed += 1
            else:
                h = synccache._quick_hash(os.path.join(device_music, p))
                rehashed += 1
                changed += 1
            new_cache[p] = (mtime, size, h)
            processed += 1
            if self._on_progress and (processed % 100 == 0 or processed == total):
                self._on_progress(processed, total)

        deleted = len(old_cache) - len(new_cache)
        changed += max(0, deleted)

        print(
            f"[skimmer] Scanner: device — {total} files, "
            f"{kept} unchanged, {rehashed} rehashed, "
            f"{changed} changes ({max(0, deleted)} deleted)"
        )

        synccache.save_cache(device_cache, device_music, new_cache)
        print(f"[skimmer] Scanner: device cache saved to {device_cache}")

        if self._on_progress:
            self._on_progress(total, total)

        return changed
