"""BlinQ render preset library.

Captures and applies a small, engine-agnostic subset of Blender's render
settings: engine, resolution, samples, output path, file format, and
color-management transform / look.

The service persists to ``{library_path}/xmd_render_presets.json``.

Snapshot/apply helpers accept a ``bpy.types.Scene`` and use ``getattr``
guards so they keep working across Blender versions where engine-specific
property paths shift (Cycles ``samples`` vs. EEVEE ``taa_render_samples``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import diagnostics
from ..models import LightInfo, LightRig, RenderPreset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CYCLES_SAMPLES_PATH = ("cycles", "samples")
_EEVEE_SAMPLES_PATH = ("eevee", "taa_render_samples")


def _samples_path_for_engine(engine: str) -> tuple[str, str] | None:
    """Return the (group, attr) sample-count path for an engine, or None."""
    if engine == "CYCLES":
        return _CYCLES_SAMPLES_PATH
    if engine.startswith("BLENDER_EEVEE"):
        return _EEVEE_SAMPLES_PATH
    return None


def _read_samples(scene: Any) -> int:
    """Read the engine-appropriate sample count from a scene, defaulting to 0."""
    path = _samples_path_for_engine(scene.render.engine)
    if path is None:
        return 0
    group = getattr(scene, path[0], None)
    if group is None:
        return 0
    return int(getattr(group, path[1], 0) or 0)


def _write_samples(scene: Any, value: int) -> None:
    """Write the engine-appropriate sample count back to a scene."""
    path = _samples_path_for_engine(scene.render.engine)
    if path is None:
        return
    group = getattr(scene, path[0], None)
    if group is None:
        return
    try:
        setattr(group, path[1], int(value))
    except (TypeError, AttributeError):
        pass


# ---------------------------------------------------------------------------
# RenderPresetService
# ---------------------------------------------------------------------------

class RenderPresetService:
    """Persist and apply named render presets in the BlinQ library.

    Args:
        library_path: Absolute path to the XMD library root folder.
    """

    FILENAME = "xmd_render_presets.json"

    def __init__(self, library_path: Path) -> None:
        self._dir = library_path
        self._items: dict[str, RenderPreset] = {}

    @property
    def file_path(self) -> Path:
        """Absolute path to the JSON file."""
        return self._dir / self.FILENAME

    def load(self) -> None:
        """Load presets from disk. Missing file starts empty."""
        self._items = {}
        if not self.file_path.exists():
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            for entry in data.get("items", []):
                preset = RenderPreset.from_dict(entry)
                if preset.id:
                    self._items[preset.id] = preset
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            diagnostics.error("render", f"failed to load presets: {exc}")

    def save(self) -> None:
        """Persist presets to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "items": [p.to_dict() for p in self._items.values()],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def all(self) -> list[RenderPreset]:
        """Return all presets, oldest first."""
        return list(self._items.values())

    def get(self, preset_id: str) -> RenderPreset | None:
        """Return a preset by ID, or None."""
        return self._items.get(preset_id)

    def remove(self, preset_id: str) -> bool:
        """Remove a preset by ID. Persists on success."""
        if preset_id in self._items:
            del self._items[preset_id]
            self.save()
            return True
        return False

    def add_from_scene(self, name: str, scene: Any) -> RenderPreset:
        """Snapshot a scene's render settings into a new preset and persist.

        Args:
            name: Display name for the preset.
            scene: A ``bpy.types.Scene`` to capture from.

        Returns:
            The newly created RenderPreset.
        """
        r = scene.render
        vs = scene.view_settings
        preset = RenderPreset(
            name=name,
            engine=r.engine,
            resolution_x=int(r.resolution_x),
            resolution_y=int(r.resolution_y),
            resolution_percentage=int(r.resolution_percentage),
            file_format=r.image_settings.file_format,
            samples=_read_samples(scene),
            output_path=r.filepath,
            view_transform=vs.view_transform,
            look=vs.look,
        )
        self._items[preset.id] = preset
        self.save()
        return preset

    def apply(self, preset_id: str, scene: Any) -> bool:
        """Apply a preset's settings to the given scene.

        Args:
            preset_id: The preset UUID.
            scene: A ``bpy.types.Scene`` to write to.

        Returns:
            True on success, False if the preset is unknown.
        """
        preset = self._items.get(preset_id)
        if preset is None:
            return False
        r = scene.render
        vs = scene.view_settings
        try:
            r.engine = preset.engine
        except (TypeError, AttributeError) as exc:
            diagnostics.warn("render", f"could not set engine '{preset.engine}': {exc}")
        r.resolution_x = preset.resolution_x
        r.resolution_y = preset.resolution_y
        r.resolution_percentage = preset.resolution_percentage
        try:
            r.image_settings.file_format = preset.file_format
        except (TypeError, AttributeError):
            pass
        if preset.output_path:
            r.filepath = preset.output_path
        try:
            vs.view_transform = preset.view_transform
            vs.look = preset.look
        except (TypeError, AttributeError):
            pass
        if preset.samples:
            _write_samples(scene, preset.samples)
        return True


