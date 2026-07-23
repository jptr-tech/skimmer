import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, Adw, Gdk, GdkPixbuf, Gio

from skimmer.config import load_config, save_config
from skimmer.worker import ProcessingManager, Task
from skimmer.scanner import BackgroundScanner
from skimmer.library import LibraryPage
from skimmer.search import SearchPage
from skimmer.processing import ProcessingPage
from skimmer.settings import SettingsPage
from skimmer.player import PlayerBar
from skimmer.playlists_ui import PlaylistsPage
from skimmer.media_integration import create_integration


class SkimmerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="tech.jptr.Skimmer")
        self.config = load_config()
        style_mgr = Adw.StyleManager.get_default()
        style_mgr.set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        self.proc_mgr = ProcessingManager(self.config)
        self.scanner = BackgroundScanner(self.config)
        self.connect("activate", self._on_activate)
        icon_path = os.path.join(os.path.dirname(__file__), "data", "tech.jptr.Skimmer.png")
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(icon_path)
            Gtk.Window.set_default_icon_list([pixbuf])
        except Exception:
            pass
        self._last_connected = False
        self._sync_task = None
        self._auto_sync_timer = None

    def _on_activate(self, app):
        win = Adw.ApplicationWindow(application=app)
        win.set_default_size(1100, 700)
        win.set_title("Skimmer")

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_global_key)
        win.add_controller(key_ctrl)

        toolbar_view = Adw.ToolbarView()
        win.set_content(toolbar_view)

        header = Adw.HeaderBar()

        self.player_bar = PlayerBar(self.config)
        toolbar_view.add_bottom_bar(self.player_bar)

        self.media_integration = create_integration(self.player_bar)
        self.media_integration.start()
        self.scanner.start()

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)

        self.pages = {}

        page = LibraryPage(self.config, player_bar=self.player_bar)
        self.stack.add_titled(page, "library", "Library")
        self.pages["library"] = page

        page = PlaylistsPage(self.config, player_bar=self.player_bar)
        self.stack.add_titled(page, "playlists", "Playlists")
        self.pages["playlists"] = page

        page = SearchPage(self.config, self.proc_mgr, player_bar=self.player_bar)
        self.stack.add_titled(page, "search", "Search")
        self.pages["search"] = page

        page = ProcessingPage(self.config, self.proc_mgr)
        self._proc_page = page
        self.stack.add_titled(page, "processing", "Processing")
        self.pages["processing"] = page
        self.proc_mgr.connect("task-added", self._on_proc_added)
        self.proc_mgr.connect("task-removed", self._on_proc_change)
        self._update_proc_badge()

        page = SettingsPage(self.config, self._on_save_settings, scanner=self.scanner)
        self.stack.add_titled(page, "settings", "Settings")
        self.pages["settings"] = page

        self.stack.connect("notify::visible-child", self._on_playlists_page_changed)

        self.player_bar.set_show_album_cb(
            lambda: self.stack.set_visible_child_name("library")
        )

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        header.set_title_widget(switcher)

        sync_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.sync_icon = Gtk.Label(label="\U0001f50c")
        sync_box.append(self.sync_icon)

        self.sync_label = Gtk.Label(label="")
        self.sync_label.add_css_class("dim-label")
        sync_box.append(self.sync_label)

        self.sync_spinner = Gtk.Spinner()
        self.sync_spinner.set_size_request(16, 16)
        self.sync_spinner.set_visible(False)
        sync_box.append(self.sync_spinner)

        self.scan_btn = Gtk.Button(label="Scan")
        self.scan_btn.add_css_class("flat")
        self.scan_btn.set_visible(False)
        self.scan_btn.connect("clicked", lambda b: self._check_mount())
        sync_box.append(self.scan_btn)

        self.sync_btn = Gtk.Button(label="Sync")
        self.sync_btn.add_css_class("flat")
        self.sync_btn.set_visible(False)
        self.sync_btn.connect("clicked", self._do_sync)
        sync_box.append(self.sync_btn)

        self.eject_btn = Gtk.Button(icon_name="media-eject-symbolic")
        self.eject_btn.add_css_class("flat")
        self.eject_btn.set_tooltip_text("Safely eject device")
        self.eject_btn.set_visible(False)
        self.eject_btn.connect("clicked", self._do_eject)
        sync_box.append(self.eject_btn)

        header.pack_end(sync_box)

        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self.stack)

        monitor = Gio.VolumeMonitor.get()
        monitor.connect("mount-added", self._on_mount_changed)
        monitor.connect("mount-removed", self._on_mount_changed)
        self._check_mount()

        win.present()

    def _on_playlists_page_changed(self, stack, pspec):
        child = stack.get_visible_child()
        if child is self.pages.get("playlists"):
            child._load()

    def _on_mount_changed(self, *args):
        self._check_mount()

    @staticmethod
    def _get_platform_mount_roots():
        if sys.platform == "darwin":
            return ["/Volumes"]
        return ["/run/media", "/media"]

    @staticmethod
    def _path_matches_device(path):
        name = os.path.basename(path).lower()
        if "y1" in name or "innioasis" in name:
            return True
        if os.path.isdir(os.path.join(path, "Music")):
            return True
        return False

    def _detect_y1_mount(self):
        for mount in Gio.VolumeMonitor.get().get_mounts():
            path = mount.get_root().get_path()
            if not path:
                continue
            vol = mount.get_volume()
            name = vol.get_name().lower() if vol else ""
            if "y1" in name or "innioasis" in name:
                return path
            if self._path_matches_device(path):
                return path
        for root in self._get_platform_mount_roots():
            if not os.path.isdir(root):
                continue
            try:
                for name in os.listdir(root):
                    path = os.path.join(root, name)
                    if not os.path.isdir(path):
                        continue
                    if self._path_matches_device(path):
                        return path
            except PermissionError:
                continue
        return None

    def _check_mount(self):
        mount_path = self.config["mount_path"]
        if not mount_path:
            detected = self._detect_y1_mount()
            if detected:
                mount_path = detected
                self.config["mount_path"] = detected
                save_config(self.config)
                print(f"[skimmer] Auto-detected Y1 at {detected}")

        mounts = [m.get_root().get_path() for m in Gio.VolumeMonitor.get().get_mounts()]
        connected = mount_path in mounts
        if not connected and mount_path and os.path.isdir(mount_path):
            print(f"[skimmer] _check_mount: {mount_path!r} exists on disk — treating as connected")
            connected = True

        print(f"[skimmer] _check_mount: mount_path={mount_path!r}")
        print(f"[skimmer] _check_mount: Gio mounts={mounts}")
        print(f"[skimmer] _check_mount: connected={connected}")

        if connected:
            self.scan_btn.set_visible(False)
            self.sync_btn.set_visible(True)
            self.eject_btn.set_visible(True)
            if self._sync_task is None:
                self.sync_label.set_text("Connected")
                if not self._last_connected:
                    if self._auto_sync_timer is not None:
                        GLib.source_remove(self._auto_sync_timer)
                    self._auto_sync_timer = GLib.timeout_add_seconds(5, self._do_sync)
        else:
            self.scan_btn.set_visible(True)
            self.sync_label.set_text("")
            self.sync_btn.set_visible(False)
            self.eject_btn.set_visible(False)
            self.sync_spinner.set_visible(False)
            if self._auto_sync_timer is not None:
                GLib.source_remove(self._auto_sync_timer)
                self._auto_sync_timer = None

        self._last_connected = connected

    def _do_sync(self, *args):
        if self._sync_task is not None:
            return
        self._auto_sync_timer = None
        self.sync_btn.set_sensitive(False)
        self.eject_btn.set_sensitive(False)
        self.sync_label.set_text("Syncing...")
        self.sync_spinner.set_visible(True)
        self.sync_spinner.start()
        self._sync_task = self.proc_mgr.add_task("sync", "Sync music to device", {})
        self._sync_task.connect("updated", self._on_sync_updated)

    def _on_sync_updated(self, task, status, progress, message):
        if status == "running":
            self.sync_label.set_text(message or "Syncing...")
        elif status == "completed":
            self.sync_label.set_text("Synced")
            self.sync_spinner.stop()
            self.sync_spinner.set_visible(False)
            self._sync_task = None
            GLib.timeout_add_seconds(1, self._reset_sync_ui)
        elif status == "failed":
            self.sync_label.set_text("Sync failed")
            self.sync_spinner.stop()
            self.sync_spinner.set_visible(False)
            self.sync_btn.set_sensitive(True)
            self.eject_btn.set_sensitive(True)
            self._sync_task = None

    def _reset_sync_ui(self):
        self.sync_label.set_text("")
        self.sync_btn.set_sensitive(True)
        self.eject_btn.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _do_eject(self, *args):
        self.eject_btn.set_sensitive(False)
        self.sync_btn.set_sensitive(False)
        self.sync_label.set_text("Ejecting...")
        threading.Thread(target=self._eject_thread, daemon=True).start()

    def _eject_thread(self):
        mount_path = self.config["mount_path"]
        try:
            for mount in Gio.VolumeMonitor.get().get_mounts():
                if mount.get_root().get_path() == mount_path:
                    mount.unmount(Gio.MountUnmountFlags.NONE, None)
                    GLib.idle_add(self._on_eject_done, None)
                    return
            if sys.platform == "darwin":
                import subprocess
                vol_name = os.path.basename(mount_path)
                subprocess.run(["diskutil", "eject", vol_name], capture_output=True)
                GLib.idle_add(self._on_eject_done, None)
                return
            GLib.idle_add(self._on_eject_done, f"Mount not found: {mount_path}")
        except Exception as e:
            GLib.idle_add(self._on_eject_done, str(e))

    def _on_eject_done(self, error):
        if error:
            self.sync_label.set_text(f"Eject failed: {error}")
            self.eject_btn.set_sensitive(True)
            self.sync_btn.set_sensitive(True)
        else:
            self.sync_label.set_text("Ejected safely")
            self.eject_btn.set_visible(False)
            self.sync_btn.set_visible(False)
            GLib.timeout_add_seconds(3, self._clear_mount_path)

    def _clear_mount_path(self):
        self.config["mount_path"] = ""
        save_config(self.config)
        self._check_mount()
        return GLib.SOURCE_REMOVE

    def _on_proc_added(self, mgr, task):
        task.connect("updated", self._on_proc_change)
        self._update_proc_badge()

    def _on_proc_change(self, *args):
        self._update_proc_badge()
        if (
            isinstance(args[0], Task)
            and args[1] == "completed"
            and args[0].type == "import"
        ):
            GLib.idle_add(self.pages["library"]._refresh)

    def _update_proc_badge(self):
        active = sum(
            1 for t in self.proc_mgr.tasks if t.status in ("pending", "running")
        )
        title = f"Processing ({active})" if active > 0 else "Processing"
        stack_page = self.stack.get_page(self._proc_page)
        if stack_page:
            stack_page.set_title(title)

    def _on_save_settings(self, config):
        self.config = config
        save_config(config)
        self.proc_mgr.config = config
        self.scanner.config = config
        self.pages["library"].config = config
        self.pages["search"].config = config

    def _on_global_key(self, ctrl, keyval, keycode, state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_KP_Space):
            focus = ctrl.get_widget().get_focus()
            if focus is not None and isinstance(
                focus, (Gtk.Entry, Gtk.SearchEntry, Gtk.ComboBoxText)
            ):
                return False
            self.player_bar._on_play_pause(None)
            return True
        return False
