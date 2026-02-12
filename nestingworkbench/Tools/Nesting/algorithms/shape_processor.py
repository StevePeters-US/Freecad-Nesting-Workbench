# Nesting/nesting/shape_processor.py

"""
This module contains functions for processing FreeCAD shapes to prepare them
for the nesting algorithm. It handles extracting 2D profiles and creating
buffered boundaries.
"""

import FreeCAD
import Part
from ....freecad_helpers import get_up_direction_rotation




def get_2d_profile_from_obj(obj, up_direction="Z+", tessellation_quality=0.1, simplification=1.0):
    """
    Extracts a usable 2D profile from a FreeCAD object by projecting it onto the XY plane.
    This captures the full silhouette of the shape from the specified viewing direction.
    
    Args:
        obj: FreeCAD object to extract profile from
        up_direction: Which direction should be treated as "up" when projecting to 2D.
                      One of "Z+", "Z-", "Y+", "Y-", "X+", "X-" (default: "Z+")
        tessellation_quality: Max deviation for meshing (mm).
        simplification: Tolerance for simplifying the polygon (mm). Applied early to reduce point count.
    """
    # Get shape in world coordinates (apply source object's placement)
    shape = obj.Shape.copy()
    if obj.Placement and not obj.Placement.isIdentity():
        shape.transformShape(obj.Placement.Matrix)
    
    # If we need to rotate the shape to align the up direction with Z+
    rotation = get_up_direction_rotation(up_direction)
    needs_rotation = up_direction != "Z+" and up_direction is not None
    
    if needs_rotation:
        # Rotate the shape around its center
        # Use BoundBox center to be safe for Compounds
        bb = shape.BoundBox
        center = FreeCAD.Vector(
            (bb.XMin + bb.XMax) / 2,
            (bb.YMin + bb.YMax) / 2,
            (bb.ZMin + bb.ZMax) / 2
        )
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
                # Discretize sketch wire to points
                w = shape.Wires[0]
                # Default deflection for sketches?
                pts = [(v.x, v.y) for v in w.discretize(Deflection=0.01)] 
                if len(pts) > 2:
                     from shapely.geometry import Polygon as ShapelyPolygon
                     if pts[0] != pts[-1]: pts.append(pts[0])
                     return ShapelyPolygon(pts)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Could not convert sketch '{obj.Label}' to polygon: {e}\n")
        
        raise ValueError(f"Sketch '{obj.Label}' contains no usable wires.")

    # Convert shape to mesh and project all mesh vertices onto XY plane
    try:
        FreeCAD.Console.PrintMessage(f"  -> Meshing shape for '{obj.Label}'\n")
        
        from shapely.geometry import MultiPoint, LineString, Polygon as ShapelyPolygon, MultiPolygon
        
        # Tessellate the shape to get mesh vertices
        # This handles curved surfaces by creating triangle vertices
        # Use finer mesh for better resolution as requested (0.1mm instead of 0.5mm)
        mesh = shape.tessellate(tessellation_quality)
        vertices = mesh[0]  # List of (x, y, z) tuples
        
        if len(vertices) >= 3 and hasattr(mesh[1], '__iter__'):
            # mesh[0] is vertices, mesh[1] is facets (indices of triangles)
            # We want to create a union of all projected triangles to preserve concavity
            from shapely.ops import unary_union
            from shapely.geometry import GeometryCollection
            
            polygons = []
            facets = mesh[1]

            # Process all facets
            for facet in facets:
                # facet is a tuple of vertex indices (v1, v2, v3)
                p1 = vertices[facet[0]]
                p2 = vertices[facet[1]]
                p3 = vertices[facet[2]]
                
                # Round coordinates to avoid precision issues with coincident edges
                # REMOVED rounding to support high-resolution meshes (avoid collapsing micro-triangles)
                p1_xy = (p1[0], p1[1])
                p2_xy = (p2[0], p2[1])
                p3_xy = (p3[0], p3[1])

                # Create triangle polygon
                # Check for degenerate triangles (collinear points)
                poly = ShapelyPolygon([p1_xy, p2_xy, p3_xy])
                
                # Buffer(0) helps fix self-intersection or invalidity issues
                if poly.is_valid and not poly.is_empty and poly.area > 1e-9:
                     cleaned = poly.buffer(0)
                     if not cleaned.is_empty:
                         if isinstance(cleaned, ShapelyPolygon):
                             polygons.append(cleaned)
                         elif isinstance(cleaned, MultiPolygon):
                             polygons.extend(cleaned.geoms)

            if polygons:
                try:
                    # Union of all triangles
                    # buffer(0) on the whole set can sometimes handle overlaps better than unary_union alone?
                    # But unary_union is designed for this.
                    FreeCAD.Console.PrintMessage(f"  -> Merging {len(polygons)} triangles for '{obj.Label}'\n")
                    merged = unary_union(polygons)
                    
                    # Sometimes simple unions result in messy collections. Clean up.
                    if hasattr(merged, "is_valid") and not merged.is_valid:
                        merged = merged.buffer(0)

                    valid_polys = []
                    # Unpack result
                    if isinstance(merged, (ShapelyPolygon, MultiPolygon)):
                         pass # handled below
                    elif isinstance(merged, GeometryCollection):
                         # Extract only polygons from collection
                         for g in merged.geoms:
                             if isinstance(g, (ShapelyPolygon, MultiPolygon)):
                                 if isinstance(g, MultiPolygon):
                                     valid_polys.extend(g.geoms)
                                 else:
                                     valid_polys.append(g)
                         merged = unary_union(valid_polys) if valid_polys else None

                    if merged and not merged.is_empty:
                        # If result is MultiPolygon, take largest (most likely the body)
                        # Or if they are disjoint islands, we might need all?
                        # Usually for a single part, islands are valid (e.g. O has a hole, but islands?)
                        # If we have islands, we should probably output a Face with multiple disjoint wires.
                        # For now, let's assume one main body like typical CNC parts.
                        if isinstance(merged, MultiPolygon):
                             merged = max(merged.geoms, key=lambda p: p.area)
                        
                        if isinstance(merged, ShapelyPolygon):
                            if hasattr(merged, 'exterior') and merged.is_valid:
                                # Apply EARLY simplification to reduce point count before buffering
                                # This is crucial for performance - the merged polygon from triangles
                                # can have thousands of points which makes later operations very slow.
                                if simplification > 0:
                                    pre_simplify = len(merged.exterior.coords)
                                    merged = merged.simplify(simplification, preserve_topology=True)
                                    post_simplify = len(merged.exterior.coords)
                                    FreeCAD.Console.PrintMessage(f"  -> Early simplify: {pre_simplify} -> {post_simplify} vertices\n")
                                
                                # RETURN SHAPELY POLYGON DIRECTLY
                                # This preserves high-resolution detail without FreeCAD wire conversion limits
                                return merged
                except Exception as union_e:
                    FreeCAD.Console.PrintWarning(f"  -> Union failed for '{obj.Label}': {union_e}. Falling back to convex hull.\n")

        # Fallback if no facets (e.g. only vertices?) or union failed: use Convex Hull of vertices
        FreeCAD.Console.PrintMessage(f"  -> Fallback to convex hull for '{obj.Label}'\n")
        points_2d = [(v[0], v[1]) for v in vertices]
        multi_point = MultiPoint(points_2d)
        hull = multi_point.convex_hull

        if isinstance(hull, LineString):
             hull = hull.buffer(0.1)

        if hasattr(hull, 'exterior') and hull.is_valid:
             return hull
        
        # Absolute Fallback: use BoundBox
        FreeCAD.Console.PrintWarning(f"  -> Using bounding box for '{obj.Label}'\n")
        bb = shape.BoundBox
        return shapely.geometry.box(bb.XMin, bb.YMin, bb.XMax, bb.YMax)
        
    except Exception as e:
        FreeCAD.Console.PrintError(f"  -> Projection failed: {e}\n")
    
    # If nothing worked
    raise ValueError(f"Unsupported object '{obj.Label}' or no valid 2D geometry found.")


