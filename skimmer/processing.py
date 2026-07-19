import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib


class ProcessingPage(Gtk.Box):
    def __init__(self, config, processing_manager):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.config = config
        self.proc_mgr = processing_manager
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label="Processing", css_classes=["title"])
        header.append(lbl)

        self.count_label = Gtk.Label(label="")
        self.count_label.set_hexpand(True)
        self.count_label.set_halign(Gtk.Align.END)
        header.append(self.count_label)

        clear_btn = Gtk.Button(label="Clear Completed")
        clear_btn.connect("clicked", self._clear_completed)
        header.append(clear_btn)
        self.append(header)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.list_box)
        scroll.set_vexpand(True)
        self.append(scroll)

        self.proc_mgr.connect("task-added", self._on_task_added)
        self.proc_mgr.connect("task-removed", self._on_task_removed)

    def _on_task_added(self, mgr, task):
        row = TaskRow(task)
        self.list_box.append(row)
        task.connect("updated", row.on_updated)
        self._update_count()

    def _on_task_removed(self, mgr, task):
        for row in self.list_box:
            child = row.get_child()
            if hasattr(child, "task") and child.task is task:
                self.list_box.remove(row)
                break
        self._update_count()

    def _clear_completed(self, *args):
        to_remove = []
        for row in self.list_box:
            child = row.get_child()
            if hasattr(child, "task") and child.task.status in ("completed", "failed"):
                to_remove.append(child.task)
        for task in to_remove:
            self.proc_mgr.remove_task(task)

    def _update_count(self):
        active = sum(1 for t in self.proc_mgr.tasks if t.status in ("pending", "running"))
        self.count_label.set_text(f"{active} active, {len(self.proc_mgr.tasks)} total")


class TaskRow(Gtk.Box):
    def __init__(self, task):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.task = task
        self.set_margin_start(6)
        self.set_margin_end(6)
        self.set_margin_top(3)
        self.set_margin_bottom(3)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.status_desc = Gtk.Label(label=task.title)
        self.status_desc.set_halign(Gtk.Align.START)
        self.status_desc.set_hexpand(True)
        self.status_desc.set_ellipsize(3)
        top.append(self.status_desc)

        self.status_label = Gtk.Label(label=task.status)
        self.status_label.add_css_class("dim-label")
        top.append(self.status_label)
        self.append(top)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_fraction(task.progress)
        self.append(self.progress_bar)

        self.progress_text = Gtk.Label(label="")
        self.progress_text.add_css_class("dim-label")
        self.progress_text.set_halign(Gtk.Align.START)
        self.progress_text.set_ellipsize(3)
        self.append(self.progress_text)

    def on_updated(self, task, status, progress, message):
        self.status_label.set_text(status)
        self.progress_bar.set_fraction(progress)

        if status == "running":
            if message:
                if "/" in message:
                    self.progress_text.set_text(message)
                    self.progress_bar.set_text("")
                elif any(kw in message.lower() for kw in ["indexing", "syncing", "saving", "files"]):
                    self.status_desc.set_text(message)
                    self.progress_bar.set_text("")
                    self.progress_text.set_text("")
                else:
                    self.progress_bar.set_text(message)
                    self.progress_text.set_text("")
            elif progress > 0:
                self.progress_bar.set_text(f"{int(progress * 100)}%")
                self.progress_text.set_text("")
            else:
                self.progress_bar.set_text("...")
                self.progress_text.set_text("")
        elif status == "completed":
            self.progress_bar.set_text("Done")
            self.progress_bar.remove_css_class("running")
            self.progress_bar.add_css_class("success")
            self.progress_text.set_text(message or "")
            if message and message != "Already up to date":
                self.status_desc.set_text(message)
        elif status == "failed":
            self.progress_bar.set_text("Failed")
            self.progress_bar.add_css_class("error")
            self.status_label.set_text(f"Error: {task.error}")
            self.progress_text.set_text("")
