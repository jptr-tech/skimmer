import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango

__all__ = ["Adw", "Gdk", "GdkPixbuf", "Gio", "GLib", "GObject", "Gtk", "Pango"]
