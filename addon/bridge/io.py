"""BlinQ bridge mesh and texture I/O.

MeshExporter  — exports selected Blender objects as OBJ for XMD Desktop.
MeshImporter  — imports an OBJ from XMD Desktop into the active scene.
TextureImporter — loads an image file into Blender as an image datablock.
MaterialBuilder — creates a Principled-BSDF material from a set of texture maps.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import bpy

from .ipc import CommandEnvelope, IPCTransport
from ..models import BridgeCommandType


class MeshExporter:
    """Exports selected Blender objects as OBJ to the bridge work directory.

    Args:
        transport: An active IPCTransport for writing the mesh_out directory
            and the outgoing command.
    """

    def __init__(self, transport: IPCTransport) -> None:
        self._t = transport

    def execute(self) -> dict[str, Any]:
        """Export selected mesh objects and write a command for XMD Desktop.

        Steps:
            1. Collect all selected MESH-type objects.
            2. Export them as OBJ to mesh_out/ using a unique filename.
            3. Ensure every exported object has an xmd_uuid custom property.
            4. Write a ``receive_mesh`` command to xmd_cmd.json.

        Returns:
            A dict with keys ``file`` (filename), ``objects`` (count),
            and ``uuids`` (mapping of object name to UUID).

        Raises:
            RuntimeError: If no mesh objects are selected.
        """
        selected = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        if not selected:
            raise RuntimeError("No mesh objects selected for export")

        job_id = str(uuid.uuid4())[:8]
        filename = f"export_{job_id}.obj"
        out_path = self._t.mesh_out_dir / filename

        bpy.ops.wm.obj_export(
            filepath=str(out_path),
            check_existing=False,
            export_selected_objects=True,
            export_materials=False,
            export_uv=True,
            export_normals=True,
        )

        # Tag each exported object with an XMD UUID if not already set
        uuids: dict[str, str] = {}
        for obj in selected:
            xmd_uuid = str(obj.get("xmd_uuid", ""))
            if not xmd_uuid:
                xmd_uuid = str(uuid.uuid4())
                obj["xmd_uuid"] = xmd_uuid
            uuids[obj.name] = xmd_uuid

        self._t.write_command(
            CommandEnvelope(
                id=job_id,
                command=BridgeCommandType.RECEIVE_MESH.value,
                payload={"file": filename, "objects": uuids},
            )
        )

        return {"file": filename, "objects": len(selected), "uuids": uuids}

    def execute_per_object(self) -> dict[str, Any]:
        """Export each selected mesh as its own OBJ — multi-SubTool style.

        ZBrush represents each SubTool as an independent mesh, so a per-object
        export gives the cleanest hand-off when the artist intends each
        Blender object to land as a separate SubTool on the other side.

        Steps:
            1. Collect all selected MESH-type objects.
            2. For each, deselect-others / select-this and export to a
               uniquely named OBJ in mesh_out/.
            3. Stamp ``xmd_uuid`` if absent.
            4. Write a single ``receive_mesh`` command listing every file.

        Returns:
            A dict with keys ``files`` (list of filenames), ``objects``
            (count), and ``uuids`` (mapping of object name to UUID).

        Raises:
            RuntimeError: If no mesh objects are selected.
        """
        selected = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        if not selected:
            raise RuntimeError("No mesh objects selected for export")

        job_id = str(uuid.uuid4())[:8]
        files: list[dict[str, str]] = []
        uuids: dict[str, str] = {}

        # Preserve the original selection so we can restore it
        prior_selection = list(bpy.context.selected_objects)
        prior_active = bpy.context.view_layer.objects.active
        try:
            for obj in selected:
                xmd_uuid = str(obj.get("xmd_uuid", ""))
                if not xmd_uuid:
                    xmd_uuid = str(uuid.uuid4())
                    obj["xmd_uuid"] = xmd_uuid
                uuids[obj.name] = xmd_uuid

                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj

                # Slug-safe filename derived from the object name
                slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in obj.name)
                filename = f"export_{job_id}_{slug}.obj"
                out_path = self._t.mesh_out_dir / filename

                bpy.ops.wm.obj_export(
                    filepath=str(out_path),
                    check_existing=False,
                    export_selected_objects=True,
                    export_materials=False,
                    export_uv=True,
                    export_normals=True,
                )
                files.append({"file": filename, "object": obj.name, "uuid": xmd_uuid})
        finally:
            bpy.ops.object.select_all(action="DESELECT")
            for o in prior_selection:
                try:
                    o.select_set(True)
                except RuntimeError:
                    pass
            if prior_active is not None:
                bpy.context.view_layer.objects.active = prior_active

        self._t.write_command(
            CommandEnvelope(
                id=job_id,
                command=BridgeCommandType.RECEIVE_MESH.value,
                payload={"files": files, "mode": "subtools"},
            )
        )

        return {"files": [f["file"] for f in files], "objects": len(files), "uuids": uuids}


class MeshImporter:
    """Imports an OBJ file from the bridge mesh_in directory into the scene.

    Args:
        transport: An active IPCTransport for locating the mesh_in directory.
    """

    def __init__(self, transport: IPCTransport) -> None:
        self._t = transport

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Import the OBJ file specified in the command payload.

        Args:
            payload: Command payload containing:
                - ``file``: filename relative to mesh_in/.
                - ``uuid`` (optional): XMD UUID to stamp on the imported object.
                - ``name`` (optional): override for the imported object name.

        Returns:
            A dict with key ``imported`` (list of imported object names).

        Raises:
            ValueError: If the payload has no ``file`` key.
            FileNotFoundError: If the specified OBJ file does not exist.
        """
        filename: str = payload.get("file", "")
        if not filename:
            raise ValueError("Payload missing 'file' key")

        in_path = self._t.mesh_in_dir / filename
        if not in_path.exists():
            raise FileNotFoundError(f"Mesh file not found: {in_path}")

        bpy.ops.object.select_all(action="DESELECT")
        before_names = {o.name for o in bpy.data.objects}

        bpy.ops.wm.obj_import(filepath=str(in_path))

        new_objects = [o for o in bpy.data.objects if o.name not in before_names]
        xmd_uuid: str = payload.get("uuid", "")
        name_override: str = payload.get("name", "")

        for obj in new_objects:
            if xmd_uuid:
                obj["xmd_uuid"] = xmd_uuid
            if name_override and len(new_objects) == 1:
                obj.name = name_override

        return {"imported": [o.name for o in new_objects]}


