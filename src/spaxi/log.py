# SPDX-FileCopyrightText: 2026 Brookhaven Science Associates, LLC.
# SPDX-License-Identifier: Apache-2.0

"""Logging configuration for spaxi.

A single ``setup_logging`` maps the general ``--log-sink``/``--log-level``
options onto the ``spaxi`` logger.  Data-processing modules obtain their
logger with ``logging.getLogger(__name__)`` and emit records; only this
module (driven from the CLI) decides where they go and at what level.
"""

import logging
import sys

# The top-level logger; module loggers ("spaxi.convert", ...) are children.
ROOT = "spaxi"


class LogError(Exception):
    """An invalid logging sink or level was requested."""


def _resolve_level(level: str) -> int:
    value = logging.getLevelName(str(level).upper())
    if not isinstance(value, int):
        raise LogError(f"unknown log level: {level!r}")
    return value


def _resolve_handler(sink: str) -> logging.Handler:
    """Map a sink name to a handler: stderr, stdout, or a file path."""
    if sink == "stderr":
        return logging.StreamHandler(sys.stderr)
    if sink == "stdout":
        return logging.StreamHandler(sys.stdout)
    try:
        return logging.FileHandler(sink)
    except OSError as err:
        raise LogError(f"cannot open log sink {sink!r}: {err}")


def setup_logging(sink: str = "stderr", level: str = "info") -> None:
    """Configure the ``spaxi`` logger from a sink name and level.

    ``sink`` is ``stderr`` (default), ``stdout``, or a file path.
    ``level`` is any Python logging level name (case-insensitive).
    """
    handler = _resolve_handler(sink)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s: %(message)s",
                          datefmt="%H:%M:%S")
    )
    logger = logging.getLogger(ROOT)
    logger.setLevel(_resolve_level(level))
    # Replace any handlers from a previous call so repeated setup (e.g. in
    # tests) does not duplicate output.
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
