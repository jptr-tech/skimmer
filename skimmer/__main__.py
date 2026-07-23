import os
import sys

# Redirect stdout/stderr to a log file when running from a PyInstaller bundle
if getattr(sys, 'frozen', False):
    from platformdirs import user_log_dir
    log_dir = user_log_dir("skimmer", ensure_exists=True)
    log_path = os.path.join(log_dir, "skimmer.log")
    log_file = open(log_path, "a")
    sys.stdout = log_file
    sys.stderr = log_file
    print("--- Skimmer started ---")
    sys.stdout.flush()

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
