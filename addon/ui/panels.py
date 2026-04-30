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

import datetime
import time
from pathlib import Path

import bpy

from .. import diagnostics
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
        col.operator("blinq.send_meshes_each", icon="OUTLINER_OB_GROUP_INSTANCE")
        col.operator("blinq.send_texture", icon="IMAGE_DATA")

        layout.separator(factor=0.3)
        row = layout.row(align=True)
        row.operator("blinq.bridge_self_test", icon="PLAY", text="Self-Test")
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
        # Batch QC + world tools
        row = layout.row(align=True)
        row.operator("blinq.audit_library", icon="VIEWZOOM", text="Audit Lib")
        row.operator("blinq.audit_blend_dependencies", icon="FILE_3D", text="Audit Blend")
        layout.operator("blinq.refresh_all_previews", icon="FILE_REFRESH", text="Refresh All Previews")
        layout.operator("blinq.load_hdri", icon="WORLD_DATA", text="Load HDRI\u2026")

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
# Panel 6 — Workflow
# ---------------------------------------------------------------------------

class BLINQ_PT_workflow(bpy.types.Panel):
    """Active workflow stack with step navigation."""

    bl_label = "Workflow"
    bl_idname = "BLINQ_PT_workflow"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 5
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context: bpy.types.Context) -> None:
        """Show the workflow icon and step counter when a stack is active."""
        active_id = getattr(context.scene, "xmd_active_workflow_id", "")
        self.layout.label(text="", icon="SEQUENCE" if active_id else "BLANK1")

    def draw(self, context: bpy.types.Context) -> None:
        """Render either the picker (no active stack) or the step list."""
        layout = self.layout
        prefs = get_prefs(context)

        if not prefs.library_path:
            row = layout.row()
            row.alert = True
            row.label(text="No library path set", icon="ERROR")
            return

        active_id = getattr(context.scene, "xmd_active_workflow_id", "")
        from ..workflow.service import WorkflowService
        svc = WorkflowService(Path(prefs.library_path))
        svc.load()
        stack = svc.get(active_id) if active_id else None

        # Picker row — always visible
        row = layout.row(align=True)
        row.menu(
            "BLINQ_MT_workflow_stacks",
            text=stack.name if stack else "Select Stack…",
            icon="SEQUENCE",
        )
        row.operator("blinq.workflow_create", text="", icon="ADD")

        if stack is None:
            stacks = svc.all()
            if stacks:
                layout.label(text=f"{len(stacks)} stack(s) available", icon="INFO")
            else:
                layout.label(text="No stacks yet — click + to create one", icon="INFO")
            return

        layout.separator(factor=0.3)

        # Step list with current highlighted
        col = layout.column(align=True)
        for i, step_name in enumerate(stack.steps):
            row = col.row(align=True)
            is_current = i == stack.current_step
            is_done = i < stack.current_step
            icon = "PLAY" if is_current else ("CHECKMARK" if is_done else "DOT")
            op = row.operator(
                "blinq.workflow_set_step",
                text=f"{i + 1}. {step_name}",
                icon=icon,
                depress=is_current,
            )
            op.step_index = i

        layout.separator(factor=0.3)

        # Action buttons
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = stack.current_step < len(stack.steps) - 1
        sub.scale_y = 1.2
        sub.operator("blinq.workflow_advance", icon="FORWARD")
        row.operator("blinq.workflow_delete", text="", icon="TRASH")


# ---------------------------------------------------------------------------
# Panel 7 — Reference Board
# ---------------------------------------------------------------------------

_MAX_REFS_DRAWN = 12


