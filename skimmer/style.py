from skimmer.gtk import Gdk, Gtk

_CSS = """
.skimmer-waiting > trough > progress {
  background-color: #f6d32d;
  background-image: none;
}

.stack-switcher > button {
  min-width: 0;
  padding-left: 8px;
  padding-right: 8px;
}
"""

_provider = None


def setup_css():
    global _provider
    if _provider is not None:
        return
    _provider = Gtk.CssProvider()
    _provider.load_from_data(_CSS.encode())
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
