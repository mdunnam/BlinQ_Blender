"""BlinQ reference board and review-snapshot services.

ReferenceBoardService — manages user-imported reference images with
notes. Persists to ``xmd_references.json`` in the library folder.

ReviewSnapshotService — manages on-demand viewport / render captures saved
to ``snapshots/`` under the library folder. Persists to
``xmd_snapshots.json``.

Both services are bpy-free; bpy interactions live in the operators that
call them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import diagnostics
from ..models import ReferenceItem, ReviewSnapshot


# ---------------------------------------------------------------------------
# ReferenceBoardService
# ---------------------------------------------------------------------------

class ReferenceBoardService:
    """Manages the reference images attached to a BlinQ library.

    Persists to ``{library_path}/xmd_references.json``.

    Args:
        library_path: Absolute path to the XMD library root folder.
    """

    FILENAME = "xmd_references.json"

    def __init__(self, library_path: Path) -> None:
        self._dir = library_path
        self._items: dict[str, ReferenceItem] = {}

    @property
    def file_path(self) -> Path:
        """Absolute path to the JSON file."""
        return self._dir / self.FILENAME

    def is_available(self) -> bool:
        """Return True — the service is implemented."""
        return True

    def load(self) -> None:
        """Load reference items from disk. Missing file starts empty."""
        self._items = {}
        if not self.file_path.exists():
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            for entry in data.get("items", []):
                ref = ReferenceItem.from_dict(entry)
                if ref.id:
                    self._items[ref.id] = ref
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            diagnostics.error("review", f"failed to load reference board: {exc}")

    def save(self) -> None:
        """Persist all reference items to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "items": [r.to_dict() for r in self._items.values()],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def add(self, file_path: str, name: str = "", note: str = "") -> ReferenceItem:
        """Register a new reference image and persist.

        Args:
            file_path: Absolute path to the image on disk.
            name: Optional display name. Defaults to the file stem.
            note: Optional free-form note.

        Returns:
            The newly created ReferenceItem.
        """
        item = ReferenceItem(
            name=name or Path(file_path).stem,
            file_path=file_path,
            note=note,
        )
        self._items[item.id] = item
        self.save()
        return item

    def remove(self, ref_id: str) -> bool:
        """Remove a reference by ID. Persists on success.

        Args:
            ref_id: The reference UUID to remove.

        Returns:
            True if found and removed, False otherwise.
        """
        if ref_id in self._items:
            del self._items[ref_id]
            self.save()
            return True
        return False

    def get(self, ref_id: str) -> ReferenceItem | None:
        """Return a reference by ID, or None if unknown."""
        return self._items.get(ref_id)

    def all(self) -> list[ReferenceItem]:
        """Return all reference items, oldest first."""
        return list(self._items.values())

    def update(self, ref_id: str, *, name: str | None = None,
               note: str | None = None, image_name: str | None = None) -> bool:
        """Mutate fields on a ref item and persist.

        Args:
            ref_id: The reference UUID.
            name: New display name, or None to leave unchanged.
            note: New note text, or None to leave unchanged.
            image_name: New Blender Image datablock name, or None to leave unchanged.

        Returns:
            True if the item exists and was updated, False otherwise.
        """
        item = self._items.get(ref_id)
        if not item:
            return False
        if name is not None:
            item.name = name
        if note is not None:
            item.note = note
        if image_name is not None:
            item.image_name = image_name
        self.save()
        return True


# ---------------------------------------------------------------------------
# ReviewSnapshotService
# ---------------------------------------------------------------------------

class ReviewSnapshotService:
    """Manages review snapshots captured from the viewport or render.

    Persists to ``{library_path}/xmd_snapshots.json`` with the captured
    PNGs stored under ``{library_path}/snapshots/``.

    Args:
        library_path: Absolute path to the XMD library root folder.
    """

    FILENAME = "xmd_snapshots.json"
    SNAPSHOT_DIRNAME = "snapshots"

    def __init__(self, library_path: Path) -> None:
        self._dir = library_path
        self._items: dict[str, ReviewSnapshot] = {}

    @property
    def file_path(self) -> Path:
        """Absolute path to the JSON file."""
        return self._dir / self.FILENAME

    @property
    def snapshot_dir(self) -> Path:
        """Absolute path to the captured snapshot images folder."""
        return self._dir / self.SNAPSHOT_DIRNAME

    def is_available(self) -> bool:
        """Return True — the service is implemented."""
        return True

    def ensure_dirs(self) -> None:
        """Create the snapshots/ subfolder if missing."""
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> None:
        """Load snapshots from disk. Missing file starts empty."""
        self._items = {}
        if not self.file_path.exists():
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            for entry in data.get("items", []):
                snap = ReviewSnapshot.from_dict(entry)
                if snap.id:
                    self._items[snap.id] = snap
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            diagnostics.error("review", f"failed to load snapshots: {exc}")

    def save(self) -> None:
        """Persist all snapshots to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "items": [s.to_dict() for s in self._items.values()],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def add(self, snap: ReviewSnapshot) -> ReviewSnapshot:
        """Add a fully-built snapshot and persist.

        Args:
            snap: The ReviewSnapshot to register.

        Returns:
            The same snap reference for chaining.
        """
        self._items[snap.id] = snap
        self.save()
        return snap

    def remove(self, snap_id: str) -> bool:
        """Remove a snapshot from the index (does not delete the PNG)."""
        if snap_id in self._items:
            del self._items[snap_id]
            self.save()
            return True
        return False

    def get(self, snap_id: str) -> ReviewSnapshot | None:
        """Return a snapshot by ID, or None if unknown."""
        return self._items.get(snap_id)

    def all(self) -> list[ReviewSnapshot]:
        """Return all snapshots, oldest first."""
        return list(self._items.values())


# ---------------------------------------------------------------------------
# Stub kept for backward compatibility
# ---------------------------------------------------------------------------

class AnnotationService:
    """Annotation overlays on reference images (Phase 5+).

    Annotation drawing uses Blender's GPU module and is deferred until
    the GPU surface for the panel is wired up.
    """

    def is_available(self) -> bool:
        """Return False until the GPU annotation overlay is implemented."""
        return False


class CompareService:
    """Side-by-side compare of two snapshots (Phase 5+)."""

    def is_available(self) -> bool:
        """Return False until the compare view is implemented."""
        return False