class TextureImporter:
    """Loads a texture image from the bridge textures_in directory.

    Args:
        transport: An active IPCTransport for locating the textures_in directory.
    """

    def __init__(self, transport: IPCTransport) -> None:
        self._t = transport

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Load an image datablock from a file in textures_in/.

        Args:
            payload: Command payload containing:
                - ``file``: filename relative to textures_in/.
                - ``name`` (optional): override for the image datablock name.

        Returns:
            A dict with key ``image`` (image datablock name).

        Raises:
            ValueError: If the payload has no ``file`` key.
            FileNotFoundError: If the texture file does not exist.
        """
        filename: str = payload.get("file", "")
        if not filename:
            raise ValueError("Payload missing 'file' key")

        in_path = self._t.textures_in_dir / filename
        if not in_path.exists():
            raise FileNotFoundError(f"Texture file not found: {in_path}")

        image = bpy.data.images.load(str(in_path), check_existing=True)
        if payload.get("name"):
            image.name = payload["name"]

        return {"image": image.name}


class MaterialBuilder:
    """Creates a Principled-BSDF material from a set of texture map files.

    Map types are auto-detected from filename suffixes:
    ``_color`` / ``_diffuse``, ``_roughness``, ``_metallic``,
    ``_normal`` / ``_nrm``, ``_displacement`` / ``_disp``,
    ``_ao`` / ``_ambient_occlusion``.

    Args:
        transport: An active IPCTransport for locating the textures_in directory.
    """

    _SOCKET_MAP: dict[str, str] = {
        "color": "Base Color",
        "diffuse": "Base Color",
        "roughness": "Roughness",
        "metallic": "Metallic",
        "normal": "__normal__",
        "nrm": "__normal__",
        "displacement": "__displacement__",
        "disp": "__displacement__",
        "ao": "__ao__",
        "ambient_occlusion": "__ao__",
    }
    _NON_COLOR_SUFFIXES = (
        "_roughness", "_metallic", "_normal", "_nrm",
        "_displacement", "_disp", "_ao", "_ambient_occlusion",
    )

    def __init__(self, transport: IPCTransport) -> None:
        self._t = transport

    def build(self, name: str, texture_files: list[str]) -> bpy.types.Material:
        """Build a new Blender material from the provided texture filenames.

        Creates image datablocks, builds a node tree, and wires textures to
        the matching Principled BSDF inputs.

        Args:
            name: Display name for the new material.
            texture_files: List of filenames relative to textures_in/.

        Returns:
            The newly created (or updated) Blender Material datablock.
        """
        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name=name)
        mat.use_nodes = True
        tree = mat.node_tree
        tree.nodes.clear()

        bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location = (300, 0)
        tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        x_base = -600
        for i, filename in enumerate(texture_files):
            file_path = self._t.textures_in_dir / filename
            if not file_path.exists():
                continue

            image = bpy.data.images.load(str(file_path), check_existing=True)
            tex_node = tree.nodes.new("ShaderNodeTexImage")
            tex_node.image = image
            tex_node.location = (x_base, 400 - i * 280)

            stem = file_path.stem.lower()
            socket = next(
                (s for k, s in self._SOCKET_MAP.items() if stem.endswith(f"_{k}")),
                None,
            )

            if stem.endswith(self._NON_COLOR_SUFFIXES):
                image.colorspace_settings.name = "Non-Color"

            if socket == "__normal__":
                nrm = tree.nodes.new("ShaderNodeNormalMap")
                nrm.location = (x_base + 200, 400 - i * 280)
                tree.links.new(tex_node.outputs["Color"], nrm.inputs["Color"])
                tree.links.new(nrm.outputs["Normal"], bsdf.inputs["Normal"])
            elif socket and socket not in ("__displacement__", "__ao__"):
                if socket in bsdf.inputs:
                    tree.links.new(tex_node.outputs["Color"], bsdf.inputs[socket])

        return mat
