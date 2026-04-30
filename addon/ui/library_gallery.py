"""Asset library gallery viewer panel with filtering, search, and management.

Provides BLINQ_PT_library_gallery panel with:
- Gallery tab: Grid layout with asset thumbnails and filtering
- Folder Manager tab: Manage library folders
- Asset detail panel: Metadata and action buttons
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy

from .. import diagnostics
from ..assets.index import CatalogManager, XMDIndex
from ..models import AssetRecord, AssetType
from ..prefs import get_prefs


# ---------------------------------------------------------------------------
# Constants & Icons
# ---------------------------------------------------------------------------

_TYPE_ICONS: dict[str, str] = {
    "OBJECT": "OBJECT_DATA",
    "MATERIAL": "MATERIAL",
    "BRUSH": "BRUSH_DATA",
    "IMAGE": "IMAGE_DATA",
    "TEXTURE": "TEXTURE",
    "NODE_GROUP": "NODETREE",
    "COLLECTION": "OUTLINER_COLLECTION",
    "WORLD": "WORLD",
    "SCENE": "SCENE_DATA",
    "UNKNOWN": "QUESTION",
}


def _get_asset_type_items() -> list[tuple[str, str, str]]:
    """Generate enum items from AssetType values."""
    items = [("", "All Types", "", 0)]
    for i, asset_type in enumerate(AssetType, start=1):
        items.append((asset_type.value, asset_type.value.replace("_", " ").title(), "", i))
    return items


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _filter_assets(
    assets: list[AssetRecord],
    search_text: str = "",
    filter_type: str = "",
    filter_tags: list[str] | None = None,
) -> list[AssetRecord]:
    """Apply search and filter criteria to asset list.

    Args:
        assets: List of all assets to filter.
        search_text: Substring to match in asset name (case-insensitive).
        filter_type: Asset type to filter by, or "" for all types.
        filter_tags: Tags to filter by, or None for no tag filtering.

    Returns:
        Filtered list of assets.
    """
    filtered = assets

    # Search by name
    if search_text.strip():
        search_lower = search_text.strip().lower()
        filtered = [a for a in filtered if search_lower in a.name.lower()]

    # Filter by type
    if filter_type:
        filtered = [a for a in filtered if a.asset_type == filter_type]

    # Filter by tags (all selected tags must be present)
    if filter_tags:
        filtered = [a for a in filtered if all(tag in a.tags for tag in filter_tags)]

    return filtered


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class BLINQ_PT_library_gallery(bpy.types.Panel):
    """Asset library gallery viewer with filtering and management."""

    bl_label = "Library Gallery"
    bl_idname = "BLINQ_PT_library_gallery"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 1

    def draw_header(self, context: bpy.types.Context) -> None:
        """Draw the panel header icon.

        Args:
            context: The current Blender context.
        """
        self.layout.label(text="", icon="IMAGE_REFERENCE")

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the main gallery interface.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        scene = context.scene
        prefs = get_prefs(context)

        if not prefs.library_path:
            layout.label(text="Set Library Path in Preferences", icon="ERROR")
            return

        lib_path = Path(prefs.library_path)
        try:
            index = XMDIndex(lib_path)
            index.load()
            all_assets = index.all()
        except Exception as exc:
            layout.label(text=f"Error loading library: {exc}", icon="ERROR")
            diagnostics.error("gallery", f"failed to load index: {exc}")
            return

        # Tabs
        row = layout.row(align=True)
        row.prop(scene, "xmd_gallery_tab", expand=True)

        # --- GALLERY TAB ---
        if scene.xmd_gallery_tab == "GALLERY":
            self._draw_gallery_tab(layout, context, all_assets, lib_path)

        # --- FOLDER MANAGER TAB ---
        elif scene.xmd_gallery_tab == "FOLDERS":
            self._draw_folders_tab(layout, context)

    def _draw_gallery_tab(
        self,
        layout: bpy.types.UILayout,
        context: bpy.types.Context,
        all_assets: list[AssetRecord],
        lib_path: Path,
    ) -> None:
        """Draw the gallery browsing tab with filters and grid.

        Args:
            layout: Parent layout to draw into.
            context: The current Blender context.
            all_assets: All assets in the library.
            lib_path: Path to the library folder.
        """
        scene = context.scene

        # Toolbar with search and filters
        box = layout.box()
        col = box.column()

        # Search row
        search_row = col.row(align=True)
        search_row.prop(scene, "xmd_gallery_search_text", text="", icon="SEARCH")
        search_row.operator("blinq.clear_gallery_search", text="", icon="X")

        # Type and tag filters
        filter_row = col.row(align=True)
        filter_row.prop(scene, "xmd_gallery_filter_type", text="Type")
        filter_row.operator("blinq.gallery_clear_filters", text="", icon="X")

        # Apply filters
        search_text = scene.xmd_gallery_search_text
        filter_type = scene.xmd_gallery_filter_type
        filtered_assets = _filter_assets(all_assets, search_text, filter_type)

        # Asset count
        count_text = f"{len(filtered_assets)} asset{'s' if len(filtered_assets) != 1 else ''}"
        if filter_type or search_text:
            count_text += f" (of {len(all_assets)})"
        layout.label(text=count_text, icon="ASSET_MANAGER")

        # --- GALLERY GRID ---
        if filtered_assets:
            grid = layout.grid_flow(row_major=True, columns=4, align=True)
            for asset in filtered_assets:
                col = grid.column(align=True)
                # Asset button (selects asset)
                op = col.operator(
                    "blinq.gallery_select_asset",
                    text=f"{asset.name}\n{asset.asset_type}",
                    icon=_TYPE_ICONS.get(asset.asset_type, "QUESTION"),
                )
                op.asset_uuid = asset.xmd_uuid

            layout.separator(factor=1.0)

            # --- SELECTED ASSET DETAIL ---
            if scene.xmd_selected_asset_uuid:
                selected = next(
                    (a for a in all_assets if a.xmd_uuid == scene.xmd_selected_asset_uuid),
                    None,
                )
                if selected:
                    self._draw_asset_detail(layout, context, selected)
        else:
            layout.label(text="No assets match the current filters", icon="INFO")

    def _draw_asset_detail(
        self,
        layout: bpy.types.UILayout,
        context: bpy.types.Context,
        asset: AssetRecord,
    ) -> None:
        """Draw detail panel for the selected asset.

        Args:
            layout: Parent layout.
            context: The current Blender context.
            asset: The selected AssetRecord.
        """
        box = layout.box()
        col = box.column()

        # Asset name and type
        col.label(text=asset.name, icon=_TYPE_ICONS.get(asset.asset_type))
        col.label(text=asset.asset_type, icon="BLANK1")

        # Metadata
        if asset.author:
            col.label(text=f"Author: {asset.author}", icon="BLANK1")
        if asset.description:
            col.label(text=f"Desc: {asset.description[:50]}...", icon="BLANK1")
        if asset.tags:
            col.label(text=f"Tags: {', '.join(asset.tags[:3])}", icon="BLANK1")

        col.separator(factor=0.5)

        # Action buttons
        button_row = col.row(align=True)
        button_row.scale_y = 1.1

        op = button_row.operator("blinq.import_asset", text="Import", icon="IMPORT")
        op.asset_uuid = asset.xmd_uuid

        op = button_row.operator(
            "blinq.export_asset_file",
            text="Export",
            icon="EXPORT",
        )
        # export_asset_file expects active object, so we log the UUID for reference
        button_row.operator("blinq.gallery_delete_asset", text="", icon="TRASH")

        col.separator(factor=0.5)

        # Asset info
        info_col = col.column(align=True)
        info_col.scale_y = 0.8
        info_col.label(text=f"UUID: {asset.xmd_uuid[:8]}...", icon="BLANK1")
        info_col.label(text=f"File: {asset.blend_file}", icon="BLANK1")
        if asset.created_at:
            info_col.label(text=f"Created: {asset.created_at[:10]}", icon="BLANK1")

    def _draw_folders_tab(
        self,
        layout: bpy.types.UILayout,
        context: bpy.types.Context,
    ) -> None:
        """Draw the folder manager tab.

        Args:
            layout: Parent layout.
            context: The current Blender context.
        """
        layout.label(text="Folder Management", icon="FOLDER_REDIRECT")

        prefs = get_prefs(context)
        box = layout.box()
        col = box.column(align=True)

        col.label(text=f"Library: {prefs.library_path}", icon="FOLDER_DATA")

        col.separator(factor=0.5)

        # Add folder button
        col.operator(
            "blinq.import_assets_folder",
            text="Add Folder of Assets",
            icon="FOLDER_REDIRECT",
        )

        col.separator(factor=1.0)
        col.label(text="(Folder management coming soon)", icon="INFO")
