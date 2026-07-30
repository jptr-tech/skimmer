import logging
import os
import sys

from platformdirs import user_log_dir


def _format():
    return logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def setup_logging():
    log_dir = user_log_dir("skimmer", ensure_exists=True)
    log_path = os.path.join(log_dir, "skimmer.log")

    root = logging.getLogger()

    # Remove all beets logger handlers so they don't add their own stdout handler
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("beets"):
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.setLevel(logging.DEBUG)
            logger.propagate = True

    # Configure root logger with both file and stdout handlers
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    fh = logging.FileHandler(log_path)
    fh.setFormatter(_format())
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(_format())
    root.addHandler(sh)

    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.info("Skimmer started")


def get_logger(name):
    return logging.getLogger(name)
