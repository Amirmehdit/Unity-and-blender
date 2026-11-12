"""
Scene visibility evaluation and filtering rules (Section 5.1).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VisibilitySettings:
    filter_mode: str = "ALL_VISIBLE"  # 'ALL_VISIBLE', 'SELECTED_ONLY', 'COLLECTIONS'
    collection_Allowlist: set[str] = field(default_factory=set)
    include_wire_bounds: bool = False
    respect_hide_render: bool = False
    include_lights: bool = False
    include_cameras: bool = False
    include_empties: bool = False


def _has_custom_bool_prop(id_data: Any, prop_name: str) -> bool | None:
    if id_data is None:
        return None
    try:
        if prop_name in id_data:
            return bool(id_data[prop_name])
    except Exception:
        pass
    return None


def build_excluded_collections_set(view_layer: Any) -> set[str]:
    """
    Walk the LayerCollection tree in view_layer to find excluded or hidden collections
    or collections tagged with unity_skip = True.
    """
    excluded: set[str] = set()

    def _walk(layer_col: Any, parent_excluded: bool) -> None:
        col = layer_col.collection
        is_excl = (
            parent_excluded
            or getattr(layer_col, "exclude", False)
            or getattr(layer_col, "hide_viewport", False)
            or getattr(col, "hide_viewport", False)
            or (_has_custom_bool_prop(col, "unity_skip") is True)
        )
        if is_excl:
            excluded.add(col.name_full if hasattr(col, "name_full") else col.name)
        for child in getattr(layer_col, "children", ()):
            _walk(child, is_excl)

    if view_layer and hasattr(view_layer, "layer_collection"):
        _walk(view_layer.layer_collection, False)
    return excluded


def get_object_collection_path(obj: Any, scene_collection: Any = None) -> str:
    """
    Return a deterministic '/'-separated collection hierarchy path for an object.
    """
    cols = getattr(obj, "users_collection", ())
    if not cols:
        return "Scene Collection"
    primary = cols[0]
    if scene_collection is not None and primary == scene_collection:
        return "Scene Collection"
    return primary.name


def is_object_type_supported(obj_type: str, settings: VisibilitySettings) -> bool:
    if obj_type in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
        return True
    if obj_type == "LIGHT":
        return settings.include_lights
    if obj_type == "CAMERA":
        return settings.include_cameras
    if obj_type == "EMPTY":
        return settings.include_empties
    return False


def is_object_visible(
    orig_obj: Any,
    view_layer: Any,
    settings: VisibilitySettings,
    excluded_collections: set[str],
    is_instance: bool = False,
) -> bool:
    """
    Evaluate all visibility rules from Section 5.1 for an object or instance.
    Note: Objects hidden by the user in the viewport but used as GN/Collection
    instance sources still appear when `is_instance=True` (the instance itself
    is emitted by a visible instancer).
    """
    if orig_obj is None:
        return False

    # 1. Explicit custom property overrides
    if _has_custom_bool_prop(orig_obj, "unity_skip") is True:
        return False
    force_include = _has_custom_bool_prop(orig_obj, "unity_force") is True

    # 2. Check object type
    obj_type = getattr(orig_obj, "type", "EMPTY")
    if not is_object_type_supported(obj_type, settings) and not force_include:
        return False

    # 3. Optional hide_render check
    if settings.respect_hide_render and getattr(orig_obj, "hide_render", False):
        return False

    # 4. Display type check (WIRE / BOUNDS skipped unless forced or enabled)
    display_type = getattr(orig_obj, "display_type", "TEXTURED")
    if display_type in {"WIRE", "BOUNDS"} and not settings.include_wire_bounds and not force_include:
        return False

    # If this is a generated instance (e.g. from Geometry Nodes), its parent instancer's
    # visibility was already verified.
    if is_instance:
        return True

    # 5. Standalone object visibility checks
    if getattr(orig_obj, "hide_viewport", False):
        return False

    if view_layer is not None and hasattr(orig_obj, "visible_get"):
        if not orig_obj.visible_get(view_layer=view_layer):
            return False

    # Check if all collections containing the object are excluded or unity_skip
    cols = getattr(orig_obj, "users_collection", ())
    if cols:
        all_excluded = True
        for c in cols:
            cname = c.name_full if hasattr(c, "name_full") else c.name
            if cname not in excluded_collections and _has_custom_bool_prop(c, "unity_skip") is not True:
                all_excluded = False
                break
        if all_excluded:
            return False

    # 6. Filter mode (Selected only / Specific collections)
    if settings.filter_mode == "SELECTED_ONLY":
        if view_layer is not None and hasattr(orig_obj, "select_get"):
            if not orig_obj.select_get(view_layer=view_layer):
                return False
    elif settings.filter_mode == "COLLECTIONS" and settings.collection_Allowlist:
        col_names = {c.name for c in cols}
        if not (col_names & settings.collection_Allowlist):
            return False

    return True
