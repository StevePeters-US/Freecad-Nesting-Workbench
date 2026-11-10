# SPDX-License-Identifier: LGPL-2.1-or-later
"""Usable offcuts (NLA rulings 16-18): the disjoint, axis-aligned free rectangles left on a
sheet, and a layout's area-squared offcut value. Runs inside worker processes; imports no
FreeCAD.

Grid lines are the sheet edges plus every part's bounding-box edges, so the measure is
conservative: a cell a part's polygon overlaps at all counts as used, and the space around a
round part's corners is lost. Extraction is greedy, largest rectangle first. No union of parts
(never unioned): each part is tested only against the cells inside its own bounding box.
"""
import numpy as np
import shapely


def _polygons(parts):
    out = []
    for p in parts:
        poly = getattr(getattr(p, 'shape', None), 'polygon', None)
        if poly is not None and not poly.is_empty:
            out.append(poly)
    return out


def _grid_lines(polys, width, height):
    xs, ys = {0.0, float(width)}, {0.0, float(height)}
    for poly in polys:
        minx, miny, maxx, maxy = poly.bounds
        xs.update((min(max(minx, 0.0), width), min(max(maxx, 0.0), width)))
        ys.update((min(max(miny, 0.0), height), min(max(maxy, 0.0), height)))
    return np.array(sorted(xs)), np.array(sorted(ys))


def _occupancy(polys, xs, ys):
    """occ[j, i] is True when a part overlaps the interior of cell (xs[i]..xs[i+1], ys[j]..ys[j+1])."""
    occ = np.zeros((len(ys) - 1, len(xs) - 1), dtype=bool)
    for poly in polys:
        minx, miny, maxx, maxy = poly.bounds
        i0 = max(int(np.searchsorted(xs, minx, side="right")) - 1, 0)
        i1 = min(int(np.searchsorted(xs, maxx, side="left")), len(xs) - 1)
        j0 = max(int(np.searchsorted(ys, miny, side="right")) - 1, 0)
        j1 = min(int(np.searchsorted(ys, maxy, side="left")), len(ys) - 1)
        if i1 <= i0 or j1 <= j0:
            continue
        x0, y0 = np.meshgrid(xs[i0:i1], ys[j0:j1])
        x1, y1 = np.meshgrid(xs[i0 + 1:i1 + 1], ys[j0 + 1:j1 + 1])
        # Shrinking each cell a hair makes `intersects` mean "interiors overlap": a part
        # that only touches a cell's edge leaves it free.
        ex = np.minimum(1e-6, (x1 - x0) / 4)
        ey = np.minimum(1e-6, (y1 - y0) / 4)
        shapely.prepare(poly)
        occ[j0:j1, i0:i1] |= shapely.intersects(poly, shapely.box(x0 + ex, y0 + ey, x1 - ex, y1 - ey))
    return occ


def _largest_free_rect(occ, xs, ys):
    """(area, (i0, i1, j0, j1)) of the largest all-free block of cells, or (0.0, None).

    The histogram method, row by row: h[i] is the height of the free run ending at this row in
    column i, and a stack of increasing heights yields every maximal rectangle.
    """
    ny, nx = occ.shape
    dy = np.diff(ys)
    h = np.zeros(nx)
    hc = np.zeros(nx, dtype=int)
    best_area, best = 0.0, None
    for j in range(ny):
        free = ~occ[j]
        h = np.where(free, h + dy[j], 0.0)
        hc = np.where(free, hc + 1, 0)
        hl, hcl = h.tolist(), hc.tolist()
        stack = []  # (first column, height, height in cells), heights increasing
        for i in range(nx + 1):
            cur = hl[i] if i < nx else 0.0
            start = i
            while stack and stack[-1][1] >= cur:
                s, sh, shc = stack.pop()
                area = sh * (xs[i] - xs[s])
                if area > best_area:
                    best_area, best = area, (s, i, j - shc + 1, j + 1)
                start = s
            if i < nx and cur > 0.0:
                stack.append((start, cur, hcl[i]))
    return best_area, best


def free_rectangles(parts, width, height, min_area):
    """Disjoint free rectangles (x0, y0, x1, y1) on one sheet, largest first, each at least
    *min_area*. *parts* are placed parts with `shape.polygon` in sheet-local coordinates."""
    if min_area <= 0:
        raise ValueError(f"min_area must be > 0, got {min_area!r}")
    polys = _polygons(parts)
    xs, ys = _grid_lines(polys, width, height)
    occ = _occupancy(polys, xs, ys)
    out = []
    while True:
        area, r = _largest_free_rect(occ, xs, ys)
        if r is None or area < min_area:
            return out
        i0, i1, j0, j1 = r
        occ[j0:j1, i0:i1] = True
        out.append((float(xs[i0]), float(ys[j0]), float(xs[i1]), float(ys[j1])))


def offcut_value(sheets, min_area):
    """Σ aᵢ² / Σ sheet area over every sheet's offcuts (ruling 17); 0.0 for no sheets.
    Never exceeds the largest sheet's area: Σ a² ≤ max(a) · Σ a, and each sheet's pieces are
    disjoint, so Σ a ≤ Σ sheet area."""
    total_area = sum(s.width * s.height for s in sheets)
    if total_area <= 0:
        return 0.0
    squares = 0.0
    for s in sheets:
        for x0, y0, x1, y1 in free_rectangles(s.parts, s.width, s.height, min_area):
            squares += ((x1 - x0) * (y1 - y0)) ** 2
    return squares / total_area
