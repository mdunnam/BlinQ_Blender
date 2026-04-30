"""BlinQ preview generation management.

Provides PreviewManager, which schedules Blender asset preview generation
using bpy.app.timers to avoid blocking the main thread during operator
execution.
"""

from __future__ import annotations

import bpy

from .. import diagnostics


class PreviewManager:
    """Schedules asset preview generation for BlinQ-registered datablocks.

    Preview generation is deferred via bpy.app.timers so that the calling
    operator returns immediately without causing a UI stall.
    """

    def request_preview(self, datablock_name: str, bpy_collection: str) -> None:
        """Schedule a preview regeneration for a named datablock.

        The actual generation runs on the next timer tick (~0.1 s after the
        calling operator returns), keeping the UI responsive.

        Args:
            datablock_name: The ``.name`` of the target Blender datablock.
            bpy_collection: The bpy.data collection attribute name that contains
                the datablock, e.g. ``"objects"``, ``"materials"``, ``"brushes"``.
        """

        def _generate() -> None:
            """Run the deferred preview generation."""
            collection = getattr(bpy.data, bpy_collection, None)
            if collection is None:
                diagnostics.error("preview", f"unknown bpy.data collection: '{bpy_collection}'")
                return

            datablock = collection.get(datablock_name)
            if datablock is None:
                diagnostics.warn("preview", f"datablock not found: '{datablock_name}'")
                return

            if not datablock.asset_data:
                diagnostics.warn("preview", f"not an asset, skipping: '{datablock_name}'")
                return

            try:
                with bpy.context.temp_override(id=datablock):
                    bpy.ops.ed.lib_id_generate_preview()
                diagnostics.debug("preview", f"regenerated preview for '{datablock_name}'")
            except Exception as exc:
                diagnostics.error("preview", f"generation failed for '{datablock_name}': {exc}")

        bpy.app.timers.register(_generate, first_interval=0.1)
