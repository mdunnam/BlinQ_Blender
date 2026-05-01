"""BlinQ operators, pie menu, and Asset Browser context menu.

All bpy.types.Operator subclasses for the add-on live here, along with the
pie menu definition and the keymap that binds Shift+X in the 3D View.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import CollectionProperty, EnumProperty, IntProperty, StringProperty

from .. import diagnostics, op_utils, usage
from .library_gallery import BLINQ_PG_AssetSelection
from ..assets.index import CatalogManager, MetadataMapper, XMDIndex
from ..assets.previews import PreviewManager
from ..models import RetopoState
from ..prefs import get_prefs


def _usage_blend(context: bpy.types.Context) -> tuple[str, str]:
    """Return (blend_file, scene_name) used to enrich usage records."""
    return (bpy.data.filepath or "", context.scene.name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TYPE_TO_COLLECTION: dict[str, str] = {
    "Object": "objects",
    "Material": "materials",
    "Brush": "brushes",
    "Image": "images",
    "Texture": "textures",
    "Collection": "collections",
    "World": "worlds",
}


def _resolve_target(context: bpy.types.Context) -> bpy.types.ID | None:
    """Return the most relevant datablock to act on from the current context.

    Checks active_object, then material, then brush.

    Args:
        context: The current Blender context.

    Returns:
        A Blender ID datablock, or None if nothing actionable is found.
    """
    if context.active_object:
        return context.active_object
    if getattr(context, "material", None):
        return context.material  # type: ignore[return-value]
    if getattr(context, "brush", None):
        return context.brush  # type: ignore[return-value]
    return None


def _load_index_and_catalog(
    prefs,
) -> tuple[XMDIndex, CatalogManager] | tuple[None, None]:
    """Load and return the XMDIndex and CatalogManager for the library path.

    Args:
        prefs: The XMDPreferences instance.

    Returns:
        A ``(XMDIndex, CatalogManager)`` tuple, or ``(None, None)`` if no
        library path is configured.
    """
    if not prefs.library_path:
        return None, None
    lib_path = Path(prefs.library_path)
    catalog = CatalogManager(lib_path)
    catalog.load()
    index = XMDIndex(lib_path)
    index.load()
    return index, catalog


def _get_transport(prefs):
    """Return an IPCTransport for the configured work directory, or None.

    Args:
        prefs: The XMDPreferences instance.

    Returns:
        An IPCTransport instance, or None if work_dir is not set.
    """
    work_dir: str = getattr(prefs, "work_dir", "")
    if not work_dir:
        return None
    from ..bridge.ipc import IPCTransport
    return IPCTransport(Path(work_dir))


# ---------------------------------------------------------------------------
# ── Asset operators ──────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_OT_register_asset(bpy.types.Operator):
    """Mark the active datablock as a Blender asset and register it in the XMD Library."""

    bl_idname = "blinq.register_asset"
    bl_label = "Register in XMD"
    bl_description = (
        "Mark the active object, material, or brush as a Blender asset "
        "and add it to the XMD Library index"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable when an actionable datablock exists in context.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        return _resolve_target(context) is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Register the active datablock into the XMD Library.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        datablock = _resolve_target(context)
        if datablock is None:
            self.report({"ERROR"}, "No active object, material, or brush to register")
            return {"CANCELLED"}

        prefs = get_prefs(context)
        if not prefs.library_path:
            self.report({"WARNING"}, "Set the XMD Library Path in Add-on Preferences first")
            return {"CANCELLED"}

        if not datablock.asset_data:
            datablock.asset_mark()

        lib_path = Path(prefs.library_path)
        catalog = CatalogManager(lib_path)
        catalog.load()
        mapper = MetadataMapper()

        blend_file = ""
        if bpy.data.filepath:
            try:
                blend_file = str(Path(bpy.data.filepath).relative_to(lib_path))
            except ValueError:
                blend_file = bpy.data.filepath

        record = mapper.to_record(datablock, catalog, blend_file=blend_file)

        if not datablock.asset_data.catalog_id and record.catalog_id:
            datablock.asset_data.catalog_id = record.catalog_id

        index = XMDIndex(lib_path)
        index.load()
        index.add(record)
        index.save()
        catalog.save()

        bpy_collection = _TYPE_TO_COLLECTION.get(type(datablock).__name__, "")
        if bpy_collection:
            PreviewManager().request_preview(datablock.name, bpy_collection)

        from ..ui.panels import invalidate_library_cache
        invalidate_library_cache()

        diagnostics.info(
            "asset",
            f"registered '{datablock.name}' ({record.asset_type}) uuid={record.xmd_uuid[:8]}",
        )
        blend_file, scene_name = _usage_blend(context)
        usage.log(
            prefs.library_path,
            event="asset.registered",
            asset_uuid=record.xmd_uuid,
            blend_file=blend_file,
            scene_name=scene_name,
            payload={"name": datablock.name, "type": record.asset_type},
        )
        self.report(
            {"INFO"},
            f"Registered '{datablock.name}' ({record.asset_type}) \u2014 UUID {record.xmd_uuid[:8]}\u2026",
        )
        return {"FINISHED"}


class BLINQ_OT_import_asset(bpy.types.Operator):
    """Import a registered asset from the library into the current blend."""

    bl_idname = "blinq.import_asset"
    bl_label = "Import Asset from XMD"
    bl_description = "Import a registered asset from the XMD Library into this blend"
    bl_options = {"REGISTER", "UNDO"}

    asset_uuid: bpy.props.StringProperty(  # type: ignore[assignment]
        name="Asset UUID", default=""
    )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Show dialog to enter asset UUID if not set.

        Args:
            context: The current Blender context.
            event: The triggering input event.

        Returns:
            Blender operator result set.
        """
        if self.asset_uuid:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the dialog.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        layout.label(text="Enter the XMD UUID of an asset to import", icon="IMPORT")
        layout.prop(self, "asset_uuid", text="UUID")

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Import the asset by UUID.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        uuid_str = self.asset_uuid.strip()
        if not uuid_str:
            self.report({"WARNING"}, "Asset UUID cannot be empty")
            return {"CANCELLED"}

        with op_utils.safe_execute(self, f"importing asset {uuid_str[:8]}"):
            prefs = get_prefs(context)
            if not op_utils.ensure_library_path(self, prefs):
                return {"CANCELLED"}

            index = XMDIndex(Path(prefs.library_path))
            index.load()
            record = index.get(uuid_str)
            if record is None:
                self.report({"ERROR"}, f"Asset '{uuid_str[:8]}...' not found in library")
                return {"CANCELLED"}

            if not record.blend_file:
                self.report({"ERROR"}, f"Asset '{record.name}' has no source blend file")
                return {"CANCELLED"}

            src_blend = Path(prefs.library_path) / record.blend_file
            if not src_blend.exists():
                self.report({"ERROR"}, f"Source file not found: {src_blend}")
                return {"CANCELLED"}

            # Determine collection to append from
            type_to_collection = {
                "Object": "objects",
                "Material": "materials",
                "Brush": "brushes",
                "Image": "images",
                "Texture": "textures",
                "Collection": "collections",
                "World": "worlds",
            }
            collection = type_to_collection.get(record.asset_type)
            if not collection:
                self.report({"ERROR"}, f"Unsupported asset type: {record.asset_type}")
                return {"CANCELLED"}

            # Append the datablock from the source blend
            try:
                with bpy.data.libraries.load(str(src_blend)) as (data_from, data_to):
                    items = getattr(data_from, collection, [])
                    if record.name not in items:
                        self.report(
                            {"ERROR"},
                            f"'{record.name}' not found in {collection} in {src_blend.name}",
                        )
                        return {"CANCELLED"}
                    setattr(data_to, collection, [record.name])

                # Get the newly imported datablock
                imported = None
                if collection == "objects":
                    imported = bpy.data.objects.get(record.name)
                    if imported:
                        context.collection.objects.link(imported)
                elif collection == "materials":
                    imported = bpy.data.materials.get(record.name)
                elif collection == "brushes":
                    imported = bpy.data.brushes.get(record.name)
                elif collection == "images":
                    imported = bpy.data.images.get(record.name)
                elif collection == "worlds":
                    imported = bpy.data.worlds.get(record.name)

                if imported is None:
                    self.report({"ERROR"}, f"Failed to import '{record.name}'")
                    return {"CANCELLED"}

                # Stamp UUID and apply metadata
                imported["xmd_uuid"] = uuid_str
                mapper = MetadataMapper()
                mapper.apply_to_blender(record, imported)

                diagnostics.info(
                    "asset",
                    f"imported {record.asset_type.lower()}: {record.name}",
                )
                blend_file, scene_name = _usage_blend(context)
                usage.log(
                    prefs.library_path,
                    event="asset.imported",
                    asset_uuid=uuid_str,
                    blend_file=blend_file,
                    scene_name=scene_name,
                    payload={"name": record.name, "type": record.asset_type},
                )
                self.report({"INFO"}, f"Imported: {record.name}")
                return {"FINISHED"}
            except Exception as exc:
                diagnostics.error("asset", f"import failed: {exc}")
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}


class BLINQ_OT_gallery_set_type_filter(bpy.types.Operator):
    """Set the asset type filter for the gallery."""

    bl_idname = "blinq.gallery_set_type_filter"
    bl_label = "Filter by Type"
    bl_options = {"REGISTER"}

    filter_type: bpy.props.StringProperty(  # type: ignore[assignment]
        name="Filter Type", default=""
    )

    _LABELS: dict[str, str] = {
        "ALL": "All Assets",
        "OBJECT": "Objects",
        "MATERIAL": "Materials",
        "BRUSH": "Brushes",
        "IMAGE": "Images",
        "TEXTURE": "Textures",
        "NODE_GROUP": "Node Groups",
        "COLLECTION": "Collections",
        "WORLD": "Worlds",
        "SCENE": "Scenes",
    }

    @classmethod
    def description(cls, context: bpy.types.Context, properties) -> str:
        return cls._LABELS.get(properties.filter_type, properties.filter_type)

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Set the type filter.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        context.scene.xmd_gallery_filter_type = self.filter_type
        return {"FINISHED"}


class BLINQ_OT_gallery_clear_selection(bpy.types.Operator):
    """Clear the selected asset."""

    bl_idname = "blinq.gallery_clear_selection"
    bl_label = "Clear Selection"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Clear selection.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        context.scene.xmd_selected_asset_uuid = ""
        return {"FINISHED"}


class BLINQ_OT_gallery_select_asset(bpy.types.Operator):
    """Select an asset in the library gallery."""

    bl_idname = "blinq.gallery_select_asset"
    bl_label = "Select Asset"
    bl_options = {"REGISTER"}

    asset_uuid: bpy.props.StringProperty(  # type: ignore[assignment]
        name="Asset UUID", default=""
    )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Set the selected asset UUID on the scene.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        context.scene.xmd_selected_asset_uuid = self.asset_uuid
        return {"FINISHED"}


class BLINQ_OT_gallery_delete_asset(bpy.types.Operator):
    """Delete a selected asset from the library."""

    bl_idname = "blinq.gallery_delete_asset"
    bl_label = "Delete Asset"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Delete the selected asset from the library index.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        scene = context.scene
        uuid_str = scene.xmd_selected_asset_uuid
        if not uuid_str:
            self.report({"WARNING"}, "No asset selected")
            return {"CANCELLED"}

        prefs = get_prefs(context)
        if not op_utils.ensure_library_path(self, prefs):
            return {"CANCELLED"}

        with op_utils.safe_execute(self, f"deleting asset {uuid_str[:8]}"):
            index = XMDIndex(Path(prefs.library_path))
            index.load()
            if index.remove(uuid_str):
                index.save()
                scene.xmd_selected_asset_uuid = ""
                diagnostics.info("asset", f"deleted asset: {uuid_str[:8]}")
                self.report({"INFO"}, "Asset deleted from library")
                from ..ui.panels import invalidate_library_cache
                invalidate_library_cache()
                return {"FINISHED"}
            else:
                self.report({"WARNING"}, "Asset not found in library")
                return {"CANCELLED"}


