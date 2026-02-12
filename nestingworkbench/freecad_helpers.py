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
