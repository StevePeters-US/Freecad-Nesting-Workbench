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
    # Get shape in world coordinates (apply source object's placement)
    shape = obj.Shape.copy()
    if obj.Placement and not obj.Placement.isIdentity():
        shape.transformShape(obj.Placement.Matrix)
    
    # If we need to rotate the shape to align the up direction with Z+
    rotation = _get_rotation_for_up_direction(up_direction)
    needs_rotation = up_direction != "Z+" and up_direction is not None
    
    if needs_rotation:
        # Rotate the shape around its center
        center = shape.CenterOfMass
        placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), rotation, center)
        shape.transformShape(placement.Matrix)
        FreeCAD.Console.PrintMessage(f"  -> Rotated shape for up_direction={up_direction}\n")
    
    # Always center the shape using bounding box center (for both rotated and non-rotated)
    bb = shape.BoundBox
    translation = FreeCAD.Vector(
        -(bb.XMin + bb.XMax) / 2,
        -(bb.YMin + bb.YMax) / 2,
        -(bb.ZMin + bb.ZMax) / 2
    )
    shape.translate(translation)

    # Special case for sketches - already 2D
    if obj.isDerivedFrom("Sketcher::SketchObject") and not needs_rotation:
        if shape.Wires:
            try:
                return Part.Face(shape.Wires[0])
            except Part.OCCError as e:
                raise ValueError(f"Could not create a face from the sketch '{obj.Label}': {e}")
        else:
            raise ValueError(f"Sketch '{obj.Label}' contains no wires to form a face.")

    # Convert shape to mesh and project all mesh vertices onto XY plane
    try:
        FreeCAD.Console.PrintMessage(f"  -> Meshing shape for '{obj.Label}'\n")
        
        from shapely.geometry import MultiPoint, LineString, Polygon as ShapelyPolygon
        
        # Tessellate the shape to get mesh vertices
        # This handles curved surfaces by creating triangle vertices
        mesh = shape.tessellate(0.5)  # tolerance in mm
        vertices = mesh[0]  # List of (x, y, z) tuples
        
        if len(vertices) >= 3:
            # Project all mesh vertices to XY plane
            points_2d = [(v[0], v[1]) for v in vertices]
            
            # Create convex hull from all projected points
            multi_point = MultiPoint(points_2d)
            hull = multi_point.convex_hull
            
            # Handle degenerate cases (thin shapes become LineString)
            if isinstance(hull, LineString):
                # Buffer the line to create a thin polygon
                hull = hull.buffer(0.1)  # 0.1mm thickness
                FreeCAD.Console.PrintMessage(f"  -> Thin shape detected, buffering line\n")
            
            if hasattr(hull, 'exterior') and hull.is_valid:
                coords = list(hull.exterior.coords)
                if len(coords) >= 4:  # Need at least 4 points (3 + closing point)
                    # Create a FreeCAD face from the hull
                    fc_points = [FreeCAD.Vector(x, y, 0) for x, y in coords]
                    wire = Part.makePolygon(fc_points)
                    return Part.Face(wire)
        
        # Fallback: use BoundBox
        FreeCAD.Console.PrintWarning(f"  -> Using bounding box for '{obj.Label}'\n")
        bb = shape.BoundBox
        points = [
            FreeCAD.Vector(bb.XMin, bb.YMin, 0),
            FreeCAD.Vector(bb.XMax, bb.YMin, 0),
            FreeCAD.Vector(bb.XMax, bb.YMax, 0),
            FreeCAD.Vector(bb.XMin, bb.YMax, 0),
            FreeCAD.Vector(bb.XMin, bb.YMin, 0)
        ]
        wire = Part.makePolygon(points)
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
    
    # The profile is already centered at origin (done in get_2d_profile_from_obj)
    # We need to compute the original world-space BB center for shape_preparer to use
    # Get the shape in world coordinates (same as get_2d_profile_from_obj does)
    temp_shape = shape_obj.Shape.copy()
    if shape_obj.Placement and not shape_obj.Placement.isIdentity():
        temp_shape.transformShape(shape_obj.Placement.Matrix)
    
    # Apply rotation if needed
    if up_direction != "Z+" and up_direction is not None:
        rotation = _get_rotation_for_up_direction(up_direction)
        center = temp_shape.CenterOfMass
        placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), rotation, center)
        temp_shape.transformShape(placement.Matrix)
    
    # Now get BB center - this is what shape_preparer should use
    bb = temp_shape.BoundBox
    source_centroid = FreeCAD.Vector(
        (bb.XMin + bb.XMax) / 2,
        (bb.YMin + bb.YMax) / 2,
        (bb.ZMin + bb.ZMax) / 2
    )
    
    # Profile is already centered, just use it directly
    outer_wire = profile_2d.OuterWire
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
    inner_wires = [w for w in profile_2d.Wires if not w.isSame(outer_wire)]
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