class BLINQ_OT_clear_gallery_search(bpy.types.Operator):
    """Clear the gallery search field."""

    bl_idname = "blinq.clear_gallery_search"
    bl_label = "Clear Search"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Clear the search text.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        context.scene.xmd_gallery_search_text = ""
        return {"FINISHED"}


class BLINQ_OT_gallery_clear_filters(bpy.types.Operator):
    """Clear all gallery filters."""

    bl_idname = "blinq.gallery_clear_filters"
    bl_label = "Clear Filters"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Clear all filter settings.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        scene = context.scene
        scene.xmd_gallery_search_text = ""
        scene.xmd_gallery_filter_type = "ALL"
        return {"FINISHED"}


class BLINQ_OT_list_library_assets(bpy.types.Operator):
    """List all registered assets in the library."""

    bl_idname = "blinq.list_library_assets"
    bl_label = "List Library Assets"
    bl_description = "Show all registered assets in the XMD Library"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """List assets to the report and diagnostics log.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        prefs = get_prefs(context)
        if not op_utils.ensure_library_path(self, prefs):
            return {"CANCELLED"}

        with op_utils.safe_execute(self, "listing library assets"):
            index = XMDIndex(Path(prefs.library_path))
            index.load()
            assets = index.all()

            if not assets:
                self.report({"INFO"}, "No assets registered in library")
                diagnostics.info("asset", "library is empty")
                return {"FINISHED"}

            # Build output
            by_type: dict[str, list] = {}
            for record in assets:
                key = record.asset_type or "Unknown"
                if key not in by_type:
                    by_type[key] = []
                by_type[key].append(record)

            # Log summary
            diagnostics.info("asset", f"library contains {len(assets)} asset(s)")
            for asset_type, records in sorted(by_type.items()):
                diagnostics.info("asset", f"  {asset_type}: {len(records)}")
                for record in records[:5]:  # Log first 5 per type
                    tags_str = ", ".join(record.tags) if record.tags else "(no tags)"
                    diagnostics.info(
                        "asset",
                        f"    • {record.name} — {tags_str}",
                    )
                if len(records) > 5:
                    diagnostics.info("asset", f"    ... and {len(records) - 5} more")

            # Report summary
            summary_lines = [f"Library contains {len(assets)} asset(s):"]
            for asset_type, records in sorted(by_type.items()):
                summary_lines.append(f"  {asset_type}: {len(records)}")

            self.report({"INFO"}, " | ".join(summary_lines))
            return {"FINISHED"}


class BLINQ_OT_import_assets_folder(bpy.types.Operator):
    """Import multiple asset .blend files from a folder into the library."""

    bl_idname = "blinq.import_assets_folder"
    bl_label = "Batch Import Assets"
    bl_description = "Import multiple .blend files from a folder into the XMD Library"
    bl_options = {"REGISTER"}

    directory: bpy.props.StringProperty(  # type: ignore[assignment]
        name="Folder",
        description="Source folder containing .blend files",
        subtype="DIR_PATH",
    )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Show folder picker.

        Args:
            context: The current Blender context.
            event: The triggering input event.

        Returns:
            Blender operator result set.
        """
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Import all .blend files from the selected folder.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        folder = Path(self.directory)
        if not folder.exists() or not folder.is_dir():
            self.report({"ERROR"}, f"Invalid folder: {folder}")
            return {"CANCELLED"}

        with op_utils.safe_execute(self, f"importing from {folder.name}"):
            prefs = get_prefs(context)
            if not op_utils.ensure_library_path(self, prefs):
                return {"CANCELLED"}

            lib_path = Path(prefs.library_path)
            blend_files = list(folder.glob("*.blend"))
            if not blend_files:
                self.report({"WARNING"}, f"No .blend files found in {folder.name}")
                return {"CANCELLED"}

            imported_count = 0
            failed = []

            for src_blend in blend_files:
                try:
                    # Copy file to library with unique name
                    dst_name = src_blend.stem + ".blend"
                    # If name collision, append a number
                    dst_path = lib_path / dst_name
                    counter = 1
                    while dst_path.exists():
                        dst_path = lib_path / f"{src_blend.stem}_{counter:03d}.blend"
                        counter += 1

                    # Copy the file
                    import shutil
                    shutil.copy2(str(src_blend), str(dst_path))

                    # Create library entry
                    from ..models import AssetRecord, AssetType
                    index = XMDIndex(lib_path)
                    index.load()
                    catalog = CatalogManager(lib_path)
                    catalog.load()

                    record = AssetRecord(
                        name=src_blend.stem,
                        asset_type=AssetType.UNKNOWN.value,
                        author=prefs.xmdsource_display_name or "Imported",
                        blend_file=dst_path.relative_to(lib_path).as_posix(),
                    )
                    record.catalog_id = catalog.get_or_create("XMD/Imported")
                    record.catalog_path = "XMD/Imported"
                    index._records[record.xmd_uuid] = record
                    index.save()
                    catalog.save()
                    imported_count += 1
                    diagnostics.info("asset", f"batch imported: {src_blend.name}")
                except Exception as exc:
                    failed.append(src_blend.name)
                    diagnostics.warn("asset", f"failed to import {src_blend.name}: {exc}")

            summary = f"Imported {imported_count} asset(s)"
            if failed:
                summary += f" ({len(failed)} failed)"
            self.report({"INFO"}, summary)
            usage.log(
                prefs.library_path,
                event="asset.batch_imported",
                payload={"imported": imported_count, "failed": len(failed)},
            )
            return {"FINISHED"}


