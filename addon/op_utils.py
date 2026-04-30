"""Operator execution utilities with error handling and user feedback."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

import bpy

from . import diagnostics


def report_error(operator: bpy.types.Operator, error: Exception, context: str = "") -> None:
    """Log an error and show it to the user.

    Args:
        operator: The operator instance.
        error: The exception that occurred.
        context: Optional context string to include in the message.
    """
    msg = str(error)
    if context:
        msg = f"{context}: {msg}"
    operator.report({"ERROR"}, msg)
    diagnostics.error("operator", msg)


def ensure_library_path(operator: bpy.types.Operator, prefs: Any) -> bool:
    """Check that library path is configured.

    Args:
        operator: The operator instance.
        prefs: The add-on preferences.

    Returns:
        True if library path is set, False otherwise.
    """
    if not prefs.library_path:
        operator.report(
            {"ERROR"},
            "Library Path not configured. See Add-on Preferences → BlinQ → Library Path",
        )
        return False
    return True


def ensure_bridge_path(operator: bpy.types.Operator, prefs: Any) -> bool:
    """Check that bridge work directory is configured.

    Args:
        operator: The operator instance.
        prefs: The add-on preferences.

    Returns:
        True if bridge path is set, False otherwise.
    """
    if not prefs.bridge_work_dir:
        operator.report(
            {"ERROR"},
            "Bridge Work Directory not configured. See Add-on Preferences → BlinQ → Bridge",
        )
        return False
    return True


def ensure_selection(
    operator: bpy.types.Operator,
    context: bpy.types.Context,
    obj_type: str = "MESH",
) -> list[bpy.types.Object]:
    """Check that at least one object of the given type is selected.

    Args:
        operator: The operator instance.
        context: The current Blender context.
        obj_type: Object type to check (default: MESH).

    Returns:
        List of selected objects of the given type, or empty list if none.
    """
    selected = [o for o in context.selected_objects if o.type == obj_type]
    if not selected:
        operator.report(
            {"ERROR"},
            f"Select at least one {obj_type.lower()} object to continue.",
        )
    return selected


@contextmanager
def safe_execute(
    operator: bpy.types.Operator,
    context: str = "",
) -> Generator[None, None, None]:
    """Context manager for safe operator execution with error reporting.

    Args:
        operator: The operator instance.
        context: Optional context string for error messages.

    Yields:
        None. Catch exceptions within the context to report to operator.

    Example:
        with safe_execute(self, "importing mesh"):
            # code that might raise exceptions
            result = importer.execute()
    """
    try:
        yield
    except Exception as exc:
        report_error(operator, exc, context)