# ---------------------------------------------------------------------------
# LightRigService
# ---------------------------------------------------------------------------

def _light_size(light_data: Any) -> float:
    """Return the relevant size attribute for a light datablock.

    Args:
        light_data: A ``bpy.types.Light`` datablock.

    Returns:
        Cone size (SPOT) or area size, defaulting to 0.0 for POINT/SUN.
    """
    light_type = getattr(light_data, "type", "POINT")
    if light_type == "SPOT":
        return float(getattr(light_data, "spot_size", 0.0))
    if light_type == "AREA":
        return float(getattr(light_data, "size", 0.0))
    return 0.0


class LightRigService:
    """Persist and recreate named light setups in the BlinQ library.

    Storage: ``{library_path}/xmd_light_rigs.json``.

    Args:
        library_path: Absolute path to the XMD library root folder.
    """

    FILENAME = "xmd_light_rigs.json"

    def __init__(self, library_path: Path) -> None:
        self._dir = library_path
        self._items: dict[str, LightRig] = {}

    @property
    def file_path(self) -> Path:
        """Absolute path to the JSON file."""
        return self._dir / self.FILENAME

    def load(self) -> None:
        """Load rigs from disk. Missing file starts empty."""
        self._items = {}
        if not self.file_path.exists():
            return
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            for entry in data.get("items", []):
                rig = LightRig.from_dict(entry)
                if rig.id:
                    self._items[rig.id] = rig
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            diagnostics.error("render", f"failed to load light rigs: {exc}")

    def save(self) -> None:
        """Persist rigs to disk."""
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

    def all(self) -> list[LightRig]:
        """Return all rigs, oldest first."""
        return list(self._items.values())

    def get(self, rig_id: str) -> LightRig | None:
        """Return a rig by ID, or None."""
        return self._items.get(rig_id)

    def remove(self, rig_id: str) -> bool:
        """Remove a rig by ID. Persists on success."""
        if rig_id in self._items:
            del self._items[rig_id]
            self.save()
            return True
        return False

    def capture_from_scene(self, name: str, scene: Any) -> LightRig:
        """Snapshot every LIGHT-type object in ``scene`` into a new rig.

        Args:
            name: Display name for the rig.
            scene: A ``bpy.types.Scene`` whose lights should be captured.

        Returns:
            The newly created LightRig.
        """
        infos: list[LightInfo] = []
        for obj in scene.objects:
            if obj.type != "LIGHT":
                continue
            data = obj.data
            infos.append(
                LightInfo(
                    name=obj.name,
                    type=getattr(data, "type", "POINT"),
                    location=list(obj.location),
                    rotation_euler=list(obj.rotation_euler),
                    energy=float(getattr(data, "energy", 1000.0)),
                    color=list(getattr(data, "color", (1.0, 1.0, 1.0))),
                    size=_light_size(data),
                )
            )
        rig = LightRig(name=name, lights=infos)
        self._items[rig.id] = rig
        self.save()
        return rig

    def apply_to_scene(self, rig_id: str, scene: Any, bpy_module: Any) -> int:
        """Recreate the rig's lights inside a scene.

        Args:
            rig_id: The rig UUID.
            scene: A ``bpy.types.Scene`` to add lights to.
            bpy_module: Reference to ``bpy`` (passed in to keep the service bpy-free).

        Returns:
            The count of lights created. Returns 0 if the rig is unknown.
        """
        rig = self._items.get(rig_id)
        if rig is None:
            return 0
        created = 0
        for li in rig.lights:
            light_data = bpy_module.data.lights.new(
                name=f"{li.name} (rig)", type=li.type,
            )
            light_data.energy = li.energy
            light_data.color = li.color[:3]
            if li.type == "SPOT" and li.size:
                light_data.spot_size = li.size
            if li.type == "AREA" and li.size:
                light_data.size = li.size

            obj = bpy_module.data.objects.new(name=f"{li.name} (rig)", object_data=light_data)
            obj.location = li.location[:3]
            obj.rotation_euler = li.rotation_euler[:3]
            scene.collection.objects.link(obj)
            created += 1
        return created
