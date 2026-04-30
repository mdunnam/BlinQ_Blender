"""Asset library gallery viewer panel with XMD ToolBox-style UI."""

from __future__ import annotations

from pathlib import Path

import bpy

from .. import diagnostics
from ..assets.index import XMDIndex
from ..models import AssetRecord, AssetType
from ..prefs import get_prefs


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
    ("ALL",        "All",        "RESTRICT_VIEW_OFF"),
    ("OBJECT",     "Objects",    "OBJECT_DATA"),
    ("MATERIAL",   "Materials",  "MATERIAL"),
    ("BRUSH",      "Brushes",    "BRUSH_DATA"),
    ("IMAGE",      "Images",     "IMAGE_DATA"),
    ("TEXTURE",    "Textures",   "TEXTURE"),
    ("COLLECTION", "Collections","OUTLINER_COLLECTION"),
    ("WORLD",      "Worlds",     "WORLD"),
]


class BLINQ_PG_AssetSelection(bpy.types.PropertyGroup):
    asset_uuid: bpy.props.StringProperty(name="Asset UUID", default="")
    is_selected: bpy.props.BoolProperty(name="Selected", default=False)
    is_favorite: bpy.props.BoolProperty(name="Favorite", default=False)


def _filter_assets(
    assets: list[AssetRecord],
    search_text: str = "",
    filter_type: str = "ALL",
) -> list[AssetRecord]:
    filtered = assets
    if search_text.strip():
        s = search_text.strip().lower()
        filtered = [a for a in filtered if s in a.name.lower()]
    if filter_type and filter_type != "ALL":
        filtered = [a for a in filtered if a.asset_type == filter_type]
    return filtered


def _get_or_create_selection(scene: bpy.types.Scene, uuid: str) -> BLINQ_PG_AssetSelection:
    for item in scene.xmd_asset_selections:
        if item.asset_uuid == uuid:
            return item
    item = scene.xmd_asset_selections.add()
    item.asset_uuid = uuid
    return item


class BLINQ_PT_library_gallery(bpy.types.Panel):
    bl_label = "Library Gallery"
    bl_idname = "BLINQ_PT_library_gallery"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 3

    def draw_header(self, context: bpy.types.Context) -> None:
        self.layout.label(text="", icon="IMAGE_REFERENCE")

    def draw(self, context: bpy.types.Context) -> None:
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

        # Tabs: Gallery | Folders
        row = layout.row(align=True)
        row.prop(scene, "xmd_gallery_tab", expand=True)

        if scene.xmd_gallery_tab == "GALLERY":
            self._draw_gallery(layout, context, all_assets, scene)
        else:
            self._draw_folders(layout, context, prefs)

    def _draw_gallery(self, layout, context, all_assets, scene):
        # ── Row 1: Category filter icons ──────────────────────────────────
        cat_row = layout.row(align=True)
        cat_row.scale_x = 1.0
        for type_id, label, icon in ASSET_CATEGORIES:
            is_active = scene.xmd_gallery_filter_type == type_id
            op = cat_row.operator(
                "blinq.gallery_set_type_filter",
                text="",
                icon=icon,
                depress=is_active,
            )
            op.filter_type = type_id

        # ── Row 2: Search bar ─────────────────────────────────────────────
        search_row = layout.row(align=True)
        search_row.prop(scene, "xmd_gallery_search_text", text="", icon="VIEWZOOM")
        search_row.operator("blinq.clear_gallery_search", text="", icon="X")

        # ── Row 3: Column count + count label ─────────────────────────────
        ctrl_row = layout.row(align=True)
        ctrl_row.prop(scene, "xmd_gallery_column_count", text="Cols")

        filtered = _filter_assets(
            all_assets,
            scene.xmd_gallery_search_text,
            scene.xmd_gallery_filter_type,
        )
        suffix = f"/{len(all_assets)}" if len(filtered) != len(all_assets) else ""
        ctrl_row.label(text=f"{len(filtered)}{suffix} assets")

        # ── Row 4: Asset grid ─────────────────────────────────────────────
        if not filtered:
            layout.label(text="No assets match filters", icon="INFO")
        else:
            cols = max(1, min(6, scene.xmd_gallery_column_count))
            grid = layout.grid_flow(
                row_major=True,
                columns=cols,
                even_columns=True,
                align=True,
            )
            for asset in filtered:
                self._draw_card(grid, asset, scene)

        # ── Row 5: Selected asset detail ──────────────────────────────────
        if scene.xmd_selected_asset_uuid:
            sel = next(
                (a for a in all_assets if a.xmd_uuid == scene.xmd_selected_asset_uuid),
                None,
            )
            if sel:
                self._draw_detail(layout, sel, scene)

        # ── Row 6: Tag cloud for checked assets ───────────────────────────
        checked = [
            a for a in all_assets
            if _get_or_create_selection(scene, a.xmd_uuid).is_selected
        ]
        if checked:
            self._draw_tag_cloud(layout, checked)

    def _draw_card(self, layout, asset: AssetRecord, scene):
        box = layout.box()
        col = box.column(align=True)

        # Asset type icon + name
        col.label(
            text=asset.name,
            icon=_TYPE_ICONS.get(asset.asset_type, "QUESTION"),
        )
        col.label(text=asset.asset_type.replace("_", " ").title())

        # Action buttons
        btn = col.row(align=True)
        op = btn.operator("blinq.import_asset", text="", icon="IMPORT")
        op.asset_uuid = asset.xmd_uuid
        op2 = btn.operator("blinq.gallery_select_asset", text="", icon="INFO")
        op2.asset_uuid = asset.xmd_uuid
        btn.operator("blinq.gallery_delete_asset", text="", icon="TRASH")

    def _draw_detail(self, layout, asset: AssetRecord, scene):
        box = layout.box()
        col = box.column(align=True)
        col.label(
            text=asset.name,
            icon=_TYPE_ICONS.get(asset.asset_type, "QUESTION"),
        )
        if asset.author:
            col.label(text=f"by {asset.author}", icon="BLANK1")
        if asset.tags:
            col.label(text="  ".join(asset.tags[:5]), icon="BOOKMARKS")
        if asset.description:
            col.label(text=asset.description[:60], icon="BLANK1")
        col.separator(factor=0.3)
        row = col.row(align=True)
        op = row.operator("blinq.import_asset", text="Import", icon="IMPORT")
        op.asset_uuid = asset.xmd_uuid
        row.operator("blinq.gallery_clear_selection", text="", icon="X")

    def _draw_tag_cloud(self, layout, assets: list[AssetRecord]):
        all_tags: set[str] = set()
        for a in assets:
            all_tags.update(a.tags or [])
        if not all_tags:
            return
        box = layout.box()
        col = box.column(align=True)
        col.label(text=f"Tags ({len(assets)} selected)", icon="BOOKMARKS")
        flow = col.grid_flow(row_major=True, columns=2, align=True)
        for tag in sorted(all_tags):
            flow.label(text=tag, icon="BOOKMARKS")

    def _draw_folders(self, layout, context, prefs):
        layout.label(text="Folder Management", icon="FOLDER_REDIRECT")
        box = layout.box()
        col = box.column(align=True)
        col.label(text=prefs.library_path or "(not set)", icon="FOLDER_DATA")
        col.separator(factor=0.5)
        col.operator(
            "blinq.import_assets_folder",
            text="Add Folder of Assets",
            icon="FOLDER_REDIRECT",
        )
