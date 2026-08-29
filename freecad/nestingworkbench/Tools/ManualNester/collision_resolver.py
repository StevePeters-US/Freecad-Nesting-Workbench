# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Collision resolver for the manual nesting tool.
Handles clamping parts to sheet boundaries and basic overlap resolution.

NOTE: This module does NOT write obj.Placement during physics.  All logical
positions are tracked in _base_cache; the tool layer commits them to
obj.Placement in batch on drop (via _commit_displaced_placements).

Collision detection uses a two-phase approach:
  1. AABB fast-rejection: if axis-aligned bounding boxes don't overlap,
     the shapes definitely don't intersect.
  2. Shapely polygon narrow-phase: when boxes *do* overlap, check the
     actual boundary polygon (extracted from BoundaryObject wires) for a
     precise answer.  Falls back to bbox-only when no polygon is available.
"""

class CollisionResolver:
    def __init__(self):
        self._cache = {}  # id(obj) -> entry dict with 'bbox' and 'poly' keys
        self._base_cache = {}  # id(obj) -> (x, y) placement base when entry was last computed

    def prime_cache(self, objs):
        """Pre-compute bbox+polygon entries for all objects. Call once per physics frame."""
        for obj in objs:
            key = id(obj)
            if key not in self._cache:
                result = self._compute_entry(obj)
                if result:
                    self._cache[key] = result
                    base = obj.Placement.Base
                    self._base_cache[key] = (base.x, base.y)

    def clear_cache(self):
        """Clear all cached entries. Call at the end of a drag session."""
        self._cache.clear()
        self._base_cache.clear()

    def invalidate(self, obj):
        """Remove a single object's cached entry after it has been moved."""
        key = id(obj)
        self._cache.pop(key, None)
        self._base_cache.pop(key, None)

    def translate_from_placement(self, obj):
        """Shift a cached entry to match obj's current placement.Base.

        Cheaper than invalidate() + prime_cache(): no FreeCAD hierarchy walk needed.
        Returns True if cache was updated, False if no entry existed (caller must
        call prime_cache instead).
        """
        key = id(obj)
        if key not in self._cache or key not in self._base_cache:
            return False
        base = obj.Placement.Base
        old = self._base_cache[key]
        dx = base.x - old[0]
        dy = base.y - old[1]
        if abs(dx) > 0.0001 or abs(dy) > 0.0001:
            self._translate_entry(obj, dx, dy)
        return True

    def _translate_entry(self, obj, dx, dy):
        """Shift a cached entry's bbox and Shapely polygon by (dx, dy).

        Cheaper than invalidate() + _compute_entry(): no FreeCAD API calls are
        needed for a pure XY translation.  No-op if obj has no cached entry.
        """
        key = id(obj)
        if key not in self._cache:
            return
        entry = self._cache[key]
        bb = entry['bbox']
        bb['min_x'] += dx
        bb['max_x'] += dx
        bb['min_y'] += dy
        bb['max_y'] += dy
        bb['center_x'] += dx
        bb['center_y'] += dy
        poly = entry.get('poly')
        if poly is not None:
            try:
                from shapely.affinity import translate as _shapely_translate
                entry['poly'] = _shapely_translate(poly, dx, dy)
            except Exception:
                entry['poly'] = None
        if key in self._base_cache:
            old = self._base_cache[key]
            self._base_cache[key] = (old[0] + dx, old[1] + dy)

    def clamp_to_sheet(self, obj, sheet_bbox):
        """Clamp obj's logical position so its bbox stays within sheet_bbox.

        Updates the cache only — does not write obj.Placement.
        """
        entry = self._get_entry(obj)
        if not entry:
            return False

        abs_bb = entry['bbox']
        cur_x, cur_y = self._get_logical_pos(obj)

        new_x = cur_x
        new_y = cur_y
        clamped = False

        if abs_bb['min_x'] < sheet_bbox.XMin:
            new_x += (sheet_bbox.XMin - abs_bb['min_x'])
            clamped = True
        elif abs_bb['max_x'] > sheet_bbox.XMax:
            new_x -= (abs_bb['max_x'] - sheet_bbox.XMax)
            clamped = True

        if abs_bb['min_y'] < sheet_bbox.YMin:
            new_y += (sheet_bbox.YMin - abs_bb['min_y'])
            clamped = True
        elif abs_bb['max_y'] > sheet_bbox.YMax:
            new_y -= (abs_bb['max_y'] - sheet_bbox.YMax)
            clamped = True

        if clamped:
            self._translate_entry(obj, new_x - cur_x, new_y - cur_y)

        return clamped

    def separate_overlapping(self, moved_obj, other_objs, max_iterations=5):
        """Iteratively separates moved_obj from overlapping other_objs.

        Only moved_obj is shifted. Updates the cache only — does not write obj.Placement.
        """
        for _ in range(max_iterations):
            any_overlap = False
            moved_entry = self._get_entry(moved_obj)
            if not moved_entry:
                return False
            moved_bb = moved_entry['bbox']
            cur_x, cur_y = self._get_logical_pos(moved_obj)

            for other in other_objs:
                if other == moved_obj:
                    continue

                other_entry = self._get_entry(other)
                if not other_entry:
                    continue

                if self._entries_intersect(moved_entry, other_entry):
                    any_overlap = True
                    other_bb = other_entry['bbox']
                    overlap_x = min(moved_bb['max_x'], other_bb['max_x']) - max(moved_bb['min_x'], other_bb['min_x']) + 0.001
                    overlap_y = min(moved_bb['max_y'], other_bb['max_y']) - max(moved_bb['min_y'], other_bb['min_y']) + 0.001

                    new_x = cur_x
                    new_y = cur_y

                    if overlap_x < overlap_y:
                        dir_x = 1.0 if moved_bb['center_x'] > other_bb['center_x'] else -1.0
                        new_x += overlap_x * dir_x
                    else:
                        dir_y = 1.0 if moved_bb['center_y'] > other_bb['center_y'] else -1.0
                        new_y += overlap_y * dir_y

                    dx = new_x - cur_x
                    dy = new_y - cur_y
                    cur_x, cur_y = new_x, new_y
                    self._translate_entry(moved_obj, dx, dy)
                    moved_entry = self._cache.get(id(moved_obj))
                    if moved_entry:
                        moved_bb = moved_entry['bbox']

            if not any_overlap:
                return True
        return False

    def overlaps_any(self, obj, others):
        """Returns True if obj overlaps any shape in others."""
        entry = self._get_entry(obj)
        if not entry:
            return False
        for other in others:
            if other == obj:
                continue
            other_entry = self._get_entry(other)
            if other_entry and self._entries_intersect(entry, other_entry):
                return True
        return False

    # Internal cache helpers

    def _get_logical_pos(self, obj):
        """Return (x, y) logical position from cache, falling back to obj.Placement.Base."""
        cached = self._base_cache.get(id(obj))
        if cached:
            return cached[0], cached[1]
        b = obj.Placement.Base
        return b.x, b.y

    def _get_logical_pos_by_key(self, key):
        """Return (x, y) logical position from cache by integer key."""
        cached = self._base_cache.get(key)
        if cached:
            return cached[0], cached[1]
        return 0.0, 0.0

    # Key-based methods (worker-thread safe — no FreeCAD object refs)

    def _translate_key(self, key, dx, dy):
        """Translate a cache entry by (dx, dy) using integer key instead of obj ref."""
        if key not in self._cache:
            return
        entry = self._cache[key]
        bb = entry['bbox']
        bb['min_x'] += dx
        bb['max_x'] += dx
        bb['center_x'] += dx
        bb['min_y'] += dy
        bb['max_y'] += dy
        bb['center_y'] += dy
        poly = entry.get('poly')
        if poly is not None:
            try:
                from shapely.affinity import translate as _shapely_translate
                entry['poly'] = _shapely_translate(poly, dx, dy)
            except Exception:
                entry['poly'] = None
        if key in self._base_cache:
            old = self._base_cache[key]
            self._base_cache[key] = (old[0] + dx, old[1] + dy)

    def separate_overlapping_by_keys(self, moved_key, other_keys, max_iterations=5):
        """Key-based variant of separate_overlapping for use in worker thread."""
        for _ in range(max_iterations):
            any_overlap = False
            moved_entry = self._cache.get(moved_key)
            if not moved_entry:
                return False
            moved_bb = moved_entry['bbox']
            cur_x, cur_y = self._get_logical_pos_by_key(moved_key)

            for other_key in other_keys:
                if other_key == moved_key:
                    continue
                other_entry = self._cache.get(other_key)
                if not other_entry:
                    continue
                if self._entries_intersect(moved_entry, other_entry):
                    any_overlap = True
                    other_bb = other_entry['bbox']
                    overlap_x = min(moved_bb['max_x'], other_bb['max_x']) - max(moved_bb['min_x'], other_bb['min_x']) + 0.001
                    overlap_y = min(moved_bb['max_y'], other_bb['max_y']) - max(moved_bb['min_y'], other_bb['min_y']) + 0.001
                    new_x, new_y = cur_x, cur_y
                    if overlap_x < overlap_y:
                        dir_x = 1.0 if moved_bb['center_x'] > other_bb['center_x'] else -1.0
                        new_x += overlap_x * dir_x
                    else:
                        dir_y = 1.0 if moved_bb['center_y'] > other_bb['center_y'] else -1.0
                        new_y += overlap_y * dir_y
                    dx = new_x - cur_x
                    dy = new_y - cur_y
                    cur_x, cur_y = new_x, new_y
                    self._translate_key(moved_key, dx, dy)
                    moved_entry = self._cache.get(moved_key)
                    if moved_entry:
                        moved_bb = moved_entry['bbox']

            if not any_overlap:
                return True
        return False

    def clamp_to_sheet_by_key(self, key, xmin, xmax, ymin, ymax):
        """Key-based variant of clamp_to_sheet for use in worker thread."""
        entry = self._cache.get(key)
        if not entry:
            return False
        abs_bb = entry['bbox']
        cur_x, cur_y = self._get_logical_pos_by_key(key)
        new_x, new_y = cur_x, cur_y
        clamped = False
        if abs_bb['min_x'] < xmin:
            new_x += xmin - abs_bb['min_x']
            clamped = True
        elif abs_bb['max_x'] > xmax:
            new_x -= abs_bb['max_x'] - xmax
            clamped = True
        if abs_bb['min_y'] < ymin:
            new_y += ymin - abs_bb['min_y']
            clamped = True
        elif abs_bb['max_y'] > ymax:
            new_y -= abs_bb['max_y'] - ymax
            clamped = True
        if clamped:
            self._translate_key(key, new_x - cur_x, new_y - cur_y)
        return clamped

    def resolve_bi_by_keys(self, key_a, key_b):
        """Key-based variant of resolve_bi_collision for use in worker thread."""
        entry_a = self._cache.get(key_a)
        entry_b = self._cache.get(key_b)
        if not entry_a or not entry_b:
            return False
        if self._entries_intersect(entry_a, entry_b):
            bb_a = entry_a['bbox']
            bb_b = entry_b['bbox']
            ox = min(bb_a['max_x'], bb_b['max_x']) - max(bb_a['min_x'], bb_b['min_x']) + 0.001
            oy = min(bb_a['max_y'], bb_b['max_y']) - max(bb_a['min_y'], bb_b['min_y']) + 0.001
            if ox < oy:
                shift = ox / 2.0
                dir_x = 1.0 if bb_a['center_x'] > bb_b['center_x'] else -1.0
                self._translate_key(key_a, shift * dir_x, 0)
                self._translate_key(key_b, -shift * dir_x, 0)
            else:
                shift = oy / 2.0
                dir_y = 1.0 if bb_a['center_y'] > bb_b['center_y'] else -1.0
                self._translate_key(key_a, 0, shift * dir_y)
                self._translate_key(key_b, 0, -shift * dir_y)
            return True
        return False

    def overlaps_any_by_keys(self, key, other_keys):
        """Key-based variant of overlaps_any for use in worker thread."""
        entry = self._cache.get(key)
        if not entry:
            return False
        for other_key in other_keys:
            if other_key == key:
                continue
            other_entry = self._cache.get(other_key)
            if other_entry and self._entries_intersect(entry, other_entry):
                return True
        return False

    @staticmethod
    def find_overlapping_pairs(keys, cache):
        """Return list of (key_a, key_b) pairs whose shapes intersect.

        Uses Shapely STRtree spatial index when available (Shapely 2.x only).
        Falls back to O(N²) AABB+polygon checks otherwise.
        """
        entries = [(k, cache[k]) for k in keys if k in cache]
        if len(entries) < 2:
            return []

        polys_with_keys = [(k, e['poly']) for k, e in entries if e.get('poly') is not None]

        try:
            from shapely.strtree import STRtree
            geoms = [p for _, p in polys_with_keys]
            tree = STRtree(geoms)
            pairs = []
            for i, (k_a, poly_a) in enumerate(polys_with_keys):
                result = tree.query(poly_a, predicate='intersects')
                for j in result:
                    if j > i:
                        k_b = polys_with_keys[j][0]
                        pairs.append((k_a, k_b))
            # Add bbox-only pairs for entries without polygon
            keys_no_poly = [k for k, e in entries if e.get('poly') is None]
            poly_keys = {k for k, _ in polys_with_keys}
            for i, k_a in enumerate(keys_no_poly):
                bb_a = cache[k_a]['bbox']
                for k_b in keys_no_poly[i + 1:]:
                    if CollisionResolver._bboxes_intersect(bb_a, cache[k_b]['bbox']):
                        pairs.append((k_a, k_b))
                for k_b in poly_keys:
                    if CollisionResolver._bboxes_intersect(bb_a, cache[k_b]['bbox']):
                        pairs.append((k_a, k_b))
            return pairs
        except Exception:
            pass  # Fallback to O(N^2) AABB+polygon checks if STRtree query fails or is unavailable

        # Fallback: O(N²)
        pairs = []
        for i, (k_a, e_a) in enumerate(entries):
            for k_b, e_b in entries[i + 1:]:
                if not CollisionResolver._bboxes_intersect(e_a['bbox'], e_b['bbox']):
                    continue
                p_a, p_b = e_a.get('poly'), e_b.get('poly')
                if p_a is not None and p_b is not None:
                    if p_a.intersects(p_b):
                        pairs.append((k_a, k_b))
                else:
                    pairs.append((k_a, k_b))
        return pairs

    def _get_entry(self, obj):
        """Return cached entry dict, computing it if not already cached."""
        key = id(obj)
        if key in self._cache:
            return self._cache[key]
        return self._compute_entry(obj)

    def _compute_entry(self, obj):
        """Compute bbox dict + Shapely polygon for obj. Returns None if no shape found."""
        placement, shape = self._find_shape_with_placement(obj, None)
        if not shape:
            return None

        transformed = self._transform_bbox(shape.BoundBox, placement)
        bbox = {
            'min_x': transformed[0],
            'max_x': transformed[1],
            'min_y': transformed[2],
            'max_y': transformed[3],
            'center_x': (transformed[0] + transformed[1]) / 2,
            'center_y': (transformed[2] + transformed[3]) / 2,
        }
        poly = self._poly_from_shape(shape, placement)
        return {'bbox': bbox, 'poly': poly}

    # Shape / placement traversal

    def _find_shape_with_placement(self, obj, parent_placement):
        """Return (accumulated_placement, Shape) or (None, None).

        Walks BoundaryObject -> Group children -> raw Shape, accumulating
        placements so the caller can transform vertices to world space.

        IMPORTANT: Group recursion is checked BEFORE raw Shape because
        App::Part.Shape returns a compound in global coordinates (already
        includes the container's Placement).  Using it with _transform_bbox
        would double-apply the placement, causing mirror/drift on clamp.
        """
        if parent_placement is None:
            current = obj.Placement
        else:
            current = parent_placement.multiply(obj.Placement)

        if hasattr(obj, "BoundaryObject") and obj.BoundaryObject and hasattr(obj.BoundaryObject, "Shape"):
            full = current.multiply(obj.BoundaryObject.Placement)
            return full, obj.BoundaryObject.Shape

        if hasattr(obj, "Group"):
            for child in obj.Group:
                result_placement, result_shape = self._find_shape_with_placement(child, current)
                if result_shape:
                    return result_placement, result_shape

        if hasattr(obj, "Shape") and obj.Shape:
            return current, obj.Shape

        return None, None

    # Polygon extraction

    @staticmethod
    def _poly_from_shape(shape, placement):
        """Build a world-space Shapely Polygon from a FreeCAD shape.

        Extracts wire vertices (exterior + holes), transforms them through
        *placement*, and constructs a Shapely Polygon.  Returns None if
        Shapely is unavailable or vertex extraction fails.
        """
        try:
            from shapely.geometry import Polygon as ShapelyPolygon

            wires = shape.Wires if hasattr(shape, 'Wires') and shape.Wires else []
            if not wires:
                return None

            Vec = type(placement.Base)

            def wire_coords(wire):
                pts = []
                for v in wire.Vertexes:
                    w = placement.multVec(Vec(v.X, v.Y, 0))
                    pts.append((w.x, w.y))
                return pts

            exterior = wire_coords(wires[0])
            if len(exterior) < 3:
                return None

            holes = [wire_coords(w) for w in wires[1:] if len(w.Vertexes) >= 3]

            poly = ShapelyPolygon(exterior, holes)
            if not poly.is_valid:
                poly = poly.buffer(0)
            return poly if not poly.is_empty else None
        except Exception:
            return None

    # Intersection tests

    def _entries_intersect(self, entry_a, entry_b):
        """Two-phase intersection test.

        Phase 1 — AABB fast rejection: if bounding boxes don't overlap the
        shapes definitely don't intersect.
        Phase 2 — Shapely narrow phase: when boxes *do* overlap and both
        entries have polygon geometry, use the actual polygon shapes.
        Falls back to True (assume overlap) when polygons are absent.
        """
        if not self._bboxes_intersect(entry_a['bbox'], entry_b['bbox']):
            return False
        poly_a = entry_a.get('poly')
        poly_b = entry_b.get('poly')
        if poly_a is not None and poly_b is not None:
            return poly_a.intersects(poly_b)
        return True  # no polygon — conservatively treat bbox overlap as intersection

    # Geometry utilities

    @staticmethod
    def _transform_bbox(bb, placement):
        """Transform 4 local BoundBox corners (XY plane) through *placement*
        and return (min_x, max_x, min_y, max_y)."""
        try:
            corners = [
                placement.multVec(type(placement.Base)(bb.XMin, bb.YMin, 0)),
                placement.multVec(type(placement.Base)(bb.XMax, bb.YMin, 0)),
                placement.multVec(type(placement.Base)(bb.XMin, bb.YMax, 0)),
                placement.multVec(type(placement.Base)(bb.XMax, bb.YMax, 0)),
            ]
        except (TypeError, AttributeError, NameError):
            import FreeCAD
            corners = [
                placement.multVec(FreeCAD.Vector(bb.XMin, bb.YMin, 0)),
                placement.multVec(FreeCAD.Vector(bb.XMax, bb.YMin, 0)),
                placement.multVec(FreeCAD.Vector(bb.XMin, bb.YMax, 0)),
                placement.multVec(FreeCAD.Vector(bb.XMax, bb.YMax, 0)),
            ]
        xs = [v.x for v in corners]
        ys = [v.y for v in corners]
        return (min(xs), max(xs), min(ys), max(ys))

    @staticmethod
    def _bboxes_intersect(bb1, bb2):
        """Check if two bbox dicts overlap. Strict inequality: touching edges don't count."""
        return not (bb1['max_x'] <= bb2['min_x'] or
                    bb1['min_x'] >= bb2['max_x'] or
                    bb1['max_y'] <= bb2['min_y'] or
                    bb1['min_y'] >= bb2['max_y'])
