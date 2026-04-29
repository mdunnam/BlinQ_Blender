"""BlinQ N-panel UI for the 3D View sidebar.

Six panels under the "XMD" tab:

- BLINQ_PT_status        — XMD Cloud activation
- BLINQ_PT_bridge        — bridge connection + send/receive
- BLINQ_PT_library       — library path, count, quick actions
- BLINQ_PT_active_asset  — registration state and actions for the active object
- BLINQ_PT_metadata      — editable metadata (author, description, tags) [sub-panel]
- BLINQ_PT_retopo        — retopo workflow state for the active object
"""

from __future__ import annotations

import time
from pathlib import Path

import bpy

from ..assets.index import XMDIndex
from ..prefs import ADDON_ID
from ..models import RetopoState
from ..prefs import get_prefs


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STATUS_ICONS: dict[str, str] = {
    "UNLICENSED": "ERROR",
    "CHECKING": "TIME",
    "ACTIVE": "CHECKMARK",
    "OFFLINE": "WORLD",
    "EXPIRED": "X",
}

_BRIDGE_ICONS: dict[str, str] = {
    "DISCONNECTED": "UNLINKED",
    "CONNECTED": "LINKED",
    "ERROR": "ERROR",
}

_TYPE_ICONS: dict[str, str] = {
    "Object": "OBJECT_DATA",
    "Material": "MATERIAL",
    "Brush": "BRUSH_DATA",
    "Image": "IMAGE_DATA",
    "Texture": "TEXTURE",
    "ShaderNodeTree": "NODETREE",
    "GeometryNodeTree": "NODETREE",
    "Collection": "OUTLINER_COLLECTION",
    "World": "WORLD",
    "Scene": "SCENE_DATA",
}

_RETOPO_ICONS: dict[str, str] = {
    RetopoState.NONE.value: "QUESTION",
    RetopoState.BLOCKED.value: "X",
    RetopoState.IN_PROGRESS.value: "TIME",
    RetopoState.DONE.value: "CHECKMARK",
    RetopoState.BAKED.value: "RENDER_RESULT",
    RetopoState.TEXTURED.value: "MATERIAL",
}


def _type_icon(datablock: bpy.types.ID) -> str:
    """Return the Blender icon for a datablock's Python type.

    Args:
        datablock: Any Blender ID datablock.

    Returns:
        A Blender icon string, defaulting to ``"QUESTION"``.
    """
    return _TYPE_ICONS.get(type(datablock).__name__, "QUESTION")


# ---------------------------------------------------------------------------
# Library count cache
# ---------------------------------------------------------------------------

_count_cache: dict[str, tuple[int, float]] = {}
_CACHE_TTL = 5.0  # seconds


def invalidate_library_cache() -> None:
    """Clear the library count cache, forcing a fresh read on the next draw."""
    _count_cache.clear()


def _cached_library_count(library_path: str) -> int:
    """Return the XMD-registered asset count, cached for up to 5 seconds.

    Args:
        library_path: Absolute path string to the XMD library folder.

    Returns:
        Asset count, or -1 if the index cannot be read.
    """
    now = time.monotonic()
    cached = _count_cache.get(library_path)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]
    try:
        index = XMDIndex(Path(library_path))
        index.load()
        count = len(index)
        _count_cache[library_path] = (count, now)
        return count
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# Panel 1 — Status (XMD Cloud activation)
# ---------------------------------------------------------------------------

