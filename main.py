#!/usr/bin/env python3
import sys

from skimmer.app import SkimmerApp
from skimmer.gtk import Gtk
from skimmer.log import setup_logging


def _set_theme_pref():
    settings = Gtk.Settings.get_default()
    if settings:
        settings.set_property("gtk-application-prefer-dark-theme", False)


def main():
    setup_logging()
    _set_theme_pref()
    app = SkimmerApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
