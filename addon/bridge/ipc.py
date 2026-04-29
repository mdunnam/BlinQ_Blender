"""BlinQ bridge IPC transport and heartbeat monitor.

File layout inside work_dir:
    blinq_alive.json  — written by BlinQ every poll tick; signals Blender is running
    xmd_alive.json    — written by XMD Desktop; signals XMD Desktop is running
    xmd_cmd.json      — command written by XMD Desktop for Blender to process
    blinq_ack.json    — response written by BlinQ after processing a command
    mesh_in/          — OBJ files sent FROM XMD Desktop TO Blender
    mesh_out/         — OBJ files exported BY Blender FOR XMD Desktop
    textures_in/      — texture files sent from XMD Desktop to Blender
    textures_out/     — texture files exported by Blender for XMD Desktop
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bpy

from ..models import BridgeCommandType


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEARTBEAT_TIMEOUT = 8.0  # seconds — XMD Desktop is considered gone after this
# Resolved at import time to the full extension module name, e.g.
# "bl_ext.user_default.blinq_blender" under Blender 4.2+ extensions.
_ADDON_ID: str = __package__.rsplit(".", 1)[0]  # strips ".bridge" suffix
_ADDON_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Module-level state (updated by poll timer)
# ---------------------------------------------------------------------------

_last_cmd_id: str = ""
_pending_imports: list[dict[str, Any]] = []  # deferred mesh imports


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------

@dataclass
class CommandEnvelope:
    """A command message sent from XMD Desktop to BlinQ Blender (or vice versa).

    Attributes:
        id: Unique job identifier used to match this command with its response.
        command: A BridgeCommandType value string.
        payload: Command-specific data dictionary.
        timestamp: ISO-8601 UTC string set when the command is created.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    command: str = BridgeCommandType.PING.value
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this envelope to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandEnvelope:
        """Deserialize a CommandEnvelope from a plain dictionary.

        Args:
            data: Dictionary loaded from xmd_cmd.json.

        Returns:
            A populated CommandEnvelope.
        """
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            command=data.get("command", BridgeCommandType.PING.value),
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp", ""),
        )


@dataclass
class ResponseEnvelope:
    """A response written by BlinQ after processing a command.

    Attributes:
        id: Must match the CommandEnvelope id this response addresses.
        command: The command that was processed.
        status: "ok" or "error".
        result: Result data on success.
        error: Human-readable error message on failure.
        timestamp: ISO-8601 UTC string.
    """

    id: str
    command: str
    status: str = "ok"
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize this response to a plain dictionary."""
        return asdict(self)


# ---------------------------------------------------------------------------
# IPCTransport
# ---------------------------------------------------------------------------

class IPCTransport:
    """File-based IPC transport between BlinQ Blender and XMD Desktop.

    Args:
        work_dir: Absolute path to the shared bridge work directory.
    """

    BLINQ_ALIVE = "blinq_alive.json"
    XMD_ALIVE = "xmd_alive.json"
    XMD_CMD = "xmd_cmd.json"
    BLINQ_ACK = "blinq_ack.json"

    def __init__(self, work_dir: Path) -> None:
        self._dir = work_dir
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create work_dir and transfer subdirectories if they do not exist."""
        for sub in ("", "mesh_in", "mesh_out", "textures_in", "textures_out"):
            (self._dir / sub).mkdir(parents=True, exist_ok=True)

    # Subdirectory accessors
    @property
    def mesh_in_dir(self) -> Path:
        """Absolute path to the mesh_in/ subdirectory."""
        return self._dir / "mesh_in"

    @property
    def mesh_out_dir(self) -> Path:
        """Absolute path to the mesh_out/ subdirectory."""
        return self._dir / "mesh_out"

    @property
    def textures_in_dir(self) -> Path:
        """Absolute path to the textures_in/ subdirectory."""
        return self._dir / "textures_in"

    @property
    def textures_out_dir(self) -> Path:
        """Absolute path to the textures_out/ subdirectory."""
        return self._dir / "textures_out"

    def write_heartbeat(self) -> None:
        """Write the BlinQ heartbeat file so XMD Desktop knows Blender is alive."""
        self._write_json(self.BLINQ_ALIVE, {
            "app": _ADDON_ID,
            "version": _ADDON_VERSION,
            "ts": time.time(),
        })

    def read_xmd_heartbeat(self) -> float:
        """Return the XMD Desktop heartbeat timestamp, or 0.0 if absent/stale.

        Returns:
            Unix timestamp of the last XMD Desktop heartbeat, or 0.0.
        """
        data = self._read_json(self.XMD_ALIVE)
        return float(data.get("ts", 0.0)) if data else 0.0

    def read_pending_command(self) -> CommandEnvelope | None:
        """Return the pending command from XMD Desktop, or None if none exists.

        Returns:
            A CommandEnvelope if xmd_cmd.json exists and parses cleanly, else None.
        """
        data = self._read_json(self.XMD_CMD)
        return CommandEnvelope.from_dict(data) if data else None

    def write_response(self, response: ResponseEnvelope) -> None:
        """Write blinq_ack.json with the response for the last processed command.

        Args:
            response: The ResponseEnvelope to persist.
        """
        self._write_json(self.BLINQ_ACK, response.to_dict())

    def write_command(self, envelope: CommandEnvelope) -> None:
        """Write a command from Blender toward XMD Desktop.

        Used when Blender initiates a roundtrip (e.g. send mesh out).

        Args:
            envelope: The CommandEnvelope to write.
        """
        self._write_json(self.XMD_CMD, envelope.to_dict())

    def _write_json(self, filename: str, data: dict[str, Any]) -> None:
        path = self._dir / filename
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"[BlinQ IPC] Write failed '{filename}': {exc}")

    def _read_json(self, filename: str) -> dict[str, Any] | None:
        path = self._dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None


