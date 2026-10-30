# SPDX-License-Identifier: LGPL-2.1-or-later
# nestingworkbench/freecad_helpers.py

"""
Shared utility functions for FreeCAD operations used across the Nesting Workbench.
Consolidates common logic that was previously duplicated in multiple modules.
"""

try:
    import FreeCAD
except ImportError:
    FreeCAD = None

from freecad.nestingworkbench import nw_logger
from .constants import (
    BACK_BOUNDARY_PREFIX, BACK_GROUP_PREFIX, LAYOUT_PREFIX, OFFCUT_GROUP_PREFIX, PROP_FLOAT,
    PROP_LENGTH, PROP_SHEET_COST, PROP_SHEET_FLIP, PROP_SHEET_HEIGHT,
    PROP_SHEET_LIBRARY_ID, PROP_SHEET_LIBRARY_NAME, PROP_SHEET_MATERIAL,
    PROP_SHEET_THICKNESS, PROP_SHEET_WIDTH, PROP_STRING,
    SHEET_BOUNDARY_PREFIX, SHEET_INSTANCE_GROUP,
)


def find_sheet_boundary(group):
    """The Sheet_Boundary_* child of *group*, or None."""
    if not group or not hasattr(group, "Group"):
        return None
    return next((c for c in group.Group if c.Label.startswith(SHEET_BOUNDARY_PREFIX)), None)


def find_back_group(sheet_group):
    """The Back_* subgroup of a Sheet_N group (a flipped sheet's back side), or None."""
    if not sheet_group or not hasattr(sheet_group, "Group"):
        return None
    return next((c for c in sheet_group.Group if c.Label.startswith(BACK_GROUP_PREFIX)), None)


def find_offcut_group(sheet_group):
    """The Offcuts_* subgroup of a Sheet_N group, or None."""
    if not sheet_group or not hasattr(sheet_group, "Group"):
        return None
    return next((c for c in sheet_group.Group if c.Label.startswith(OFFCUT_GROUP_PREFIX)), None)


def find_back_boundary(back_group):
    """The Back_Boundary_* child of a Back_* group, or None."""
    if not back_group or not hasattr(back_group, "Group"):
        return None
    return next((c for c in back_group.Group if c.Label.startswith(BACK_BOUNDARY_PREFIX)), None)


def world_shape(obj):
    """A copy of obj.Shape in world coordinates.

    obj.Shape carries obj's own Placement only. A parent's placement (a PartDesign
    Body around a Pad or Sketch, an App::Part around any feature) is not in it, so
    reading obj.Shape as world drew such parts unrotated and at the parent's origin
    (GLB-001). getGlobalPlacement() is the whole chain, own Placement included.
    """
    shape = obj.Shape.copy()
    shape.Placement = obj.getGlobalPlacement()
    return shape


def get_up_vector_rotation(up_vector):
    """
    Returns a FreeCAD.Rotation that rotates the given up_vector to point along +Z.

    Args:
        up_vector: A FreeCAD.Vector giving the part's local "up" direction, or None.

    Returns:
        FreeCAD.Rotation to apply so up_vector ends up pointing at (0, 0, 1).
        Returns identity for (0, 0, 1), None, or a zero-length vector.
    """
    if up_vector is None:
        return FreeCAD.Rotation()
    v = FreeCAD.Vector(up_vector)
    if v.Length < 1e-9:
        nw_logger.warn(f"Up vector '{up_vector}' has zero length, using (0, 0, 1)")
        return FreeCAD.Rotation()
    return FreeCAD.Rotation(v, FreeCAD.Vector(0, 0, 1))

def recursive_delete(doc, obj, protected_names=None):
    """
    Recursively deletes a FreeCAD object and all its children from the document.
    Children are deleted first since FreeCAD doesn't cascade deletes.

    Args:
        doc: The FreeCAD document.
        obj: The FreeCAD object to delete.
        protected_names: Optional set of object names to skip (not delete).
    """
    if not obj:
        return

    try:
        obj_name = obj.Name
    except Exception as e:
        nw_logger.debug(f"[freecad_helpers] recursive_delete failed reading Name: {e}")
        return  # Object already deleted or invalid reference

    if protected_names and obj_name in protected_names:
        return

    # Recursively delete all children first (if it's a group-like object)
    if hasattr(obj, "Group"):
        for child in list(obj.Group):  # Copy list to avoid modification during iteration
            recursive_delete(doc, child, protected_names)

    # Delete the object itself
    try:
        if doc.getObject(obj_name):
            doc.removeObject(obj_name)
    except Exception as e:
        nw_logger.debug(f"[freecad_helpers] recursive_delete removeObject failed: {e}")

