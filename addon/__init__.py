"""BlinQ Blender — XMD ToolBox bridge and asset library management.

Entry point for the BlinQ Blender extension. Registers and unregisters all
submodule classes and UI hooks in the correct dependency order, and starts/stops
the bridge heartbeat timer.
"""

from __future__ import annotations

import bpy

from . import diagnostics, prefs
from .bridge import ipc as bridge_ipc
from .integrations import cloud
from .integrations.cloud import CloudClient
from .ui import menus, panels


# ---------------------------------------------------------------------------
# Startup activation refresh
# ---------------------------------------------------------------------------

def _refresh_activation_status() -> None:
    """Re-validate runtime license against XMDSource if a JWT is present.

    Safe to call from timer or load_post — silently no-ops when the user
    is not signed in or the addon is not yet loaded.
    """
    addon = bpy.context.preferences.addons.get(__package__)
    if addon is None:
        return
    addon_prefs = addon.preferences
    client = CloudClient(addon_prefs)
    if not client.is_logged_in():
        return
    ok, _, msg = client.sync_runtime_license()
    addon_prefs.activation_status = client.resolve_activation_status(ok, msg)
    diagnostics.info(
        "cloud", f"startup activation refresh → {addon_prefs.activation_status}"
    )


@bpy.app.handlers.persistent
def _on_load_post(_dummy: object) -> None:
    """Handler — runs after every .blend file load."""
    _refresh_activation_status()


def _first_activation_check() -> None:
    """Timer callback — runs once shortly after register()."""
    _refresh_activation_status()
    return None  # one-shot


def register() -> None:
    """Register all BlinQ Blender classes, operators, and UI hooks.

    Registration order:
    1. Preferences (required by all other modules via get_prefs)
    2. Cloud / activation (no bpy class deps)
    3. Panels
    4. Menus (operators + pie menu + keymap)
    5. Bridge timer (started last so preferences are already accessible)
    6. Activation handler + first-check timer
    """
    prefs.register()
    cloud.register()
    panels.register()
    menus.register()
    bridge_ipc.get_monitor().start()

    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.timers.register(_first_activation_check, first_interval=3.0)

    diagnostics.info("addon", "BlinQ Blender registered")


def unregister() -> None:
    """Unregister all BlinQ Blender classes and stop the bridge timer."""
    diagnostics.info("addon", "BlinQ Blender unregistering")

    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    try:
        bpy.app.timers.unregister(_first_activation_check)
    except (ValueError, RuntimeError):
        pass

    bridge_ipc.get_monitor().stop()
    menus.unregister()
    panels.unregister()
    cloud.unregister()
    prefs.unregister()
