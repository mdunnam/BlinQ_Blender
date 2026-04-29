"""BlinQ Blender add-on preferences.

Provides XMDPreferences (AddonPreferences subclass) and the get_prefs()
accessor helper used throughout the add-on.

All XMDSource cloud state is persisted here as StringProperty / IntProperty
fields, mirroring the AppSettings keys used by XMD ToolBox 4.0.
"""

from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)

# Resolved at import time to the full extension module name, e.g.
# "bl_ext.user_default.blinq_blender" under Blender 4.2+ extensions.
# DO NOT replace with a string literal — the extensions system namespaces addon
# keys and bl_idname must match exactly.
ADDON_ID: str = __package__  # type: ignore[assignment]


_ACTIVATION_ITEMS = [
    ("UNLICENSED", "Unlicensed", "Not signed in or no BlinQ license found", "ERROR", 0),
    ("CHECKING", "Checking\u2026", "Contacting XMDSource to verify access", "TIME", 1),
    ("ACTIVE", "Active", "License verified and active", "CHECKMARK", 2),
    ("OFFLINE", "Offline (grace)", "Using cached license; XMDSource temporarily unreachable", "WORLD", 3),
    ("EXPIRED", "Expired", "Grace window has ended; sign in again to continue", "X", 4),
    ("NO_ACCESS", "No Access", "Signed in but no BlinQ license found on this account", "ERROR", 5),
]

_BRIDGE_STATUS_ITEMS = [
    ("DISCONNECTED", "Disconnected", "XMD Desktop is not detected", "UNLINKED", 0),
    ("CONNECTED", "Connected", "XMD Desktop heartbeat is live", "LINKED", 1),
    ("ERROR", "Error", "Bridge encountered an error", "ERROR", 2),
]


