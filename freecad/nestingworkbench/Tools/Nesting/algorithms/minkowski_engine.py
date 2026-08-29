# SPDX-License-Identifier: LGPL-2.1-or-later

import math
import time
import numpy as np
import FreeCAD
from threading import Lock
import shapely
from shapely.geometry import Polygon
from shapely.affinity import translate, rotate
from . import minkowski_utils
from ....datatypes.shape import Shape

def compute_and_cache_nfp(shape_A, angle_A, part_to_place, angle_B, cache_key, log=None, step_size=5.0):
    """Computes the NFP for one (A, B, relative-angle) pair and stores it in
    Shape.nfp_cache under cache_key. Pure Shapely — safe on any thread.
    Returns the cache entry."""
    if log is None:
        def log_fallback(msg, level=None):
            if level == "warning":
                FreeCAD.Console.PrintWarning(f"MINKOWSKI_ENGINE: {msg}\n")
            elif level == "error":
                FreeCAD.Console.PrintError(f"MINKOWSKI_ENGINE: {msg}\n")
            else:
                FreeCAD.Console.PrintMessage(f"MINKOWSKI_ENGINE: {msg}\n")
        log = log_fallback

    with Shape.nfp_cache_lock:
        cached_nfp_data = Shape.nfp_cache.get(cache_key)
        if cached_nfp_data:
            return cached_nfp_data
    try:
        mA, mB = shape_A.original_polygon, part_to_place.original_polygon
        cA, cB = mA.centroid, mB.centroid
        poly_A_centered = translate(mA, -cA.x, -cA.y)
        poly_B_centered = translate(mB, -cB.x, -cB.y)
        nfp_exterior = minkowski_utils.minkowski_sum(poly_A_centered, angle_A, False, poly_B_centered, angle_B, True, log)
        nfp_interiors = []
        if poly_A_centered.interiors:
            B_rot = rotate(poly_B_centered, angle_B, origin=(0, 0))
            for hole in poly_A_centered.interiors:
                hole_poly = Polygon(hole.coords)
                if (B_rot.bounds[2] - B_rot.bounds[0] < hole_poly.bounds[2] - hole_poly.bounds[0] and
                    B_rot.bounds[3] - B_rot.bounds[1] < hole_poly.bounds[3] - hole_poly.bounds[1] and
                    B_rot.area < hole_poly.area):
                    ifp = minkowski_utils.calculate_inner_fit_polygon(hole_poly, 0, poly_B_centered, angle_B, log)
                    if ifp and not ifp.is_empty:
                        if ifp.geom_type == 'Polygon':
                            nfp_interiors.append(ifp.exterior)
                        elif ifp.geom_type == 'MultiPolygon':
                            for p in ifp.geoms:
                                nfp_interiors.append(p.exterior)
        master_nfp = Polygon(nfp_exterior.exterior, nfp_interiors) if nfp_exterior and nfp_exterior.area > 0 else None
        if master_nfp:
            rings = [master_nfp.exterior] + list(master_nfp.interiors)
            pts_parts = [MinkowskiEngine._discretize_ring_np(r, step_size) for r in rings]
            local_pts = np.concatenate(pts_parts, axis=0) if pts_parts else np.empty((0, 2), dtype=np.float64)
            nfp_data = {"polygon": master_nfp, "local_points": local_pts}
        else:
            nfp_data = {}
    except Exception as e:
        log(f"Error calculating NFP for {cache_key}: {e}", level="error")
        nfp_data = {'error': str(e)}
    with Shape.nfp_cache_lock:
        Shape.nfp_cache[cache_key] = nfp_data
    return nfp_data

