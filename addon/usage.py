"""BlinQ usage log — append-only JSONL of meaningful artist events.

Records register/use/send/receive/preset events one-per-line into
``{library_path}/xmd_usage.jsonl``. Studio analytics later mines this file;
the artist sees nothing unless they explicitly open it.

The log is best-effort — writes are wrapped in try/except so a disk
failure never disrupts the calling operator.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import diagnostics


USAGE_FILENAME: str = "xmd_usage.jsonl"


def _resolve_path(library_path: str) -> Path | None:
    """Return the absolute path to the usage log, or None if no library is set."""
    if not library_path:
        return None
    return Path(library_path) / USAGE_FILENAME


def log(
    library_path: str,
    event: str,
    *,
    asset_uuid: str = "",
    blend_file: str = "",
    scene_name: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one event to the JSONL usage log.

    Args:
        library_path: Configured XMD library path. Empty string disables the call.
        event: Short event name (e.g. ``"asset.registered"``, ``"bridge.send_mesh"``).
        asset_uuid: Optional XMD UUID of the asset involved.
        blend_file: Optional .blend file path the event occurred in.
        scene_name: Optional Blender scene name.
        payload: Optional event-specific dictionary.
    """
    path = _resolve_path(library_path)
    if path is None:
        return
    record = {
        "ts": time.time(),
        "event": event,
        "asset_uuid": asset_uuid,
        "blend_file": blend_file,
        "scene_name": scene_name,
        "payload": payload or {},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        diagnostics.warn("usage", f"failed to append usage log: {exc}")
