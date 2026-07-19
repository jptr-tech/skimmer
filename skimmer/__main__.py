import sys
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

settings = Gtk.Settings.get_default()
if settings:
    settings.set_property("gtk-application-prefer-dark-theme", False)

gi.require_version("Adw", "1")

from skimmer.app import SkimmerApp


def main():
    app = SkimmerApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
