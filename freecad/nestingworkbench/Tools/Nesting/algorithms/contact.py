# SPDX-License-Identifier: LGPL-2.1-or-later
"""Contact and direction measures for the Scoring weights (NLA rulings 27-28).

Contact is the length of a part's spaced outline that lies within CONTACT_TOL of a placed part's
outline or a sheet edge, as a fraction of its perimeter. Each placed part is tested on its own;
nothing is unioned (AGENTS.md). FreeCAD-free; runs in worker processes.
"""
import numpy as np
import shapely
from shapely.affinity import translate
from shapely.geometry import box

CONTACT_TOL = 0.5  # mm


def direction_deviation(angle_deg, target_deg):
    """0 when the part is at target_deg or target_deg + 180°, 1 at 90° off (ruling 28)."""
    d = abs(float(angle_deg) - float(target_deg)) % 180.0
    return min(d, 180.0 - d) / 90.0


def edge_lengths(poly, tol=CONTACT_TOL):
    """(left, right, bottom, top): length of poly's boundary within tol of each side of its own
    bounding box: what it shares with a sheet edge when it sits flush against that edge."""
    minx, miny, maxx, maxy = poly.bounds
    b = poly.boundary
    strips = (box(minx - tol, miny - tol, minx + tol, maxy + tol),
              box(maxx - tol, miny - tol, maxx + tol, maxy + tol),
              box(minx - tol, miny - tol, maxx + tol, miny + tol),
              box(minx - tol, maxy - tol, maxx + tol, maxy + tol))
    return tuple(float(b.intersection(s).length) for s in strips)


def shared_fractions(pts, rotated_poly, centroid, extents, sheet, tol=CONTACT_TOL):
    """Per candidate centroid in pts (N x 2, sheet frame): shared outline length / perimeter, in
    [0, 1], for rotated_poly moved so its centroid sits on the candidate."""
    n = len(pts)
    shared = np.zeros(n)
    if not n:
        return shared
    perimeter = rotated_poly.length
    if perimeter <= 0:
        return shared
    rminx, rminy, rmaxx, rmaxy = extents
    left, right, bottom, top = edge_lengths(rotated_poly, tol)
    shared += left * (np.abs(pts[:, 0] + rminx) <= tol)
    shared += right * (np.abs(pts[:, 0] + rmaxx - sheet.width) <= tol)
    shared += bottom * (np.abs(pts[:, 1] + rminy) <= tol)
    shared += top * (np.abs(pts[:, 1] + rmaxy - sheet.height) <= tol)
    if sheet.parts:
        boundary = rotated_poly.boundary
        cx, cy = centroid
        lines = np.array([translate(boundary, x - cx, y - cy) for x, y in pts], dtype=object)
        for pp in sheet.parts:
            zone = pp.shape.polygon.buffer(tol)
            zx0, zy0, zx1, zy1 = zone.bounds
            idx = np.flatnonzero((pts[:, 0] + rmaxx >= zx0) & (pts[:, 0] + rminx <= zx1) &
                                 (pts[:, 1] + rmaxy >= zy0) & (pts[:, 1] + rminy <= zy1))
            if len(idx):
                shared[idx] += shapely.length(shapely.intersection(lines[idx], zone))
    return np.minimum(shared / perimeter, 1.0)


def layout_contact_fraction(sheets, tol=CONTACT_TOL):
    """Σ shared outline length / Σ perimeter over every placed part of every sheet. Each part
    counts its own shared length, so a shared edge counts once from each side. 0 with no parts."""
    total_shared = total_perimeter = 0.0
    for sheet in sheets:
        polys = [pp.shape.polygon for pp in sheet.parts]
        if not polys:
            continue
        edge = box(0, 0, sheet.width, sheet.height).boundary.buffer(tol)
        zones = [p.buffer(tol) for p in polys]
        for i, poly in enumerate(polys):
            b = poly.boundary
            total_perimeter += poly.length
            s = b.intersection(edge).length
            bx0, by0, bx1, by1 = poly.bounds
            for j, zone in enumerate(zones):
                if j == i:
                    continue
                zx0, zy0, zx1, zy1 = zone.bounds
                if bx1 >= zx0 and bx0 <= zx1 and by1 >= zy0 and by0 <= zy1:
                    s += b.intersection(zone).length
            total_shared += min(s, poly.length)
    return total_shared / total_perimeter if total_perimeter > 0 else 0.0


def layout_direction_deviation(sheets, target_deg):
    """Mean direction_deviation over every placed part, 0 with no parts."""
    devs = [direction_deviation(pp.angle, target_deg) for s in sheets for pp in s.parts]
    return sum(devs) / len(devs) if devs else 0.0
