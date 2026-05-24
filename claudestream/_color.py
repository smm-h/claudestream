"""ANSI color support with TTY auto-detection."""

from __future__ import annotations

import os
import sys


def should_color(stream: object | None = None, no_color_flag: bool = False) -> bool:
    """Determine if color output should be used.

    Returns False when:
    - no_color_flag is True (--no-color CLI flag)
    - NO_COLOR environment variable is set (https://no-color.org/)
    - The stream is not a TTY (piping/redirection)
    """
    if no_color_flag:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stderr
    return hasattr(stream, "isatty") and stream.isatty()


# ANSI escape codes
_RESET = "\033[0m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_BOLD = "\033[1m"


class Colorizer:
    """Wraps text with ANSI escape codes. No-op when color is disabled."""

    def __init__(self, use_color: bool) -> None:
        self._use_color = use_color

    def _wrap(self, code: str, text: str) -> str:
        if not self._use_color:
            return text
        return f"{code}{text}{_RESET}"

    def red(self, text: str) -> str:
        return self._wrap(_RED, text)

    def yellow(self, text: str) -> str:
        return self._wrap(_YELLOW, text)

    def cyan(self, text: str) -> str:
        return self._wrap(_CYAN, text)

    def dim(self, text: str) -> str:
        return self._wrap(_DIM, text)

    def bold(self, text: str) -> str:
        return self._wrap(_BOLD, text)
