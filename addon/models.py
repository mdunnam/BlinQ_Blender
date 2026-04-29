"""BlinQ data models.

Pure-Python dataclasses representing all core domain objects. These are
intentionally free of bpy dependencies so they can be used in tests and
sidecar processes without a Blender runtime.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AssetType(str, Enum):
    """Blender ID types that BlinQ can register as XMD assets."""

    OBJECT = "OBJECT"
    MATERIAL = "MATERIAL"
    BRUSH = "BRUSH"
    IMAGE = "IMAGE"
    TEXTURE = "TEXTURE"
    NODE_GROUP = "NODE_GROUP"
    COLLECTION = "COLLECTION"
    WORLD = "WORLD"
    SCENE = "SCENE"
    UNKNOWN = "UNKNOWN"


class BridgeCommandType(str, Enum):
    """Commands that XMD Desktop can send to BlinQ Blender, and vice versa."""

    PING = "ping"
    SEND_MESH = "send_mesh"
    RECEIVE_MESH = "receive_mesh"
    SEND_TEXTURE = "send_texture"
    SEND_MATERIAL = "send_material"


class BridgeStatus(str, Enum):
    """Connection state of the XMD Desktop ↔ BlinQ bridge."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


class RetopoState(str, Enum):
    """Stage in the retopology pipeline for a given asset."""

    NONE = "NONE"
    BLOCKED = "BLOCKED"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    BAKED = "BAKED"
    TEXTURED = "TEXTURED"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AssetRecord:
    """A single XMD-managed asset entry in the library index.

    Attributes:
        xmd_uuid: Stable UUID4 assigned at registration time.
        name: Display name (mirrors the Blender datablock name at registration).
        asset_type: The Blender ID type (an AssetType value string).
        tags: Tag strings synced to/from Blender asset metadata.
        author: Author name synced to/from Blender asset metadata.
        description: Description synced to/from Blender asset metadata.
        catalog_path: Slash-separated catalog path, e.g. "XMD/Materials/Stone".
        catalog_id: UUID4 of the matching catalog entry in blender_assets.cats.txt.
        source_id: Optional XMD Desktop source identifier for bridge roundtrips.
        sync_hash: Short hash of the asset at last sync, used for change detection.
        created_at: ISO-8601 UTC timestamp of first registration.
        modified_at: ISO-8601 UTC timestamp of last modification.
        blend_file: Path to the .blend file (relative to library_path).
        preview_path: Path to the generated preview image (relative to library_path).
    """

    xmd_uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    asset_type: str = AssetType.UNKNOWN.value
    tags: list[str] = field(default_factory=list)
    author: str = ""
    description: str = ""
    catalog_path: str = "XMD"
    catalog_id: str = ""
    source_id: str = ""
    sync_hash: str = ""
    created_at: str = field(default_factory=_now)
    modified_at: str = field(default_factory=_now)
    blend_file: str = ""
    preview_path: str = ""

    def touch(self) -> None:
        """Update modified_at to the current UTC time."""
        self.modified_at = _now()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary for JSON storage."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssetRecord:
        """Deserialize from a plain dictionary, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class BridgeJob:
    """Tracks the state of a single bridge command in flight.

    Attributes:
        id: UUID for this job, used to match commands with responses.
        command: A BridgeCommandType value string.
        status: "PENDING" | "RUNNING" | "DONE" | "ERROR"
        payload: Command-specific data dict sent with the command.
        result: Result dict written after the command completes.
        created_at: ISO-8601 UTC timestamp.
        completed_at: ISO-8601 UTC timestamp when finished, or empty.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    command: str = BridgeCommandType.PING.value
    status: str = "PENDING"
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BridgeJob:
        """Deserialize from a plain dictionary, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class RetopoRecord:
    """Retopology workflow state for a single XMD-registered asset.

    Attributes:
        xmd_uuid: The asset's XMD UUID.
        asset_name: Display name for the asset.
        source_object: Blender object name of the high-resolution source mesh.
        retopo_object: Blender object name of the retopologised mesh.
        state: A RetopoState value string.
        notes: Free-form text notes attached to this retopo session.
        created_at: ISO-8601 UTC timestamp of first creation.
        modified_at: ISO-8601 UTC timestamp of last modification.
    """

    xmd_uuid: str = ""
    asset_name: str = ""
    source_object: str = ""
    retopo_object: str = ""
    state: str = RetopoState.NONE.value
    notes: str = ""
    created_at: str = field(default_factory=_now)
    modified_at: str = field(default_factory=_now)

    def touch(self) -> None:
        """Update modified_at to the current UTC time."""
        self.modified_at = _now()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RetopoRecord:
        """Deserialize from a plain dictionary, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class WorkflowStack:
    """A named sequence of pipeline steps associated with an asset.

    Attributes:
        id: Stable UUID4 for this stack.
        name: Human-readable stack name, e.g. "Creature Head Pipeline".
        asset_uuid: Optional XMD UUID of the primary asset this stack tracks.
        steps: Ordered list of step name strings.
        current_step: Zero-based index of the active step.
        created_at: ISO-8601 UTC timestamp.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    asset_uuid: str = ""
    steps: list[str] = field(default_factory=list)
    current_step: int = 0
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStack:
        """Deserialize from a plain dictionary, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