class BLINQ_PT_reference(bpy.types.Panel):
    """Reference image board — managed list of reference photos."""

    bl_label = "Reference Board"
    bl_idname = "BLINQ_PT_reference"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 6
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context: bpy.types.Context) -> None:
        """Show the count badge in the header when items exist."""
        prefs = get_prefs(context)
        if not prefs.library_path:
            self.layout.label(text="", icon="IMAGE_REFERENCE")
            return
        try:
            from ..review.service import ReferenceBoardService
            svc = ReferenceBoardService(Path(prefs.library_path))
            svc.load()
            count = len(svc.all())
            self.layout.label(text=str(count) if count else "", icon="IMAGE_REFERENCE")
        except Exception:
            self.layout.label(text="", icon="IMAGE_REFERENCE")

    def draw(self, context: bpy.types.Context) -> None:
        """Render the add/clear row and the list of references."""
        layout = self.layout
        prefs = get_prefs(context)

        if not prefs.library_path:
            row = layout.row()
            row.alert = True
            row.label(text="No library path set", icon="ERROR")
            return

        from ..review.service import ReferenceBoardService
        svc = ReferenceBoardService(Path(prefs.library_path))
        svc.load()
        items = svc.all()

        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("blinq.reference_add", icon="IMPORT", text="Add References…")
        sub = row.row(align=True)
        sub.enabled = len(items) > 0
        sub.scale_y = 1.2
        sub.operator("blinq.reference_clear_all", text="", icon="TRASH")

        if not items:
            layout.label(text="No reference images yet", icon="INFO")
            return

        col = layout.column(align=True)
        for ref in items[:_MAX_REFS_DRAWN]:
            row = col.row(align=True)
            op_open = row.operator(
                "blinq.reference_open",
                text=ref.name,
                icon="IMAGE_DATA",
            )
            op_open.ref_id = ref.id
            op_rm = row.operator("blinq.reference_remove", text="", icon="X")
            op_rm.ref_id = ref.id

        if len(items) > _MAX_REFS_DRAWN:
            layout.label(
                text=f"… {len(items) - _MAX_REFS_DRAWN} more not shown",
                icon="INFO",
            )


# ---------------------------------------------------------------------------
# Panel 8 — Review Snapshots
# ---------------------------------------------------------------------------

_MAX_SNAPSHOTS_DRAWN = 12


class BLINQ_PT_snapshots(bpy.types.Panel):
    """Review snapshots — viewport captures with one-click open."""

    bl_label = "Snapshots"
    bl_idname = "BLINQ_PT_snapshots"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 7
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context: bpy.types.Context) -> None:
        """Show the count badge in the header."""
        prefs = get_prefs(context)
        if not prefs.library_path:
            self.layout.label(text="", icon="RENDER_RESULT")
            return
        try:
            from ..review.service import ReviewSnapshotService
            svc = ReviewSnapshotService(Path(prefs.library_path))
            svc.load()
            count = len(svc.all())
            self.layout.label(text=str(count) if count else "", icon="RENDER_RESULT")
        except Exception:
            self.layout.label(text="", icon="RENDER_RESULT")

    def draw(self, context: bpy.types.Context) -> None:
        """Render the capture button and the list of snapshots."""
        layout = self.layout
        prefs = get_prefs(context)

        if not prefs.library_path:
            row = layout.row()
            row.alert = True
            row.label(text="No library path set", icon="ERROR")
            return

        from ..review.service import ReviewSnapshotService
        svc = ReviewSnapshotService(Path(prefs.library_path))
        svc.load()
        items = svc.all()

        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("blinq.snapshot_capture", icon="CAMERA_DATA", text="Capture Viewport")

        if not items:
            layout.label(text="No snapshots yet", icon="INFO")
            return

        # Newest first
        col = layout.column(align=True)
        for snap in reversed(items[-_MAX_SNAPSHOTS_DRAWN:]):
            row = col.row(align=True)
            op_open = row.operator(
                "blinq.snapshot_open",
                text=snap.name,
                icon="RENDER_RESULT",
            )
            op_open.snap_id = snap.id
            op_rm = row.operator("blinq.snapshot_remove", text="", icon="X")
            op_rm.snap_id = snap.id

        if len(items) > _MAX_SNAPSHOTS_DRAWN:
            layout.label(
                text=f"… {len(items) - _MAX_SNAPSHOTS_DRAWN} earlier not shown",
                icon="INFO",
            )


