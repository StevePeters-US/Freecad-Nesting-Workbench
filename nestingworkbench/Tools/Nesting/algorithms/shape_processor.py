# Nesting/nesting/shape_processor.py

"""
This module contains functions for processing FreeCAD shapes to prepare them
for the nesting algorithm. It handles extracting 2D profiles and creating
buffered boundaries.
"""

import FreeCAD
import Part


def _get_rotation_for_up_direction(up_direction):
    """
    Returns a FreeCAD.Rotation that transforms the given up_direction to Z+.
    
    Args:
        up_direction: One of "Z+", "Z-", "Y+", "Y-", "X+", "X-"
    
    Returns:
        FreeCAD.Rotation to apply to make the given direction point to Z+
    """
    if up_direction == "Z+" or up_direction is None:
        return FreeCAD.Rotation()  # Identity - no rotation needed
    elif up_direction == "Z-":
        return FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)  # Rotate 180° around X
    elif up_direction == "Y+":
        return FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -90)  # Rotate -90° around X
    elif up_direction == "Y-":
        return FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90)  # Rotate 90° around X
    elif up_direction == "X+":
        return FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 90)  # Rotate 90° around Y
    elif up_direction == "X-":
        return FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), -90)  # Rotate -90° around Y
    else:
        FreeCAD.Console.PrintWarning(f"Unknown up_direction '{up_direction}', using Z+\n")
        return FreeCAD.Rotation()


def get_2d_profile_from_obj(obj, up_direction="Z+"):
    """
    Extracts a usable 2D profile from a FreeCAD object by projecting it onto the XY plane.
    This captures the full silhouette of the shape from the specified viewing direction.
    
    Args:
        obj: FreeCAD object to extract profile from
        up_direction: Which direction should be treated as "up" when projecting to 2D.
                      One of "Z+", "Z-", "Y+", "Y-", "X+", "X-" (default: "Z+")
    """
    shape = obj.Shape
    
    # If we need to rotate the shape to align the up direction with Z+
    rotation = _get_rotation_for_up_direction(up_direction)
    needs_rotation = up_direction != "Z+" and up_direction is not None
    
    if needs_rotation:
        # Create a rotated copy of the shape
        center = shape.CenterOfMass
        placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), rotation, center)
        shape = shape.copy()
        shape.transformShape(placement.Matrix)
        FreeCAD.Console.PrintMessage(f"  -> Rotated shape for up_direction={up_direction}\n")

    # Special case for sketches - already 2D
    if obj.isDerivedFrom("Sketcher::SketchObject") and not needs_rotation:
        if shape.Wires:
            try:
                return Part.Face(shape.Wires[0])
            except Part.OCCError as e:
                raise ValueError(f"Could not create a face from the sketch '{obj.Label}': {e}")
        else:
            raise ValueError(f"Sketch '{obj.Label}' contains no wires to form a face.")

    # Try to find an existing XY-aligned planar face first (simpler and faster)
    if shape.Faces:
        bottom_face = None
        min_z = float('inf')
        
        for face in shape.Faces:
            try:
                if face.Surface.isPlanar():
                    normal = face.normalAt(0, 0)
                    tolerance = 0.01
                    is_parallel_to_xy = abs(abs(normal.z) - 1.0) < tolerance
                    
                    if is_parallel_to_xy:
                        if face.BoundBox.ZMin < min_z:
                            min_z = face.BoundBox.ZMin
                            bottom_face = face
            except:
                continue
        
        if bottom_face:
            return bottom_face
    
    # Fallback: Project the entire shape onto the XY plane to get a silhouette
    # This captures all features visible from the Z direction
    try:
        FreeCAD.Console.PrintMessage(f"  -> Using projection for '{obj.Label}'\n")
        
        # Project onto XY plane (Z direction)
        projection_dir = FreeCAD.Vector(0, 0, 1)
        
        # Create a projected wire using makeProjection
        projected_edges = []
        for edge in shape.Edges:
            try:
                # Project each edge onto XY plane
                proj = edge.makeParallelProjection(Part.Plane(), projection_dir)
                if proj and proj.Edges:
                    projected_edges.extend(proj.Edges)
            except:
                continue
        
        if projected_edges:
            # Try to create a face from the projected edges
            try:
                sorted_edges = Part.sortEdges(projected_edges)
                wires = [Part.Wire(edges) for edges in sorted_edges if edges]
                if wires:
                    # Use the largest wire as the outer boundary
                    outer_wire = max(wires, key=lambda w: Part.Face(w).Area if w.isClosed() else 0)
                    if outer_wire.isClosed():
                        return Part.Face(outer_wire)
            except:
                pass
        
        # Alternative: use BoundBox as last resort
        bb = shape.BoundBox
        points = [
            FreeCAD.Vector(bb.XMin, bb.YMin, 0),
            FreeCAD.Vector(bb.XMax, bb.YMin, 0),
            FreeCAD.Vector(bb.XMax, bb.YMax, 0),
            FreeCAD.Vector(bb.XMin, bb.YMax, 0),
            FreeCAD.Vector(bb.XMin, bb.YMin, 0)
        ]
        wire = Part.makePolygon(points)
        FreeCAD.Console.PrintWarning(f"  -> Using bounding box for '{obj.Label}'\n")
        return Part.Face(wire)
        
    except Exception as e:
        FreeCAD.Console.PrintError(f"  -> Projection failed: {e}\n")
    
    # If nothing worked
    raise ValueError(f"Unsupported object '{obj.Label}' or no valid 2D geometry found.")