class BLINQ_PT_status(bpy.types.Panel):
    """XMD Cloud activation state."""

    bl_label = "BlinQ"
    bl_idname = "BLINQ_PT_status"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 0

    def draw_header(self, context: bpy.types.Context) -> None:
        """Draw the panel header icon.

        Args:
            context: The current Blender context.
        """
        self.layout.label(text="", icon="ASSET_MANAGER")

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the license status row and action buttons.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        prefs = get_prefs(context)

        box = layout.box()
        col = box.column(align=True)

        # Signed-in user display
        display = prefs.xmdsource_display_name or prefs.xmdsource_username
        if display:
            col.label(text=display, icon="USER")

        # Status row with refresh button
        row = col.row(align=True)
        row.label(
            text=f"License: {prefs.activation_status.replace('_', ' ').title()}",
            icon=_STATUS_ICONS.get(prefs.activation_status, "QUESTION"),
        )
        row.operator("blinq.check_activation", text="", icon="FILE_REFRESH")

        # Seat / lease info when active
        if prefs.xmdsource_runtime_product_id:
            mode = (prefs.xmdsource_runtime_license_mode or "named").replace("_", "-").title()
            detail_row = col.row()
            detail_row.enabled = False
            detail_row.label(text=f"{mode} license", icon="INFO")
            if prefs.xmdsource_runtime_lease_expires_at:
                lease_row = col.row()
                lease_row.enabled = False
                lease_row.label(
                    text=f"Lease: {prefs.xmdsource_runtime_lease_expires_at[:16]}",
                    icon="TIME",
                )

        # No Access warning (signed in but no BlinQ license on the account)
        if display and prefs.activation_status == "NO_ACCESS":
            warn = col.row()
            warn.alert = True
            warn.label(text="No BlinQ license on this account", icon="ERROR")

        layout.separator(factor=0.3)
        col2 = layout.column(align=True)
        if not display:
            # Not signed in — show Sign In prominently
            col2.scale_y = 1.3
            col2.operator("blinq.sign_in", icon="USER")
        else:
            # Signed in — offer Sign Out and a switch-account option
            col2.operator("blinq.sign_out", text="Sign Out", icon="PANEL_CLOSE")
            col2.scale_y = 0.9
            col2.operator("blinq.sign_in", text="Switch Account", icon="USER")


# ---------------------------------------------------------------------------
# Panel 2 — Bridge
# ---------------------------------------------------------------------------