# ---------------------------------------------------------------------------
# Panel 9 — Render Presets
# ---------------------------------------------------------------------------

_MAX_RENDER_PRESETS_DRAWN = 10


class BLINQ_PT_render_presets(bpy.types.Panel):
    """Render preset library — save and apply named render configurations."""

    bl_label = "Render Presets"
    bl_idname = "BLINQ_PT_render_presets"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 8
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context: bpy.types.Context) -> None:
        """Show preset count badge."""
        prefs = get_prefs(context)
        if not prefs.library_path:
            self.layout.label(text="", icon="OUTPUT")
            return
        try:
            from ..integrations.render import RenderPresetService
            svc = RenderPresetService(Path(prefs.library_path))
            svc.load()
            count = len(svc.all())
            self.layout.label(text=str(count) if count else "", icon="OUTPUT")
        except Exception:
            self.layout.label(text="", icon="OUTPUT")

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        prefs = get_prefs(context)

        if not prefs.library_path:
            row = layout.row()
            row.alert = True
            row.label(text="No library path set", icon="ERROR")
            return

        from ..integrations.render import RenderPresetService
        svc = RenderPresetService(Path(prefs.library_path))
        svc.load()
        items = svc.all()

        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("blinq.render_preset_save", icon="ADD", text="Save Current…")

        if not items:
            layout.label(text="No presets saved yet", icon="INFO")
            return

        col = layout.column(align=True)
        for preset in items[:_MAX_RENDER_PRESETS_DRAWN]:
            row = col.row(align=True)
            engine_label = preset.engine.replace("BLENDER_", "").lower()
            op_apply = row.operator(
                "blinq.render_preset_apply",
                text=f"{preset.name}  ({engine_label} {preset.resolution_x}×{preset.resolution_y})",
                icon="PLAY",
            )
            op_apply.preset_id = preset.id
            op_del = row.operator("blinq.render_preset_delete", text="", icon="X")
            op_del.preset_id = preset.id

        if len(items) > _MAX_RENDER_PRESETS_DRAWN:
            layout.label(
                text=f"… {len(items) - _MAX_RENDER_PRESETS_DRAWN} more not shown",
                icon="INFO",
            )


# ---------------------------------------------------------------------------
# Panel 10 — Light Rigs
# ---------------------------------------------------------------------------

_MAX_RIGS_DRAWN = 8


class BLINQ_PT_light_rigs(bpy.types.Panel):
    """Saved light setups — add named light rigs back into a scene."""

    bl_label = "Light Rigs"
    bl_idname = "BLINQ_PT_light_rigs"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 9
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context: bpy.types.Context) -> None:
        prefs = get_prefs(context)
        if not prefs.library_path:
            self.layout.label(text="", icon="LIGHT")
            return
        try:
            from ..integrations.render import LightRigService
            svc = LightRigService(Path(prefs.library_path))
            svc.load()
            count = len(svc.all())
            self.layout.label(text=str(count) if count else "", icon="LIGHT")
        except Exception:
            self.layout.label(text="", icon="LIGHT")

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        prefs = get_prefs(context)

        if not prefs.library_path:
            row = layout.row()
            row.alert = True
            row.label(text="No library path set", icon="ERROR")
            return

        from ..integrations.render import LightRigService
        svc = LightRigService(Path(prefs.library_path))
        svc.load()
        items = svc.all()

        light_count = sum(1 for o in context.scene.objects if o.type == "LIGHT")
        row = layout.row(align=True)
        row.scale_y = 1.2
        sub = row.row(align=True)
        sub.enabled = light_count > 0
        sub.scale_y = 1.2
        sub.operator(
            "blinq.light_rig_save",
            icon="ADD",
            text=f"Save Current ({light_count} light{'s' if light_count != 1 else ''})",
        )

        if not items:
            layout.label(text="No saved rigs yet", icon="INFO")
            return

        col = layout.column(align=True)
        for rig in items[:_MAX_RIGS_DRAWN]:
            row = col.row(align=True)
            op_apply = row.operator(
                "blinq.light_rig_apply",
                text=f"{rig.name}  ({len(rig.lights)} lt)",
                icon="LIGHT",
            )
            op_apply.rig_id = rig.id
            op_del = row.operator("blinq.light_rig_delete", text="", icon="X")
            op_del.rig_id = rig.id

        if len(items) > _MAX_RIGS_DRAWN:
            layout.label(
                text=f"… {len(items) - _MAX_RIGS_DRAWN} more not shown",
                icon="INFO",
            )


