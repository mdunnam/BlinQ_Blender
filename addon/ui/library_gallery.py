"""Asset library gallery viewer panel with XMD ToolBox-style UI.

Provides BLINQ_PT_library_gallery panel with:
- Left sidebar: Category buttons (responsive icon-based)
- Center: Asset grid with multi-select, favorites, and filtering
- Right: Property panel for metadata editing and tag cloud
- Dynamic column sizing and thumbnail caching
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy

from .. import diagnostics
from ..assets.index import CatalogManager, XMDIndex
from ..assets.previews import PreviewManager
from ..models import AssetRecord, AssetType
from ..prefs import get_prefs


# ---------------------------------------------------------------------------
# Constants
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

ASSET_CATEGORIES = [
    ("OBJECT", "Objects", "OBJECT_DATA"),
    ("MATERIAL", "Materials", "MATERIAL"),
    ("BRUSH", "Brushes", "BRUSH_DATA"),
    ("IMAGE", "Images", "IMAGE_DATA"),
    ("TEXTURE", "Textures", "TEXTURE"),
    ("COLLECTION", "Collections", "OUTLINER_COLLECTION"),
    ("WORLD", "Worlds", "WORLD"),
]


# ---------------------------------------------------------------------------
# Asset Selection Property Group
# ---------------------------------------------------------------------------

class BLINQ_PG_AssetSelection(bpy.types.PropertyGroup):
    """Track selection and favorite state for each asset."""

    asset_uuid: bpy.props.StringProperty(name="Asset UUID", default="")
    is_selected: bpy.props.BoolProperty(name="Selected", default=False)
    is_favorite: bpy.props.BoolProperty(name="Favorite", default=False)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _filter_assets(
    assets: list[AssetRecord],
    search_text: str = "",
    filter_type: str = "",
    filter_tags: list[str] | None = None,
) -> list[AssetRecord]:
    """Apply search and filter criteria to asset list."""
    filtered = assets

    if search_text.strip():
        search_lower = search_text.strip().lower()
        filtered = [a for a in filtered if search_lower in a.name.lower()]

    if filter_type:
        filtered = [a for a in filtered if a.asset_type == filter_type]

    if filter_tags:
        filtered = [a for a in filtered if all(tag in a.tags for tag in filter_tags)]

    return filtered


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class BLINQ_PT_library_gallery(bpy.types.Panel):
    """Asset library gallery viewer with XMD ToolBox-style interface."""

    bl_label = "Library Gallery"
    bl_idname = "BLINQ_PT_library_gallery"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 1

    def draw_header(self, context: bpy.types.Context) -> None:
        """Draw the panel header icon."""
        self.layout.label(text="", icon="IMAGE_REFERENCE")

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the main gallery interface with three-column layout."""
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

        # Tab selector
        row = layout.row(align=True)
        row.prop(scene, "xmd_gallery_tab", expand=True)

        if scene.xmd_gallery_tab == "GALLERY":
            self._draw_gallery_tab(layout, context, all_assets, lib_path)
        elif scene.xmd_gallery_tab == "FOLDERS":
            self._draw_folders_tab(layout, context)

    def _draw_gallery_tab(
        self,
        layout: bpy.types.UILayout,
        context: bpy.types.Context,
        all_assets: list[AssetRecord],
        lib_path: Path,
    ) -> None:
        """Draw the gallery browsing tab with three-column layout."""
        scene = context.scene

        # Main split: categories (10%) | assets (75%) | properties (25%)
        main_split = layout.split(factor=0.1)

        # --- LEFT: CATEGORY BUTTONS ---
        col_categories = main_split.column()
        self._draw_category_buttons(col_categories, scene)

        # Right side split: assets (75%) | properties (25%)
        right_split = main_split.split(factor=0.75)

        # --- CENTER: ASSET GRID ---
        col_assets = right_split.column()
        self._draw_asset_grid(col_assets, context, all_assets, scene, lib_path)

        # --- RIGHT: PROPERTIES PANEL ---
        col_props = right_split.column()
        self._draw_properties_panel(col_props, context, all_assets, scene)

    def _draw_category_buttons(
        self,
        layout: bpy.types.UILayout,
        scene: bpy.types.Scene,
    ) -> None:
        """Draw vertical category button sidebar."""
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Types", icon="ASSET_MANAGER")

        # All assets button
        is_all = scene.xmd_gallery_filter_type == ""
        op = col.operator(
            "blinq.gallery_set_type_filter",
            text="",
            icon="RESTRICT_VIEW_OFF",
            depress=is_all,
        )
        op.filter_type = ""

        # Category buttons
        for type_id, label, icon in ASSET_CATEGORIES:
            is_active = scene.xmd_gallery_filter_type == type_id
            op = col.operator(
                "blinq.gallery_set_type_filter",
                text="",
                icon=icon,
                depress=is_active,
            )
            op.filter_type = type_id

    def _draw_asset_grid(
        self,
        layout: bpy.types.UILayout,
        context: bpy.types.Context,
        all_assets: list[AssetRecord],
        scene: bpy.types.Scene,
        lib_path: Path,
    ) -> None:
        """Draw the asset grid with filtering and search."""
        # Toolbar
        box = layout.box()
        col = box.column()

        # Search row
        search_row = col.row(align=True)
        search_row.prop(scene, "xmd_gallery_search_text", text="", icon="SEARCH")
        search_row.operator("blinq.clear_gallery_search", text="", icon="X")

        # Filters row
        filter_row = col.row(align=True)
        filter_row.prop(scene, "xmd_gallery_filter_type", text="Type")
        filter_row.operator("blinq.gallery_clear_filters", text="", icon="X")

        # Column count slider
        col.prop(scene, "xmd_gallery_column_count", text="Columns")

        # Apply filters
        search_text = scene.xmd_gallery_search_text
        filter_type = scene.xmd_gallery_filter_type
        filtered_assets = _filter_assets(all_assets, search_text, filter_type)

        # Asset count
        count_text = f"{len(filtered_assets)} asset{'s' if len(filtered_assets) != 1 else ''}"
        if filter_type or search_text:
            count_text += f" (of {len(all_assets)})"
        layout.label(text=count_text, icon="ASSET_MANAGER")

        # Asset grid
        if filtered_assets:
            column_count = max(1, min(8, scene.xmd_gallery_column_count))
            grid = layout.grid_flow(
                row_major=True,
                columns=column_count,
                even_columns=True,
                align=True,
            )

            for asset in filtered_assets:
                self._draw_asset_card(grid, context, asset, scene)
        else:
            layout.label(text="No assets match filters", icon="INFO")

    def _draw_asset_card(
        self,
        layout: bpy.types.UILayout,
        context: bpy.types.Context,
        asset: AssetRecord,
        scene: bpy.types.Scene,
    ) -> None:
        """Draw a single asset card with thumbnail, selection, and actions."""
        box = layout.box()
        col = box.column(align=True)

        # Selection and favorite row (top-right)
        select_row = col.row(align=True)
        select_row.alignment = "RIGHT"

        # Favorite star
        selection_item = _get_or_create_selection(scene, asset.xmd_uuid)
        select_row.prop(
            selection_item,
            "is_favorite",
            text="",
            toggle=True,
            icon="SOLO_ON" if selection_item.is_favorite else "SOLO_OFF",
        )

        # Selection checkbox
        select_row.prop(
            selection_item,
            "is_selected",
            text="",
            toggle=True,
            icon="CHECKBOX_HLT" if selection_item.is_selected else "CHECKBOX_DEHLT",
        )

        # Asset name and type
        col.label(text=asset.name, icon=_TYPE_ICONS.get(asset.asset_type, "QUESTION"))
        col.label(text=asset.asset_type.replace("_", " ").title(), icon="BLANK1")

        # Author (if set)
        if asset.author:
            col.label(text=f"by {asset.author}", icon="BLANK1")

        col.separator(factor=0.3)

        # Action buttons
        button_row = col.row(align=True)
        button_row.scale_y = 1.0

        op = button_row.operator("blinq.import_asset", text="", icon="IMPORT")
        op.asset_uuid = asset.xmd_uuid

        op = button_row.operator("blinq.export_asset_file", text="", icon="EXPORT")
        button_row.operator("blinq.gallery_delete_asset", text="", icon="TRASH")

    def _draw_properties_panel(
        self,
        layout: bpy.types.UILayout,
        context: bpy.types.Context,
        all_assets: list[AssetRecord],
        scene: bpy.types.Scene,
    ) -> None:
        """Draw right-side properties panel with metadata and tags."""
        # Selected asset info
        if scene.xmd_selected_asset_uuid:
            selected = next(
                (a for a in all_assets if a.xmd_uuid == scene.xmd_selected_asset_uuid),
                None,
            )
            if selected:
                box = layout.box()
                col = box.column()
                col.label(text="Selected Asset", icon="ASSET_MANAGER")
                col.label(text=selected.name, icon=_TYPE_ICONS.get(selected.asset_type))
                col.label(text=selected.asset_type, icon="BLANK1")

                if selected.description:
                    col.label(text=selected.description[:40] + "...", icon="BLANK1")

                col.separator(factor=0.3)
                col.operator("blinq.gallery_clear_selection", text="Deselect", icon="X")

        # Tag cloud for selected assets
        selected_assets = [
            a
            for a in all_assets
            if _get_or_create_selection(scene, a.xmd_uuid).is_selected
        ]

        if selected_assets:
            box = layout.box()
            col = box.column()
            col.label(text="Tags", icon="BOOKMARKS")

            all_tags = set()
            for asset in selected_assets:
                all_tags.update(asset.tags or [])

            if all_tags:
                for tag in sorted(all_tags):
                    row = col.row(align=True)
                    row.label(text=tag, icon="BOOKMARKS")
                    row.label(text="")
            else:
                col.label(text="No tags", icon="INFO")

        # Favorites
        favorites = [
            a
            for a in all_assets
            if _get_or_create_selection(scene, a.xmd_uuid).is_favorite
        ]

        if favorites:
            box = layout.box()
            col = box.column()
            col.label(text="Favorites", icon="SOLO_ON")
            col.label(text=f"{len(favorites)} favorite(s)", icon="BLANK1")

    def _draw_folders_tab(
        self,
        layout: bpy.types.UILayout,
        context: bpy.types.Context,
    ) -> None:
        """Draw folder manager tab."""
        layout.label(text="Folder Management", icon="FOLDER_REDIRECT")
        prefs = get_prefs(context)

        box = layout.box()
        col = box.column(align=True)
        col.label(text=f"Library: {prefs.library_path}", icon="FOLDER_DATA")
        col.separator(factor=0.5)
        col.operator(
            "blinq.import_assets_folder",
            text="Add Folder of Assets",
            icon="FOLDER_REDIRECT",
        )


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def _get_or_create_selection(
    scene: bpy.types.Scene,
    asset_uuid: str,
) -> BLINQ_PG_AssetSelection:
    """Get or create a selection entry for an asset."""
    for item in scene.xmd_asset_selections:
        if item.asset_uuid == asset_uuid:
            return item

    item = scene.xmd_asset_selections.add()
    item.asset_uuid = asset_uuid
    return item