class MinkowskiEngine:
    """
    Handles geometric operations for Minkowski nesting, such as NFP generation,
    candidate point finding, and placement validation.
    """
    def __init__(self, bin_width, bin_height, step_size, discretize_edges=True, log_callback=None, verbose=False, search_direction=(0, -1), rng=None):
        self.bin_width = bin_width
        self.bin_height = bin_height
        self.step_size = step_size
        self.discretize_edges = discretize_edges
        self.log_callback = log_callback
        self.verbose = verbose
        
        self.search_direction = search_direction
        self.rng = rng
        self._log_lock = Lock()

        self.bin_polygon = Polygon([(0, 0), (self.bin_width, 0), (self.bin_width, self.bin_height), (0, self.bin_height)])
        self._perf_stats = {'cache_hits': 0, 'cache_misses': 0, 'nfp_compute_ms': 0.0}
        self._perf_lock = Lock()
        self._cand_cache_lock = Lock()

    def log(self, message):
        if self.log_callback:
            with self._log_lock:
                self.log_callback("MINKOWSKI_ENGINE: " + message)
        else:
             FreeCAD.Console.PrintMessage(f"MINKOWSKI_ENGINE: {message}\n")

    def get_global_nfp_for(self, part_to_place, angle, sheet):
        """
        Build placement collision data for part_to_place at angle on sheet.

        GEOMETRIC & ALGORITHMIC PRINCIPLES:
        1. Container Boundaries & Inner Fit Polygons (IFPs):
           The container (sheet) boundary defines the outer limits. An Inner Fit Polygon (IFP)
           represents the set of valid centroid positions where the part remains entirely inside 
           the container bounds. Any candidate placement outside the IFP is discarded.
        2. Holes & Nested Inner Regions:
           If a placed part contains interior loops (holes), the engine computes the Inner Fit 
           Polygon of the hole with respect to the part being nested. This allows smaller shapes
           to be packed inside the negative spaces of larger, already placed parts.
        3. Pairwise NFPs & The "No-Union" Architecture:
           Rather than merging all placed parts into a single global union polygon (which gets 
           quadratically slower, prone to self-intersection errors, and loses fine detail), 
           the engine computes individual pairwise NFPs between the current part and each placed 
           part. Candidate centroids are evaluated against these individual NFPs.
           This preserves exact contact boundaries, allowing parts to pack tightly (touching) 
           without numeric/geometric overlap false positives.

        Returns dict:
          'points'       — (N,2) float32: candidate positions
        Returns None if any pairwise NFP has an error flag.
        """
        part_label = part_to_place.source_freecad_object.Label
        _pt_arrays = []
        t0_total = time.perf_counter()
        n_hits = 0
        n_misses = 0

        for p in sheet.parts:
            placed_label = p.shape.source_freecad_object.Label
            placed_angle = p.angle

            relative_angle = (angle - placed_angle) % 360.0
            if abs(relative_angle - 360.0) < 1e-5:
                relative_angle = 0.0
            relative_angle = round(relative_angle, 4)

            nfp_cache_key = (
                placed_label, part_label, relative_angle,
                part_to_place.spacing, part_to_place.deflection, part_to_place.simplification,
            )

            nfp_data = Shape.nfp_cache.get(nfp_cache_key)

            if not nfp_data:
                n_misses += 1
                t_miss = time.perf_counter()
                nfp_data = self._calculate_and_cache_nfp(
                    p.shape, 0.0, part_to_place, relative_angle, nfp_cache_key
                )
                dt_miss = (time.perf_counter() - t_miss) * 1000
                if self.verbose:
                    self.log(f"[PERF] NFP cache MISS key={nfp_cache_key[:3]} angle={relative_angle:.1f} -> {dt_miss:.1f}ms")
                with self._perf_lock:
                    self._perf_stats['nfp_compute_ms'] += dt_miss
            else:
                n_hits += 1

            if not nfp_data:
                continue
            if nfp_data.get('error'):
                self.log(f"Skipping rotation due to NFP error: {nfp_data['error']}")
                return None

            cent = p.shape.centroid
            master = nfp_data.get('polygon')
            if not master:
                continue

            # Candidate points — use pre-discretized local_points when available
            local_pts = nfp_data.get('local_points')
            if local_pts is not None and len(local_pts):
                pts = local_pts.copy()
                if abs(placed_angle) > 1e-9:
                    a = math.radians(placed_angle)
                    ca, sa = math.cos(a), math.sin(a)
                    pts = pts @ np.array([[ca, -sa], [sa, ca]], dtype=np.float64).T
                pts[:, 0] += cent.x
                pts[:, 1] += cent.y
                _pt_arrays.append(pts.astype(np.float32))
            else:
                rotated = rotate(master, placed_angle, origin=(0, 0))
                translated = translate(rotated, xoff=cent.x, yoff=cent.y)
                ring_pts = self._discretize_ring_np(translated.exterior, self.step_size)
                if len(ring_pts):
                    _pt_arrays.append(ring_pts.astype(np.float32))
                for interior in translated.interiors:
                    int_pts = self._discretize_ring_np(interior, self.step_size)
                    if len(int_pts):
                        _pt_arrays.append(int_pts.astype(np.float32))

        dt_total = (time.perf_counter() - t0_total) * 1000
        with self._perf_lock:
            self._perf_stats['cache_hits'] += n_hits
            self._perf_stats['cache_misses'] += n_misses
        if _pt_arrays:
            all_pts = np.concatenate(_pt_arrays, axis=0)
            grid = max(1.0, self.step_size)
            rounded = np.round(all_pts / grid).astype(np.int32)
            _, unique_idx = np.unique(rounded, axis=0, return_index=True)
            points = all_pts[unique_idx]
        else:
            points = np.empty((0, 2), dtype=np.float32)
        if self.verbose and (n_misses > 0 or dt_total > 10.0):
            self.log(f"[PERF] get_global_nfp_for angle={angle:.1f} "
                     f"hits={n_hits} misses={n_misses} "
                     f"total={dt_total:.1f}ms candidates={len(points)}")
        return {'points': points}

    def get_incremental_candidates(self, part_to_place, angle, sheet, corner_candidates, part_extents):
        """Return valid candidate centroid positions for part_to_place at angle on sheet.

        Maintains a per-sheet cache keyed by (part type, angle): candidates
        already tested against the first n placed parts are only re-tested
        against parts placed since, and a position invalidated once is never
        reconsidered — placed parts never move, so constraints only grow.
        Collision tests run against each placed part's NFP individually
        (vectorized point-in-polygon); NFPs are never unioned.

        corner_candidates: (4,2) bin-corner flush positions for this rotation
        part_extents: (min_x, min_y, max_x, max_y) of the rotated part
                      relative to its centroid, for the bin-bounds check

        Returns (N,2) float64 array of currently-valid positions, or None when
        a pairwise NFP carries an error flag (skip this rotation).
        """
        part_label = part_to_place.source_freecad_object.Label
        key = (part_label, round(angle % 360.0, 4), part_to_place.spacing,
               part_to_place.deflection, part_to_place.simplification)
        with self._cand_cache_lock:
            cache = sheet.__dict__.setdefault('_cand_cache', {})
            entry = cache.get(key)
            if entry is None:
                entry = cache[key] = {'n': 0, 'pts': None, 'polys': [], 'seen': set()}
        # Past this point the entry is only touched by one thread: angles map
        # 1:1 to threads within an attempt, and attempts are sequential.

        tol = 1e-7
        grid = max(1.0, self.step_size)
        rminx, rminy, rmaxx, rmaxy = part_extents

        def _bounds_ok(pts):
            return ((pts[:, 0] + rminx >= -tol) & (pts[:, 0] + rmaxx <= self.bin_width + tol) &
                    (pts[:, 1] + rminy >= -tol) & (pts[:, 1] + rmaxy <= self.bin_height + tol))

        def _drop_inside(polys, pts, keep):
            """Clear keep-mask bits for points strictly inside any poly."""
            for poly in polys:
                if not keep.any():
                    return
                bx0, by0, bx1, by1 = poly.bounds
                idx = np.flatnonzero(keep)
                sub = pts[idx]
                in_bbox = ((sub[:, 0] >= bx0) & (sub[:, 0] <= bx1) &
                           (sub[:, 1] >= by0) & (sub[:, 1] <= by1))
                hits = idx[in_bbox]
                if len(hits):
                    inside = shapely.contains_xy(poly, pts[hits, 0], pts[hits, 1])
                    keep[hits[inside]] = False

        if entry['pts'] is None:
            seed = np.asarray(corner_candidates, dtype=np.float64).reshape(-1, 2)
            seed = seed[_bounds_ok(seed)]
            entry['pts'] = seed
            for row in np.round(seed / grid).astype(np.int64):
                entry['seen'].add((int(row[0]), int(row[1])))

        m = len(sheet.parts)
        n_prev = entry['n']
        if n_prev >= m:
            return entry['pts']

        t0_total = time.perf_counter()
        n_hits = 0
        n_misses = 0
        new_polys = []
        new_pt_arrays = []

        for p in sheet.parts[n_prev:m]:
            placed_label = p.shape.source_freecad_object.Label
            placed_angle = p.angle

            relative_angle = (angle - placed_angle) % 360.0
            if abs(relative_angle - 360.0) < 1e-5:
                relative_angle = 0.0
            relative_angle = round(relative_angle, 4)

            nfp_cache_key = (
                placed_label, part_label, relative_angle,
                part_to_place.spacing, part_to_place.deflection, part_to_place.simplification,
            )
            nfp_data = Shape.nfp_cache.get(nfp_cache_key)
            if not nfp_data:
                n_misses += 1
                t_miss = time.perf_counter()
                nfp_data = self._calculate_and_cache_nfp(
                    p.shape, 0.0, part_to_place, relative_angle, nfp_cache_key
                )
                with self._perf_lock:
                    self._perf_stats['nfp_compute_ms'] += (time.perf_counter() - t_miss) * 1000
            else:
                n_hits += 1

            if not nfp_data:
                continue
            if nfp_data.get('error'):
                self.log(f"Skipping rotation due to NFP error: {nfp_data['error']}")
                with self._perf_lock:
                    self._perf_stats['cache_hits'] += n_hits
                    self._perf_stats['cache_misses'] += n_misses
                return None

            master = nfp_data.get('polygon')
            if not master:
                continue

            cent = p.shape.centroid
            tpoly = translate(rotate(master, placed_angle, origin=(0, 0)), xoff=cent.x, yoff=cent.y)
            
            # CPU rejection
            shapely.prepare(tpoly)
            new_polys.append(tpoly)

            local_pts = nfp_data.get('local_points')
            if local_pts is not None and len(local_pts):
                pts = local_pts.copy()
                if abs(placed_angle) > 1e-9:
                    a = math.radians(placed_angle)
                    ca, sa = math.cos(a), math.sin(a)
                    pts = pts @ np.array([[ca, -sa], [sa, ca]], dtype=np.float64).T
                pts[:, 0] += cent.x
                pts[:, 1] += cent.y
                new_pt_arrays.append(pts)
            else:
                for ring in [tpoly.exterior] + list(tpoly.interiors):
                    ring_pts = self._discretize_ring_np(ring, self.step_size)
                    if len(ring_pts):
                        new_pt_arrays.append(ring_pts)

        # 1) Surviving candidates only need testing against the NEW parts' NFPs.
        pts = entry['pts']
        if len(pts) and new_polys:
            keep = np.ones(len(pts), dtype=bool)
            _drop_inside(new_polys, pts, keep)
            pts = pts[keep]

        # 2) Candidates contributed by the new parts: bounds-check, dedup against
        #    every position ever admitted, then test against ALL placed NFPs.
        if new_pt_arrays:
            fresh = np.concatenate(new_pt_arrays, axis=0).astype(np.float64)
            fresh = fresh[_bounds_ok(fresh)]
            if len(fresh):
                gridded = np.round(fresh / grid).astype(np.int64)
                _, first_idx = np.unique(gridded, axis=0, return_index=True)
                first_idx.sort()
                seen = entry['seen']
                rows = []
                for i in first_idx:
                    gkey = (int(gridded[i, 0]), int(gridded[i, 1]))
                    if gkey not in seen:
                        seen.add(gkey)
                        rows.append(i)
                fresh = fresh[rows]
            if len(fresh):
                keep = np.ones(len(fresh), dtype=bool)
                _drop_inside(entry['polys'], fresh, keep)
                _drop_inside(new_polys, fresh, keep)
                fresh = fresh[keep]
            if len(fresh):
                pts = np.concatenate([pts, fresh], axis=0) if len(pts) else fresh

        entry['pts'] = pts
        entry['polys'].extend(new_polys)
        entry['n'] = m

        with self._perf_lock:
            self._perf_stats['cache_hits'] += n_hits
            self._perf_stats['cache_misses'] += n_misses
        if self.verbose:
            dt = (time.perf_counter() - t0_total) * 1000
            self.log(f"[PERF] incremental_candidates angle={angle:.1f} "
                     f"new_parts={m - n_prev} hits={n_hits} misses={n_misses} "
                     f"total={dt:.1f}ms candidates={len(pts)}")
        return pts

    @staticmethod
    def score_gravity(pts_np, valid, direction, rng=None):
        """Score candidates by gravity direction. Lower metric = better (furthest along direction).

        pts_np: (N, 2) float array of candidate positions
        valid:  (N,) bool mask — invalid positions get metric=inf
        direction: (gx, gy) unit vector pointing toward preferred side
        rng: optional random.Random — when given, ties for the best score are
             broken randomly instead of always taking the first index
        Returns: (best_idx, metric) or (None, inf) when no valid candidates exist.
        """
        gx, gy = direction
        scores = np.where(valid, -(pts_np[:, 0] * gx + pts_np[:, 1] * gy), np.inf)
        metric = float(scores.min())
        if not np.isfinite(metric):
            return None, float('inf')
        tied = np.flatnonzero(scores == metric)
        if rng is not None and len(tied) > 1:
            best_idx = int(tied[rng.randrange(len(tied))])
        else:
            best_idx = int(tied[0])
        return best_idx, metric

    def _calculate_and_cache_nfp(self, shape_A, angle_A, part_to_place, angle_B, cache_key):
        return compute_and_cache_nfp(shape_A, angle_A, part_to_place, angle_B, cache_key, self.log, self.step_size)

    def get_perf_stats(self):
        with self._perf_lock:
            return dict(self._perf_stats)

    def reset_perf_stats(self):
        with self._perf_lock:
            self._perf_stats = {'cache_hits': 0, 'cache_misses': 0, 'nfp_compute_ms': 0.0}

    @staticmethod
    def _discretize_ring_np(ring, step_size):
        """Vectorized ring discretisation. Returns (N, 2) float64 array.

        Replaces the Shapely interpolate() loop — samples at equal arc-length
        intervals using numpy cumulative distance + np.interp.
        """
        coords = np.array(ring.coords, dtype=np.float64)
        diffs = np.diff(coords, axis=0)
        seg_lens = np.hypot(diffs[:, 0], diffs[:, 1])
        cum_dist = np.empty(len(seg_lens) + 1, dtype=np.float64)
        cum_dist[0] = 0.0
        np.cumsum(seg_lens, out=cum_dist[1:])
        total = cum_dist[-1]
        if total < step_size:
            return coords[:1]
        n = max(2, int(total / step_size))
        sample_dists = np.linspace(0.0, total, n, endpoint=False)
        xs = np.interp(sample_dists, cum_dist, coords[:, 0])
        ys = np.interp(sample_dists, cum_dist, coords[:, 1])
        return np.column_stack([xs, ys])

