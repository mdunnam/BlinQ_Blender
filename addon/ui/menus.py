"""BlinQ operators, pie menu, and Asset Browser context menu.

All bpy.types.Operator subclasses for the add-on live here, along with the
pie menu definition and the keymap that binds Shift+X in the 3D View.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty

from ..assets.index import CatalogManager, MetadataMapper, XMDIndex
from ..assets.previews import PreviewManager
from ..models import RetopoState
from ..prefs import get_prefs


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

        self.report(
            {"INFO"},
            f"Registered '{datablock.name}' ({record.asset_type}) \u2014 UUID {record.xmd_uuid[:8]}\u2026",
        )
        return {"FINISHED"}


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
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        # Login succeeded — now sync the runtime license
        ok2, _, msg2 = client.sync_runtime_license()
        if ok2 and prefs.xmdsource_runtime_product_id:
            if prefs.xmdsource_runtime_lease_id or prefs.xmdsource_runtime_product_id:
                prefs.activation_status = "ACTIVE"
            else:
                prefs.activation_status = "ACTIVE"
        elif ok2:
            prefs.activation_status = "NO_ACCESS"
        else:
            prefs.activation_status = "UNLICENSED"

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

        if ok and prefs.xmdsource_runtime_product_id:
            if client.has_active_runtime_access():
                prefs.activation_status = "ACTIVE"
            else:
                ok2, cached_msg = client.cached_runtime_access_status(msg)
                prefs.activation_status = "OFFLINE" if ok2 else "NO_ACCESS"
        elif "grace" in msg.lower() or "cached" in msg.lower():
            prefs.activation_status = "OFFLINE"
        else:
            prefs.activation_status = "EXPIRED" if "expired" in msg.lower() else "UNLICENSED"

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
            self.report(
                {"INFO"},
                f"Sent {result['objects']} object(s) \u2014 {result['file']}",
            )
            return {"FINISHED"}
        except Exception as exc:
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
            self.report({"INFO"}, f"Imported: {', '.join(result['imported'])}")
            return {"FINISHED"}
        except Exception as exc:
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

        self.report({"INFO"}, f"Texture sent: {src.name}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# ── Retopo operators ─────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

_RETOPO_STATE_ITEMS = [
    (s.value, s.value.replace("_", " ").title(), "")
    for s in RetopoState
]


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
                print(f"[BlinQ] RetopoTracker save error: {exc}")

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
# ── Pie menu ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class BLINQ_MT_pie_menu(bpy.types.Menu):
    """BlinQ quick-action pie menu. Default hotkey: Shift+X in the 3D View."""

    bl_idname = "BLINQ_MT_pie_menu"
    bl_label = "BlinQ"

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the 8-slot pie menu.

        Slot order: W, E, S, N, NW, NE, SW, SE

        Args:
            context: The current Blender context.
        """
        pie = self.layout.menu_pie()
        # W — Send Mesh
        pie.operator("blinq.send_mesh", icon="EXPORT")
        # E — Receive Mesh
        pie.operator("blinq.receive_mesh", icon="IMPORT")
        # S — Register in XMD
        pie.operator("blinq.register_asset", icon="ADD")
        # N — Open Library
        pie.operator("blinq.open_library", icon="FOLDER_REDIRECT")
        # NW — Send Texture
        pie.operator("blinq.send_texture", icon="IMAGE_DATA")
        # NE — Sync Preview
        pie.operator("blinq.sync_preview", icon="FILE_REFRESH")
        # SW — Push Metadata
        pie.operator("blinq.push_metadata", icon="EXPORT")
        # SE — Refresh Library
        pie.operator("blinq.refresh_library", icon="FILE_REFRESH")


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
    BLINQ_OT_push_metadata,
    BLINQ_OT_pull_metadata,
    BLINQ_OT_sync_preview,
    BLINQ_OT_add_tag,
    BLINQ_OT_remove_tag,
    BLINQ_OT_open_library,
    BLINQ_OT_refresh_library,
    BLINQ_OT_check_activation,
    # Bridge
    BLINQ_OT_send_mesh,
    BLINQ_OT_receive_mesh,
    BLINQ_OT_send_texture,
    # Retopo
    BLINQ_OT_set_retopo_state,
    BLINQ_OT_set_retopo_objects,
    # Auth
    BLINQ_OT_sign_in,
    BLINQ_OT_sign_out,
    # Pie
    BLINQ_MT_pie_menu,
    BLINQ_OT_call_pie,
]


def register() -> None:
    """Register operator/menu classes, Asset Browser menu, and keymap."""
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    try:
        bpy.types.ASSETBROWSER_MT_context_menu.append(_asset_browser_menu)
    except AttributeError:
        print("[BlinQ] ASSETBROWSER_MT_context_menu not found — Asset Browser menu skipped")
    _add_keymap()


def unregister() -> None:
    """Unregister everything in reverse order."""
    _remove_keymap()
    try:
        bpy.types.ASSETBROWSER_MT_context_menu.remove(_asset_browser_menu)
    except AttributeError:
        pass
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