class BLINQ_PT_bridge(bpy.types.Panel):
    """Bridge connection status and send/receive controls."""

    bl_label = "Bridge"
    bl_idname = "BLINQ_PT_bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 1

    def draw_header(self, context: bpy.types.Context) -> None:
        """Draw the panel header icon based on bridge status.

        Args:
            context: The current Blender context.
        """
        prefs = get_prefs(context)
        icon = _BRIDGE_ICONS.get(prefs.bridge_status, "UNLINKED")
        self.layout.label(text="", icon=icon)

    def draw(self, context: bpy.types.Context) -> None:
        """Draw bridge status, work dir info, and send/receive buttons.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        prefs = get_prefs(context)

        # Status row
        status_icon = _BRIDGE_ICONS.get(prefs.bridge_status, "UNLINKED")
        status_label = prefs.bridge_status.replace("_", " ").title()

        box = layout.box()
        row = box.row(align=True)
        row.label(text=f"XMD Desktop: {status_label}", icon=status_icon)

        # Work dir missing hint
        if not prefs.work_dir:
            warn = box.row()
            warn.alert = True
            warn.label(text="Set Work Directory in preferences", icon="ERROR")
            box.operator(
                "preferences.addon_show",
                text="Open Preferences",
                icon="PREFERENCES",
            ).module = ADDON_ID
        else:
            p = prefs.work_dir
            box.label(
                text=("\u2026" + p[-26:]) if len(p) > 29 else p,
                icon="FILE_FOLDER",
            )

        layout.separator(factor=0.5)

        # Send / receive mesh
        col = layout.column(align=True)
        col.scale_y = 1.3
        row = col.row(align=True)
        row.operator("blinq.send_mesh", icon="EXPORT")
        row.operator("blinq.receive_mesh", icon="IMPORT")

        col.scale_y = 1.0
        col.operator("blinq.send_texture", icon="IMAGE_DATA")

        layout.separator(factor=0.3)
        layout.label(
            text="Press Shift+X for the quick pie menu",
            icon="INFO",
        )


# ---------------------------------------------------------------------------
# Panel 3 — Library
# ---------------------------------------------------------------------------

class BLINQ_PT_library(bpy.types.Panel):
    """Library path, asset count, and quick management actions."""

    bl_label = "Library"
    bl_idname = "BLINQ_PT_library"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 2

    def draw_header(self, context: bpy.types.Context) -> None:
        """Draw the panel header icon.

        Args:
            context: The current Blender context.
        """
        self.layout.label(text="", icon="ASSET_MANAGER")

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the library path, count, refresh button, and setup hint.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        prefs = get_prefs(context)

        if not prefs.library_path:
            row = layout.row(align=True)
            row.alert = True
            row.label(text="No library path set", icon="ERROR")
            row.operator(
                "preferences.addon_show", text="", icon="PREFERENCES"
            ).module = ADDON_ID
            return

        p = prefs.library_path
        row = layout.row(align=True)
        row.label(
            text=("\u2026" + p[-27:]) if len(p) > 30 else p,
            icon="FILE_FOLDER",
        )
        row.operator("blinq.open_library", text="", icon="FOLDER_REDIRECT")

        count = _cached_library_count(prefs.library_path)
        row = layout.row(align=True)
        row.label(
            text=f"{count} asset(s) registered" if count >= 0 else "Index unreadable",
            icon="CHECKMARK" if count > 0 else ("INFO" if count == 0 else "ERROR"),
        )
        row.operator("blinq.refresh_library", text="", icon="FILE_REFRESH")

        layout.separator(factor=0.5)
        col = layout.column(align=True)
        col.scale_y = 0.8
        col.label(text="Add this folder as an Asset Library in", icon="INFO")
        col.label(text="Preferences \u203a File Paths \u203a Asset Libraries")


# ---------------------------------------------------------------------------
# Panel 4 — Active Asset
# ---------------------------------------------------------------------------

class BLINQ_PT_active_asset(bpy.types.Panel):
    """Registration state and quick actions for the currently active object."""

    bl_label = "Active Asset"
    bl_idname = "BLINQ_PT_active_asset"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 3

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Show this panel only when an active object exists.

        Args:
            context: The current Blender context.

        Returns:
            True when there is an active object.
        """
        return context.active_object is not None

    def draw_header(self, context: bpy.types.Context) -> None:
        """Draw the panel header with the active object's type icon.

        Args:
            context: The current Blender context.
        """
        obj = context.active_object
        if obj:
            self.layout.label(text="", icon=_type_icon(obj))

    def draw(self, context: bpy.types.Context) -> None:
        """Draw object name, XMD registration state, UUID, and action buttons.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        obj = context.active_object
        if not obj:
            layout.label(text="Nothing selected")
            return

        xmd_uuid: str = str(obj.get("xmd_uuid", ""))
        is_asset: bool = obj.asset_data is not None

        layout.label(text=obj.name, icon=_type_icon(obj))
        layout.separator(factor=0.3)

        if xmd_uuid and is_asset:
            layout.label(text="Registered in XMD", icon="CHECKMARK")
        elif xmd_uuid and not is_asset:
            row = layout.row()
            row.alert = True
            row.label(text="UUID set — not a Blender asset", icon="ERROR")
        else:
            layout.label(text="Not registered in XMD", icon="QUESTION")

        if xmd_uuid:
            row = layout.row()
            row.enabled = False
            row.label(text=f"UUID  {xmd_uuid[:8]}\u2026")

        layout.separator(factor=0.5)

        col = layout.column(align=True)
        if not (xmd_uuid and is_asset):
            col.scale_y = 1.4
        col.operator("blinq.register_asset", icon="ADD")
        col.scale_y = 1.0
        row = col.row(align=True)
        row.operator("blinq.push_metadata", icon="EXPORT", text="Push")
        row.operator("blinq.pull_metadata", icon="IMPORT", text="Pull")
        col.operator("blinq.sync_preview", icon="IMAGE_DATA")


# ---------------------------------------------------------------------------
# Panel 4a — Metadata (sub-panel of Active Asset)
# ---------------------------------------------------------------------------

class BLINQ_PT_metadata(bpy.types.Panel):
    """Editable metadata sub-panel: author, description, and tags."""

    bl_label = "Metadata"
    bl_idname = "BLINQ_PT_metadata"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_parent_id = "BLINQ_PT_active_asset"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Show only when the active object is marked as a Blender asset.

        Args:
            context: The current Blender context.

        Returns:
            True when the active object has asset_data.
        """
        obj = context.active_object
        return obj is not None and obj.asset_data is not None

    def draw(self, context: bpy.types.Context) -> None:
        """Draw author, description, tags list with add/remove, and catalog ID.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        obj = context.active_object
        if not obj or not obj.asset_data:
            return

        ad = obj.asset_data
        col = layout.column(align=True)
        col.prop(ad, "author", text="Author")
        col.prop(ad, "description", text="Description")

        layout.separator(factor=0.5)

        row = layout.row(align=True)
        row.label(text="Tags", icon="BOOKMARKS")
        row.operator("blinq.add_tag", text="", icon="ADD")

        if ad.tags:
            for tag in ad.tags:
                row = layout.row(align=True)
                row.label(text=tag.name, icon="DOT")
                op = row.operator("blinq.remove_tag", text="", icon="X")
                op.tag_name = tag.name
        else:
            layout.label(text="No tags yet", icon="INFO")

        layout.separator(factor=0.5)
        if ad.catalog_id:
            col = layout.column(align=True)
            col.label(text="Catalog ID", icon="FILE_FOLDER")
            row = col.row()
            row.enabled = False
            uid = ad.catalog_id
            row.label(text=(uid[:18] + "\u2026") if len(uid) > 18 else uid)


# ---------------------------------------------------------------------------
# Panel 5 — Retopo
# ---------------------------------------------------------------------------

class BLINQ_PT_retopo(bpy.types.Panel):
    """Retopology workflow state for the currently active object."""

    bl_label = "Retopo"
    bl_idname = "BLINQ_PT_retopo"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 4
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        """Show this panel only when an XMD-registered object is active.

        Args:
            context: The current Blender context.

        Returns:
            True when the active object has an xmd_uuid.
        """
        obj = context.active_object
        return obj is not None and bool(obj.get("xmd_uuid", ""))

    def draw_header(self, context: bpy.types.Context) -> None:
        """Draw the panel header icon based on the retopo state.

        Args:
            context: The current Blender context.
        """
        obj = context.active_object
        if not obj:
            return
        state = str(obj.get("xmd_retopo_state", RetopoState.NONE.value))
        self.layout.label(text="", icon=_RETOPO_ICONS.get(state, "QUESTION"))

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the retopo state indicator and state-change buttons.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        obj = context.active_object
        if not obj:
            return

        state = str(obj.get("xmd_retopo_state", RetopoState.NONE.value))
        state_icon = _RETOPO_ICONS.get(state, "QUESTION")

        # Current state display
        box = layout.box()
        box.label(
            text=f"State: {state.replace('_', ' ').title()}",
            icon=state_icon,
        )

        # Source and retopo object name (read-only)
        source = str(obj.get("xmd_retopo_source", ""))
        retopo = str(obj.get("xmd_retopo_mesh", ""))
        if source:
            row = box.row()
            row.enabled = False
            row.label(text=f"Source: {source}", icon="MESH_DATA")
        if retopo:
            row = box.row()
            row.enabled = False
            row.label(text=f"Retopo: {retopo}", icon="MESH_GRID")

        layout.separator(factor=0.5)

        # State buttons in a 3-column grid
        layout.label(text="Set State:", icon="SETTINGS")
        grid = layout.grid_flow(row_major=True, columns=3, align=True)
        for rs in RetopoState:
            op = grid.operator(
                "blinq.set_retopo_state",
                text=rs.value.replace("_", " ").title(),
                icon=_RETOPO_ICONS.get(rs.value, "QUESTION"),
                depress=(state == rs.value),
            )
            op.state = rs.value

        layout.separator(factor=0.5)
        layout.operator("blinq.set_retopo_objects", icon="LINKED")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_CLASSES = [
    BLINQ_PT_status,
    BLINQ_PT_bridge,
    BLINQ_PT_library,
    BLINQ_PT_active_asset,
    BLINQ_PT_metadata,   # must follow its parent BLINQ_PT_active_asset
    BLINQ_PT_retopo,
]


def register() -> None:
    """Register all BlinQ panel classes."""
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    """Unregister all BlinQ panel classes in reverse order."""
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
