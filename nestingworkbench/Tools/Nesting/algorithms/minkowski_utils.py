import math
from shapely.geometry import Polygon, MultiPoint
from shapely.ops import unary_union, triangulate
from shapely.affinity import rotate, scale, translate

from ....datatypes.shape import Shape


def decompose_if_needed(polygon, logger):
    """Decomposes a non-convex polygon into convex parts."""
    if not polygon or polygon.is_empty:
        return []
    
    cache_key = polygon.wkt
    if Shape.decomposition_cache.get(cache_key):
        return Shape.decomposition_cache.get(cache_key)

    if polygon.geom_type == 'MultiPolygon':
        all_decomposed_parts = []
        for p in polygon.geoms:
            all_decomposed_parts.extend(decompose_if_needed(p, logger))
        return all_decomposed_parts

    if math.isclose(polygon.area, polygon.convex_hull.area):
        return [polygon]
    
    try:
        triangles = triangulate(polygon)
        decomposed = [tri for tri in triangles if polygon.contains(tri.representative_point())]
        Shape.decomposition_cache[cache_key] = decomposed
        return decomposed
    except Exception as e:
        logger(f"      - Shapely triangulation not available or failed: {e}. Falling back to convex hull.", level="warning")

    result = [polygon.convex_hull]
    Shape.decomposition_cache[cache_key] = result
    return result


def minkowski_sum_convex(poly1, poly2):
    """Computes the Minkowski sum of two convex polygons."""
    # The Minkowski sum of two convex polygons is the convex hull of the sum of their vertices.
    # This is a standard and robust method.
    v1 = poly1.exterior.coords
    v2 = poly2.exterior.coords
    
    sum_vertices = []
    for p1 in v1:
        for p2 in v2:
            sum_vertices.append((p1[0] + p2[0], p1[1] + p2[1]))
    
    # The convex hull of these summed points is the Minkowski sum.
    return MultiPoint(sum_vertices).convex_hull


def minkowski_difference_convex(poly1, poly2):
    """
    Computes the erosion of poly1 by poly2, which is the Inner-Fit Polygon.
    This is NOT the Minkowski Difference, which would enlarge the polygon.
    """
    if not poly1 or poly1.is_empty or not poly2 or poly2.is_empty:
        return None

    # Erosion P ⊖ Q is the intersection of P translated by each of the negated vertices of Q.
    v2 = poly2.exterior.coords
    
    # Start with the first translated polygon
    first_translation = translate(poly1, xoff=-v2[0][0], yoff=-v2[0][1])
    
    # Intersect with the rest
    eroded_poly = first_translation
    for i in range(1, len(v2)):
        translated_poly = translate(poly1, xoff=-v2[i][0], yoff=-v2[i][1])
        eroded_poly = eroded_poly.intersection(translated_poly)
        # If the intersection is empty, we can stop early
        if eroded_poly.is_empty:
            return None
    
    return eroded_poly


def minkowski_difference(master_poly1, angle1, master_poly2, angle2, logger):
    """
    Computes the Inner-Fit Polygon for master_poly2 inside master_poly1.
    The IFP represents valid positions for poly2's CENTROID where poly2 fits inside poly1.
    This is Hole - Part. If Part is not convex, this is (Hole - P1) ∩ (Hole - P2) ...
    """
    if not master_poly1 or master_poly1.is_empty or not master_poly2 or master_poly2.is_empty:
        return None
    
    # Transform both polygons around their centroids
    poly1_transformed = rotate(master_poly1, angle1, origin='centroid')
    poly2_convex_parts = decompose_if_needed(master_poly2, logger)
    poly2_centroid = master_poly2.centroid
    poly2_convex_transformed = [rotate(p, angle2, origin=poly2_centroid) for p in poly2_convex_parts]
    
    # Translate both polygons so poly2's centroid is at the origin
    # This makes the Minkowski difference compute placement zones relative to poly2's centroid
    poly1_exterior_only = Polygon(poly1_transformed.exterior.coords)
    
    pairwise_diffs = []
    for i, p2 in enumerate(poly2_convex_transformed):
        # Get poly2's centroid
        p2_centroid = p2.centroid
        
        # Translate poly2 so its centroid is at origin
        p2_at_origin = translate(p2, xoff=-p2_centroid.x, yoff=-p2_centroid.y)
        
        # Compute Minkowski difference
        diff = minkowski_difference_convex(poly1_exterior_only, p2_at_origin)
        pairwise_diffs.append(diff)
    
    if not pairwise_diffs:
        return None
    
    # Intersection of all pairwise differences
    final_difference = pairwise_diffs[0]
    for i in range(1, len(pairwise_diffs)):
        final_difference = final_difference.intersection(pairwise_diffs[i])
    
    return final_difference


def minkowski_sum(master_poly1, angle1, reflect1, master_poly2, angle2, reflect2, logger, rot_origin1=None, rot_origin2=None):
    """
    Computes the Minkowski sum of two polygons.
    It uses the pre-cached decomposition of the master polygons and rotates
    the individual convex parts before summing them.
    """
    if master_poly1.is_empty or master_poly2.is_empty:
        return master_poly1.buffer(0) if master_poly2.is_empty else master_poly2.buffer(0)

    # Get the pre-decomposed convex parts from the cache.
    poly1_convex_parts = decompose_if_needed(master_poly1, logger)
    poly2_convex_parts = decompose_if_needed(master_poly2, logger)

    # --- Transform Convex Parts ---
    # CRITICAL: Use the MASTER polygon's centroid for all transformations
    # to keep the decomposed convex parts in their correct relative positions.
    c1 = master_poly1.centroid
    c2 = master_poly2.centroid

    poly1_convex_transformed = []
    for p in poly1_convex_parts:
        # Use master centroid for rotation to preserve relative positions of parts
        use_origin = c1 if (rot_origin1 is None or rot_origin1 == 'centroid') else rot_origin1
        p_new = rotate(p, angle1, origin=use_origin)
        if reflect1:
            # CRITICAL FIX: Reflect around the MASTER centroid, not (0,0)
            # This keeps all convex parts in correct relative positions after reflection
            p_new = scale(p_new, xfact=-1.0, yfact=-1.0, origin=(c1.x, c1.y))
        poly1_convex_transformed.append(p_new)

    poly2_convex_transformed = []
    for p in poly2_convex_parts:
        # Use master centroid for rotation to preserve relative positions of parts
        use_origin = c2 if (rot_origin2 is None or rot_origin2 == 'centroid') else rot_origin2
        p_new = rotate(p, angle2, origin=use_origin)
        if reflect2:
            # CRITICAL FIX: Reflect around the MASTER centroid, not (0,0)
            # This keeps all convex parts in correct relative positions after reflection
            p_new = scale(p_new, xfact=-1.0, yfact=-1.0, origin=(c2.x, c2.y))
        poly2_convex_transformed.append(p_new)

    minkowski_parts = []
    for p1 in poly1_convex_transformed:
        for p2 in poly2_convex_transformed:
            minkowski_parts.append(minkowski_sum_convex(p1, p2))

    return unary_union(minkowski_parts)