class BLINQ_OT_export_asset_file(bpy.types.Operator):
    """Export a registered asset as a portable .blend file."""

    bl_idname = "blinq.export_asset_file"
    bl_label = "Export Asset to File"
    bl_description = "Save a registered asset as a standalone .blend file for sharing"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(  # type: ignore[assignment]
        name="File Path",
        description="Output .blend file path",
        subtype="FILE_PATH",
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable when an asset-registered datablock is active.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        db = _resolve_target(context)
        return bool(db and db.get("xmd_uuid"))

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Show file save dialog.

        Args:
            context: The current Blender context.
            event: The triggering input event.

        Returns:
            Blender operator result set.
        """
        db = _resolve_target(context)
        if db:
            self.filepath = f"{db.name}.blend"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Save the asset to a .blend file.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        db = _resolve_target(context)
        if not db or not db.get("xmd_uuid"):
            self.report({"ERROR"}, "No XMD-registered asset selected")
            return {"CANCELLED"}

        filepath = Path(self.filepath)
        if not filepath.parent.exists():
            try:
                filepath.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.report({"ERROR"}, f"Cannot create output folder: {exc}")
                return {"CANCELLED"}

        with op_utils.safe_execute(self, f"exporting {db.name}"):
            # Create a temporary blend to hold just this asset
            temp_filepath = str(filepath)

            # Save current blend, export asset, restore
            current_filepath = bpy.data.filepath
            try:
                bpy.ops.wm.save_as_mainfile(filepath=temp_filepath)
                bpy.data.filepath = current_filepath
                diagnostics.info("asset", f"exported asset: {filepath.name}")
                usage.log(
                    get_prefs(context).library_path,
                    event="asset.exported",
                    asset_uuid=str(db.get("xmd_uuid", "")),
                    payload={"name": db.name, "path": str(filepath)},
                )
                self.report({"INFO"}, f"Exported: {filepath.name}")
                return {"FINISHED"}
            except Exception as exc:
                bpy.data.filepath = current_filepath
                diagnostics.error("asset", f"export failed: {exc}")
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}


class BLINQ_OT_push_metadata(bpy.types.Operator):
    """Push current Blender metadata for the active datablock into the XMD Library index."""

    bl_idname = "blinq.push_metadata"
    bl_label = "Push Metadata to XMD"
    bl_description = (
        "Update the XMD Library index entry from Blender's current "
        "metadata (author, tags, description, catalog)"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable only when the active datablock is registered and is a Blender asset.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        db = _resolve_target(context)
        return (
            db is not None
            and bool(db.get("xmd_uuid", ""))
            and db.asset_data is not None
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Sync Blender metadata to the XMD index.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        datablock = _resolve_target(context)
        if datablock is None:
            return {"CANCELLED"}
        prefs = get_prefs(context)
        index, catalog = _load_index_and_catalog(prefs)
        if index is None or catalog is None:
            self.report({"WARNING"}, "Set the XMD Library Path in Add-on Preferences first")
            return {"CANCELLED"}
        index.add(MetadataMapper().to_record(datablock, catalog))
        index.save()
        catalog.save()
        self.report({"INFO"}, f"Metadata pushed for '{datablock.name}'")
        return {"FINISHED"}


class BLINQ_OT_pull_metadata(bpy.types.Operator):
    """Pull XMD Library index metadata onto the active Blender datablock."""

    bl_idname = "blinq.pull_metadata"
    bl_label = "Pull Metadata from XMD"
    bl_description = (
        "Update the active asset's Blender metadata "
        "(author, tags, description, catalog) from the XMD Library index"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable only when the active datablock has an XMD UUID.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        db = _resolve_target(context)
        return db is not None and bool(db.get("xmd_uuid", ""))

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Sync XMD index metadata to the Blender datablock.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        datablock = _resolve_target(context)
        if datablock is None:
            return {"CANCELLED"}
        prefs = get_prefs(context)
        index, _ = _load_index_and_catalog(prefs)
        if index is None:
            self.report({"WARNING"}, "Set the XMD Library Path in Add-on Preferences first")
            return {"CANCELLED"}
        xmd_uuid = str(datablock.get("xmd_uuid", ""))
        record = index.get(xmd_uuid)
        if record is None:
            self.report({"WARNING"}, f"No XMD record for UUID '{xmd_uuid[:8]}\u2026'")
            return {"CANCELLED"}
        MetadataMapper().apply_to_blender(record, datablock)
        self.report({"INFO"}, f"Metadata pulled for '{datablock.name}'")
        return {"FINISHED"}


class BLINQ_OT_sync_preview(bpy.types.Operator):
    """Regenerate the asset preview thumbnail for the active datablock."""

    bl_idname = "blinq.sync_preview"
    bl_label = "Sync Preview"
    bl_description = "Regenerate the Blender asset preview thumbnail for the active object"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable only when the active datablock is marked as a Blender asset.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        db = _resolve_target(context)
        return db is not None and db.asset_data is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Schedule preview regeneration.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        datablock = _resolve_target(context)
        if datablock is None or not datablock.asset_data:
            return {"CANCELLED"}
        bpy_collection = _TYPE_TO_COLLECTION.get(type(datablock).__name__, "")
        if not bpy_collection:
            self.report({"WARNING"}, f"Preview not supported for '{type(datablock).__name__}'")
            return {"CANCELLED"}
        PreviewManager().request_preview(datablock.name, bpy_collection)
        self.report({"INFO"}, f"Preview scheduled for '{datablock.name}'")
        return {"FINISHED"}


class BLINQ_OT_add_tag(bpy.types.Operator):
    """Open a dialog to add a tag to the active asset."""

    bl_idname = "blinq.add_tag"
    bl_label = "Add Tag"
    bl_description = "Add a tag to the active asset's metadata"
    bl_options = {"REGISTER", "UNDO"}

    tag_name: StringProperty(name="Tag", default="")  # type: ignore[assignment]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable only when the active datablock is a Blender asset.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        db = _resolve_target(context)
        return db is not None and db.asset_data is not None

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Show a small dialog for the tag name.

        Args:
            context: The current Blender context.
            event: The triggering input event.

        Returns:
            Blender operator result set.
        """
        self.tag_name = ""
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the tag name input inside the dialog.

        Args:
            context: The current Blender context.
        """
        self.layout.prop(self, "tag_name", text="Tag Name")

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Add the entered tag to the active asset.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        tag = self.tag_name.strip()
        if not tag:
            self.report({"WARNING"}, "Tag name cannot be empty")
            return {"CANCELLED"}
        db = _resolve_target(context)
        if not db or not db.asset_data:
            return {"CANCELLED"}
        db.asset_data.tags.new(tag, skip_if_exists=True)
        return {"FINISHED"}


class BLINQ_OT_remove_tag(bpy.types.Operator):
    """Remove a named tag from the active asset."""

    bl_idname = "blinq.remove_tag"
    bl_label = "Remove Tag"
    bl_description = "Remove this tag from the active asset"
    bl_options = {"REGISTER", "UNDO"}

    tag_name: StringProperty(name="Tag", default="")  # type: ignore[assignment]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable only when the active datablock is a Blender asset.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        db = _resolve_target(context)
        return db is not None and db.asset_data is not None

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Remove the tag matching tag_name from the active asset.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        db = _resolve_target(context)
        if not db or not db.asset_data:
            return {"CANCELLED"}
        for tag in db.asset_data.tags:
            if tag.name == self.tag_name:
                db.asset_data.tags.remove(tag)
                break
        return {"FINISHED"}


class BLINQ_OT_open_library(bpy.types.Operator):
    """Open the XMD Library folder in the system file explorer."""

    bl_idname = "blinq.open_library"
    bl_label = "Open XMD Library Folder"
    bl_description = "Open the configured XMD Library folder in the system file explorer"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable only when a library path is configured.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        return bool(get_prefs(context).library_path)

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Open or create the library folder.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        path = Path(get_prefs(context).library_path)
        if not path.exists():
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.report({"ERROR"}, f"Could not create library folder: {exc}")
                return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=str(path))
        return {"FINISHED"}


class BLINQ_OT_refresh_library(bpy.types.Operator):
    """Force-reload the XMD library index and reset the panel count cache."""

    bl_idname = "blinq.refresh_library"
    bl_label = "Refresh Library"
    bl_description = "Reload the XMD library index from disk and refresh the asset count"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Clear the cache and reload the index.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        from ..ui.panels import invalidate_library_cache
        invalidate_library_cache()
        prefs = get_prefs(context)
        if not prefs.library_path:
            self.report({"WARNING"}, "No library path configured")
            return {"CANCELLED"}
        index = XMDIndex(Path(prefs.library_path))
        index.load()
        self.report({"INFO"}, f"Library refreshed: {len(index)} asset(s)")
        return {"FINISHED"}


class BLINQ_OT_sign_in(bpy.types.Operator):
    """Sign in to XMDSource with email and password to activate BlinQ."""

    bl_idname = "blinq.sign_in"
    bl_label = "Sign In to XMDSource"
    bl_description = "Sign in with your XMDSource account to activate BlinQ Blender"
    bl_options = {"REGISTER"}

    username: bpy.props.StringProperty(  # type: ignore[assignment]
        name="Email / Username", default=""
    )
    password: bpy.props.StringProperty(  # type: ignore[assignment]
        name="Password", default="", subtype="PASSWORD"
    )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Pre-populate username from preferences and show the dialog.

        Args:
            context: The current Blender context.
            event: The triggering input event.

        Returns:
            Blender operator result set.
        """
        prefs = get_prefs(context)
        self.username = prefs.xmdsource_username or ""
        self.password = ""
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the sign-in dialog fields.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        layout.label(text="Sign in to XMDSource.com", icon="WORLD_DATA")
        layout.prop(self, "username", text="Email / Username")
        layout.prop(self, "password", text="Password")
        layout.label(text="xmdsource.com", icon="URL")

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Call CloudClient.login() and update activation_status in preferences.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        username = self.username.strip()
        password = self.password
        if not username or not password:
            self.report({"WARNING"}, "Enter both email and password")
            return {"CANCELLED"}

        from ..integrations.cloud import CloudClient
        prefs = get_prefs(context)
        prefs.activation_status = "CHECKING"

        client = CloudClient(prefs)
        ok, msg = client.login(username, password)
        if not ok:
            prefs.activation_status = "UNLICENSED"
            diagnostics.warn("cloud", f"login failed: {msg}")
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        diagnostics.info("cloud", f"signed in as {prefs.xmdsource_display_name or username}")
        usage.log(
            prefs.library_path,
            event="cloud.sign_in",
            payload={"username": username, "display_name": prefs.xmdsource_display_name},
        )

        # Login succeeded — now sync the runtime license
        ok2, _, msg2 = client.sync_runtime_license()
        prefs.activation_status = client.resolve_activation_status(ok2, msg2)
        diagnostics.info("cloud", f"activation status → {prefs.activation_status}")

        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BLINQ_OT_sign_out(bpy.types.Operator):
    """Sign out of XMDSource, release the active lease, and clear stored credentials."""

    bl_idname = "blinq.sign_out"
    bl_label = "Sign Out"
    bl_description = "Sign out of XMDSource, release any active license lease, and clear stored credentials"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Call CloudClient.logout() and reset activation_status.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        from ..integrations.cloud import CloudClient
        prefs = get_prefs(context)
        client = CloudClient(prefs)
        client.logout()
        prefs.activation_status = "UNLICENSED"
        diagnostics.info("cloud", "signed out")
        self.report({"INFO"}, "Signed out of XMDSource")
        return {"FINISHED"}


class BLINQ_OT_check_activation(bpy.types.Operator):
    """Refresh the runtime license check against XMDSource without re-entering credentials."""

    bl_idname = "blinq.check_activation"
    bl_label = "Refresh Activation"
    bl_description = "Re-check runtime license state against XMDSource using the stored session"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Run sync_runtime_license() and update activation_status.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        from ..integrations.cloud import CloudClient
        prefs = get_prefs(context)

        client = CloudClient(prefs)
        if not client.is_logged_in():
            prefs.activation_status = "UNLICENSED"
            self.report({"WARNING"}, "Not signed in — use Sign In first")
            return {"CANCELLED"}

        prefs.activation_status = "CHECKING"
        ok, _, msg = client.sync_runtime_license()
        prefs.activation_status = client.resolve_activation_status(ok, msg)
        diagnostics.info("cloud", f"activation refresh → {prefs.activation_status}")

        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BLINQ_OT_check_health(bpy.types.Operator):
    """Validate addon configuration, paths, and connectivity."""

    bl_idname = "blinq.check_health"
    bl_label = "Check Add-on Health"
    bl_description = "Validate configuration, paths, and XMD Desktop connectivity"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Run configuration and connectivity checks.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        with op_utils.safe_execute(self, "health check"):
            prefs = get_prefs(context)
            issues = []

            # Check library path
            if not prefs.library_path:
                issues.append("Library Path not configured")
            else:
                lib = Path(prefs.library_path)
                if not lib.exists():
                    issues.append(f"Library Path does not exist: {lib}")
                elif not lib.is_dir():
                    issues.append(f"Library Path is not a directory: {lib}")

            # Check bridge work directory
            if not prefs.work_dir:
                issues.append("Bridge Work Directory not configured")
            else:
                work = Path(prefs.work_dir)
                if not work.exists():
                    issues.append(f"Bridge Work Directory does not exist: {work}")
                elif not work.is_dir():
                    issues.append(f"Bridge Work Directory is not a directory: {work}")
                else:
                    # Try to verify bridge connectivity
                    try:
                        transport = _get_transport(prefs)
                        if transport:
                            transport.self_test()
                            diagnostics.info("health", "bridge connectivity OK")
                    except Exception as exc:
                        issues.append(f"Bridge connectivity failed: {exc}")

            # Check cloud activation
            from ..integrations.cloud import CloudClient
            client = CloudClient(prefs)
            if not client.is_logged_in():
                issues.append("Not signed in to XMDSource")
            elif not client.has_active_runtime_access():
                issues.append(f"License inactive: {prefs.activation_status}")

            if issues:
                msg = " | ".join(issues)
                diagnostics.warn("health", msg)
                self.report({"WARNING"}, msg)
            else:
                msg = "All checks passed ✓"
                diagnostics.info("health", msg)
                self.report({"INFO"}, msg)

            return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Bridge operators ─────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_OT_send_mesh(bpy.types.Operator):
    """Export selected mesh objects to the bridge work directory for XMD Desktop."""

    bl_idname = "blinq.send_mesh"
    bl_label = "Send Mesh \u2192 ZBrush"
    bl_description = (
        "Export selected mesh objects as OBJ to the bridge work directory "
        "and signal XMD Desktop to receive them"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable when at least one mesh object is selected.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Export and signal XMD Desktop.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        prefs = get_prefs(context)
        transport = _get_transport(prefs)
        if transport is None:
            self.report({"WARNING"}, "Set the Bridge Work Directory in Add-on Preferences first")
            return {"CANCELLED"}

        try:
            from ..bridge.io import MeshExporter
            result = MeshExporter(transport).execute()
            diagnostics.info(
                "bridge",
                f"sent mesh: {result['objects']} object(s) \u2192 {result['file']}",
            )
            blend_file, scene_name = _usage_blend(context)
            usage.log(
                prefs.library_path,
                event="bridge.send_mesh",
                blend_file=blend_file,
                scene_name=scene_name,
                payload={
                    "file": result["file"],
                    "objects": result["objects"],
                    "uuids": result["uuids"],
                },
            )
            self.report(
                {"INFO"},
                f"Sent {result['objects']} object(s) \u2014 {result['file']}",
            )
            return {"FINISHED"}
        except Exception as exc:
            diagnostics.error("bridge", f"send mesh failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class BLINQ_OT_send_meshes_each(bpy.types.Operator):
    """Send each selected mesh as a separate OBJ (SubTool-style multi-send)."""

    bl_idname = "blinq.send_meshes_each"
    bl_label = "Send Each → SubTools"
    bl_description = (
        "Export each selected mesh object as its own OBJ in mesh_out/ "
        "and signal XMD Desktop to receive them as separate SubTools"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = get_prefs(context)
        transport = _get_transport(prefs)
        if transport is None:
            self.report({"WARNING"}, "Set the Bridge Work Directory in Add-on Preferences first")
            return {"CANCELLED"}
        try:
            from ..bridge.io import MeshExporter
            result = MeshExporter(transport).execute_per_object()
            diagnostics.info(
                "bridge",
                f"sent {result['objects']} object(s) as SubTools "
                f"({len(result['files'])} file(s))",
            )
            blend_file, scene_name = _usage_blend(context)
            usage.log(
                prefs.library_path,
                event="bridge.send_meshes_each",
                blend_file=blend_file,
                scene_name=scene_name,
                payload={"objects": result["objects"], "files": result["files"]},
            )
            self.report(
                {"INFO"},
                f"Sent {result['objects']} object(s) as SubTools",
            )
            return {"FINISHED"}
        except Exception as exc:
            diagnostics.error("bridge", f"send-each failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class BLINQ_OT_receive_mesh(bpy.types.Operator):
    """Manually import the latest mesh from the bridge mesh_in directory."""

    bl_idname = "blinq.receive_mesh"
    bl_label = "Receive Mesh \u2190 ZBrush"
    bl_description = (
        "Import the most recent OBJ file from the bridge mesh_in directory "
        "into the current scene"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable only when a work directory is configured.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        return bool(getattr(get_prefs(context), "work_dir", ""))

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Import the latest OBJ from mesh_in/.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        prefs = get_prefs(context)
        transport = _get_transport(prefs)
        if transport is None:
            self.report({"WARNING"}, "Set the Bridge Work Directory in Add-on Preferences first")
            return {"CANCELLED"}

        obj_files = sorted(
            transport.mesh_in_dir.glob("*.obj"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not obj_files:
            self.report({"WARNING"}, "No OBJ files found in mesh_in/")
            return {"CANCELLED"}

        latest = obj_files[0]
        try:
            from ..bridge.io import MeshImporter
            result = MeshImporter(transport).execute({"file": latest.name})
            diagnostics.info("bridge", f"imported {latest.name}: {', '.join(result['imported'])}")
            self.report({"INFO"}, f"Imported: {', '.join(result['imported'])}")
            return {"FINISHED"}
        except Exception as exc:
            diagnostics.error("bridge", f"receive mesh failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class BLINQ_OT_send_texture(bpy.types.Operator):
    """Export the active material's base-color texture to the bridge work directory."""

    bl_idname = "blinq.send_texture"
    bl_label = "Send Texture \u2192 ZBrush"
    bl_description = (
        "Copy the active object's base-color texture to the bridge textures_out directory "
        "and signal XMD Desktop"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable when the active object has a material with a texture.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        obj = context.active_object
        if not obj or not obj.active_material:
            return False
        mat = obj.active_material
        return mat.use_nodes and bool(mat.node_tree)

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Copy the first Image texture node's file to textures_out/ and signal XMD.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        import shutil

        prefs = get_prefs(context)
        transport = _get_transport(prefs)
        if transport is None:
            self.report({"WARNING"}, "Set the Bridge Work Directory in Add-on Preferences first")
            return {"CANCELLED"}

        mat = context.active_object.active_material
        # Find the first Image Texture node with a loaded image
        image = None
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image and node.image.filepath:
                image = node.image
                break

        if image is None:
            self.report({"WARNING"}, "No file-backed Image Texture node found in active material")
            return {"CANCELLED"}

        src = Path(bpy.path.abspath(image.filepath))
        if not src.exists():
            self.report({"ERROR"}, f"Texture file not found: {src}")
            return {"CANCELLED"}

        dst = transport.textures_out_dir / src.name
        try:
            shutil.copy2(str(src), str(dst))
        except OSError as exc:
            self.report({"ERROR"}, f"Copy failed: {exc}")
            return {"CANCELLED"}

        from ..bridge.ipc import CommandEnvelope
        from ..models import BridgeCommandType
        transport.write_command(
            CommandEnvelope(
                command=BridgeCommandType.SEND_TEXTURE.value,
                payload={"file": src.name, "image": image.name},
            )
        )

        diagnostics.info("bridge", f"sent texture: {src.name}")
        self.report({"INFO"}, f"Texture sent: {src.name}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Retopo operators ─────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

_RETOPO_STATE_ITEMS = [
    (s.value, s.value.replace("_", " ").title(), "")
    for s in RetopoState
]


# ---------------------------------------------------------------------------
# ── Workflow operators ───────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def _workflow_service(context: bpy.types.Context):
    """Return a loaded WorkflowService for the configured library path, or None.

    Args:
        context: The current Blender context.

    Returns:
        A loaded ``WorkflowService`` or ``None`` if no library path is set.
    """
    prefs = get_prefs(context)
    if not prefs.library_path:
        return None
    from ..workflow.service import WorkflowService
    svc = WorkflowService(Path(prefs.library_path))
    svc.load()
    return svc


class BLINQ_OT_workflow_create(bpy.types.Operator):
    """Create a new workflow stack with the default sculpt pipeline."""

    bl_idname = "blinq.workflow_create"
    bl_label = "New Workflow Stack"
    bl_description = "Create a new workflow stack and set it active for this scene"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(  # type: ignore[assignment]
        name="Stack Name",
        description="Display name for the new workflow stack",
        default="",
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable when an XMD library path is configured."""
        return bool(get_prefs(context).library_path)

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Show the new-stack dialog with the default step list previewed."""
        self.name = ""
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the dialog body — name input plus a non-editable preview of default steps."""
        from ..workflow.service import WorkflowService
        layout = self.layout
        layout.prop(self, "name", text="Name")
        col = layout.column(align=True)
        col.label(text="Default steps:", icon="SEQUENCE")
        for step in WorkflowService.DEFAULT_STEPS:
            row = col.row()
            row.enabled = False
            row.label(text=f"  {step}")

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Create the stack via WorkflowService and set it active for the scene."""
        name = self.name.strip()
        if not name:
            self.report({"WARNING"}, "Stack name cannot be empty")
            return {"CANCELLED"}

        svc = _workflow_service(context)
        if svc is None:
            self.report({"WARNING"}, "Set the XMD Library Path in Add-on Preferences first")
            return {"CANCELLED"}

        stack = svc.create(name=name)
        context.scene.xmd_active_workflow_id = stack.id

        diagnostics.info(
            "workflow",
            f"created stack '{stack.name}' ({len(stack.steps)} steps), set active",
        )
        self.report({"INFO"}, f"Created '{stack.name}'")
        return {"FINISHED"}


class BLINQ_OT_workflow_select(bpy.types.Operator):
    """Set the active workflow stack for the current scene."""

    bl_idname = "blinq.workflow_select"
    bl_label = "Select Workflow Stack"
    bl_description = "Make this workflow stack the active one for the current scene"
    bl_options = {"REGISTER", "UNDO"}

    stack_id: StringProperty(name="Stack ID", default="")  # type: ignore[assignment]

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Persist the selected stack ID on Scene.xmd_active_workflow_id."""
        if not self.stack_id:
            return {"CANCELLED"}
        context.scene.xmd_active_workflow_id = self.stack_id
        return {"FINISHED"}


class BLINQ_OT_workflow_clear_active(bpy.types.Operator):
    """Clear the active workflow stack for the current scene."""

    bl_idname = "blinq.workflow_clear_active"
    bl_label = "Clear Active Workflow"
    bl_description = "Detach the active workflow stack from this scene"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Empty the scene's xmd_active_workflow_id property."""
        context.scene.xmd_active_workflow_id = ""
        return {"FINISHED"}


class BLINQ_OT_workflow_advance(bpy.types.Operator):
    """Advance the active workflow stack by one step."""

    bl_idname = "blinq.workflow_advance"
    bl_label = "Advance Step"
    bl_description = "Move the active workflow stack to the next step"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable only when an active stack is set on the scene."""
        return bool(getattr(context.scene, "xmd_active_workflow_id", ""))

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Advance the active stack via WorkflowService and persist."""
        svc = _workflow_service(context)
        if svc is None:
            return {"CANCELLED"}
        stack_id = context.scene.xmd_active_workflow_id
        before = svc.get(stack_id)
        if before is None:
            self.report({"WARNING"}, "Active stack no longer exists")
            return {"CANCELLED"}
        if before.current_step >= len(before.steps) - 1:
            self.report({"INFO"}, "Already at the final step")
            return {"CANCELLED"}
        after = svc.advance(stack_id)
        if after is not None:
            label = after.steps[after.current_step]
            diagnostics.info("workflow", f"'{after.name}' advanced → step {after.current_step}: {label}")
            blend_file, scene_name = _usage_blend(context)
            usage.log(
                get_prefs(context).library_path,
                event="workflow.advance",
                blend_file=blend_file,
                scene_name=scene_name,
                payload={"stack_name": after.name, "step_index": after.current_step, "step_name": label},
            )
            self.report({"INFO"}, f"Step → {label}")
        return {"FINISHED"}


class BLINQ_OT_workflow_set_step(bpy.types.Operator):
    """Jump the active workflow stack to a specific step."""

    bl_idname = "blinq.workflow_set_step"
    bl_label = "Set Step"
    bl_description = "Jump the active workflow stack to a specific step"
    bl_options = {"REGISTER", "UNDO"}

    step_index: bpy.props.IntProperty(name="Step", default=0, min=0)  # type: ignore[assignment]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(getattr(context.scene, "xmd_active_workflow_id", ""))

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Set ``current_step`` directly on the active stack."""
        svc = _workflow_service(context)
        if svc is None:
            return {"CANCELLED"}
        stack = svc.get(context.scene.xmd_active_workflow_id)
        if stack is None:
            return {"CANCELLED"}
        idx = max(0, min(self.step_index, len(stack.steps) - 1))
        if stack.current_step == idx:
            return {"CANCELLED"}
        stack.current_step = idx
        svc.save()
        diagnostics.info("workflow", f"'{stack.name}' jumped → step {idx}: {stack.steps[idx]}")
        return {"FINISHED"}


class BLINQ_OT_workflow_delete(bpy.types.Operator):
    """Delete the active workflow stack permanently."""

    bl_idname = "blinq.workflow_delete"
    bl_label = "Delete Workflow Stack"
    bl_description = "Permanently delete the active workflow stack"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(getattr(context.scene, "xmd_active_workflow_id", ""))

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Confirm before deletion."""
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Remove the stack from the service and clear the scene reference."""
        svc = _workflow_service(context)
        if svc is None:
            return {"CANCELLED"}
        stack_id = context.scene.xmd_active_workflow_id
        stack = svc.get(stack_id)
        if stack is None:
            return {"CANCELLED"}
        # The service has no remove method yet — manage internally.
        svc._stacks.pop(stack_id, None)  # type: ignore[attr-defined]
        svc.save()
        context.scene.xmd_active_workflow_id = ""
        diagnostics.info("workflow", f"deleted stack '{stack.name}'")
        self.report({"INFO"}, f"Deleted '{stack.name}'")
        return {"FINISHED"}


class BLINQ_OT_workflow_export(bpy.types.Operator):
    """Export the active workflow stack to a JSON file."""

    bl_idname = "blinq.workflow_export"
    bl_label = "Export Stack…"
    bl_description = "Save the active workflow stack as a portable JSON file"
    bl_options = {"REGISTER"}

    filepath: StringProperty(subtype="FILE_PATH", default="workflow.json")  # type: ignore[assignment]
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})  # type: ignore[assignment]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(getattr(context.scene, "xmd_active_workflow_id", ""))

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        svc = _workflow_service(context)
        stack = svc.get(context.scene.xmd_active_workflow_id) if svc else None
        if stack is None:
            return {"CANCELLED"}
        # Sanitise the suggested filename
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stack.name) or "workflow"
        self.filepath = f"{safe}.xmdwork.json"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        import json
        svc = _workflow_service(context)
        if svc is None:
            return {"CANCELLED"}
        stack = svc.get(context.scene.xmd_active_workflow_id)
        if stack is None:
            return {"CANCELLED"}
        try:
            Path(self.filepath).write_text(
                json.dumps({"schema_version": "1", "stack": stack.to_dict()},
                           indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            self.report({"ERROR"}, f"Export failed: {exc}")
            return {"CANCELLED"}
        diagnostics.info("workflow", f"exported '{stack.name}' → {Path(self.filepath).name}")
        self.report({"INFO"}, f"Exported to {Path(self.filepath).name}")
        return {"FINISHED"}


class BLINQ_OT_workflow_import(bpy.types.Operator):
    """Import a workflow stack from a JSON file."""

    bl_idname = "blinq.workflow_import"
    bl_label = "Import Stack…"
    bl_description = "Import a workflow stack from a JSON file"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", default="")  # type: ignore[assignment]
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})  # type: ignore[assignment]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(get_prefs(context).library_path)

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        import json
        import uuid
        from ..models import WorkflowStack

        svc = _workflow_service(context)
        if svc is None:
            return {"CANCELLED"}
        try:
            data = json.loads(Path(self.filepath).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, f"Import failed: {exc}")
            return {"CANCELLED"}
        stack_data = data.get("stack")
        if not isinstance(stack_data, dict):
            self.report({"ERROR"}, "File does not contain a workflow stack")
            return {"CANCELLED"}
        stack = WorkflowStack.from_dict(stack_data)
        # Re-issue ID so an imported stack never collides with an existing one
        stack.id = str(uuid.uuid4())
        svc._stacks[stack.id] = stack  # type: ignore[attr-defined]
        svc.save()
        context.scene.xmd_active_workflow_id = stack.id
        diagnostics.info("workflow", f"imported stack '{stack.name}' ({len(stack.steps)} steps)")
        self.report({"INFO"}, f"Imported '{stack.name}'")
        return {"FINISHED"}


class BLINQ_MT_workflow_stacks(bpy.types.Menu):
    """Dropdown menu listing all workflow stacks for selection."""

    bl_idname = "BLINQ_MT_workflow_stacks"
    bl_label = "Workflow Stacks"

    def draw(self, context: bpy.types.Context) -> None:
        """Populate the menu from the WorkflowService at draw time."""
        layout = self.layout
        svc = _workflow_service(context)
        if svc is None:
            layout.label(text="Set library path first", icon="ERROR")
            return
        stacks = svc.all()
        if not stacks:
            layout.label(text="No stacks defined", icon="INFO")
        else:
            current = getattr(context.scene, "xmd_active_workflow_id", "")
            for s in stacks:
                op = layout.operator(
                    "blinq.workflow_select",
                    text=s.name,
                    icon="DOT" if s.id == current else "BLANK1",
                )
                op.stack_id = s.id
            layout.separator()
            layout.operator("blinq.workflow_clear_active", icon="X")
        layout.separator()
        layout.operator("blinq.workflow_create", icon="ADD")
        layout.operator("blinq.workflow_import", icon="IMPORT")
        if getattr(context.scene, "xmd_active_workflow_id", ""):
            layout.operator("blinq.workflow_export", icon="EXPORT")


class BLINQ_OT_set_retopo_state(bpy.types.Operator):
    """Set the retopology state for the active XMD-registered object."""

    bl_idname = "blinq.set_retopo_state"
    bl_label = "Set Retopo State"
    bl_description = "Update the retopo workflow state for the active object"
    bl_options = {"REGISTER", "UNDO"}

    state: EnumProperty(  # type: ignore[assignment]
        name="State",
        items=_RETOPO_STATE_ITEMS,
        default=RetopoState.NONE.value,
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable when the active object has an XMD UUID.

        Args:
            context: The current Blender context.

        Returns:
            True if the operator can run.
        """
        obj = context.active_object
        return obj is not None and bool(obj.get("xmd_uuid", ""))

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Apply the new retopo state to the object and optionally persist to library.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        obj = context.active_object
        if not obj:
            return {"CANCELLED"}

        obj["xmd_retopo_state"] = self.state

        # Persist to the library tracker if a library path is set
        prefs = get_prefs(context)
        if prefs.library_path:
            try:
                from ..workflow.service import RetopoTracker
                tracker = RetopoTracker(Path(prefs.library_path))
                tracker.load()
                xmd_uuid = str(obj.get("xmd_uuid", ""))
                tracker.set_state(xmd_uuid, self.state, asset_name=obj.name)
            except Exception as exc:
                diagnostics.error("workflow", f"retopo tracker save failed: {exc}")

        self.report({"INFO"}, f"Retopo state \u2192 {self.state}")
        return {"FINISHED"}


class BLINQ_OT_set_retopo_objects(bpy.types.Operator):
    """Assign active object as source and selected object as retopo mesh."""

    bl_idname = "blinq.set_retopo_objects"
    bl_label = "Assign Source / Retopo Objects"
    bl_description = (
        "Use the active object as the high-res source and the other "
        "selected object as the retopo mesh"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable when exactly two objects are selected.

        Args:
            context: The current Blender context.

        Returns:
            True when exactly 2 mesh objects are selected.
        """
        selected = [o for o in context.selected_objects if o.type == "MESH"]
        return len(selected) == 2

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Set xmd_retopo_source and xmd_retopo_mesh on the active object.

        The active object becomes the source (high-res); the other selected
        object becomes the retopo mesh.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        active = context.active_object
        if not active:
            return {"CANCELLED"}
        other = next(
            (o for o in context.selected_objects if o != active and o.type == "MESH"),
            None,
        )
        if other is None:
            return {"CANCELLED"}

        active["xmd_retopo_source"] = active.name
        active["xmd_retopo_mesh"] = other.name

        self.report(
            {"INFO"},
            f"Source: '{active.name}'  \u2192  Retopo: '{other.name}'",
        )
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Light Rig operators ──────────────────────────────────────────────────
# ---------------------------------------------------------------------------

def _light_rig_service(context: bpy.types.Context):
    """Return a loaded LightRigService, or None."""
    prefs = get_prefs(context)
    if not prefs.library_path:
        return None
    from ..integrations.render import LightRigService
    svc = LightRigService(Path(prefs.library_path))
    svc.load()
    return svc


class BLINQ_OT_light_rig_save(bpy.types.Operator):
    """Save every LIGHT object in the current scene as a named light rig."""

    bl_idname = "blinq.light_rig_save"
    bl_label = "Save Light Rig"
    bl_description = "Capture all LIGHT objects in the current scene as a named light rig"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Rig Name", default="")  # type: ignore[assignment]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        if not get_prefs(context).library_path:
            return False
        return any(o.type == "LIGHT" for o in context.scene.objects)

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        from datetime import datetime as _dt
        self.name = f"Rig {_dt.now().strftime('%Y%m%d-%H%M')}"
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.prop(self, "name", text="Name")
        light_count = sum(1 for o in context.scene.objects if o.type == "LIGHT")
        col = layout.column(align=True)
        col.label(text=f"Will capture {light_count} light(s):", icon="LIGHT")
        for obj in context.scene.objects:
            if obj.type != "LIGHT":
                continue
            row = col.row()
            row.enabled = False
            row.label(text=f"  {obj.name} ({obj.data.type})")

    def execute(self, context: bpy.types.Context) -> set[str]:
        name = self.name.strip()
        if not name:
            self.report({"WARNING"}, "Rig name cannot be empty")
            return {"CANCELLED"}
        svc = _light_rig_service(context)
        if svc is None:
            self.report({"WARNING"}, "Set the XMD Library Path in Add-on Preferences first")
            return {"CANCELLED"}
        rig = svc.capture_from_scene(name=name, scene=context.scene)
        diagnostics.info("render", f"saved light rig '{rig.name}' ({len(rig.lights)} light(s))")
        self.report({"INFO"}, f"Saved '{rig.name}' ({len(rig.lights)} light(s))")
        return {"FINISHED"}


class BLINQ_OT_light_rig_apply(bpy.types.Operator):
    """Add a saved light rig's lights to the current scene."""

    bl_idname = "blinq.light_rig_apply"
    bl_label = "Apply Light Rig"
    bl_description = "Add all lights from a saved rig into the current scene"
    bl_options = {"REGISTER", "UNDO"}

    rig_id: StringProperty(name="Rig ID", default="")  # type: ignore[assignment]

    def execute(self, context: bpy.types.Context) -> set[str]:
        svc = _light_rig_service(context)
        if svc is None:
            return {"CANCELLED"}
        rig = svc.get(self.rig_id)
        if rig is None:
            return {"CANCELLED"}
        created = svc.apply_to_scene(self.rig_id, context.scene, bpy)
        diagnostics.info("render", f"applied rig '{rig.name}': {created} light(s) added")
        blend_file, scene_name = _usage_blend(context)
        usage.log(
            get_prefs(context).library_path,
            event="render.light_rig_apply",
            blend_file=blend_file,
            scene_name=scene_name,
            payload={"rig_name": rig.name, "lights_created": created},
        )
        self.report({"INFO"}, f"Added {created} light(s)")
        return {"FINISHED"}


class BLINQ_OT_light_rig_delete(bpy.types.Operator):
    """Delete a saved light rig from the library."""

    bl_idname = "blinq.light_rig_delete"
    bl_label = "Delete Light Rig"
    bl_description = "Permanently delete this light rig from the library"
    bl_options = {"REGISTER", "UNDO"}

    rig_id: StringProperty(name="Rig ID", default="")  # type: ignore[assignment]

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        svc = _light_rig_service(context)
        if svc is None:
            return {"CANCELLED"}
        if svc.remove(self.rig_id):
            diagnostics.info("render", f"deleted light rig {self.rig_id[:8]}")
            return {"FINISHED"}
        return {"CANCELLED"}


# ---------------------------------------------------------------------------
# ── Library audit + batch QC ─────────────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_OT_audit_blend_dependencies(bpy.types.Operator):
    """List which XMD library assets are used in the current .blend file."""

    bl_idname = "blinq.audit_blend_dependencies"
    bl_label = "Audit This Blend"
    bl_description = (
        "Scan the current .blend for datablocks tagged with xmd_uuid and "
        "show which ones match XMD Library records"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(get_prefs(context).library_path)

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = get_prefs(context)
        index = XMDIndex(Path(prefs.library_path))
        index.load()

        # Walk all datablock collections that BlinQ can register
        matched: list[tuple[str, str, str]] = []   # (name, type, uuid8)
        unmatched: list[tuple[str, str, str]] = [] # same shape — uuid set but no library record
        for coll_name in _TYPE_TO_COLLECTION.values():
            coll = getattr(bpy.data, coll_name, None)
            if coll is None:
                continue
            for db in coll:
                uuid_val = str(db.get("xmd_uuid", ""))
                if not uuid_val:
                    continue
                type_label = type(db).__name__
                row = (db.name, type_label, uuid_val[:8])
                if index.get(uuid_val):
                    matched.append(row)
                else:
                    unmatched.append(row)

        diagnostics.info(
            "audit",
            f"blend audit: matched={len(matched)} unmatched={len(unmatched)} "
            f"library_total={len(index)}",
        )

        def draw_popup(self_popup, _ctx):
            layout = self_popup.layout
            layout.label(
                text=f"This .blend uses {len(matched) + len(unmatched)} XMD-tagged datablock(s)",
                icon="ASSET_MANAGER",
            )
            if matched:
                layout.separator()
                layout.label(text=f"Matched in library ({len(matched)}):", icon="CHECKMARK")
                for name, type_label, u8 in matched[:8]:
                    row = layout.row()
                    row.enabled = False
                    row.label(text=f"  {name}  ({type_label} {u8}…)")
                if len(matched) > 8:
                    layout.label(text=f"  … and {len(matched) - 8} more")
            if unmatched:
                layout.separator()
                layout.label(text=f"Tagged but not in library ({len(unmatched)}):", icon="ERROR")
                for name, type_label, u8 in unmatched[:8]:
                    row = layout.row()
                    row.alert = True
                    row.label(text=f"  {name}  ({type_label} {u8}…)")
                if len(unmatched) > 8:
                    layout.label(text=f"  … and {len(unmatched) - 8} more")
            if not matched and not unmatched:
                layout.label(text="No XMD-tagged datablocks found", icon="INFO")

        context.window_manager.popup_menu(
            draw_popup, title="Blend Dependency Audit", icon="ASSET_MANAGER"
        )
        self.report(
            {"INFO"},
            f"Blend audit: {len(matched)} matched, {len(unmatched)} unmatched",
        )
        return {"FINISHED"}


class BLINQ_OT_audit_library(bpy.types.Operator):
    """Audit the XMD library index for orphans, missing UUIDs, and missing files."""

    bl_idname = "blinq.audit_library"
    bl_label = "Audit Library"
    bl_description = (
        "Scan the XMD Library index for missing files, missing UUIDs, "
        "and broken catalog references. Results in the Diagnostics panel"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(get_prefs(context).library_path)

    def execute(self, context: bpy.types.Context) -> set[str]:
        prefs = get_prefs(context)
        lib_path = Path(prefs.library_path)
        index = XMDIndex(lib_path)
        index.load()
        catalog = CatalogManager(lib_path)
        catalog.load()

        records = index.all()
        if not records:
            self.report({"INFO"}, "Library is empty")
            return {"FINISHED"}

        missing_uuid = sum(1 for r in records if not r.xmd_uuid)
        missing_catalog = sum(1 for r in records if not r.catalog_id)
        unknown_catalog = sum(
            1 for r in records
            if r.catalog_id and catalog.path_for_id(r.catalog_id) is None
        )

        # Check blend_file existence (relative to library)
        missing_files = 0
        for r in records:
            if not r.blend_file:
                continue
            candidate = Path(r.blend_file)
            if not candidate.is_absolute():
                candidate = lib_path / r.blend_file
            if not candidate.exists():
                missing_files += 1

        # Detect duplicate UUIDs (defensive — XMDIndex keys by UUID so dup is unlikely)
        uuids = [r.xmd_uuid for r in records if r.xmd_uuid]
        duplicate_uuids = len(uuids) - len(set(uuids))

        diagnostics.info(
            "audit",
            f"library audit: {len(records)} records, "
            f"missing_uuid={missing_uuid}, missing_catalog={missing_catalog}, "
            f"unknown_catalog={unknown_catalog}, missing_files={missing_files}, "
            f"duplicate_uuids={duplicate_uuids}",
        )

        problems = (
            missing_uuid + missing_catalog + unknown_catalog
            + missing_files + duplicate_uuids
        )

        def draw_popup(self_popup, _ctx):
            layout = self_popup.layout
            layout.label(text=f"{len(records)} records audited", icon="ASSET_MANAGER")
            layout.separator()
            for label, count, icon in (
                ("Missing UUID",          missing_uuid,      "QUESTION"),
                ("Missing catalog",       missing_catalog,   "OUTLINER_OB_GROUP_INSTANCE"),
                ("Unknown catalog ID",    unknown_catalog,   "ERROR"),
                ("Missing blend file",    missing_files,     "FILE"),
                ("Duplicate UUID",        duplicate_uuids,   "DUPLICATE"),
            ):
                row = layout.row()
                if count:
                    row.alert = True
                row.label(text=f"  {label}: {count}", icon=icon)
            layout.separator()
            if problems == 0:
                layout.label(text="No issues found", icon="CHECKMARK")
            else:
                layout.label(text=f"{problems} issue(s) — see Diagnostics", icon="INFO")

        context.window_manager.popup_menu(
            draw_popup, title="Library Audit", icon="ASSET_MANAGER"
        )
        self.report(
            {"INFO" if problems == 0 else "WARNING"},
            f"Audit: {problems} issue(s) across {len(records)} records",
        )
        return {"FINISHED"}


class BLINQ_OT_refresh_all_previews(bpy.types.Operator):
    """Schedule preview regeneration for every loaded XMD-registered asset."""

    bl_idname = "blinq.refresh_all_previews"
    bl_label = "Refresh All Previews"
    bl_description = (
        "Schedule preview regeneration for every XMD-registered datablock "
        "currently loaded in this .blend file"
    )
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        scheduled = 0

        # Walk the relevant bpy.data collections; if a datablock has an xmd_uuid
        # custom property AND asset_data, schedule a preview.
        for coll_name in _TYPE_TO_COLLECTION.values():
            coll = getattr(bpy.data, coll_name, None)
            if coll is None:
                continue
            for db in coll:
                if not db.get("xmd_uuid"):
                    continue
                if not getattr(db, "asset_data", None):
                    continue
                PreviewManager().request_preview(db.name, coll_name)
                scheduled += 1

        diagnostics.info("preview", f"scheduled {scheduled} preview regeneration(s)")
        self.report({"INFO"}, f"Scheduled previews for {scheduled} asset(s)")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── HDRI loader ──────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_OT_load_hdri(bpy.types.Operator):
    """Load an HDRI image and set it as the world environment."""

    bl_idname = "blinq.load_hdri"
    bl_label = "Load HDRI"
    bl_description = (
        "Load an HDR/EXR/image file and wire it into the World shader as "
        "an environment background"
    )
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", default="")  # type: ignore[assignment]
    filter_glob: StringProperty(  # type: ignore[assignment]
        default="*.hdr;*.exr;*.png;*.jpg;*.jpeg;*.tif;*.tiff",
        options={"HIDDEN"},
    )

    strength: bpy.props.FloatProperty(  # type: ignore[assignment]
        name="Strength",
        description="World background strength multiplier",
        default=1.0,
        min=0.0,
        soft_max=10.0,
    )

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        if not self.filepath:
            return {"CANCELLED"}

        path = Path(self.filepath)
        if not path.is_file():
            self.report({"ERROR"}, f"File not found: {path}")
            return {"CANCELLED"}

        try:
            image = bpy.data.images.load(str(path), check_existing=True)
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Could not load image: {exc}")
            return {"CANCELLED"}
        # HDRIs and EXRs should not be sRGB-interpreted
        if path.suffix.lower() in {".hdr", ".exr"}:
            image.colorspace_settings.name = "Non-Color"

        scene = context.scene
        world = scene.world or bpy.data.worlds.new("XMD World")
        scene.world = world
        world.use_nodes = True
        tree = world.node_tree
        tree.nodes.clear()

        # Build: Texture Coord → Mapping → Environment → Background → Output
        tex_coord = tree.nodes.new("ShaderNodeTexCoord")
        mapping = tree.nodes.new("ShaderNodeMapping")
        env = tree.nodes.new("ShaderNodeTexEnvironment")
        bg = tree.nodes.new("ShaderNodeBackground")
        out = tree.nodes.new("ShaderNodeOutputWorld")

        env.image = image
        bg.inputs["Strength"].default_value = float(self.strength)

        tex_coord.location = (-700, 0)
        mapping.location  = (-500, 0)
        env.location      = (-250, 0)
        bg.location       = (   0, 0)
        out.location      = ( 200, 0)

        tree.links.new(tex_coord.outputs["Generated"], mapping.inputs["Vector"])
        tree.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
        tree.links.new(env.outputs["Color"], bg.inputs["Color"])
        tree.links.new(bg.outputs["Background"], out.inputs["Surface"])

        diagnostics.info("render", f"HDRI loaded: {path.name} (strength={self.strength})")
        self.report({"INFO"}, f"HDRI: {path.name}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Render Preset operators ──────────────────────────────────────────────
# ---------------------------------------------------------------------------

def _render_preset_service(context: bpy.types.Context):
    """Return a loaded RenderPresetService, or None."""
    prefs = get_prefs(context)
    if not prefs.library_path:
        return None
    from ..integrations.render import RenderPresetService
    svc = RenderPresetService(Path(prefs.library_path))
    svc.load()
    return svc


class BLINQ_OT_render_preset_save(bpy.types.Operator):
    """Save the current scene's render settings as a named preset."""

    bl_idname = "blinq.render_preset_save"
    bl_label = "Save Render Preset"
    bl_description = "Capture the current scene's render settings as a new preset"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Preset Name", default="")  # type: ignore[assignment]

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(get_prefs(context).library_path)

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        scene = context.scene
        engine = scene.render.engine
        # Suggest a meaningful default name from engine + resolution
        self.name = f"{engine.replace('BLENDER_', '').title()} {scene.render.resolution_x}x{scene.render.resolution_y}"
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        scene = context.scene
        layout.prop(self, "name", text="Name")
        col = layout.column(align=True)
        col.label(text="Will capture:", icon="INFO")
        for row_text in (
            f"  Engine:     {scene.render.engine}",
            f"  Resolution: {scene.render.resolution_x} × {scene.render.resolution_y}"
            f" @ {scene.render.resolution_percentage}%",
            f"  Format:     {scene.render.image_settings.file_format}",
            f"  Output:     {scene.render.filepath or '(blank)'}",
            f"  View:       {scene.view_settings.view_transform} / {scene.view_settings.look}",
        ):
            row = col.row()
            row.enabled = False
            row.label(text=row_text)

    def execute(self, context: bpy.types.Context) -> set[str]:
        name = self.name.strip()
        if not name:
            self.report({"WARNING"}, "Preset name cannot be empty")
            return {"CANCELLED"}
        svc = _render_preset_service(context)
        if svc is None:
            self.report({"WARNING"}, "Set the XMD Library Path in Add-on Preferences first")
            return {"CANCELLED"}
        preset = svc.add_from_scene(name=name, scene=context.scene)
        diagnostics.info("render", f"saved preset '{preset.name}' (engine={preset.engine})")
        self.report({"INFO"}, f"Saved render preset '{preset.name}'")
        return {"FINISHED"}


class BLINQ_OT_render_preset_apply(bpy.types.Operator):
    """Apply a saved render preset to the current scene."""

    bl_idname = "blinq.render_preset_apply"
    bl_label = "Apply Render Preset"
    bl_description = "Apply a saved render preset's settings to the current scene"
    bl_options = {"REGISTER", "UNDO"}

    preset_id: StringProperty(name="Preset ID", default="")  # type: ignore[assignment]

    def execute(self, context: bpy.types.Context) -> set[str]:
        svc = _render_preset_service(context)
        if svc is None:
            return {"CANCELLED"}
        preset = svc.get(self.preset_id)
        if preset is None:
            self.report({"WARNING"}, "Preset not found")
            return {"CANCELLED"}
        if not svc.apply(self.preset_id, context.scene):
            return {"CANCELLED"}
        diagnostics.info("render", f"applied preset '{preset.name}'")
        blend_file, scene_name = _usage_blend(context)
        usage.log(
            get_prefs(context).library_path,
            event="render.preset_apply",
            blend_file=blend_file,
            scene_name=scene_name,
            payload={"preset_name": preset.name, "engine": preset.engine},
        )
        self.report({"INFO"}, f"Applied '{preset.name}'")
        return {"FINISHED"}


class BLINQ_OT_render_preset_delete(bpy.types.Operator):
    """Delete a saved render preset."""

    bl_idname = "blinq.render_preset_delete"
    bl_label = "Delete Render Preset"
    bl_description = "Permanently delete this render preset"
    bl_options = {"REGISTER", "UNDO"}

    preset_id: StringProperty(name="Preset ID", default="")  # type: ignore[assignment]

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        svc = _render_preset_service(context)
        if svc is None:
            return {"CANCELLED"}
        if svc.remove(self.preset_id):
            diagnostics.info("render", f"deleted preset {self.preset_id[:8]}")
            return {"FINISHED"}
        return {"CANCELLED"}


# ---------------------------------------------------------------------------
# ── Random Kit + Challenge generators ────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_OT_random_kit(bpy.types.Operator):
    """Pick a random selection of XMD-registered assets for inspiration."""

    bl_idname = "blinq.random_kit"
    bl_label = "Random Kit"
    bl_description = (
        "Sample N random assets from the XMD Library and list them — "
        "useful when starting a session and feeling stuck"
    )
    bl_options = {"REGISTER"}

    count: bpy.props.IntProperty(  # type: ignore[assignment]
        name="Count",
        description="How many assets to sample from the library",
        default=5,
        min=1,
        max=20,
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(get_prefs(context).library_path)

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_props_dialog(self, width=280)

    def draw(self, context: bpy.types.Context) -> None:
        self.layout.prop(self, "count", slider=True)

    def execute(self, context: bpy.types.Context) -> set[str]:
        import random
        prefs = get_prefs(context)
        index = XMDIndex(Path(prefs.library_path))
        index.load()
        records = index.all()
        if not records:
            self.report({"WARNING"}, "Library is empty — register some assets first")
            return {"CANCELLED"}

        picked = random.sample(records, k=min(self.count, len(records)))
        names = [r.name for r in picked]
        diagnostics.info("kit", f"random kit ({len(names)}): {', '.join(names)}")

        # Show as a popup so the user sees the list without opening Diagnostics
        def draw_popup(self_popup, _ctx):
            layout = self_popup.layout
            layout.label(text="Random Kit:", icon="OUTLINER_OB_GROUP_INSTANCE")
            for r in picked:
                row = layout.row()
                row.label(text=f"  {r.name}", icon="DOT")
                row.label(text=f"({r.asset_type.lower()})")
        context.window_manager.popup_menu(
            draw_popup, title="Random Kit", icon="OUTLINER_OB_GROUP_INSTANCE"
        )
        self.report({"INFO"}, f"Sampled {len(picked)} asset(s)")
        return {"FINISHED"}


_CHALLENGE_SUBJECTS: tuple[str, ...] = (
    "ancient warrior", "deep-sea creature", "elder dragon", "forest spirit",
    "broken android", "alpine herder", "swamp witch", "fungal druid",
    "cosmic prophet", "shipwreck salvager", "desert nomad", "lichen giant",
    "miniature mech pilot", "pearl-diver djinn", "post-volcanic gardener",
    "feral cherub", "scorched gunslinger", "translucent jellyfish wizard",
)
_CHALLENGE_STYLES: tuple[str, ...] = (
    "stylized cartoon", "hyper-realistic", "low-poly retro",
    "anime/manga", "Studio Ghibli pastel", "soulslike grim",
    "weighty Pixar volumes", "hand-painted texture", "1990s pre-rendered",
    "monochrome ink wash", "Saturday-morning toon",
)
_CHALLENGE_CONSTRAINTS: tuple[str, ...] = (
    "in 30 minutes", "with a single symmetrical pose", "with under 10k tris",
    "using only vertex colors", "in a single material",
    "with no textures", "with hard-surface only",
    "using only sculpt brushes", "without booleans",
    "in two values + one accent color",
)


class BLINQ_OT_random_challenge(bpy.types.Operator):
    """Generate a random sculpting/modeling challenge prompt."""

    bl_idname = "blinq.random_challenge"
    bl_label = "New Challenge"
    bl_description = "Generate a random subject + style + constraint prompt"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        import random
        subject = random.choice(_CHALLENGE_SUBJECTS)
        style = random.choice(_CHALLENGE_STYLES)
        constraint = random.choice(_CHALLENGE_CONSTRAINTS)
        prompt = f"Sculpt a {subject}, {style}, {constraint}."
        diagnostics.info("challenge", prompt)

        def draw_popup(self_popup, _ctx):
            self_popup.layout.label(text=prompt, icon="LIGHT_DATA")
        context.window_manager.popup_menu(
            draw_popup, title="Today's Challenge", icon="LIGHT_DATA"
        )
        self.report({"INFO"}, prompt)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Reference Board operators ────────────────────────────────────────────
# ---------------------------------------------------------------------------

def _reference_service(context: bpy.types.Context):
    """Return a loaded ReferenceBoardService, or None if no library path is set."""
    prefs = get_prefs(context)
    if not prefs.library_path:
        return None
    from ..review.service import ReferenceBoardService
    svc = ReferenceBoardService(Path(prefs.library_path))
    svc.load()
    return svc


def _snapshot_service(context: bpy.types.Context):
    """Return a loaded ReviewSnapshotService, or None if no library path is set."""
    prefs = get_prefs(context)
    if not prefs.library_path:
        return None
    from ..review.service import ReviewSnapshotService
    svc = ReviewSnapshotService(Path(prefs.library_path))
    svc.load()
    return svc


class BLINQ_OT_reference_add(bpy.types.Operator):
    """Add image files to the BlinQ Reference Board."""

    bl_idname = "blinq.reference_add"
    bl_label = "Add Reference Images"
    bl_description = "Add one or more image files to the Reference Board"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", default="")  # type: ignore[assignment]
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)  # type: ignore[assignment]
    directory: StringProperty(subtype="DIR_PATH", default="")  # type: ignore[assignment]
    filter_glob: StringProperty(  # type: ignore[assignment]
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.bmp;*.webp",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(get_prefs(context).library_path)

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Open the file browser."""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Register every selected file and load it as an Image datablock."""
        svc = _reference_service(context)
        if svc is None:
            self.report({"WARNING"}, "Set the XMD Library Path in Add-on Preferences first")
            return {"CANCELLED"}

        # files[] is populated when multiple are selected; otherwise use filepath
        chosen: list[str] = []
        if self.files and self.directory:
            for f in self.files:
                if f.name:
                    chosen.append(str(Path(self.directory) / f.name))
        elif self.filepath:
            chosen.append(self.filepath)

        if not chosen:
            return {"CANCELLED"}

        added = 0
        for fp in chosen:
            p = Path(fp)
            if not p.is_file():
                continue
            item = svc.add(file_path=str(p))
            try:
                image = bpy.data.images.load(str(p), check_existing=True)
                svc.update(item.id, image_name=image.name)
            except RuntimeError as exc:
                diagnostics.warn("review", f"could not load image '{p.name}': {exc}")
            added += 1
            diagnostics.info("review", f"reference added: '{item.name}'")

        if added == 0:
            self.report({"WARNING"}, "No valid image files selected")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Added {added} reference(s)")
        return {"FINISHED"}


class BLINQ_OT_reference_remove(bpy.types.Operator):
    """Remove a reference image entry from the board."""

    bl_idname = "blinq.reference_remove"
    bl_label = "Remove Reference"
    bl_description = "Remove this entry from the Reference Board (does not delete the file)"
    bl_options = {"REGISTER", "UNDO"}

    ref_id: StringProperty(name="Reference ID", default="")  # type: ignore[assignment]

    def execute(self, context: bpy.types.Context) -> set[str]:
        svc = _reference_service(context)
        if svc is None:
            return {"CANCELLED"}
        if svc.remove(self.ref_id):
            diagnostics.info("review", f"reference removed: {self.ref_id[:8]}")
            return {"FINISHED"}
        return {"CANCELLED"}


class BLINQ_OT_reference_open(bpy.types.Operator):
    """Open the reference image in Blender's Image Editor."""

    bl_idname = "blinq.reference_open"
    bl_label = "Open Reference"
    bl_description = "Open this reference in the Image Editor"
    bl_options = {"REGISTER"}

    ref_id: StringProperty(name="Reference ID", default="")  # type: ignore[assignment]

    def execute(self, context: bpy.types.Context) -> set[str]:
        svc = _reference_service(context)
        if svc is None:
            return {"CANCELLED"}
        item = svc.get(self.ref_id)
        if item is None:
            return {"CANCELLED"}

        # Make sure the image is loaded
        image = None
        if item.image_name:
            image = bpy.data.images.get(item.image_name)
        if image is None:
            try:
                image = bpy.data.images.load(item.file_path, check_existing=True)
                svc.update(item.id, image_name=image.name)
            except RuntimeError as exc:
                self.report({"ERROR"}, f"Could not load image: {exc}")
                return {"CANCELLED"}

        # Find an Image Editor area or convert the largest non-3D area
        target_area = None
        for area in context.screen.areas:
            if area.type == "IMAGE_EDITOR":
                target_area = area
                break

        if target_area is None:
            self.report(
                {"WARNING"},
                f"Loaded '{image.name}' — open an Image Editor to view it",
            )
            return {"FINISHED"}

        target_area.spaces.active.image = image
        target_area.tag_redraw()
        self.report({"INFO"}, f"Opened '{image.name}' in the Image Editor")
        return {"FINISHED"}


class BLINQ_OT_reference_clear_all(bpy.types.Operator):
    """Clear all reference entries from the board."""

    bl_idname = "blinq.reference_clear_all"
    bl_label = "Clear All References"
    bl_description = "Remove every entry from the Reference Board"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context: bpy.types.Context) -> set[str]:
        svc = _reference_service(context)
        if svc is None:
            return {"CANCELLED"}
        ids = [r.id for r in svc.all()]
        for ref_id in ids:
            svc.remove(ref_id)
        diagnostics.info("review", f"reference board cleared ({len(ids)} item(s))")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Review Snapshot operators ────────────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_OT_snapshot_capture(bpy.types.Operator):
    """Capture the active 3D Viewport to a PNG and register it as a snapshot."""

    bl_idname = "blinq.snapshot_capture"
    bl_label = "Capture Viewport"
    bl_description = (
        "Save a PNG of the active 3D Viewport into the library snapshots/ folder "
        "and register it as a Review Snapshot"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        return bool(get_prefs(context).library_path) and any(
            a.type == "VIEW_3D" for a in context.screen.areas
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        from datetime import datetime as _dt
        from ..models import ReviewSnapshot

        svc = _snapshot_service(context)
        if svc is None:
            self.report({"WARNING"}, "Set the XMD Library Path in Add-on Preferences first")
            return {"CANCELLED"}

        svc.ensure_dirs()
        ts_label = _dt.now().strftime("%Y%m%d-%H%M%S")
        snap_name = f"viewport-{ts_label}"
        out_path = svc.snapshot_dir / f"{snap_name}.png"

        # Use bpy.ops.screen.screenshot_area on the largest VIEW_3D
        target_area = None
        target_area_size = 0
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                size = area.width * area.height
                if size > target_area_size:
                    target_area_size = size
                    target_area = area
        if target_area is None:
            self.report({"WARNING"}, "No 3D Viewport to capture")
            return {"CANCELLED"}

        try:
            with context.temp_override(area=target_area):
                bpy.ops.screen.screenshot_area(filepath=str(out_path))
        except Exception as exc:
            diagnostics.error("review", f"snapshot capture failed: {exc}")
            self.report({"ERROR"}, f"Capture failed: {exc}")
            return {"CANCELLED"}

        snap = ReviewSnapshot(
            name=snap_name,
            file_path=str(out_path),
            kind="viewport",
            scene_name=context.scene.name,
            camera_name=context.scene.camera.name if context.scene.camera else "",
        )
        svc.add(snap)
        diagnostics.info("review", f"viewport snapshot captured: {out_path.name}")
        self.report({"INFO"}, f"Snapshot saved: {out_path.name}")
        return {"FINISHED"}


class BLINQ_OT_snapshot_remove(bpy.types.Operator):
    """Remove a snapshot from the index (the PNG file is left in place)."""

    bl_idname = "blinq.snapshot_remove"
    bl_label = "Remove Snapshot"
    bl_description = "Remove this snapshot from the index (does not delete the PNG)"
    bl_options = {"REGISTER", "UNDO"}

    snap_id: StringProperty(name="Snapshot ID", default="")  # type: ignore[assignment]

    def execute(self, context: bpy.types.Context) -> set[str]:
        svc = _snapshot_service(context)
        if svc is None:
            return {"CANCELLED"}
        if svc.remove(self.snap_id):
            diagnostics.info("review", f"snapshot removed from index: {self.snap_id[:8]}")
            return {"FINISHED"}
        return {"CANCELLED"}


class BLINQ_OT_snapshot_open(bpy.types.Operator):
    """Open a snapshot's PNG in the system image viewer."""

    bl_idname = "blinq.snapshot_open"
    bl_label = "Open Snapshot"
    bl_description = "Open this snapshot's PNG in the system image viewer"
    bl_options = {"REGISTER"}

    snap_id: StringProperty(name="Snapshot ID", default="")  # type: ignore[assignment]

    def execute(self, context: bpy.types.Context) -> set[str]:
        svc = _snapshot_service(context)
        if svc is None:
            return {"CANCELLED"}
        snap = svc.get(self.snap_id)
        if snap is None or not snap.file_path:
            return {"CANCELLED"}
        try:
            bpy.ops.wm.path_open(filepath=snap.file_path)
        except Exception as exc:
            self.report({"ERROR"}, f"Could not open: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Bridge self-test ─────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_OT_bridge_self_test(bpy.types.Operator):
    """Send a PING through the bridge transport and verify the round-trip."""

    bl_idname = "blinq.bridge_self_test"
    bl_label = "Bridge Self-Test"
    bl_description = (
        "Write a fake XMD Desktop heartbeat and a PING command, "
        "then verify BlinQ writes a valid ack within 4 seconds. "
        "Results appear in the Diagnostics panel"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Enable only when a bridge work directory is configured."""
        return bool(getattr(get_prefs(context), "work_dir", ""))

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Drive the IPC layer through one full PING round-trip.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set. Always FINISHED — actual pass/fail is
            reported asynchronously to the diagnostics log.
        """
        import json
        import time as _time
        import uuid

        prefs = get_prefs(context)
        from ..bridge.ipc import IPCTransport, CommandEnvelope
        from ..models import BridgeCommandType

        work_dir = Path(prefs.work_dir)
        try:
            transport = IPCTransport(work_dir)
        except OSError as exc:
            self.report({"ERROR"}, f"Cannot prepare work dir: {exc}")
            return {"CANCELLED"}

        cmd_id = str(uuid.uuid4())
        ack_path = transport.work_dir / IPCTransport.BLINQ_ACK
        # Capture the existing ack mtime so we can tell whether the bridge
        # has actually rewritten the file (vs an old ack still on disk).
        baseline_mtime = ack_path.stat().st_mtime if ack_path.exists() else 0.0

        # 1) Write a fake XMD Desktop heartbeat so the bridge counts as connected.
        xmd_alive_path = transport.work_dir / IPCTransport.XMD_ALIVE
        try:
            xmd_alive_path.write_text(
                json.dumps({
                    "app": "blinq.self-test",
                    "version": "0.0.0",
                    "ts": _time.time(),
                }),
                encoding="utf-8",
            )
        except OSError as exc:
            self.report({"ERROR"}, f"Failed to write fake heartbeat: {exc}")
            return {"CANCELLED"}

        # 2) Write the PING command for the heartbeat poll to pick up.
        transport.write_command(
            CommandEnvelope(id=cmd_id, command=BridgeCommandType.PING.value)
        )
        diagnostics.info("bridge", f"self-test: wrote PING id={cmd_id[:8]}")

        # 3) Schedule a check after a few poll cycles. The default poll interval
        #    is 2 s; 4 s gives us roughly two ticks of margin.
        def _verify_ack() -> None:
            try:
                if not ack_path.exists():
                    diagnostics.error("bridge", "self-test FAIL: no ack file written")
                    return
                if ack_path.stat().st_mtime <= baseline_mtime:
                    diagnostics.error(
                        "bridge",
                        "self-test FAIL: ack file was not rewritten by the bridge",
                    )
                    return
                data = json.loads(ack_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                diagnostics.error("bridge", f"self-test FAIL: bad ack: {exc}")
                return

            if data.get("id") != cmd_id:
                diagnostics.error(
                    "bridge",
                    f"self-test FAIL: ack id mismatch "
                    f"(got {str(data.get('id'))[:8]}, expected {cmd_id[:8]})",
                )
                return
            if data.get("status") != "ok":
                diagnostics.error(
                    "bridge",
                    f"self-test FAIL: status={data.get('status')!r} error={data.get('error')!r}",
                )
                return
            if not data.get("result", {}).get("pong"):
                diagnostics.error("bridge", "self-test FAIL: missing pong in result")
                return

            diagnostics.info("bridge", f"self-test PASS: PING/ACK round-trip OK ({cmd_id[:8]})")

        bpy.app.timers.register(_verify_ack, first_interval=4.0)
        self.report(
            {"INFO"},
            "Bridge self-test started — result will appear in the Diagnostics panel within 4 s",
        )
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Diagnostics operators ────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_OT_clear_log(bpy.types.Operator):
    """Clear the BlinQ diagnostics log buffer."""

    bl_idname = "blinq.clear_log"
    bl_label = "Clear Log"
    bl_description = "Empty the BlinQ diagnostics log buffer"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Empty the log ring buffer.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        diagnostics.clear()
        # Tag area redraws so the panel updates immediately
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
        return {"FINISHED"}


class BLINQ_OT_copy_log(bpy.types.Operator):
    """Copy the BlinQ diagnostics log to the clipboard for sharing."""

    bl_idname = "blinq.copy_log"
    bl_label = "Copy Log"
    bl_description = "Copy the entire BlinQ diagnostics log to the clipboard"
    bl_options = {"REGISTER"}

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Write the formatted log to ``window_manager.clipboard``.

        Args:
            context: The current Blender context.

        Returns:
            Blender operator result set.
        """
        text = diagnostics.to_text()
        if not text:
            self.report({"INFO"}, "Log is empty")
            return {"CANCELLED"}
        context.window_manager.clipboard = text
        line_count = text.count("\n") + 1
        self.report({"INFO"}, f"Copied {line_count} log line(s) to clipboard")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Pie menu ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_MT_pie_menu(bpy.types.Menu):
    """BlinQ quick-action pie menu. Default hotkey: Shift+X in the 3D View."""

    bl_idname = "BLINQ_MT_pie_menu"
    bl_label = "BlinQ"

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the 8-slot pie menu.

        Slot order in Blender's menu_pie: W, E, S, N, NW, NE, SW, SE.

        Args:
            context: The current Blender context.
        """
        pie = self.layout.menu_pie()
        # W — Send Mesh → ZBrush
        pie.operator("blinq.send_mesh", icon="EXPORT")
        # E — Receive Mesh ← ZBrush
        pie.operator("blinq.receive_mesh", icon="IMPORT")
        # S — Capture Snapshot
        pie.operator("blinq.snapshot_capture", icon="CAMERA_DATA")
        # N — New Challenge
        pie.operator("blinq.random_challenge", icon="LIGHT_DATA")
        # NW — Send Texture → ZBrush
        pie.operator("blinq.send_texture", icon="IMAGE_DATA")
        # NE — Random Kit
        pie.operator("blinq.random_kit", icon="OUTLINER_OB_GROUP_INSTANCE")
        # SW — Register in XMD
        pie.operator("blinq.register_asset", icon="ADD")
        # SE — Open Library
        pie.operator("blinq.open_library", icon="FOLDER_REDIRECT")


class BLINQ_OT_call_pie(bpy.types.Operator):
    """Internal operator that opens the BlinQ pie menu. Bound to Shift+X."""

    bl_idname = "blinq.call_pie"
    bl_label = "Open BlinQ Pie Menu"
    bl_options = {"REGISTER"}

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Open the pie menu at the cursor.

        Args:
            context: The current Blender context.
            event: The triggering input event.

        Returns:
            Blender operator result set.
        """
        bpy.ops.wm.call_menu_pie(name="BLINQ_MT_pie_menu")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Asset Browser context menu contribution ───────────────────────────────
# ---------------------------------------------------------------------------

def _asset_browser_menu(self: bpy.types.Menu, context: bpy.types.Context) -> None:
    """Draw BlinQ actions in the Asset Browser right-click context menu.

    Args:
        self: The menu being drawn.
        context: The current Blender context.
    """
    layout = self.layout
    layout.separator()
    layout.label(text="BlinQ", icon="ASSET_MANAGER")
    layout.operator("blinq.register_asset", icon="ADD")
    layout.operator("blinq.push_metadata", icon="EXPORT")
    layout.operator("blinq.pull_metadata", icon="IMPORT")
    layout.operator("blinq.sync_preview", icon="FILE_REFRESH")
    layout.separator()
    layout.operator("blinq.refresh_all_previews", icon="RENDER_RESULT")
    layout.operator("blinq.audit_library", icon="VIEWZOOM")


# ---------------------------------------------------------------------------
# Keymap
# ---------------------------------------------------------------------------

_keymaps: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []


def _add_keymap() -> None:
    """Register the Shift+X hotkey for the BlinQ pie menu in the 3D View."""
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return
    km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
    kmi = km.keymap_items.new(
        "blinq.call_pie",
        type="X",
        value="PRESS",
        shift=True,
    )
    _keymaps.append((km, kmi))


def _remove_keymap() -> None:
    """Unregister all BlinQ keymaps."""
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_CLASSES = [
    BLINQ_OT_register_asset,
    BLINQ_OT_import_asset,
    BLINQ_OT_import_assets_folder,
    BLINQ_OT_export_asset_file,
    BLINQ_OT_list_library_assets,
    BLINQ_OT_push_metadata,
    BLINQ_OT_pull_metadata,
    BLINQ_OT_sync_preview,
    BLINQ_OT_add_tag,
    BLINQ_OT_remove_tag,
    BLINQ_OT_open_library,
    BLINQ_OT_refresh_library,
    BLINQ_OT_check_activation,
    BLINQ_OT_check_health,
    # Bridge
    BLINQ_OT_send_mesh,
    BLINQ_OT_send_meshes_each,
    BLINQ_OT_receive_mesh,
    BLINQ_OT_send_texture,
    # Retopo
    BLINQ_OT_set_retopo_state,
    BLINQ_OT_set_retopo_objects,
    # Auth
    BLINQ_OT_sign_in,
    BLINQ_OT_sign_out,
    # Bridge self-test
    BLINQ_OT_bridge_self_test,
    # Workflow
    BLINQ_OT_workflow_create,
    BLINQ_OT_workflow_select,
    BLINQ_OT_workflow_clear_active,
    BLINQ_OT_workflow_advance,
    BLINQ_OT_workflow_set_step,
    BLINQ_OT_workflow_delete,
    BLINQ_OT_workflow_export,
    BLINQ_OT_workflow_import,
    BLINQ_MT_workflow_stacks,
    # Reference board
    BLINQ_OT_reference_add,
    BLINQ_OT_reference_remove,
    BLINQ_OT_reference_open,
    BLINQ_OT_reference_clear_all,
    # Review snapshots
    BLINQ_OT_snapshot_capture,
    BLINQ_OT_snapshot_remove,
    BLINQ_OT_snapshot_open,
    # Generators
    BLINQ_OT_random_kit,
    BLINQ_OT_random_challenge,
    # Render presets
    BLINQ_OT_render_preset_save,
    BLINQ_OT_render_preset_apply,
    BLINQ_OT_render_preset_delete,
    # Light rigs
    BLINQ_OT_light_rig_save,
    BLINQ_OT_light_rig_apply,
    BLINQ_OT_light_rig_delete,
    # Library QC
    BLINQ_OT_audit_library,
    BLINQ_OT_audit_blend_dependencies,
    BLINQ_OT_refresh_all_previews,
    # Gallery operators
    BLINQ_OT_gallery_select_asset,
    BLINQ_OT_gallery_delete_asset,
    BLINQ_OT_clear_gallery_search,
    BLINQ_OT_gallery_clear_filters,
    BLINQ_OT_gallery_set_type_filter,
    BLINQ_OT_gallery_clear_selection,
    # World / HDRI
    BLINQ_OT_load_hdri,
    # Diagnostics
    BLINQ_OT_clear_log,
    BLINQ_OT_copy_log,
    # Pie
    BLINQ_MT_pie_menu,
    BLINQ_OT_call_pie,
]


def register() -> None:
    """Register operator/menu classes, Asset Browser menu, scene props, and keymap."""
    # Register property group first
    bpy.utils.register_class(BLINQ_PG_AssetSelection)

    for cls in _CLASSES:
        bpy.utils.register_class(cls)

    # Per-scene state
    bpy.types.Scene.xmd_active_workflow_id = StringProperty(
        name="XMD Active Workflow",
        description="UUID of the currently active BlinQ workflow stack",
        default="",
    )

    # Gallery viewer state
    bpy.types.Scene.xmd_gallery_tab = EnumProperty(
        name="Gallery Tab",
        description="Current gallery viewer tab",
        items=[
            ("GALLERY", "Gallery", "Asset gallery and browsing"),
            ("FOLDERS", "Folders", "Folder management"),
        ],
        default="GALLERY",
    )
    bpy.types.Scene.xmd_selected_asset_uuid = StringProperty(
        name="Selected Asset UUID",
        description="UUID of the currently selected asset in the gallery",
        default="",
    )
    bpy.types.Scene.xmd_gallery_search_text = StringProperty(
        name="Search",
        description="Search text to filter assets by name",
        default="",
    )
    bpy.types.Scene.xmd_gallery_filter_type = EnumProperty(
        name="Asset Type",
        description="Filter assets by type",
        items=[
            ("ALL", "All Types", ""),
            ("OBJECT", "Objects", ""),
            ("MATERIAL", "Materials", ""),
            ("BRUSH", "Brushes", ""),
            ("IMAGE", "Images", ""),
            ("TEXTURE", "Textures", ""),
            ("NODE_GROUP", "Node Groups", ""),
            ("COLLECTION", "Collections", ""),
            ("WORLD", "Worlds", ""),
            ("SCENE", "Scenes", ""),
        ],
        default="ALL",
    )

    # Advanced gallery features
    bpy.types.Scene.xmd_gallery_column_count = IntProperty(
        name="Gallery Columns",
        description="Number of columns in asset grid",
        default=4,
        min=1,
        max=8,
    )
    bpy.types.Scene.xmd_asset_selections = CollectionProperty(
        type=BLINQ_PG_AssetSelection,
        name="Asset Selections",
    )

    try:
        bpy.types.ASSETBROWSER_MT_context_menu.append(_asset_browser_menu)
    except AttributeError:
        diagnostics.warn("ui", "ASSETBROWSER_MT_context_menu not found — Asset Browser menu skipped")
    _add_keymap()


def unregister() -> None:
    """Unregister everything in reverse order."""
    _remove_keymap()
    try:
        bpy.types.ASSETBROWSER_MT_context_menu.remove(_asset_browser_menu)
    except AttributeError:
        pass

    try:
        del bpy.types.Scene.xmd_active_workflow_id
    except AttributeError:
        pass

    # Gallery properties
    for prop in [
        "xmd_gallery_tab",
        "xmd_selected_asset_uuid",
        "xmd_gallery_search_text",
        "xmd_gallery_filter_type",
        "xmd_gallery_column_count",
        "xmd_asset_selections",
    ]:
        try:
            delattr(bpy.types.Scene, prop)
        except AttributeError:
            pass

    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)

    # Unregister property group last
    try:
        bpy.utils.unregister_class(BLINQ_PG_AssetSelection)
    except RuntimeError:
        pass
