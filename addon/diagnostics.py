"""BlinQ diagnostics — central ring-buffer logger.

A lightweight, dependency-free log used across the add-on. Every module
records bridge events, cloud calls, asset operations, and errors here so
the Diagnostics panel in the N-panel sidebar (and the Copy Log button)
can surface them to the user without forcing them to open the console.

Entries are tee'd to stdout so they remain visible in the Blender system
console. The buffer is bounded so it cannot leak memory in long sessions.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Iterable

# Levels in ascending order of severity. Index in this tuple is the
# numeric severity used by ``entries(level_min=...)``.
LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARN", "ERROR")

_BUFFER_SIZE: int = 200
_buffer: "deque[LogEntry]" = deque(maxlen=_BUFFER_SIZE)


@dataclass
class LogEntry:
    """A single diagnostics log entry.

    Attributes:
        timestamp: Unix epoch seconds when the entry was created.
        level: Severity — one of DEBUG / INFO / WARN / ERROR.
        source: Subsystem name, e.g. ``"bridge"``, ``"cloud"``, ``"asset"``.
        message: Human-readable message text.
    """

    timestamp: float
    level: str
    source: str
    message: str


def log(level: str, source: str, message: str) -> None:
    """Append an entry to the ring buffer and tee it to stdout.

    Args:
        level: One of LEVELS. Unknown values are stored verbatim.
        source: Short subsystem identifier (lowercase by convention).
        message: The message text to record.
    """
    entry = LogEntry(time.time(), level, source, message)
    _buffer.append(entry)
    print(f"[BlinQ {level}/{source}] {message}")


def debug(source: str, message: str) -> None:
    """Record a DEBUG-level entry."""
    log("DEBUG", source, message)


def info(source: str, message: str) -> None:
    """Record an INFO-level entry."""
    log("INFO", source, message)


def warn(source: str, message: str) -> None:
    """Record a WARN-level entry."""
    log("WARN", source, message)


def error(source: str, message: str) -> None:
    """Record an ERROR-level entry."""
    log("ERROR", source, message)


def entries(level_min: str = "DEBUG") -> list[LogEntry]:
    """Return entries at severity ``level_min`` or higher, oldest first.

    Args:
        level_min: Minimum severity to include. Unknown values fall back
            to the lowest severity (no filtering).

    Returns:
        List of LogEntry instances. The list is a snapshot — safe to
        iterate while the buffer is mutated by other code.
    """
    try:
        threshold = LEVELS.index(level_min)
    except ValueError:
        threshold = 0
    return [
        e for e in _buffer
        if e.level in LEVELS and LEVELS.index(e.level) >= threshold
    ]


def count_at_level(level: str) -> int:
    """Return how many entries currently match exactly ``level``.

    Args:
        level: Severity string to match.

    Returns:
        Count of matching entries in the buffer.
    """
    return sum(1 for e in _buffer if e.level == level)


def clear() -> None:
    """Empty the ring buffer."""
    _buffer.clear()


def to_text() -> str:
    """Format the entire buffer as a plain-text block.

    Returns:
        Newline-separated text, oldest entry first, suitable for the
        clipboard or a bug report.
    """
    import datetime as _dt
    lines: list[str] = []
    for e in _buffer:
        ts = _dt.datetime.fromtimestamp(e.timestamp).strftime("%H:%M:%S")
        lines.append(f"{ts} [{e.level:<5}] {e.source}: {e.message}")
    return "\n".join(lines)
