import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Adw, Gdk

from skimmer.config import load_config, save_config
from skimmer.worker import ProcessingManager
from skimmer.library import LibraryPage
from skimmer.search import SearchPage
from skimmer.processing import ProcessingPage
from skimmer.settings import SettingsPage
from skimmer.player import PlayerBar
from skimmer.media_integration import create_integration


class SkimmerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.y1.skimmer")
        self.config = load_config()
        style_mgr = Adw.StyleManager.get_default()
        style_mgr.set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        self.proc_mgr = ProcessingManager(self.config)
        self.connect("activate", self._on_activate)
        self._last_connected = False
        self._sync_task = None
        self._auto_sync_timer = None

    def _on_activate(self, app):
        win = Adw.ApplicationWindow(application=app)
        win.set_default_size(1100, 700)
        win.set_title("Y1 Skimmer")

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

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)

        self.pages = {}

        page = LibraryPage(self.config, player_bar=self.player_bar)
        self.stack.add_titled(page, "library", "Library")
        self.pages["library"] = page

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

        page = SettingsPage(self.config, self._on_save_settings)
        self.stack.add_titled(page, "settings", "Settings")
        self.pages["settings"] = page

        self.player_bar.set_show_album_cb(
            lambda: self.stack.set_visible_child_name("library")
        )

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        header.set_title_widget(switcher)

        sync_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.sync_icon = Gtk.Image.new_from_icon_name("drive-harddisk-usb-symbolic")
        self.sync_icon.set_pixel_size(16)
        sync_box.append(self.sync_icon)

        self.sync_label = Gtk.Label(label="")
        self.sync_label.add_css_class("dim-label")
        sync_box.append(self.sync_label)

        self.sync_spinner = Gtk.Spinner()
        self.sync_spinner.set_size_request(16, 16)
        self.sync_spinner.set_visible(False)
        sync_box.append(self.sync_spinner)

        self.sync_btn = Gtk.Button(label="Sync")
        self.sync_btn.add_css_class("flat")
        self.sync_btn.set_visible(False)
        self.sync_btn.connect("clicked", self._do_sync)
        sync_box.append(self.sync_btn)

        header.pack_end(sync_box)

        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self.stack)

        self._detect_timer = GLib.timeout_add_seconds(5, self._check_y1)
        self._check_y1()

        win.present()

    def _check_y1(self):
        mount_path = self.config["y1_mount_path"]
        connected = os.path.isdir(mount_path)

        if connected:
            self.sync_icon.set_from_icon_name("drive-harddisk-usb-symbolic")
            self.sync_btn.set_visible(True)
            if self._sync_task is None:
                self.sync_label.set_text("Y1 connected")
                if not self._last_connected:
                    if self._auto_sync_timer is not None:
                        GLib.source_remove(self._auto_sync_timer)
                    self._auto_sync_timer = GLib.timeout_add_seconds(5, self._do_sync)
        else:
            self.sync_icon.set_from_icon_name("drive-harddisk-usb-symbolic")
            self.sync_label.set_text("")
            self.sync_btn.set_visible(False)
            self.sync_spinner.set_visible(False)
            if self._auto_sync_timer is not None:
                GLib.source_remove(self._auto_sync_timer)
                self._auto_sync_timer = None

        self._last_connected = connected
        return GLib.SOURCE_CONTINUE

    def _do_sync(self, *args):
        if self._sync_task is not None:
            return
        self._auto_sync_timer = None
        self.sync_btn.set_sensitive(False)
        self.sync_label.set_text("Syncing...")
        self.sync_spinner.set_visible(True)
        self.sync_spinner.start()
        self._sync_task = self.proc_mgr.add_task("sync", "Sync music to Y1", {})
        self._sync_task.connect("updated", self._on_sync_updated)

    def _on_sync_updated(self, task, status, progress, message):
        if status == "running":
            self.sync_label.set_text(message or "Syncing...")
        elif status == "completed":
            self.sync_label.set_text("Sync complete")
            self.sync_spinner.stop()
            self.sync_spinner.set_visible(False)
            self._sync_task = None
            GLib.timeout_add_seconds(3, self._reset_sync_ui)
        elif status == "failed":
            self.sync_label.set_text("Sync failed")
            self.sync_spinner.stop()
            self.sync_spinner.set_visible(False)
            self.sync_btn.set_sensitive(True)
            self._sync_task = None

    def _reset_sync_ui(self):
        self.sync_label.set_text("Y1 connected")
        self.sync_btn.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _on_proc_added(self, mgr, task):
        task.connect("updated", self._on_proc_change)
        self._update_proc_badge()

    def _on_proc_change(self, *args):
        self._update_proc_badge()

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
        self.pages["library"].config = config
        self.pages["search"].config = config

    def _on_global_key(self, ctrl, keyval, keycode, state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_KP_Space):
            focus = ctrl.get_widget().get_focus()
            if focus is not None and isinstance(focus, (Gtk.Entry, Gtk.SearchEntry, Gtk.ComboBoxText)):
                return False
            self.player_bar._on_play_pause(None)
            return True
        return False
