import sys
import logging
import os
from decimal import Decimal
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

logger = logging.getLogger(__name__)


class MediaIntegration:
    def __init__(self, player_bar):
        self._player = player_bar

    def start(self):
        pass

    def stop(self):
        pass


class NullIntegration(MediaIntegration):
    pass


def create_integration(player_bar):
    platform = sys.platform
    if platform == "linux":
        try:
            from .mpris_adapter import LinuxMPRIS

            return LinuxMPRIS(player_bar)
        except Exception as e:
            import traceback

            logger.warning("Failed to start MPRIS integration: %s\n%s", e, traceback.format_exc())
            return NullIntegration(player_bar)
    elif platform == "darwin":
        logger.info(
            "macOS Now Playing integration not implemented yet. "
            "See https://developer.apple.com/documentation/mediaplayer "
            "for MPNowPlayingInfoCenter / MPRemoteCommandCenter"
        )
        return NullIntegration(player_bar)
    elif platform == "win32":
        logger.info(
            "Windows SystemMediaTransportControls integration not implemented yet. "
            "See https://pypi.org/project/py-now-playing/ for a starting point"
        )
        return NullIntegration(player_bar)
    else:
        return NullIntegration(player_bar)
