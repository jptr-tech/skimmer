import sys
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from skimmer.app import SkimmerApp


def main():
    app = SkimmerApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
