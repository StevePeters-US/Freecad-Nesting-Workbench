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
from shapely.affinity import interpret_origin, rotate, translate

from ....datatypes.shape import Shape

_decomposition_lock = threading.Lock()

def outer_boundary(polygon):
    """The polygon with its holes filled: what the convex cover needs for an NFP
    exterior or an inner fit.

    The exterior of A + (-B) depends only on the outer boundaries of A and B, and a
    part fits in a hole by its outline. Decomposing the holes only multiplies the
    pieces: 24 bolt holes took one plate from 1 piece to 795. Holes reach the NFP
    through compute_and_cache_nfp's per-hole loop instead. Callers keep the holed
    polygon's centroid as the transform origin, so the pieces stay in the part's frame.
    """
    if polygon.geom_type == 'Polygon':
        return Polygon(polygon.exterior) if polygon.interiors else polygon
    if polygon.geom_type == 'MultiPolygon':
        return shapely.MultiPolygon([Polygon(p.exterior) for p in polygon.geoms])
    return polygon

def _outline_pieces(polygon, logger):
    """(pieces, coords) for *polygon*'s outer boundary: decompose_if_needed(outer_boundary(...))
    and each piece's exterior vertices without the closing one, as float64 arrays.

    Cached by identity: every instance of a part type shares its master's polygon object, so
    after the first NFP a lookup is one dict hit instead of an outline, a validity check and a
    WKT key (ARS round 4). The entry holds the polygon, so its id can't be reused while cached;
    Shape.clear_caches() empties it.
    """
    entry = Shape.outline_cache.get(id(polygon))
    if entry is not None and entry[0] is polygon:
        return entry[1], entry[2]
    pieces = decompose_if_needed(outer_boundary(polygon), logger)
    coords = [np.asarray(p.exterior.coords[:-1], dtype=np.float64) for p in pieces]
    Shape.outline_cache[id(polygon)] = (polygon, pieces, coords)
    return pieces, coords


def _transformed_coords(coords, angle, origin, reflect, reflect_origin):
    """Each piece's vertices rotated by *angle* degrees about *origin* and, when *reflect*,
    reflected through *reflect_origin*.

    The same operations in the same order as shapely.affinity.rotate then scale(-1, -1)
    (affine_transform's 2D path: xp = a*x + b*y + xoff), so the doubles are identical to
    transforming each piece polygon with them, without building any polygons.
    """
    rad = angle * math.pi / 180.0
    cosp, sinp = math.cos(rad), math.sin(rad)
    if abs(cosp) < 2.5e-16:
        cosp = 0.0
    if abs(sinp) < 2.5e-16:
        sinp = 0.0
    x0, y0 = origin
    xoff, yoff = x0 - x0 * cosp + y0 * sinp, y0 - x0 * sinp - y0 * cosp
    rx0, ry0 = reflect_origin
    rxoff, ryoff = rx0 - rx0 * -1.0, ry0 - ry0 * -1.0
    out = []
    for xy in coords:
        x, y = xy[:, 0], xy[:, 1]
        xp = cosp * x + -sinp * y + xoff
        yp = sinp * x + cosp * y + yoff
        if reflect:
            xp, yp = -1.0 * xp + 0.0 * yp + rxoff, 0.0 * xp + -1.0 * yp + ryoff
        out.append(np.stack([xp, yp]).T)
    return out

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
    # The part's outline only (outer_boundary); c2 stays the holed centroid.
    pieces = [translate(rotate(p, angle2, origin=c2), -c2.x, -c2.y)
              for p in _outline_pieces(master_poly2, logger)[0]]

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

def _rotation_origin(master_poly, centroid, rot_origin):
    """minkowski_sum's rotation origin as (x, y): the master's centroid by default, or an
    explicit Point / (x, y). 'center' is refused: it used to mean each piece's own bbox centre,
    which scatters the pieces, and nothing passes it."""
    if rot_origin is None or rot_origin == 'centroid':
        return (centroid.x, centroid.y)
    if rot_origin == 'center':
        raise ValueError("minkowski_sum: rot_origin='center' is not supported")
    return tuple(interpret_origin(master_poly, rot_origin, 2))

def minkowski_sum(master_poly1, angle1, reflect1, master_poly2, angle2, reflect2, logger, rot_origin1=None, rot_origin2=None):
    """
    Computes the Minkowski sum of two polygons' outlines.
    It uses the cached convex cover of each master's outer boundary
    (outer_boundary) and rotates the pieces about the holed master's centroid.
    The result's exterior is the NFP exterior; it carries none of the masters'
    holes, and compute_and_cache_nfp reads only its exterior.
    """
    if master_poly1.is_empty or master_poly2.is_empty:
        return master_poly1.buffer(0) if master_poly2.is_empty else master_poly2.buffer(0)

    # Outlines only: holes never move the exterior and multiply the pieces.
    # c1/c2 below stay the holed masters' centroids (the part's frame).
    _pieces1, piece_coords1 = _outline_pieces(master_poly1, logger)
    _pieces2, piece_coords2 = _outline_pieces(master_poly2, logger)

    # CRITICAL: Use the MASTER polygon's centroid for all transformations
    # to keep the decomposed convex parts in their correct relative positions.
    c1 = master_poly1.centroid
    c2 = master_poly2.centroid
    origin1 = _rotation_origin(master_poly1, c1, rot_origin1)
    origin2 = _rotation_origin(master_poly2, c2, rot_origin2)

    # Reflect around the MASTER centroid, not (0,0), so the pieces keep their
    # relative positions.
    coords1 = _transformed_coords(piece_coords1, angle1, origin1, reflect1, (c1.x, c1.y))
    coords2 = _transformed_coords(piece_coords2, angle2, origin2, reflect2, (c2.x, c2.y))
    clouds = [_summed_point_cloud(a, b) for a in coords1 for b in coords2]
    if not clouds:
        return Polygon()
    hulls = shapely.convex_hull(np.asarray(clouds, dtype=object))
    return shapely.union_all(hulls)
