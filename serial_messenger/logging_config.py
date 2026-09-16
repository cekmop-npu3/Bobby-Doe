"""Application-wide configuration for standard-library logging."""

from __future__ import annotations

import logging
from typing import Final


__all__ = ("configure_logging",)


LOG_FORMAT: Final[str] = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


def configure_logging() -> None:
    """Configure the root logger for console diagnostics.

    The configuration is applied only when the host application has not already
    installed handlers, which keeps the project safe to import from tests.

    :return: ``None``.
    """
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
