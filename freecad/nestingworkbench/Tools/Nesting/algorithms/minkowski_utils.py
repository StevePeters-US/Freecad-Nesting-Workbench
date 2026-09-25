# SPDX-License-Identifier: LGPL-2.1-or-later
"""Computes No-Fit Polygons (NFP) and Inner-Fit Polygons (IFP) using Minkowski sums/differences.

This module provides geometric utility functions for calculating Minkowski sums
and differences (including containment/erosion for IFP) to determine valid
placement zones for nesting operations.
"""
import math
import threading
import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import triangulate
from shapely.affinity import rotate, scale, translate

from ....datatypes.shape import Shape

_decomposition_lock = threading.Lock()

def _merge_convex_parts(parts):
    """Greedily merge convex pieces whose union is still convex (Hertel-Mehlhorn).

    Raw Delaunay output gives ~n pieces for an n-gon, and minkowski_sum costs
    len(parts_A) * len(parts_B) convex sums — so piece count is quadratic in
    run time. Merging is union-preserving (two pieces are only ever replaced
    by their exact union), so the covering guarantee decompose_if_needed
    depends on is unchanged.
    """
    if len(parts) < 2:
        return parts
    merged = list(parts)
    changed = True
    while changed:
        changed = False
        out, consumed = [], set()
        for i in range(len(merged)):
            if i in consumed:
                continue
            cur = merged[i]
            for j in range(i + 1, len(merged)):
                if j in consumed or not cur.intersects(merged[j]):
                    continue
                u = shapely.union(cur, merged[j])
                if u.geom_type != 'Polygon' or u.interiors or u.is_empty:
                    continue
                if math.isclose(u.area, u.convex_hull.area, rel_tol=1e-9):
                    cur = u
                    consumed.add(j)
                    changed = True
            consumed.add(i)
            out.append(cur)
        merged = out
    return merged

def decompose_if_needed(polygon, logger):
    """Decomposes a non-convex polygon into convex parts (triangles)."""
    if not polygon or polygon.is_empty:
        return []
    
    # Ensure valid geometry
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    
    # Use WKT for cache key
    cache_key = polygon.wkt
    with _decomposition_lock:
        if cache_key in Shape.decomposition_cache:
            return Shape.decomposition_cache[cache_key]

    if polygon.geom_type == 'MultiPolygon':
        all_decomposed_parts = []
        for p in polygon.geoms:
            all_decomposed_parts.extend(decompose_if_needed(p, logger))
        return all_decomposed_parts

    # If already convex-ish (triangle or area match convex hull), return as is
    # Using a strict tolerance for robustness
    is_convex = False
    if polygon.geom_type == 'Polygon' and not polygon.interiors:
        if len(polygon.exterior.coords) <= 4: # Triangle (4 coords including closure)
            is_convex = True
        elif math.isclose(polygon.area, polygon.convex_hull.area, rel_tol=1e-7):
            is_convex = True
            
    if is_convex:
        res = [polygon]
        with _decomposition_lock:
            Shape.decomposition_cache[cache_key] = res
        return res
    
    try:
        # Delaunay triangulation is unconstrained (ignores polygon edges), so
        # triangles can cross the boundary of a concave polygon. Clip each
        # triangle to the polygon and keep the convex hull of every clipped
        # piece: hulls stay inside the (convex) triangle, so pieces remain
        # convex, and their union always COVERS the polygon. Coverage can only
        # err toward slight over-cover, which rejects a placement
        # conservatively — under-cover would miss collisions and let parts
        # overlap (the rep-point filter previously used here did exactly that).
        triangles = triangulate(polygon)
        decomposed = []
        for tri in triangles:
            if tri.area <= 1e-9:
                continue
            clip = tri.intersection(polygon)
            if clip.is_empty or clip.area <= 1e-9:
                continue
            if math.isclose(clip.area, tri.area, rel_tol=1e-9):
                decomposed.append(tri)
            else:
                pieces = clip.geoms if hasattr(clip, 'geoms') else [clip]
                for piece in pieces:
                    if piece.geom_type == 'Polygon' and piece.area > 1e-9:
                        decomposed.append(piece.convex_hull)

        if not decomposed:
            # An empty shell list means "no collision constraint" downstream —
            # never return that for a real polygon.
            decomposed = [polygon.convex_hull]
        else:
            decomposed = _merge_convex_parts(decomposed)

        with _decomposition_lock:
            Shape.decomposition_cache[cache_key] = decomposed
        return decomposed
    except Exception as e:
        logger(f"      - Triangulation failed: {e}. Falling back to convex hull.", level="warning")

    result = [polygon.convex_hull]
    with _decomposition_lock:
        Shape.decomposition_cache[cache_key] = result
    return result

