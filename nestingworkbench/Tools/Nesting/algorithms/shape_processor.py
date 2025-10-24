# Nesting/nesting/shape_processor.py

"""
This module contains functions for processing FreeCAD shapes to prepare them
for the nesting algorithm. It handles extracting 2D profiles and creating
buffered boundaries.
"""

import FreeCAD
import Part
import math
from ....datatypes.vert_state import VertState
from .... import utils # Using the new utils file for helper functions

def get_2d_profile_from_obj(obj):
    """
    Extracts a usable 2D face profile from various FreeCAD object types,
    including sketches, bodies, and imported SVG geometry.
    """
    shape = obj.Shape

    # Case 1: PartDesign Body - Find the bottom-most planar face parallel to the XY plane
    if obj.isDerivedFrom("PartDesign::Body"):
        bottom_face = None
        min_z = float('inf')
        for face in shape.Faces:
            if face.Surface.isPlanar():
                normal = face.normalAt(0, 0)
                # Check if normal is parallel to Z-axis (up or down)
                is_parallel_to_xy = normal.isEqual(FreeCAD.Vector(0, 0, -1), 1e-6) or \
                                    normal.isEqual(FreeCAD.Vector(0, 0, 1), 1e-6)
                if is_parallel_to_xy:
                    if face.BoundBox.ZMin < min_z:
                        min_z = face.BoundBox.ZMin
                        bottom_face = face
        if bottom_face:
            return bottom_face
        else:
            raise ValueError(f"Could not find a suitable bottom face on Body '{obj.Label}'.")

    # Case 2: Sketch - Create a face directly from the sketch's wire
    elif obj.isDerivedFrom("Sketcher::SketchObject"):
        if shape.Wires:
            try:
                return Part.Face(shape.Wires[0])
            except Part.OCCError as e:
                 raise ValueError(f"Could not create a face from the sketch '{obj.Label}': {e}")
        else:
            raise ValueError(f"Sketch '{obj.Label}' contains no wires to form a face.")

    # Case 3: Generic shapes (like imported SVGs or other Part features)
    # If it already has a planar face, use the first one found.
    if shape.Faces:
        for face in shape.Faces:
            if face.Surface.isPlanar():
                return face # Return the first valid planar face

    # If it has no faces but has wires (common for imported SVGs), try to build a face.
    if shape.Wires:
        try:
            # We assume the first wire is the outer boundary
            return Part.Face(shape.Wires[0])
        except Part.OCCError as e:
            raise ValueError(f"Could not create a face from the wire of '{obj.Label}': {e}")

    # If no suitable geometry can be found after all checks
    raise ValueError(f"Unsupported object '{obj.Label}' or no valid 2D geometry found.")


def create_single_nesting_part(shape_obj, spacing, resolution=75, part_grid_resolution=10):
    """
    Creates one ShapeBounds boundary from a single FreeCAD object using Shapely.
    The created boundary is normalized to be centered at the origin (0,0),
    which simplifies placement calculations later in the process.
    """
    from ....datatypes.shape_bounds import ShapeBounds
    from ..nesting_logic import SHAPELY_AVAILABLE
    if not SHAPELY_AVAILABLE:
        raise ImportError("The shapely library is required for boundary creation but is not installed.")
        
    from shapely.geometry import Polygon, MultiPolygon
    from shapely.affinity import translate
    from shapely.validation import make_valid

    profile_2d = get_2d_profile_from_obj(shape_obj)
    
    # Store the original centroid position before normalizing. This will be the
    # rotation center for the final placement.
    source_centroid = profile_2d.CenterOfMass

    # Create a copy of the profile and move it to the origin.
    # All subsequent operations are performed on this normalized profile.
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
    shape_bounds = ShapeBounds()
    shape_bounds.polygon = final_buffered_polygon
    shape_bounds.original_polygon = final_buffered_polygon
    shape_bounds.unbuffered_polygon = final_unbuffered_polygon
    shape_bounds.source_centroid = source_centroid + offset_from_origin
    
    # Populate the grid representation of the shape
    populate_shape_bounds_grid(shape_bounds, part_grid_resolution)

    return shape_bounds

def populate_shape_bounds_grid(shape_bounds, grid_resolution):
    """
    Populates a 2D grid representing the shape's occupancy.

    Args:
        shape_bounds (ShapeBounds): The shape bounds object to populate.
        grid_resolution (float): The distance between grid points.
    """
    from shapely.geometry import Point
    from shapely.affinity import translate

    polygon = shape_bounds.unbuffered_polygon
    if not polygon or polygon.is_empty or grid_resolution <= 0:
        shape_bounds.shape_bounds_grid = []
        return

    # The unbuffered_polygon may have a slight offset from the origin due to the
    # re-centering of the buffered polygon. To ensure the grid is generated from a
    # perfectly origin-centered shape, we translate it by its own centroid.
    polygon = translate(polygon, xoff=-polygon.centroid.x, yoff=-polygon.centroid.y)

    min_x, min_y, max_x, max_y = polygon.bounds

    # Calculate grid dimensions
    cols = int(math.ceil((max_x - min_x) / grid_resolution)) + 1
    rows = int(math.ceil((max_y - min_y) / grid_resolution)) + 1

    grid = [[VertState.EMPTY for _ in range(cols)] for _ in range(rows)]

    # A small tolerance for boundary checks
    tolerance = 1e-6

    for r in range(rows):
        for c in range(cols):
            # Calculate the real-world coordinate of the grid point
            px = min_x + c * grid_resolution
            py = min_y + r * grid_resolution
            point = Point(px, py)

            if polygon.contains(point):
                grid[r][c] = VertState.FILLED
            elif polygon.boundary.distance(point) < tolerance:
                grid[r][c] = VertState.EDGE

    shape_bounds.shape_bounds_grid = grid