def get_layout_group(doc):
    """
    Finds the most relevant layout group in the active document.
    Prioritizes the temporary group (__temp_Layout) if it exists,
    otherwise returns the most recently created Layout_* group.

    Args:
        doc: The FreeCAD document.

    Returns:
        The layout group object, or None if not found.
    """
    if not doc:
        return None

    # Prioritize the temporary group as it's the one being actively worked on
    temp_group = doc.getObject("__temp_Layout")
    if temp_group:
        return temp_group

    # Otherwise, find the most recently created final layout group
    groups = [o for o in doc.Objects if o.isDerivedFrom("App::DocumentObjectGroup")]
    packed_groups = sorted(
        [g for g in groups if g.Label.startswith("Layout_")],
        key=lambda x: x.Name
    )
    if packed_groups:
        return packed_groups[-1]

    return None

def is_layout_group(obj):
    """True if *obj* is a nesting Layout_* document group."""
    return (obj.isDerivedFrom("App::DocumentObjectGroup") and
            obj.Label.startswith(LAYOUT_PREFIX))

def get_sheet_groups(layout_group):
    """
    Gets all the direct child Sheet groups from a layout group, sorted numerically.

    Args:
        layout_group: The parent layout group object.

    Returns:
        Sorted list of Sheet_* group objects.
    """
    if not layout_group:
        return []

    sheet_groups = [obj for obj in layout_group.Group if obj.Label.startswith("Sheet_")]
    sheet_groups.sort(key=lambda g: int(g.Label.split('_')[1]))
    return sheet_groups

_SHEET_INSTANCE_PROPS = (
    # (key in the values dict, property name, FreeCAD type)
    ("sheet_id", PROP_SHEET_LIBRARY_ID, PROP_STRING),
    ("sheet_name", PROP_SHEET_LIBRARY_NAME, PROP_STRING),
    ("material", PROP_SHEET_MATERIAL, PROP_STRING),
    ("cost", PROP_SHEET_COST, PROP_FLOAT),
    ("width", PROP_SHEET_WIDTH, PROP_LENGTH),
    ("height", PROP_SHEET_HEIGHT, PROP_LENGTH),
    ("thickness", PROP_SHEET_THICKNESS, PROP_LENGTH),
    ("flip", PROP_SHEET_FLIP, PROP_STRING),
)


def write_sheet_instance(sheet_group, values):
    """Stamp a Sheet_N group with the sheet it was spawned from.

    *values* must have every key in _SHEET_INSTANCE_PROPS; a missing key is a
    KeyError raised before anything is written, not a default. An empty
    sheet_id means a Custom size.
    """
    missing = [key for key, _prop, _type in _SHEET_INSTANCE_PROPS if key not in values]
    if missing:
        raise KeyError(f"sheet instance values missing {missing}")
    for key, prop, prop_type in _SHEET_INSTANCE_PROPS:
        if not hasattr(sheet_group, prop):
            sheet_group.addProperty(prop_type, prop, SHEET_INSTANCE_GROUP, "")
        setattr(sheet_group, prop, values[key])


def read_sheet_instance(sheet_group):
    """The values write_sheet_instance stored, or None if the group was never stamped.

    None means a layout nested before the sheet library existed (or a Sheet_N
    added by the manual nester to such a layout). None also for a Sheet_N stamped
    before SheetFlip existed (no migration, SVF decision 13).
    """
    if not all(hasattr(sheet_group, prop) for _key, prop, _type in _SHEET_INSTANCE_PROPS):
        return None
    return {key: (float(getattr(sheet_group, prop)) if prop_type in (PROP_LENGTH, PROP_FLOAT)
                  else str(getattr(sheet_group, prop)))
            for key, prop, prop_type in _SHEET_INSTANCE_PROPS}


def get_nested_containers(sheet_group):
    """
    Gets the nested_* App::Part containers holding a sheet's placed parts.

    Containers live in the sheet's Shapes_* subgroup. Documents where they
    sit directly under the sheet group predate that structure and must be
    converted with the MigrateNestingDocuments macro.

    Args:
        sheet_group: A Sheet_* App::DocumentObjectGroup.

    Returns:
        List of nested_* App::Part container objects.
    """
    containers = []
    for sub_group in sheet_group.Group:
        if (sub_group.isDerivedFrom("App::DocumentObjectGroup")
                and sub_group.Label.startswith("Shapes_")):
            containers.extend(
                obj for obj in sub_group.Group
                if obj.TypeId == "App::Part" and obj.Label.startswith("nested_")
            )
    return containers

