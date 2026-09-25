# SPDX-License-Identifier: LGPL-2.1-or-later
# nestingworkbench/Tools/Silhouette/silhouette_creator.py

"""
Creates 2D silhouette faces from 3D objects.

Supports two methods:
1. Cross-section: Cuts the part with a plane at a specified Z height (simpler, more reliable)
2. Projection: Projects the full 3D shape onto the XY plane (for complex shapes)
"""

import FreeCAD
import Part
from ..Nesting.algorithms.shape_processor import get_2d_profile_from_obj
from ...freecad_helpers import get_nested_containers

SILHOUETTE_COLOR = (0.2, 0.6, 1.0)   # light blue
SILHOUETTE_TRANSPARENCY = 50
SILHOUETTE_LINE_WIDTH = 2.0

def _apply_silhouette_style(view_object):
    view_object.ShapeColor = SILHOUETTE_COLOR
    view_object.Transparency = SILHOUETTE_TRANSPARENCY
    view_object.LineWidth = SILHOUETTE_LINE_WIDTH

def create_cross_section(obj, cut_height=None):
    """Cut *obj* with a horizontal plane at *cut_height* (Z height, None=midpoint), returning a 2D Face."""
    try:
        shape = obj.Shape
        
        if shape.isNull():
            FreeCAD.Console.PrintWarning(f"[CrossSection] Shape is null for '{obj.Label}'\n")
            return None
        
        # Get bounding box to determine cut height
        bbox = shape.BoundBox
        
        if cut_height is None:
            # Default to midpoint of the part's height
            cut_height = (bbox.ZMin + bbox.ZMax) / 2.0
        
        # Create a cutting plane and slice
        cutting_direction = FreeCAD.Vector(0, 0, 1)  # Normal to XY plane
        wires = shape.slice(cutting_direction, cut_height)
        
        if not wires:
            FreeCAD.Console.PrintWarning(f"[Silhouette] No cross-section at Z={cut_height:.2f} for '{obj.Label}'\n")
            return None
        
        # Convert wires to faces
        faces = []
        for wire in wires:
            if wire.isClosed():
                try:
                    face = Part.Face(wire)
                    faces.append(face)
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"[CrossSection] Could not make face from wire: {e}\n")
        
        if not faces:
            FreeCAD.Console.PrintWarning(f"[CrossSection] No closed wires found for '{obj.Label}'\n")
            return None
        
        # Sort faces by Area descending
        faces.sort(key=lambda f: getattr(f, "Area", 0.0), reverse=True)
        
        # Separate outer boundaries from holes (handling multiple disjoint bodies)
        outer_faces = []
        for face in faces:
            bb = face.BoundBox
            is_hole = False
            for idx, outer in enumerate(outer_faces):
                outer_bb = outer.BoundBox
                if (bb.XMin >= outer_bb.XMin - 1e-4 and bb.XMax <= outer_bb.XMax + 1e-4 and
                    bb.YMin >= outer_bb.YMin - 1e-4 and bb.YMax <= outer_bb.YMax + 1e-4):
                    try:
                        # BoundBox containment is only a pre-filter: a separate body can
                        # sit inside another's bbox (an island in a hole, a piece in a
                        # U-notch). Slice wires never cross, so a face lies either wholly
                        # on the outer's material (a hole) or wholly off it (a body).
                        if outer.common(face).Area < 0.5 * face.Area:
                            continue
                        outer_faces[idx] = outer.cut(face)
                        is_hole = True
                        break
                    except Exception as e:
                        # Can't tell hole from island without the boolean op, and
                        # appending the face as a body would fill the hole in.
                        FreeCAD.Console.PrintError(
                            f"[CrossSection] Could not cut a hole out of '{obj.Label}': {e}. "
                            f"No silhouette created rather than one with the hole filled in.\n"
                        )
                        return None
            if not is_hole:
                outer_faces.append(face)
        
        if not outer_faces:
            return None
        elif len(outer_faces) == 1:
            result = outer_faces[0]
        else:
            result = Part.Compound(outer_faces)
        
        # Move the result to Z=0
        result.translate(FreeCAD.Vector(0, 0, -cut_height))
        
        return result
        
    except Exception as e:
        FreeCAD.Console.PrintError(f"[CrossSection] Error for '{obj.Label}': {e}\n")
        return None

