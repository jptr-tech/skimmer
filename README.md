# Skimmer

<p align="center">
  <img src="assets/skimmer.svg" style="width: 250px;">
</p>

![screenshot](assets/screenshot.jpg)

GTK4 music manager built for the Innioasis Y1 (though it works with any USB
media device/Rockbox device)

Built with Python, GTK4 + Adwaita, GStreamer, and MPRIS integration.

## Features

- Sync music library to Innioasis Y1
- MPRIS media controls (playerctl, D-Bus)
- YouTube Music integration (search, download)
- Album art display
- Queue-based playback

## Dependencies

- Python ≥ 3.11
- GTK4, Adwaita, GStreamer (provided by GNOME Platform runtime on Flatpak)
- [uv](https://docs.astral.sh/uv/) — project manager

## Build from source

```bash
uv sync
uv run skimmer
```

## Flatpak

```bash
flatpak-builder --user --install --force-clean build-dir build-aux/flatpak/tech.jptr.Skimmer.yml
flatpak run tech.jptr.Skimmer
```

## Updating dependencies

1. Edit `pyproject.toml`
2. `uv sync`
3. `bash build-aux/flatpak/update-deps.sh` (regenerates Flatpak dep bundle,
   neccesary because of python deps needing to be prefetched)

## Installation

Icons and desktop file are bundled in the Flatpak. For local desktop integration:

```bash
cp build-aux/data/icons/512x512/apps/tech.jptr.Skimmer.png ~/.local/share/icons/hicolor/512x512/apps/
cp build-aux/data/icons/256x256/apps/tech.jptr.Skimmer.png ~/.local/share/icons/hicolor/256x256/apps/
gtk4-update-icon-cache -f -t ~/.local/share/icons/hicolor/
```

## License

MIT
