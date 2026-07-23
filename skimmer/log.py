import os
import logging

from platformdirs import user_log_dir

_LOG_CONFIGURED = False


def setup_logging():
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    log_dir = user_log_dir("skimmer", ensure_exists=True)
    logging.basicConfig(
        filename=os.path.join(log_dir, "skimmer.log"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG,
        force=True,
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.info("Skimmer started")
    _LOG_CONFIGURED = True


def get_logger(name):
    return logging.getLogger(name)
