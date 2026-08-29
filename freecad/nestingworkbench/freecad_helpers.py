# SPDX-License-Identifier: LGPL-2.1-or-later
# nestingworkbench/freecad_helpers.py

"""
Shared utility functions for FreeCAD operations used across the Nesting Workbench.
Consolidates common logic that was previously duplicated in multiple modules.
"""

import FreeCAD

def get_up_direction_rotation(up_direction):
    """
    Returns a FreeCAD.Rotation that transforms the given up_direction to Z+.

    Args:
        up_direction: One of "Z+", "Z-", "Y+", "Y-", "X+", "X-", or None.

    Returns:
        FreeCAD.Rotation to apply to make the given direction point to Z+.
        Returns identity rotation for Z+ or None.
    """
    if up_direction == "Z+" or up_direction is None:
        return FreeCAD.Rotation()  # Identity - no rotation needed
    elif up_direction == "Z-":
        return FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
    elif up_direction == "Y+":
        return FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -90)
    elif up_direction == "Y-":
        return FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90)
    elif up_direction == "X+":
        return FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 90)
    elif up_direction == "X-":
        return FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), -90)
    else:
        FreeCAD.Console.PrintWarning(f"Unknown up_direction '{up_direction}', using Z+\n")
        return FreeCAD.Rotation()

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
    except Exception:
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
    except Exception:
        pass  # Already deleted

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