def _summed_point_cloud(v1, v2):
    # Point cloud of every pairwise vertex sum, as one shapely MultiPoint.
    # Building one shapely.Point per summed vertex was ~70% of total nesting
    # run time; the whole grid is one numpy broadcast instead.
    return shapely.multipoints((v1[:, None, :] + v2[None, :, :]).reshape(-1, 2))

def minkowski_sum_convex(poly1, poly2):
    """Computes the Minkowski sum of two convex polygons.

    The sum of two convex polygons is the convex hull of the pairwise sums of
    their vertices. Built as a single numpy array: constructing one
    shapely.Point per summed vertex was ~70% of total nesting run time.
    """
    v1 = np.asarray(poly1.exterior.coords[:-1], dtype=np.float64)
    v2 = np.asarray(poly2.exterior.coords[:-1], dtype=np.float64)
    return shapely.convex_hull(_summed_point_cloud(v1, v2))

def minkowski_difference_convex(poly1, poly2):
    """
    Computes the erosion of poly1 by poly2, which is the Inner-Fit Polygon.
    This is NOT the Inner-Fit Polygon calculation (calculate_inner_fit_polygon), which would enlarge the polygon.
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

def calculate_inner_fit_polygon(master_poly1, angle1, master_poly2, angle2, logger):
    """
    Computes the Inner-Fit Polygon for master_poly2 inside master_poly1.
    The IFP represents valid positions for poly2's CENTROID where poly2 fits inside poly1.

    Exact for non-convex parts and non-convex holes: the vertex erosion of
    the hole by each convex piece (pieces kept at their true offset from
    the centroid), minus every position where a hole edge crosses a piece.
    Returns a Polygon, a MultiPolygon, or None.
    """
    if not master_poly1 or master_poly1.is_empty or not master_poly2 or master_poly2.is_empty:
        return None

    hole = Polygon(rotate(master_poly1, angle1, origin='centroid').exterior.coords)
    c2 = master_poly2.centroid
    # Rotate about the part's centroid, then put that centroid at the origin.
    # Every piece keeps its offset from the centroid; recentring pieces
    # individually shifts each piece's constraint and lets parts escape.
    pieces = [translate(rotate(p, angle2, origin=c2), -c2.x, -c2.y)
              for p in decompose_if_needed(master_poly2, logger)]

    hole_xy = np.asarray(hole.exterior.coords, dtype=np.float64)
    edges = np.stack([hole_xy[:-1], hole_xy[1:]], axis=1)

    result, sweeps = None, []
    for p in pieces:
        eroded = minkowski_difference_convex(hole, p)
        if eroded is None or eroded.is_empty:
            return None
        result = eroded if result is None else result.intersection(eroded)
        if result.is_empty:
            return None
        # Vertex erosion is exact only for a convex hole: also exclude every
        # position where a hole edge touches or crosses this piece (e ⊕ −P).
        neg = -np.asarray(p.exterior.coords[:-1], dtype=np.float64)
        sweeps.extend(shapely.convex_hull(_summed_point_cloud(e, neg)) for e in edges)

    # One bounded union per pairwise hole fit, cached with the NFP. This is
    # a bounded once-per-pair union, not a union in the placement path.
    result = result.difference(shapely.union_all(sweeps))
    polys = [g for g in getattr(result, 'geoms', [result])
             if g.geom_type == 'Polygon' and g.area > 1e-9]
    if not polys:
        return None
    return polys[0] if len(polys) == 1 else shapely.MultiPolygon(polys)

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

    coords1 = [np.asarray(p.exterior.coords[:-1], dtype=np.float64)
               for p in poly1_convex_transformed]
    coords2 = [np.asarray(p.exterior.coords[:-1], dtype=np.float64)
               for p in poly2_convex_transformed]
    clouds = [_summed_point_cloud(a, b) for a in coords1 for b in coords2]
    if not clouds:
        return Polygon()
    hulls = shapely.convex_hull(np.asarray(clouds, dtype=object))
    return shapely.union_all(hulls)
