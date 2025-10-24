# Nesting/nesting/drawing_utils.py

"""
This module contains shared utility functions for drawing objects in FreeCAD.
"""

import FreeCAD
import Part

def draw_polygon_boundary(doc, polygon, name_prefix, group):
    """
    Draws the exterior and interior boundaries of a Shapely polygon in FreeCAD.

    Args:
        doc (FreeCAD.Document): The active document.
        polygon (shapely.geometry.Polygon): The polygon to draw.
        name_prefix (str): A unique prefix for naming the created FreeCAD objects.
        group (App.DocumentObjectGroup): The group to add the new objects to.
    """
    if not polygon:
        return

    # Draw exterior
    exterior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in polygon.exterior.coords]
    if len(exterior_verts) > 2:
        bound_wire = Part.makePolygon(exterior_verts)
        bound_obj = doc.addObject("Part::Feature", f"{name_prefix}_ext")
        bound_obj.Shape = bound_wire
        group.addObject(bound_obj)
        if FreeCAD.GuiUp:
            bound_obj.ViewObject.LineColor = (1.0, 0.0, 0.0) # Red for bounds

    # Draw interiors (holes)
    for i, interior in enumerate(polygon.interiors):
        interior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in interior.coords]
        if len(interior_verts) > 2:
            hole_wire = Part.makePolygon(interior_verts)
            hole_obj = doc.addObject("Part::Feature", f"{name_prefix}_int_{i}")
            hole_obj.Shape = hole_wire
            group.addObject(hole_obj)
            if FreeCAD.GuiUp:
                hole_obj.ViewObject.LineColor = (1.0, 0.0, 0.0) # Red for bounds

def create_face_from_polygon(polygon):
    """
    Creates a FreeCAD Part.Face from a Shapely Polygon, including holes.

    Args:
        polygon (shapely.geometry.Polygon): The polygon to convert.

    Returns:
        Part.Face: The resulting FreeCAD face, or None if conversion fails.
    """
    if not polygon or polygon.is_empty:
        return None

    # Create outer wire
    exterior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in polygon.exterior.coords]
    if len(exterior_verts) < 4: # A polygon needs at least 3 points + closing point
        return None
    outer_wire = Part.makePolygon(exterior_verts)
    try:
        face = Part.Face(outer_wire)
    except Part.OCCError:
        # If the outer wire cannot form a face, we cannot proceed.
        return None

    # Create inner wires (holes)
    for interior in polygon.interiors:
        interior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in interior.coords]
        if len(interior_verts) >= 4:
            hole_wire = Part.makePolygon(interior_verts)
            try:
                hole_face = Part.Face(hole_wire)
                face = face.cut(hole_face)
            except Part.OCCError:
                FreeCAD.Console.PrintWarning("Could not create or cut a hole for debug union shape.\n")

    return face