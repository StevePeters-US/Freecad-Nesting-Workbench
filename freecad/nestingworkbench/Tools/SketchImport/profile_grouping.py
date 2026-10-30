# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/SketchImport/profile_grouping.py

"""Grouping of imported closed loops into parts: outline plus direct holes (ISK-004).

Pure geometry on shapely polygons, so it runs without FreeCAD. Loop counts per file
are small (tens to low hundreds), so the containment test is pairwise.
"""

from collections import namedtuple

PartLoops = namedtuple("PartLoops", "outline holes")


def _parents(polys, i):
    """Indices of every loop that strictly contains loop i, smallest first."""
    probe = polys[i].representative_point()
    found = [
        j for j, p in enumerate(polys)
        if j != i and p.area > polys[i].area and p.contains(probe)
    ]
    return sorted(found, key=lambda j: polys[j].area)


def group_loops(polys):
    """Returns PartLoops for each part, outlines in input order.

    Even containment depth is an outline (an island inside a hole is its own part);
    odd depth is a hole of its immediate parent.
    """
    parents = [_parents(polys, i) for i in range(len(polys))]
    parts = {}
    for i, chain in enumerate(parents):
        if len(chain) % 2 == 0:
            parts.setdefault(i, [])
    for i, chain in enumerate(parents):
        if len(chain) % 2 == 1:
            parts[chain[0]].append(i)
    return [PartLoops(i, sorted(parts[i])) for i in sorted(parts)]


def owner_of_point(polys, parts, point):
    """Index into parts of the smallest outline containing point, or None.

    point is an (x, y) tuple; a point inside a hole still belongs to that hole's part,
    so an engraving across a cut-out stays with its part.
    """
    from shapely.geometry import Point
    p = Point(point)
    hits = [k for k, part in enumerate(parts) if polys[part.outline].covers(p)]
    return min(hits, key=lambda k: polys[parts[k].outline].area) if hits else None