def is_valid_shape_object(obj):
    """Check if *obj* is a valid non-empty Part::Feature; returns (is_valid, reason)."""
    # Check if object exists
    if obj is None:
        return False, "Object is None"
    
    # Check for Shape attribute
    if not hasattr(obj, "Shape"):
        return False, f"No Shape attribute (type: {obj.TypeId})"
    
    # Check if Shape is null/empty
    if obj.Shape.isNull():
        return False, "Shape is null"
    
    # Check for groups/containers that may have a Shape but aren't geometric objects
    group_types = [
        "App::DocumentObjectGroup",
        "App::Part",
        "App::Origin",
        "App::Line",
        "App::Plane"
    ]
    if obj.TypeId in group_types:
        return False, f"Object is a container/group ({obj.TypeId})"
    
    # Check if shape has actual geometry (faces, edges, or solids)
    shape = obj.Shape
    if not (shape.Faces or shape.Edges or shape.Solids):
        return False, "Shape has no geometry (no faces, edges, or solids)"
    
    return True, "Valid"

def create_silhouette(obj, up_direction="Z+"):
    """Project *obj* onto the XY plane along *up_direction* (e.g. 'Z+', 'Y-'), returning a 2D Face."""
    try:
        # Use existing projection function to get Shapely polygon
        shapely_polygon = get_2d_profile_from_obj(obj, up_direction)
        
        if shapely_polygon is None or shapely_polygon.is_empty:
            FreeCAD.Console.PrintError(f"Failed to create silhouette for '{obj.Label}': Empty projection\n")
            return None
        
        # Convert Shapely polygon to FreeCAD face
        face = shapely_to_fc_face(shapely_polygon)
        return face
        
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to create silhouette for '{obj.Label}': {e}\n")
        return None

def shapely_to_fc_face(shapely_polygon):
    """Convert a shapely.geometry.Polygon (with or without holes) to a FreeCAD Face."""
    from shapely.geometry import Polygon
    
    if not isinstance(shapely_polygon, Polygon):
        raise ValueError(f"Expected Polygon, got {type(shapely_polygon)}")
    
    # Create outer wire from exterior coordinates
    exterior_coords = list(shapely_polygon.exterior.coords)
    outer_points = [FreeCAD.Vector(x, y, 0) for x, y in exterior_coords]
    
    if len(outer_points) < 3:
        raise ValueError("Not enough points to create face")
    
    # Close the wire if needed
    if outer_points[0] != outer_points[-1]:
        outer_points.append(outer_points[0])
    
    # Create outer wire
    outer_wire = Part.makePolygon(outer_points)
    
    # Create wires for holes (interior rings)
    hole_wires = []
    for interior in shapely_polygon.interiors:
        hole_coords = list(interior.coords)
        hole_points = [FreeCAD.Vector(x, y, 0) for x, y in hole_coords]
        if len(hole_points) >= 3:
            if hole_points[0] != hole_points[-1]:
                hole_points.append(hole_points[0])
            hole_wires.append(Part.makePolygon(hole_points))
    
    # Create face from outer wire
    face = Part.Face(outer_wire)
    
    # Cut out holes if any
    if hole_wires:
        for hole_wire in hole_wires:
            try:
                hole_face = Part.Face(hole_wire)
                face = face.cut(hole_face)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Could not cut hole: {e}\n")
    
    return face


def _find_valid_part_in_container(container):
    """Find the valid part_* child object inside a container."""
    if hasattr(container, "Group"):
        for child in container.Group:
            if child.Label.startswith("part_"):
                is_valid, _ = is_valid_shape_object(child)
                if is_valid:
                    return child
    return None

def _compute_silhouette_face(part_obj, cut_height=None, method="cross_section"):
    """Compute the silhouette face using either cross_section or projection."""
    if method == "cross_section":
        return create_cross_section(part_obj, cut_height)
    return create_silhouette(part_obj)

def _create_silhouette_object(doc, label_base, silhouette_face, parent_container=None):
    """Create and style a silhouette Part::Feature, optionally adding to a container."""
    silhouette_obj = doc.addObject("Part::Feature", f"outline_{label_base}")
    silhouette_obj.Shape = silhouette_face
    silhouette_obj.Placement = FreeCAD.Placement()
    if hasattr(silhouette_obj, "ViewObject"):
        _apply_silhouette_style(silhouette_obj.ViewObject)
    if parent_container and hasattr(parent_container, "addObject"):
        parent_container.addObject(silhouette_obj)
    return silhouette_obj