def get_master_shapes_group(layout_group):
    """
    Gets the MasterShapes group of a layout.

    The label is matched by prefix, not equality: a document that already
    holds a MasterShapes group gets MasterShapes001, MasterShapes002, ... for
    every layout created after it.

    Args:
        layout_group: A Layout_* App::DocumentObjectGroup.

    Returns:
        The MasterShapes group object, or None.
    """
    if not layout_group or not hasattr(layout_group, "Group"):
        return None
    return next((c for c in layout_group.Group if c.Label.startswith("MasterShapes")), None)

def _set_visibility(obj, visible):
    """Sets an object's visibility, tolerating console mode and dead references."""
    try:
        view_object = getattr(obj, "ViewObject", None)
        if view_object is not None:
            view_object.Visibility = visible
    except (AttributeError, ReferenceError, RuntimeError) as e:
        nw_logger.debug(f"[freecad_helpers] visibility skipped (deleted object or no GUI): {e}")

def set_master_shapes_visible(layout_group, visible):
    """
    Shows or hides a layout's master shapes together with their boundary outlines.

    Toggling only the group is not enough. FreeCAD pushes a group's visibility
    down to its children at the moment the group changes, so a boundary that is
    switched on afterwards - by the placement highlighter or the Show Bounds
    checkbox - keeps rendering even though its group reads as hidden, and ghosts
    over the master row of the next run. Every level is set explicitly.

    Args:
        layout_group: A Layout_* App::DocumentObjectGroup.
        visible (bool): Target visibility for the whole master row.
    """
    master_shapes_group = get_master_shapes_group(layout_group)
    if not master_shapes_group:
        return

    _set_visibility(master_shapes_group, visible)
    for container in master_shapes_group.Group:
        _set_visibility(container, visible)
        for child in getattr(container, "Group", []):
            _set_visibility(child, visible)
            boundary = getattr(child, "BoundaryObject", None)
            if boundary:
                _set_visibility(boundary, visible)

def hide_all_master_shapes(doc, except_layout=None):
    """
    Hides the master row of every layout in the document.

    Args:
        doc: The FreeCAD document.
        except_layout: Optional layout group to leave untouched.
    """
    if not doc:
        return
    for obj in doc.Objects:
        if obj is except_layout:
            continue
        if obj.isDerivedFrom("App::DocumentObjectGroup") and obj.Label.startswith("Layout"):
            set_master_shapes_visible(obj, False)

def get_all_objects_recursive(group):
    """
    Recursively finds all leaf objects within a group and its subgroups.

    Args:
        group: The parent group object.

    Returns:
        List of all non-group objects found recursively.
    """
    all_objects = []
    for obj in group.Group:
        if obj.isDerivedFrom("App::DocumentObjectGroup"):
            all_objects.extend(get_all_objects_recursive(obj))
        else:
            all_objects.append(obj)
    return all_objects

def calculate_label_placement(shapestring_center, container_rotation, label_z_offset=0.1):
    """
    Calculates the local placement for a label centered on a part.
    
    Args:
        shapestring_center (FreeCAD.Vector): The center of the label's bound box.
        container_rotation (FreeCAD.Rotation): The rotation of the parent container.
        label_z_offset (float): Vertical offset above the part.
        
    Returns:
        FreeCAD.Placement: The local placement for the label.
    """
    inverse_rotation = container_rotation.inverted()
    target_label_center = FreeCAD.Vector(0, 0, label_z_offset)
    shapestring_center_rotated = inverse_rotation.multVec(shapestring_center)
    label_placement_base = target_label_center - shapestring_center_rotated
    return FreeCAD.Placement(label_placement_base, inverse_rotation)

def calculate_container_centroid(polygon, sheet_origin):
    """
    Calculates the target world position for a container based on the polygon's centroid.
    
    Args:
        polygon (shapely.geometry.Polygon): The nesting boundary polygon.
        sheet_origin (FreeCAD.Vector): The origin of the sheet.
        
    Returns:
        FreeCAD.Vector: The target world position.
    """
    nested_centroid_shapely = polygon.centroid
    nested_centroid = FreeCAD.Vector(nested_centroid_shapely.x, nested_centroid_shapely.y, 0)
    return sheet_origin + nested_centroid

def create_part_feature(doc, name, shape, group=None, visible=True):
    """
    Standardises Part Feature creation, shape assignment, grouping, and visibility.
    """
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    if group:
        group.addObject(obj)
    if hasattr(obj, "ViewObject") and obj.ViewObject:
        obj.ViewObject.Visibility = visible
    return obj


