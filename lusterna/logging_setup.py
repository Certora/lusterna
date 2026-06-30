"""Configure stdlib logging: structured lines on stderr, level from env."""
import logging
import os
import sys

_FMT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATE = "%Y-%m-%dT%H:%M:%S"


def setup(verbose: bool = False) -> None:
    level_name = os.environ.get("LUSTERNA_LOG_LEVEL", "DEBUG" if verbose else "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATE))
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