def create_silhouettes_for_layout(doc, layout_group, cut_height=None, method="cross_section"):
    """
    Creates silhouettes for all placed parts in a Layout group.
    
    Silhouettes are placed INSIDE each nested container (App::Part) alongside the part.
    
    Args:
        doc: The FreeCAD document
        layout_group: The Layout DocumentObjectGroup
        cut_height: For cross-section method, Z height to cut at. None = midpoint of each part.
        method: "cross_section" (default) or "projection"
        
    Returns:
        list: All created silhouette objects
    """
    all_silhouettes = []
    sheets_processed = set()
    
    FreeCAD.Console.PrintMessage(f"[Silhouette] Processing Layout '{layout_group.Label}'...\n")
    
    # Traverse layout → sheets → shapes groups → containers
    for sheet_group in layout_group.Group:
        if not sheet_group.isDerivedFrom("App::DocumentObjectGroup"):
            continue
        if not sheet_group.Label.startswith("Sheet_"):
            continue
        
        sheets_processed.add(sheet_group.Label)
        
        for container in get_nested_containers(sheet_group):
            # Check for existing outline_* objects to remove them
            existing_outlines = []
            if hasattr(container, "Group"):
                for child in container.Group:
                    if child.Label.startswith("outline_"):
                        existing_outlines.append(child)
            
            # Remove existing silhouettes before creating new ones
            for old_outline in existing_outlines:
                try:
                    doc.removeObject(old_outline.Name)
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"[Silhouette] Could not remove old outline '{old_outline.Label}': {e}\n")
            
            part_obj = _find_valid_part_in_container(container)
            if part_obj is None:
                continue
            
            try:
                silhouette_face = _compute_silhouette_face(part_obj, cut_height, method)
                if silhouette_face is None:
                    FreeCAD.Console.PrintWarning(f"[Silhouette] Could not create silhouette for '{container.Label}'\n")
                    continue
                
                silhouette_obj = _create_silhouette_object(doc, container.Label, silhouette_face, container)
                all_silhouettes.append(silhouette_obj)
            except Exception as e:
                FreeCAD.Console.PrintError(f"[Silhouette] Error for '{container.Label}': {e}\n")
                continue
    
    FreeCAD.Console.PrintMessage(f"[Silhouette] Created {len(all_silhouettes)} silhouettes across {len(sheets_processed)} sheets\n")
    
    return all_silhouettes

def is_nested_container(obj):
    """
    Check if an object is a nested part container (App::Part like nested_Side_1).
    
    Args:
        obj: FreeCAD object to check
        
    Returns:
        bool: True if it's a nested container
    """
    if obj.TypeId != "App::Part":
        return False
    # Nested containers from nesting start with "nested_"
    return obj.Label.startswith("nested_")

def create_silhouette_for_container(doc, container, cut_height=None, method="cross_section"):
    """Create a silhouette for the part inside *container* (placed inside alongside the part)."""
    if not is_nested_container(container):
        FreeCAD.Console.PrintWarning(f"[Silhouette] '{container.Label}' is not a nested container\n")
        return None
    
    part_obj = _find_valid_part_in_container(container)
    if part_obj is None:
        FreeCAD.Console.PrintWarning(f"[Silhouette] No part object found in '{container.Label}'\n")
        return None
    
    silhouette_face = _compute_silhouette_face(part_obj, cut_height, method)
    if silhouette_face is None:
        FreeCAD.Console.PrintWarning(f"[Silhouette] Could not create silhouette for '{container.Label}'\n")
        return None
    
    return _create_silhouette_object(doc, container.Label, silhouette_face, container)

def create_silhouette_for_part(doc, part_obj, parent_container=None, cut_height=None, method="cross_section"):
    """Create a silhouette for *part_obj*, placed in *parent_container* or at document root."""
    is_valid, reason = is_valid_shape_object(part_obj)
    if not is_valid:
        FreeCAD.Console.PrintWarning(f"[Silhouette] '{part_obj.Label}' is not valid: {reason}\n")
        return None
    
    silhouette_face = _compute_silhouette_face(part_obj, cut_height, method)
    if silhouette_face is None:
        FreeCAD.Console.PrintWarning(f"[Silhouette] Could not create silhouette for '{part_obj.Label}'\n")
        return None
    
    return _create_silhouette_object(doc, part_obj.Label, silhouette_face, parent_container)

