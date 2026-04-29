"""BlinQ Blender — XMD ToolBox bridge and asset library management.

Entry point for the BlinQ Blender extension. Registers and unregisters all
submodule classes and UI hooks in the correct dependency order, and starts/stops
the bridge heartbeat timer.
"""

from __future__ import annotations

from . import prefs
from .bridge import ipc as bridge_ipc
from .integrations import cloud
from .ui import menus, panels


def register() -> None:
    """Register all BlinQ Blender classes, operators, and UI hooks.

    Registration order:
    1. Preferences (required by all other modules via get_prefs)
    2. Cloud / activation (no bpy class deps)
    3. Panels
    4. Menus (operators + pie menu + keymap)
    5. Bridge timer (started last so preferences are already accessible)
    """
    prefs.register()
    cloud.register()
    panels.register()
    menus.register()
    bridge_ipc.get_monitor().start()


def unregister() -> None:
    """Unregister all BlinQ Blender classes and stop the bridge timer."""
    bridge_ipc.get_monitor().stop()
    menus.unregister()
    panels.unregister()
    cloud.unregister()
    prefs.unregister()
