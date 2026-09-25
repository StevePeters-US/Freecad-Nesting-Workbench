# SPDX-License-Identifier: LGPL-2.1-or-later

import math
import time
import numpy as np
try:
    import FreeCAD
except ImportError:
    FreeCAD = None
from threading import Lock
import shapely
from shapely.geometry import Polygon
from shapely.affinity import translate, rotate
from . import minkowski_utils
from ....datatypes.shape import Shape

ANGLE_WRAP_EPS_DEG = 1e-5  # treat 359.99999→360 as 0 after the modulo

_hole_fit_warned = set()
_hole_fit_warned_lock = Lock()

def _warn_hole_fit_failed(cache_key, error, log):
    """Warns once per part pair (not once per angle) that a hole fit failed."""
    pair = cache_key[:2]
    with _hole_fit_warned_lock:
        if pair in _hole_fit_warned:
            return
        _hole_fit_warned.add(pair)
    log(f"Could not compute placements inside the hole(s) of '{pair[0]}' for '{pair[1]}' "
        f"({error}). '{pair[1]}' will not be nested inside those holes; "
        f"outside placements are unaffected.", level="warning")

def compute_and_cache_nfp(shape_A, angle_A, part_to_place, angle_B, cache_key, log=None, step_size=5.0):
    """Computes the NFP for one (A, B, relative-angle) pair and stores it in
    Shape.nfp_cache under cache_key. Pure Shapely — safe on any thread.
    Returns the cache entry."""
    if log is None:
        def log_fallback(msg, level=None):
            if FreeCAD and hasattr(FreeCAD, 'Console'):
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
    if shape_A.original_polygon is None or part_to_place.original_polygon is None:
        # Do NOT cache this. Shape.nfp_cache persists across runs by design,
        # so a cached failure would disable this pair for the whole session.
        # Returning empty lets the placement-time path recompute after
        # Nester.find_best_placement's original_polygon fallback
        # (nesting_strategy.py:43-44) has repaired the part.
        log(f"NFP skipped for {cache_key}: master polygon missing "
            f"(A set: {shape_A.original_polygon is not None}, "
            f"B set: {part_to_place.original_polygon is not None})",
            level="error")
        return {}
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
                    # A failed hole fit only loses the in-hole placements. Letting
                    # it reach the outer except would cache an error for the whole
                    # pair and skip this rotation everywhere, not just in the hole.
                    try:
                        ifp = minkowski_utils.calculate_inner_fit_polygon(hole_poly, 0, poly_B_centered, angle_B, log)
                    except Exception as e:
                        _warn_hole_fit_failed(cache_key, e, log)
                        continue
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
        self._perf_stats = {'cache_hits': 0, 'cache_misses': 0, 'nfp_compute_ms': 0.0, 'rotations_skipped': 0}
        self._perf_lock = Lock()
        self._cand_cache_lock = Lock()

    def log(self, message):
        if self.log_callback:
            with self._log_lock:
                self.log_callback("MINKOWSKI_ENGINE: " + message)
        elif FreeCAD and hasattr(FreeCAD, 'Console'):
             FreeCAD.Console.PrintMessage(f"MINKOWSKI_ENGINE: {message}\n")

    @staticmethod
    def _nfp_cache_key(placed_shape, placed_angle, part_to_place, part_label, angle):
        """Build the Shape.nfp_cache key for (placed_shape, part_to_place) at their relative angle.

        Key layout must stay in sync with enumerate_nfp_jobs / _calculate_and_cache_nfp.
        """
        placed_label = getattr(placed_shape, 'type_label', None) or placed_shape.source_freecad_object.Label
        relative_angle = (angle - placed_angle) % 360.0
        if abs(relative_angle - 360.0) < ANGLE_WRAP_EPS_DEG:
            relative_angle = 0.0
        relative_angle = round(relative_angle, 4)
        return (
            placed_label, part_label, relative_angle,
            part_to_place.spacing, part_to_place.deflection, part_to_place.simplification,
        ), relative_angle

    @staticmethod
    def candidate_cache_key(part_to_place, angle):
        """Key for this part TYPE at this angle in a sheet's `_cand_cache`.

        Type-level, not instance-level: every instance of a master shares one
        entry, which is what makes a repeat attempt cheap. Must stay in sync
        with the key `get_incremental_candidates` stores under — it calls
        this method for exactly that reason.
        """
        part_label = (getattr(part_to_place, 'type_label', None)
                      or part_to_place.source_freecad_object.Label)
        return (part_label, round(angle % 360.0, 4), part_to_place.spacing,
                part_to_place.deflection, part_to_place.simplification)

    def is_known_infeasible(self, part_to_place, angle, sheet):
        """True when this part type at this angle has already been shown to
        have no valid position on this sheet, AND the sheet has not changed
        since — so re-running the sweep cannot produce a different answer.

        Scoped to an unchanged occupancy on purpose. `entry['pts']` does not
        shrink monotonically: placing a part contributes new candidate points
        from its own NFP boundary, so an empty set can in principle refill.
        Never promote this to a permanent retirement — see the `nw_nfp_algorithm`
        skill, "Invariant: infeasibility holds only at a fixed occupancy".
        """
        cache = sheet.__dict__.get('_cand_cache')
        if not cache:
            return False
        entry = cache.get(self.candidate_cache_key(part_to_place, angle))
        return (entry is not None
                and entry['pts'] is not None
                and entry['n'] == len(sheet.parts)
                and len(entry['pts']) == 0)

    def count_skipped_rotations(self, n):
        """Records rotation sweeps skipped as known-infeasible (DEDUP-002)."""
        if n <= 0:
            return
        with self._perf_lock:
            self._perf_stats['rotations_skipped'] += n

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
        part_label = getattr(part_to_place, 'type_label', None) or part_to_place.source_freecad_object.Label
        key = self.candidate_cache_key(part_to_place, angle)
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
            placed_angle = p.angle
            nfp_cache_key, relative_angle = self._nfp_cache_key(p.shape, placed_angle, part_to_place, part_label, angle)
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
            self._perf_stats = {'cache_hits': 0, 'cache_misses': 0, 'nfp_compute_ms': 0.0, 'rotations_skipped': 0}

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

