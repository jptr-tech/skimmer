import logging
import os

from skimmer.config import resolve_podcasts_dir
from skimmer.gtk import GdkPixbuf, Gtk, Pango

log = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".opus", ".flac", ".wav", ".ogg", ".mp4"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
CARD_SIZE = 150


def _make_placeholder(size=CARD_SIZE):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, size, size)
    pixbuf.fill(0x44444400)
    return pixbuf


class PodcastCard(Gtk.FlowBoxChild):
    def __init__(self, file_path, title, thumb_path, on_delete):
        super().__init__()
        self.file_path = file_path
        self.title = title
        self._thumb_path = thumb_path
        self.set_margin_start(4)
        self.set_margin_end(4)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.image = Gtk.Image()
        self.image.set_pixel_size(CARD_SIZE)
        self.image.set_halign(Gtk.Align.CENTER)
        box.append(self.image)

        lbl = Gtk.Label(label=title)
        lbl.set_halign(Gtk.Align.CENTER)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_single_line_mode(True)
        lbl.set_max_width_chars(18)
        lbl.add_css_class("body")
        box.append(lbl)

        self.set_child(box)
        self.set_size_request(CARD_SIZE + 12, -1)
        self._load_thumb()

        gesture = Gtk.GestureClick()
        gesture.set_button(3)
        gesture.connect("pressed", lambda g, n, x, y: on_delete(self))
        self.add_controller(gesture)

    def _load_thumb(self):
        if self._thumb_path and os.path.exists(self._thumb_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    self._thumb_path, CARD_SIZE, CARD_SIZE, True
                )
                self.image.set_from_pixbuf(pixbuf)
                return
            except Exception:
                pass
        self.image.set_from_pixbuf(_make_placeholder(CARD_SIZE))


class PodcastsPage(Gtk.Box):
    def __init__(self, config, proc_mgr, player_bar=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.config = config
        self.proc_mgr = proc_mgr
        self._player_bar = player_bar
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text(
            "YouTube URL (e.g. https://www.youtube.com/watch?v=...)"
        )
        self.url_entry.set_hexpand(True)
        self.url_entry.connect("activate", self._on_download)
        toolbar.append(self.url_entry)

        self.dl_btn = Gtk.Button(label="Download")
        self.dl_btn.add_css_class("suggested-action")
        self.dl_btn.connect("clicked", self._on_download)
        toolbar.append(self.dl_btn)
        self.append(toolbar)

        self.status_lbl = Gtk.Label(label="")
        self.status_lbl.set_halign(Gtk.Align.START)
        self.status_lbl.add_css_class("dim-label")
        self.append(self.status_lbl)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_homogeneous(True)
        self.flowbox.set_column_spacing(8)
        self.flowbox.set_row_spacing(12)
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.connect("child-activated", self._on_activated)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.flowbox)
        scroll.set_vexpand(True)
        self.append(scroll)

        self._load()

    def _load(self):
        self.flowbox.remove_all()
        podcasts_dir = resolve_podcasts_dir(self.config)
        if not os.path.isdir(podcasts_dir):
            return
        for fname in sorted(os.listdir(podcasts_dir)):
            path = os.path.join(podcasts_dir, fname)
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in AUDIO_EXTS:
                continue
            stem = os.path.splitext(fname)[0]
            thumb = next(
                (
                    os.path.join(podcasts_dir, stem + iext)
                    for iext in IMG_EXTS
                    if os.path.exists(os.path.join(podcasts_dir, stem + iext))
                ),
                None,
            )
            title = stem or fname
            self.flowbox.append(PodcastCard(path, title, thumb, on_delete=self._on_delete))

    def _on_activated(self, flowbox, child):
        if not self._player_bar:
            return
        if not os.path.exists(child.file_path):
            return
        self._player_bar.play_file(child.file_path, title=child.title, artist="")

    def _on_download(self, *args):
        url = self.url_entry.get_text().strip()
        if not url:
            return
        self.url_entry.set_text("")
        self.status_lbl.set_text("Queued download...")
        task = self.proc_mgr.add_task("podcast", "Podcast Download", {"url": url})
        task.connect("updated", self._on_task_updated)

    def _on_task_updated(self, task, status, progress, message):
        if status == "completed":
            self.status_lbl.set_text("Download complete")
            self._load()
        elif status == "failed":
            self.status_lbl.set_text(f"Download failed: {task.error or message}")
        elif status == "cancelled":
            self.status_lbl.set_text("Download cancelled")

    def _on_delete(self, card):
        parent = self.get_root() if self.get_root() else None
        dialog = Gtk.MessageDialog(
            transient_for=parent,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f'Delete "{card.title}"?',
        )
        dialog.connect("response", lambda d, r: self._do_delete(d, r, card))
        dialog.present()

    def _do_delete(self, dialog, response, card):
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        for p in (card.file_path, card._thumb_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        self._load()