def create_single_nesting_part(shape_to_populate, shape_obj, spacing, resolution=300, up_direction="Z+"):
    """
    Processes a FreeCAD object to generate a shapely-based boundary and populates
    the geometric properties of the provided Shape object. The created boundary is
    normalized to be centered at the origin (0,0), which simplifies placement
    calculations later.

    :param shape_to_populate: The Shape object to populate with geometry.
    :param shape_obj: The FreeCAD object to process.
    :param spacing: The spacing/buffer to add around the shape.
    :param resolution: Number of points for discretizing curves.
    :param up_direction: Which direction is "up" for 2D projection ("Z+", "Z-", "Y+", "Y-", "X+", "X-").
    """
    from ..nesting_logic import SHAPELY_AVAILABLE
    if not SHAPELY_AVAILABLE:
        raise ImportError("The shapely library is required for boundary creation but is not installed.")
    
    FreeCAD.Console.PrintMessage(f"Processing shape '{shape_obj.Label}'...\n")
    
    from shapely.geometry import Polygon, MultiPolygon
    from shapely.affinity import translate
    from shapely.validation import make_valid

    profile_2d = get_2d_profile_from_obj(shape_obj, up_direction)
    
    # The 2D profile's center - this is used for BOTH:
    # 1. Centering the polygon at origin
    # 2. Offsetting the rotated 3D shape inside the container
    # This ensures bounds and shape stay aligned
    source_centroid = profile_2d.CenterOfMass

    # Create a copy of the profile and move it to the origin.
    normalized_profile = profile_2d.copy()
    normalized_profile.translate(-source_centroid)
    
    outer_wire = normalized_profile.OuterWire
    if not outer_wire:
        raise ValueError("2D Profile has no outer wire.")

    # Discretize the wire to convert it into a series of points for Shapely.
    discretize_distance = outer_wire.Length / float(resolution)
    if discretize_distance < 1e-3:
        discretize_distance = 1e-3

    # --- Process Outer Wire ---
    points = [(v.x, v.y) for v in outer_wire.discretize(Distance=discretize_distance)]
    if len(points) < 3:
        raise ValueError("Not enough points in outer wire to form a polygon.")
    if points[0] != points[-1]:
        points.append(points[0])
    
    outer_polygon = Polygon(points)
    if not outer_polygon.is_valid:
        outer_polygon = make_valid(outer_polygon)
        if isinstance(outer_polygon, MultiPolygon):
             outer_polygon = max(outer_polygon.geoms, key=lambda p: p.area)
        if outer_polygon.geom_type != 'Polygon':
            raise ValueError("Outer wire did not produce a usable polygon.")

    # --- Process Inner Wires (Holes) ---
    inner_wires = [w for w in normalized_profile.Wires if not w.isSame(outer_wire)]
    hole_contours = []
    for inner_wire in inner_wires:
        hole_points = [(v.x, v.y) for v in inner_wire.discretize(Distance=discretize_distance)]
        if len(hole_points) < 3:
            continue
        if hole_points[0] != hole_points[-1]:
            hole_points.append(hole_points[0])
        
        hole_poly = Polygon(hole_points)
        if not hole_poly.is_valid:
            hole_poly = make_valid(hole_poly)
            if isinstance(hole_poly, MultiPolygon):
                hole_poly = max(hole_poly.geoms, key=lambda p: p.area)
        
        if hole_poly.is_valid and hole_poly.geom_type == 'Polygon':
             hole_contours.append(hole_poly.exterior.coords)

    # --- Create final polygon with holes ---
    final_polygon_unbuffered = Polygon(outer_polygon.exterior.coords, hole_contours)

    # Buffer the polygon for spacing.
    buffered_polygon = final_polygon_unbuffered.buffer(spacing / 2.0, join_style=1)
    
    # Simplify the buffered polygon to reduce vertex count.
    # We use the discretization distance as the tolerance.
    buffered_polygon = buffered_polygon.simplify(discretize_distance, preserve_topology=True)

    if buffered_polygon.is_empty:
         raise ValueError("Buffering operation did not produce a valid polygon.")

    # --- Post-processing to perfectly center all polygons at the origin ---
    # The buffering operation can shift the centroid of the resulting polygon.
    # For non-symmetrical shapes, this shift can be significant. We must re-center
    # both the buffered and unbuffered polygons so that their centroids are at (0,0).
    # This ensures that rotation operations during nesting behave predictably around the origin.
    buffered_centroid = buffered_polygon.centroid
    offset_from_origin = FreeCAD.Vector(buffered_centroid.x, buffered_centroid.y, 0)

    # Translate all polygons by the inverse of the buffered polygon's centroid.
    final_buffered_polygon = translate(buffered_polygon, xoff=-buffered_centroid.x, yoff=-buffered_centroid.y)
    final_unbuffered_polygon = translate(final_polygon_unbuffered, xoff=-buffered_centroid.x, yoff=-buffered_centroid.y)

    # --- Create the ShapeBounds object ---
    # The source_centroid is the pivot point for the final part placement.
    # It's the original geometry's centroid, adjusted by the offset that occurred
    # during buffering and re-centering. This ensures the final part rotates around
    # its true geometric center.
    shape_to_populate.polygon = final_buffered_polygon
    shape_to_populate.original_polygon = final_buffered_polygon
    shape_to_populate.spacing = spacing
    shape_to_populate.resolution = float(resolution)
    shape_to_populate.unbuffered_polygon = final_unbuffered_polygon
    shape_to_populate.source_centroid = source_centroid + offset_from_origin
