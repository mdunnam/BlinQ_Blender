"""BlinQ workflow and retopo tracking services.

RetopoTracker  — tracks retopology state per XMD UUID.
WorkflowService — manages named pipeline stacks.

Both services persist their state as JSON files in the XMD library folder.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import diagnostics
from ..models import RetopoRecord, RetopoState, WorkflowStack


class RetopoTracker:
    """Tracks retopology state for XMD-registered assets.

    State is persisted to ``{library_path}/xmd_retopo.json``.

    Args:
        library_path: Absolute path to the XMD library root folder.
    """

    FILENAME = "xmd_retopo.json"

    def __init__(self, library_path: Path) -> None:
        self._dir = library_path
        self._records: dict[str, RetopoRecord] = {}

    @property
    def file_path(self) -> Path:
        """Absolute path to xmd_retopo.json."""
        return self._dir / self.FILENAME

    def load(self) -> None:
        """Load retopo records from disk. Missing file starts empty."""
        self._records = {}
        if not self.file_path.exists():
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            for entry in data.get("records", []):
                rec = RetopoRecord.from_dict(entry)
                if rec.xmd_uuid:
                    self._records[rec.xmd_uuid] = rec
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            diagnostics.error("workflow", f"failed to load retopo tracker: {exc}")

    def save(self) -> None:
        """Persist retopo records to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "records": [r.to_dict() for r in self._records.values()],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def get(self, xmd_uuid: str) -> RetopoRecord | None:
        """Return the RetopoRecord for a UUID, or None.

        Args:
            xmd_uuid: The asset's XMD UUID.

        Returns:
            The RetopoRecord or None.
        """
        return self._records.get(xmd_uuid)

    def get_or_create(self, xmd_uuid: str, asset_name: str = "") -> RetopoRecord:
        """Return an existing record or create a new one.

        Args:
            xmd_uuid: The asset's XMD UUID.
            asset_name: Display name used only if a new record is created.

        Returns:
            A RetopoRecord (existing or newly created).
        """
        if xmd_uuid not in self._records:
            self._records[xmd_uuid] = RetopoRecord(
                xmd_uuid=xmd_uuid, asset_name=asset_name
            )
        return self._records[xmd_uuid]

    def set_state(
        self,
        xmd_uuid: str,
        state: str,
        source_object: str = "",
        retopo_object: str = "",
        notes: str = "",
    ) -> RetopoRecord:
        """Update the retopo state for an asset and persist immediately.

        Args:
            xmd_uuid: The asset's XMD UUID.
            state: A RetopoState value string.
            source_object: Optional Blender object name of the high-res source mesh.
            retopo_object: Optional Blender object name of the retopo mesh.
            notes: Optional free-form text notes.

        Returns:
            The updated RetopoRecord.
        """
        rec = self.get_or_create(xmd_uuid)
        rec.state = state
        if source_object:
            rec.source_object = source_object
        if retopo_object:
            rec.retopo_object = retopo_object
        if notes:
            rec.notes = notes
        rec.touch()
        self.save()
        return rec

    def all(self) -> list[RetopoRecord]:
        """Return all tracked records.

        Returns:
            A list of all RetopoRecord instances.
        """
        return list(self._records.values())


class WorkflowService:
    """Manages named workflow pipeline stacks.

    Stacks are persisted to ``{library_path}/xmd_workflows.json``.

    Args:
        library_path: Absolute path to the XMD library root folder.
    """

    FILENAME = "xmd_workflows.json"

    DEFAULT_STEPS = [
        "Block",
        "Detail",
        "Retopo",
        "UV Unwrap",
        "Bake",
        "Texture",
        "Review",
    ]

    def __init__(self, library_path: Path) -> None:
        self._dir = library_path
        self._stacks: dict[str, WorkflowStack] = {}

    @property
    def file_path(self) -> Path:
        """Absolute path to xmd_workflows.json."""
        return self._dir / self.FILENAME

    def load(self) -> None:
        """Load workflow stacks from disk. Missing file starts empty."""
        self._stacks = {}
        if not self.file_path.exists():
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            for entry in data.get("stacks", []):
                ws = WorkflowStack.from_dict(entry)
                self._stacks[ws.id] = ws
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            diagnostics.error("workflow", f"failed to load workflow stacks: {exc}")

    def save(self) -> None:
        """Persist workflow stacks to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "stacks": [s.to_dict() for s in self._stacks.values()],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def create(
        self,
        name: str,
        asset_uuid: str = "",
        steps: list[str] | None = None,
    ) -> WorkflowStack:
        """Create a new workflow stack and persist it.

        Args:
            name: Display name for the stack.
            asset_uuid: Optional XMD UUID of the primary asset.
            steps: Optional step list. Defaults to the standard sculpt pipeline.

        Returns:
            The newly created WorkflowStack.
        """
        stack = WorkflowStack(
            name=name,
            asset_uuid=asset_uuid,
            steps=steps if steps is not None else list(self.DEFAULT_STEPS),
        )
        self._stacks[stack.id] = stack
        self.save()
        return stack

    def get(self, stack_id: str) -> WorkflowStack | None:
        """Return a stack by its ID, or None.

        Args:
            stack_id: The stack UUID.

        Returns:
            The WorkflowStack or None.
        """
        return self._stacks.get(stack_id)

    def all(self) -> list[WorkflowStack]:
        """Return all stacks.

        Returns:
            A list of all WorkflowStack instances.
        """
        return list(self._stacks.values())

    def advance(self, stack_id: str) -> WorkflowStack | None:
        """Advance a stack's current step by one and persist.

        Args:
            stack_id: The stack UUID.

        Returns:
            The updated WorkflowStack, or None if not found.
        """
        stack = self._stacks.get(stack_id)
        if stack and stack.current_step < len(stack.steps) - 1:
            stack.current_step += 1
            self.save()
        return stack
