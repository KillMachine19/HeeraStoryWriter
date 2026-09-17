"""
Shared logging setup — structured, timestamped, consistent across modules.
"""

import logging
import sys
from pathlib import Path

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_fmt = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
_datefmt = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger that writes to stdout AND to logs/scanner.log.
    Calling this multiple times with the same name is safe (handlers are not duplicated).
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(_fmt, datefmt=_datefmt)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler (appends; one log per process, not per call)
    fh = logging.FileHandler(LOG_DIR / "scanner.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger
