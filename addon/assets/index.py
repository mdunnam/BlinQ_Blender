"""XMD library index and Blender asset metadata management.

This module provides three classes:

- ``XMDIndex``: Manages the ``xmd_index.json`` file that tracks all
  BlinQ-registered assets in the library folder.
- ``CatalogManager``: Manages ``blender_assets.cats.txt``, Blender's catalog
  definition file for an asset library folder.
- ``MetadataMapper``: Converts between Blender ID datablocks and AssetRecord
  instances, and manages the ``xmd_uuid`` custom property on datablocks.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import bpy

from ..models import AssetRecord, AssetType


# ---------------------------------------------------------------------------
# XMDIndex
# ---------------------------------------------------------------------------

class XMDIndex:
    """Persistent JSON index of all BlinQ-registered assets.

    The index is stored as ``xmd_index.json`` inside the configured library
    path. It is loaded on demand and saved explicitly; no automatic write-back
    occurs.

    Args:
        library_path: Absolute path to the XMD library root folder.
    """

    INDEX_FILENAME = "xmd_index.json"

    def __init__(self, library_path: Path) -> None:
        self._library_path = library_path
        self._records: dict[str, AssetRecord] = {}

    @property
    def index_path(self) -> Path:
        """Absolute path to the xmd_index.json file."""
        return self._library_path / self.INDEX_FILENAME

    def load(self) -> None:
        """Load records from xmd_index.json.

        If the file does not exist the in-memory index starts empty.
        Malformed entries are skipped with a warning printed to the console.
        """
        self._records = {}
        if not self.index_path.exists():
            return

        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[BlinQ] Failed to load index: {exc}")
            return

        for entry in raw.get("assets", []):
            try:
                record = AssetRecord.from_dict(entry)
                self._records[record.xmd_uuid] = record
            except (TypeError, KeyError) as exc:
                print(f"[BlinQ] Skipping malformed index entry: {exc}")

    def save(self) -> None:
        """Persist the current in-memory index to xmd_index.json.

        Creates the library directory if it does not exist.
        """
        self._library_path.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1",
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "assets": [r.to_dict() for r in self._records.values()],
        }
        self.index_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def add(self, record: AssetRecord) -> None:
        """Add or replace a record in the index.

        If a record with the same ``xmd_uuid`` already exists it is replaced
        and its ``modified_at`` timestamp is updated.

        Args:
            record: The AssetRecord to store, keyed by its xmd_uuid.
        """
        record.touch()
        self._records[record.xmd_uuid] = record

    def get(self, xmd_uuid: str) -> AssetRecord | None:
        """Retrieve a record by its XMD UUID.

        Args:
            xmd_uuid: The UUID to look up.

        Returns:
            The matching AssetRecord, or None if not found.
        """
        return self._records.get(xmd_uuid)

    def remove(self, xmd_uuid: str) -> bool:
        """Remove a record from the index.

        Args:
            xmd_uuid: The UUID of the record to remove.

        Returns:
            True if the record was found and removed, False otherwise.
        """
        if xmd_uuid in self._records:
            del self._records[xmd_uuid]
            return True
        return False

    def all(self) -> list[AssetRecord]:
        """Return all records as a list.

        Returns:
            A list of all AssetRecord instances currently in the index.
        """
        return list(self._records.values())

    def find_by_name(self, name: str) -> list[AssetRecord]:
        """Find records whose name contains the search string (case-insensitive).

        Args:
            name: Substring to search for in record names.

        Returns:
            A list of matching AssetRecord instances.
        """
        name_lower = name.lower()
        return [r for r in self._records.values() if name_lower in r.name.lower()]

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[AssetRecord]:
        return iter(self._records.values())


# ---------------------------------------------------------------------------
# CatalogManager
# ---------------------------------------------------------------------------

class CatalogManager:
    """Manages Blender's blender_assets.cats.txt catalog definition file.

    Each entry in the file maps a UUID to a slash-separated catalog path
    (e.g. ``XMD/Materials/Stone``) and a simple display name. BlinQ uses this
    to group assets by type and category in Blender's Asset Browser.

    Args:
        library_path: Absolute path to the XMD library root folder.
    """

    CATALOG_FILENAME = "blender_assets.cats.txt"
    FILE_VERSION = "VERSION 1"

    def __init__(self, library_path: Path) -> None:
        self._library_path = library_path
        # Maps catalog_id (UUID string) → catalog_path string
        self._catalogs: dict[str, str] = {}

    @property
    def catalog_file_path(self) -> Path:
        """Absolute path to blender_assets.cats.txt."""
        return self._library_path / self.CATALOG_FILENAME

    def load(self) -> None:
        """Parse blender_assets.cats.txt into the in-memory catalog map.

        If the file does not exist the map starts empty.
        """
        self._catalogs = {}
        if not self.catalog_file_path.exists():
            return

        for line in self.catalog_file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("VERSION"):
                continue
            parts = line.split(":", maxsplit=2)
            if len(parts) >= 2:
                cat_id, cat_path = parts[0].strip(), parts[1].strip()
                self._catalogs[cat_id] = cat_path

    def save(self) -> None:
        """Write the current catalog map to blender_assets.cats.txt.

        Creates the library directory if it does not exist.
        """
        self._library_path.mkdir(parents=True, exist_ok=True)
        lines = [
            self.FILE_VERSION,
            "# Managed by BlinQ Blender — do not edit manually.",
            "",
        ]
        for cat_id, cat_path in sorted(self._catalogs.items(), key=lambda x: x[1]):
            simple_name = cat_path.split("/")[-1]
            lines.append(f"{cat_id}:{cat_path}:{simple_name}")
        self.catalog_file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def get_or_create(self, catalog_path: str) -> str:
        """Return the UUID for a catalog path, creating it if it does not exist.

        Also ensures all parent paths in the hierarchy exist as catalog entries
        so the tree is fully navigable in the Asset Browser.

        Args:
            catalog_path: Slash-separated path, e.g. "XMD/Materials/Stone".

        Returns:
            The UUID string for the requested catalog path.
        """
        # Ensure every parent path exists first
        parts = catalog_path.split("/")
        for i in range(1, len(parts)):
            parent_path = "/".join(parts[:i])
            if not self.id_for_path(parent_path):
                self._catalogs[str(uuid.uuid4())] = parent_path

        # Find or create the leaf
        existing = self.id_for_path(catalog_path)
        if existing:
            return existing

        new_id = str(uuid.uuid4())
        self._catalogs[new_id] = catalog_path
        return new_id

    def id_for_path(self, catalog_path: str) -> str | None:
        """Return the UUID for an exact catalog path, or None if not found.

        Args:
            catalog_path: The exact catalog path to look up.

        Returns:
            The UUID string if found, or None.
        """
        for cat_id, path in self._catalogs.items():
            if path == catalog_path:
                return cat_id
        return None

    def path_for_id(self, catalog_id: str) -> str | None:
        """Return the catalog path for a UUID, or None if not found.

        Args:
            catalog_id: The catalog UUID to look up.

        Returns:
            The catalog path string if found, or None.
        """
        return self._catalogs.get(catalog_id)

    def default_catalog_id(self, asset_type: str) -> str:
        """Return the UUID for the default catalog for a given asset type.

        Creates the standard XMD catalog hierarchy if the entry does not
        exist yet.

        Args:
            asset_type: An AssetType value string (e.g. "MATERIAL").

        Returns:
            The catalog UUID for the asset type's default path.
        """
        type_to_path: dict[str, str] = {
            AssetType.OBJECT.value: "XMD/Objects",
            AssetType.MATERIAL.value: "XMD/Materials",
            AssetType.BRUSH.value: "XMD/Brushes",
            AssetType.IMAGE.value: "XMD/Alphas",
            AssetType.TEXTURE.value: "XMD/Textures",
            AssetType.NODE_GROUP.value: "XMD/Node Groups",
            AssetType.COLLECTION.value: "XMD/Collections",
            AssetType.WORLD.value: "XMD/Environments",
            AssetType.SCENE.value: "XMD/Scenes",
        }
        path = type_to_path.get(asset_type, "XMD/Other")
        return self.get_or_create(path)


# ---------------------------------------------------------------------------
# MetadataMapper
# ---------------------------------------------------------------------------

_BLENDER_TYPE_TO_ASSET_TYPE: dict[str, str] = {
    "Object": AssetType.OBJECT.value,
    "Material": AssetType.MATERIAL.value,
    "Brush": AssetType.BRUSH.value,
    "Image": AssetType.IMAGE.value,
    "Texture": AssetType.TEXTURE.value,
    "ShaderNodeTree": AssetType.NODE_GROUP.value,
    "GeometryNodeTree": AssetType.NODE_GROUP.value,
    "Collection": AssetType.COLLECTION.value,
    "World": AssetType.WORLD.value,
    "Scene": AssetType.SCENE.value,
}

_XMD_UUID_PROP = "xmd_uuid"
_XMD_SOURCE_ID_PROP = "xmd_source_id"
_XMD_SYNC_HASH_PROP = "xmd_sync_hash"


class MetadataMapper:
    """Maps between Blender ID datablocks and AssetRecord instances.

    Handles reading and writing Blender's native asset metadata
    (author, description, tags, catalog_id) and BlinQ's custom properties
    (xmd_uuid, xmd_source_id, xmd_sync_hash).
    """

    def get_or_create_uuid(self, datablock: bpy.types.ID) -> str:
        """Return the XMD UUID for a datablock, creating one if absent.

        The UUID is stored as a custom property ``xmd_uuid`` on the datablock.

        Args:
            datablock: Any Blender ID (Object, Material, Brush, etc.).

        Returns:
            A stable UUID4 string for this datablock.
        """
        existing = datablock.get(_XMD_UUID_PROP, "")
        if existing:
            return str(existing)
        new_uuid = str(uuid.uuid4())
        datablock[_XMD_UUID_PROP] = new_uuid
        return new_uuid

    def asset_type_for(self, datablock: bpy.types.ID) -> str:
        """Resolve the AssetType enum value for a datablock.

        Args:
            datablock: Any Blender ID datablock.

        Returns:
            An AssetType value string.
        """
        return _BLENDER_TYPE_TO_ASSET_TYPE.get(
            type(datablock).__name__,
            AssetType.UNKNOWN.value,
        )

    def to_record(
        self,
        datablock: bpy.types.ID,
        catalog_manager: CatalogManager,
        blend_file: str = "",
    ) -> AssetRecord:
        """Build an AssetRecord from a Blender datablock's current state.

        Reads native asset metadata (if the datablock is marked as an asset)
        and BlinQ custom properties. The returned record is ready to be added
        to an XMDIndex.

        Args:
            datablock: A Blender ID datablock. Should be marked as an asset.
            catalog_manager: A CatalogManager to resolve or create catalog IDs.
            blend_file: Path to the .blend file (relative to library path) for the record.

        Returns:
            An AssetRecord populated from the datablock's current metadata.
        """
        xmd_uuid = self.get_or_create_uuid(datablock)
        asset_type = self.asset_type_for(datablock)

        tags: list[str] = []
        author = ""
        description = ""
        catalog_id = ""
        catalog_path = ""

        if datablock.asset_data:
            ad = datablock.asset_data
            tags = [t.name for t in ad.tags]
            author = ad.author or ""
            description = ad.description or ""
            if ad.catalog_id:
                catalog_id = ad.catalog_id
                catalog_path = catalog_manager.path_for_id(catalog_id) or ""

        # Fall back to the default catalog for this asset type
        if not catalog_id:
            catalog_id = catalog_manager.default_catalog_id(asset_type)
            catalog_path = catalog_manager.path_for_id(catalog_id) or f"XMD/{asset_type.title()}s"

        sync_hash = _compute_hash(datablock)

        return AssetRecord(
            xmd_uuid=xmd_uuid,
            name=datablock.name,
            asset_type=asset_type,
            tags=tags,
            author=author,
            description=description,
            catalog_path=catalog_path,
            catalog_id=catalog_id,
            source_id=str(datablock.get(_XMD_SOURCE_ID_PROP, "")),
            sync_hash=sync_hash,
            blend_file=blend_file,
        )

    def apply_to_blender(
        self,
        record: AssetRecord,
        datablock: bpy.types.ID,
    ) -> None:
        """Apply an AssetRecord's metadata back onto a Blender datablock.

        Marks the datablock as an asset if it is not already, then writes
        tags, author, description, catalog_id, and BlinQ custom properties.

        Args:
            record: The AssetRecord whose metadata should be applied.
            datablock: The target Blender ID datablock.
        """
        if not datablock.asset_data:
            datablock.asset_mark()

        ad = datablock.asset_data
        ad.author = record.author
        ad.description = record.description
        if record.catalog_id:
            ad.catalog_id = record.catalog_id

        # Sync tags — add missing, remove stale
        existing_tag_names = {t.name for t in ad.tags}
        desired = set(record.tags)
        for tag_name in record.tags:
            if tag_name not in existing_tag_names:
                ad.tags.new(tag_name, skip_if_exists=True)
        for tag in list(ad.tags):
            if tag.name not in desired:
                ad.tags.remove(tag)

        # BlinQ custom properties
        datablock[_XMD_UUID_PROP] = record.xmd_uuid
        if record.source_id:
            datablock[_XMD_SOURCE_ID_PROP] = record.source_id
        if record.sync_hash:
            datablock[_XMD_SYNC_HASH_PROP] = record.sync_hash


def _compute_hash(datablock: bpy.types.ID) -> str:
    """Compute a lightweight identity hash for a Blender datablock.

    Uses the datablock type name and display name as a minimal change signal.
    A full implementation would hash mesh geometry or node trees, but this is
    sufficient to detect renames between syncs.

    Args:
        datablock: Any Blender ID datablock.

    Returns:
        A 16-character hex digest string.
    """
    content = f"{type(datablock).__name__}:{datablock.name}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]