# ---------------------------------------------------------------------------
# HeartbeatMonitor — manages the bpy.app.timers polling loop
# ---------------------------------------------------------------------------

class HeartbeatMonitor:
    """Manages the BlinQ bridge polling timer.

    Registers a ``bpy.app.timers`` callback that:
    1. Writes the BlinQ heartbeat file.
    2. Reads the XMD Desktop heartbeat to determine connection status.
    3. Checks for pending commands and dispatches them.
    """

    def __init__(self) -> None:
        self._registered = False

    def start(self) -> None:
        """Register the bridge poll timer. Safe to call multiple times."""
        if not self._registered:
            bpy.app.timers.register(_poll_bridge, first_interval=1.5)
            self._registered = True
            print("[BlinQ] Bridge timer started.")

    def stop(self) -> None:
        """Unregister the bridge poll timer if it is running."""
        if self._registered:
            try:
                bpy.app.timers.unregister(_poll_bridge)
            except ValueError:
                pass
            self._registered = False
            print("[BlinQ] Bridge timer stopped.")


# Singleton
_monitor = HeartbeatMonitor()


def get_monitor() -> HeartbeatMonitor:
    """Return the module-level HeartbeatMonitor singleton.

    Returns:
        The shared HeartbeatMonitor instance.
    """
    return _monitor


# ---------------------------------------------------------------------------
# Poll timer callback
# ---------------------------------------------------------------------------

def _poll_bridge() -> float:
    """Timer callback: write heartbeat, update status, dispatch commands.

    Returns:
        Seconds until the next invocation (read from preferences).
    """
    global _last_cmd_id

    addon = bpy.context.preferences.addons.get(_ADDON_ID)
    if not addon:
        return 2.0

    prefs = addon.preferences
    work_dir_str: str = getattr(prefs, "work_dir", "")
    poll_interval: float = float(getattr(prefs, "bridge_poll_interval", 2.0))

    if not work_dir_str:
        # No work dir set; keep status DISCONNECTED and return quickly
        if getattr(prefs, "bridge_status", "") != "DISCONNECTED":
            prefs.bridge_status = "DISCONNECTED"
        return poll_interval

    try:
        transport = IPCTransport(Path(work_dir_str))
        transport.write_heartbeat()

        xmd_ts = transport.read_xmd_heartbeat()
        is_connected = (time.time() - xmd_ts) < HEARTBEAT_TIMEOUT if xmd_ts else False
        new_status = "CONNECTED" if is_connected else "DISCONNECTED"

        if getattr(prefs, "bridge_status", "DISCONNECTED") != new_status:
            prefs.bridge_status = new_status
            _redraw_panels()

        # Dispatch any pending command from XMD Desktop
        cmd = transport.read_pending_command()
        if cmd and cmd.id != _last_cmd_id:
            _last_cmd_id = cmd.id
            _dispatch(transport, cmd)

    except Exception as exc:
        print(f"[BlinQ] Bridge poll error: {exc}")
        try:
            prefs.bridge_status = "ERROR"
        except Exception:
            pass

    return poll_interval


def _redraw_panels() -> None:
    """Request a redraw of all 3D View areas to refresh bridge status display."""
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass


def _dispatch(transport: IPCTransport, cmd: CommandEnvelope) -> None:
    """Route a received command to the appropriate handler.

    Mesh imports are scheduled via a deferred timer so they run in a fresh
    event-loop tick with a properly populated bpy.context.

    Args:
        transport: The active IPCTransport for writing the response.
        cmd: The CommandEnvelope to process.
    """
    from . import io as bridge_io

    if cmd.command == BridgeCommandType.PING.value:
        transport.write_response(
            ResponseEnvelope(id=cmd.id, command=cmd.command, result={"pong": True})
        )
        return

    if cmd.command == BridgeCommandType.RECEIVE_MESH.value:
        payload = cmd.payload

        def _deferred_import() -> None:
            try:
                result = bridge_io.MeshImporter(transport).execute(payload)
                transport.write_response(
                    ResponseEnvelope(id=cmd.id, command=cmd.command, result=result)
                )
            except Exception as exc:
                print(f"[BlinQ] Mesh import error: {exc}")
                transport.write_response(
                    ResponseEnvelope(
                        id=cmd.id, command=cmd.command, status="error", error=str(exc)
                    )
                )

        bpy.app.timers.register(_deferred_import, first_interval=0.05)
        return

    if cmd.command == BridgeCommandType.SEND_TEXTURE.value:
        payload = cmd.payload

        def _deferred_texture() -> None:
            try:
                result = bridge_io.TextureImporter(transport).execute(payload)
                transport.write_response(
                    ResponseEnvelope(id=cmd.id, command=cmd.command, result=result)
                )
            except Exception as exc:
                print(f"[BlinQ] Texture import error: {exc}")
                transport.write_response(
                    ResponseEnvelope(
                        id=cmd.id, command=cmd.command, status="error", error=str(exc)
                    )
                )

        bpy.app.timers.register(_deferred_texture, first_interval=0.05)
        return

    # Unknown command — acknowledge with a warning
    transport.write_response(
        ResponseEnvelope(
            id=cmd.id,
            command=cmd.command,
            status="error",
            error=f"Unknown command: '{cmd.command}'",
        )
    )