class XMDPreferences(bpy.types.AddonPreferences):
    """Preferences for the BlinQ Blender add-on.

    Stores library path, IPC work directory, XMDSource credentials, and
    all runtime license state. Accessible via get_prefs(context).
    """

    bl_idname = __package__  # resolved to the full extension module name at import time

    # ------------------------------------------------------------------ Library
    library_path: StringProperty(  # type: ignore[assignment]
        name="XMD Library Path",
        description=(
            "Folder where BlinQ stores the XMD asset library index and catalog. "
            "Add this folder as an Asset Library in Blender Preferences \u203a File Paths"
        ),
        subtype="DIR_PATH",
        default="",
    )

    # ------------------------------------------------------------------ Bridge
    work_dir: StringProperty(  # type: ignore[assignment]
        name="Bridge Work Directory",
        description=(
            "Shared folder used for IPC between BlinQ Blender and XMD Desktop. "
            "Both apps must have read/write access to this folder. "
            "Set the same path in XMD Desktop settings"
        ),
        subtype="DIR_PATH",
        default="",
    )

    bridge_status: EnumProperty(  # type: ignore[assignment]
        name="Bridge Status",
        items=_BRIDGE_STATUS_ITEMS,
        default="DISCONNECTED",
    )

    bridge_poll_interval: FloatProperty(  # type: ignore[assignment]
        name="Bridge Poll Interval (s)",
        description="How often BlinQ checks for messages from XMD Desktop",
        default=2.0,
        min=0.5,
        max=15.0,
    )

    # ------------------------------------------------------------------ Account (display only)
    xmdsource_username: StringProperty(  # type: ignore[assignment]
        name="XMDSource Username",
        description="Email / username used to sign in to XMDSource",
        default="",
    )

    xmdsource_display_name: StringProperty(  # type: ignore[assignment]
        name="Display Name",
        default="",
    )

    # ------------------------------------------------------------------ JWT
    xmdsource_token: StringProperty(  # type: ignore[assignment]
        name="XMDSource Token",
        description="JWT from XMDSource.com. Do not edit manually",
        subtype="PASSWORD",
        default="",
    )

    xmdsource_token_exp: IntProperty(  # type: ignore[assignment]
        name="Token Expiry",
        description="Unix timestamp of JWT expiry",
        default=0,
    )

    # ------------------------------------------------------------------ Runtime license
    xmdsource_runtime_product_id: StringProperty(default="")  # type: ignore[assignment]
    xmdsource_runtime_entitlement_id: StringProperty(default="")  # type: ignore[assignment]
    xmdsource_runtime_license_mode: StringProperty(default="")  # type: ignore[assignment]
    xmdsource_runtime_seat_count: IntProperty(default=0)  # type: ignore[assignment]
    xmdsource_runtime_active_count: IntProperty(default=0)  # type: ignore[assignment]
    xmdsource_runtime_lease_id: StringProperty(default="")  # type: ignore[assignment]
    xmdsource_runtime_lease_expires_at: StringProperty(default="")  # type: ignore[assignment]
    xmdsource_runtime_last_verified_at: IntProperty(default=0)  # type: ignore[assignment]

    # ------------------------------------------------------------------ Device identity
    xmdsource_device_id: StringProperty(default="")  # type: ignore[assignment]
    xmdsource_install_id: StringProperty(default="")  # type: ignore[assignment]

    # ------------------------------------------------------------------ UI / computed
    activation_status: EnumProperty(  # type: ignore[assignment]
        name="Activation Status",
        description="Cached activation state resolved from runtime license check",
        items=_ACTIVATION_ITEMS,
        default="UNLICENSED",
    )

    show_advanced: BoolProperty(name="Show Advanced", default=False)  # type: ignore[assignment]

    # ------------------------------------------------------------------ draw

    def draw(self, context: bpy.types.Context) -> None:
        """Draw the preferences panel in Edit \u203a Preferences \u203a Add-ons.

        Args:
            context: The current Blender context.
        """
        layout = self.layout

        # --- Library ---
        box = layout.box()
        box.label(text="Library", icon="ASSET_MANAGER")
        row = box.row(align=True)
        row.prop(self, "library_path", text="Library Path")
        if self.library_path:
            row.operator("blinq.open_library", text="", icon="FOLDER_REDIRECT")
        box.label(
            text="Add this folder as an Asset Library in Preferences \u203a File Paths \u203a Asset Libraries",
            icon="INFO",
        )

        layout.separator()

        # --- Bridge ---
        box = layout.box()
        box.label(text="Bridge \u2014 XMD Desktop IPC", icon="LINKED")
        row = box.row(align=True)
        row.prop(self, "work_dir", text="Work Directory")
        box.label(
            text="Set the same folder as the bridge path in XMD Desktop settings",
            icon="INFO",
        )

        layout.separator()

        # --- Account / Activation ---
        box = layout.box()
        box.label(text="XMDSource Account", icon="WORLD_DATA")

        # Sign-in fields
        col = box.column(align=True)
        col.prop(self, "xmdsource_username", text="Email / Username")

        if self.xmdsource_display_name:
            col.label(
                text=f"Signed in as: {self.xmdsource_display_name}",
                icon="USER",
            )

        # Status row
        status_icon = {
            "UNLICENSED": "ERROR", "CHECKING": "TIME", "ACTIVE": "CHECKMARK",
            "OFFLINE": "WORLD", "EXPIRED": "X", "NO_ACCESS": "ERROR",
        }.get(self.activation_status, "QUESTION")
        box.label(
            text=f"Status: {self.activation_status.replace('_', ' ').title()}",
            icon=status_icon,
        )

        if self.xmdsource_runtime_product_id:
            mode = self.xmdsource_runtime_license_mode.replace("_", "-").title() or "Named-user"
            box.label(text=f"License mode: {mode}", icon="INFO")
            if self.xmdsource_runtime_lease_expires_at:
                box.label(
                    text=f"Lease expires: {self.xmdsource_runtime_lease_expires_at}",
                    icon="TIME",
                )

        row = box.row(align=True)
        row.operator("blinq.sign_in", icon="USER")
        row.operator("blinq.check_activation", text="Refresh", icon="FILE_REFRESH")
        row.operator("blinq.sign_out", text="Sign Out", icon="PANEL_CLOSE")

        layout.separator()

        # --- Advanced ---
        layout.prop(self, "show_advanced", toggle=True, icon="PREFERENCES")
        if self.show_advanced:
            adv = layout.box()
            adv.label(text="Advanced", icon="SETTINGS")
            adv.prop(self, "bridge_poll_interval")
            adv.label(
                text="Token stored (do not share):",
                icon="INFO",
            )
            row = adv.row()
            row.enabled = False
            token = self.xmdsource_token
            row.label(text=(token[:12] + "\u2026") if len(token) > 12 else (token or "(none)"))


def get_prefs(context: bpy.types.Context) -> XMDPreferences:
    """Return the BlinQ add-on preferences from the given context.

    Args:
        context: The current Blender context.

    Returns:
        The XMDPreferences instance for this add-on.
    """
    return context.preferences.addons[__package__].preferences  # type: ignore[return-value]


def register() -> None:
    """Register preference classes."""
    bpy.utils.register_class(XMDPreferences)


def unregister() -> None:
    """Unregister preference classes."""
    bpy.utils.unregister_class(XMDPreferences)