def create_single_nesting_part(shape_to_populate, shape_obj, spacing, deflection=0.05, simplification=1.0, up_direction="Z+"):
    """
    Processes a FreeCAD object to generate a shapely-based boundary and populates
    the geometric properties of the provided Shape object. The created boundary is
    normalized to be centered at the origin (0,0), which simplifies placement
    calculations later.

    :param shape_to_populate: The Shape object to populate with geometry.
    :param shape_obj: The FreeCAD object to process.
    :param spacing: The spacing/buffer to add around the shape.
    :param deflection: Max deviation for curve creation (mm).
    :param simplification: Tolerance for smoothing (mm).
    :param up_direction: Which direction is "up" for 2D projection ("Z+", "Z-", "Y+", "Y-", "X+", "X-").
    """
    from ..nesting_logic import SHAPELY_AVAILABLE
    if not SHAPELY_AVAILABLE:
        raise ImportError("The shapely library is required for boundary creation but is not installed.")
    
    FreeCAD.Console.PrintMessage(f"Processing shape '{shape_obj.Label}'...\n")
    
    from shapely.geometry import Polygon, MultiPolygon
    from shapely.affinity import translate
    from shapely.validation import make_valid

    profile_2d = get_2d_profile_from_obj(shape_obj, up_direction, deflection, simplification)
    
    # Compute the world-space BB center from the NON-ROTATED shape
    # The rotation is handled by the placement in shape_preparer
    temp_shape = shape_obj.Shape.copy()
    if shape_obj.Placement and not shape_obj.Placement.isIdentity():
        temp_shape.transformShape(shape_obj.Placement.Matrix)
    
    # Get BB center BEFORE rotation - this is the offset for centering
    bb = temp_shape.BoundBox
    source_centroid = FreeCAD.Vector(
        (bb.XMin + bb.XMax) / 2,
        (bb.YMin + bb.YMax) / 2,
        (bb.ZMin + bb.ZMax) / 2
    )
    
    # Profile is already a Shapely Polygon (centered)
    # Re-verify validity just in case
    if not profile_2d.is_valid:
         profile_2d = make_valid(profile_2d)
         if isinstance(profile_2d, MultiPolygon):
             profile_2d = max(profile_2d.geoms, key=lambda p: p.area)
    
    if profile_2d.is_empty:
        raise ValueError("2D Profile is empty.")

    # --- Create final polygon with holes ---
    # Since get_2d_profile_from_obj now returns a full Shapely Polygon,
    # we can use it directly as the unbuffered base.
    # No need to re-discretize or reconstruct holes manualy!
    final_polygon_unbuffered = profile_2d

    # Buffer the polygon for spacing.
    buffered_polygon = final_polygon_unbuffered.buffer(spacing / 2.0, join_style=1)
    
    # Simplify the buffered polygon to reduce vertex count.
    # Use explicitly passed simplification tolerance
    original_points = len(buffered_polygon.exterior.coords)
    buffered_polygon = buffered_polygon.simplify(simplification, preserve_topology=True)
    final_points = len(buffered_polygon.exterior.coords)
    
    # Also simplify the unbuffered polygon for consistent visualization
    final_polygon_unbuffered = final_polygon_unbuffered.simplify(simplification, preserve_topology=True)
    
    FreeCAD.Console.PrintMessage(f"  -> Generated boundary: {original_points} -> {final_points} vertices (Simp: {simplification})\n")

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
    # It must map the new Polygon Centroid (Origin) back to the 3D Geometry.
    
    # offset_from_origin is the vector in the 2D PROFILE PLANE.
    # It needs to be rotated back to world space.
    rotation = get_up_direction_rotation(up_direction)
    # The inverse rotation is the conjugate (for rotations) or negative angle
    # FreeCAD Rotation objects have .inverted() method? 
    # Or just use the same axis with negative angle.
    
    # Re-calculate inverse rotation manually to be safe
    inv_rotation = None
    if up_direction == "Z-":
        inv_rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -180)
    elif up_direction == "Y+":
        inv_rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90) # Inverse of -90
    elif up_direction == "Y-":
        inv_rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -90) # Inverse of 90
    elif up_direction == "X+":
        inv_rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), -90) # Inverse of 90
    elif up_direction == "X-":
        inv_rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 90) # Inverse of -90
    else:
        inv_rotation = FreeCAD.Rotation()

    offset_3d = FreeCAD.Vector(offset_from_origin.x, offset_from_origin.y, 0)
    rotated_offset = inv_rotation.multVec(offset_3d)

    shape_to_populate.polygon = final_buffered_polygon
    shape_to_populate.original_polygon = final_buffered_polygon
    shape_to_populate.spacing = spacing
    shape_to_populate.deflection = float(deflection)
    shape_to_populate.simplification = float(simplification)
    shape_to_populate.unbuffered_polygon = final_unbuffered_polygon
    shape_to_populate.source_centroid = source_centroid + rotated_offset