# ---------------------------------------------------------------------------
# Panel 11 — Diagnostics
# ---------------------------------------------------------------------------

_DIAG_LEVEL_ICONS: dict[str, str] = {
    "DEBUG": "DOT",
    "INFO":  "INFO",
    "WARN":  "ERROR",
    "ERROR": "CANCEL",
}

_MAX_DIAG_ROWS = 15        # cap rows drawn in the panel
_MAX_DIAG_MSG_CHARS = 60   # truncate long messages


class BLINQ_PT_diagnostics(bpy.types.Panel):
    """In-app log surface for bridge events, cloud calls, and errors."""

    bl_label = "Diagnostics"
    bl_idname = "BLINQ_PT_diagnostics"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "XMD"
    bl_order = 10
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context: bpy.types.Context) -> None:
        """Show an alert icon when there are unread errors.

        Args:
            context: The current Blender context.
        """
        err_count = diagnostics.count_at_level("ERROR")
        warn_count = diagnostics.count_at_level("WARN")
        if err_count:
            self.layout.label(text=str(err_count), icon="CANCEL")
        elif warn_count:
            self.layout.label(text=str(warn_count), icon="ERROR")
        else:
            self.layout.label(text="", icon="CONSOLE")

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the level filter, action buttons, and most recent entries.

        Args:
            context: The current Blender context.
        """
        layout = self.layout
        prefs = get_prefs(context)

        row = layout.row(align=True)
        row.prop(prefs, "diagnostics_min_level", text="Level")
        row.operator("blinq.copy_log", text="", icon="COPYDOWN")
        row.operator("blinq.clear_log", text="", icon="X")

        items = diagnostics.entries(prefs.diagnostics_min_level)
        if not items:
            layout.label(text="No log entries", icon="INFO")
            return

        # Show the tail — newest entry at the bottom.
        tail = items[-_MAX_DIAG_ROWS:]
        col = layout.column(align=True)
        col.scale_y = 0.85
        for entry in tail:
            ts = datetime.datetime.fromtimestamp(entry.timestamp).strftime("%H:%M:%S")
            msg = entry.message
            if len(msg) > _MAX_DIAG_MSG_CHARS:
                msg = msg[: _MAX_DIAG_MSG_CHARS - 1] + "…"
            row = col.row(align=True)
            if entry.level == "ERROR":
                row.alert = True
            row.label(
                text=f"{ts} {entry.source}: {msg}",
                icon=_DIAG_LEVEL_ICONS.get(entry.level, "DOT"),
            )

        if len(items) > len(tail):
            layout.label(
                text=f"… {len(items) - len(tail)} earlier entries (use Copy Log)",
                icon="INFO",
            )


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
    BLINQ_PT_workflow,
    BLINQ_PT_reference,
    BLINQ_PT_snapshots,
    BLINQ_PT_render_presets,
    BLINQ_PT_light_rigs,
    BLINQ_PT_diagnostics,
]


def register() -> None:
    """Register all BlinQ panel classes."""
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    """Unregister all BlinQ panel classes in reverse order."""
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
