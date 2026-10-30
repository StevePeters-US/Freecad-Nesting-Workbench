# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/SketchImport/sketch_importer.py

"""Import a DXF or SVG file as one sketch per part (ISK-).

FreeCAD's importers make one object per edge (DXF) or per element (SVG). The file is
read into a hidden temp document with those importers, so units and the user's Draft
import preferences still apply, and its edges are regrouped here into parts.
"""

import os
from collections import namedtuple

import FreeCAD
import Part
from shapely.geometry import Polygon as ShapelyPolygon

from ... import nw_logger
from ...ui_helpers import length_to_text
from ..Nesting.shape_processor import discretize_ring
from .profile_grouping import group_loops, owner_of_point

JOIN_TOL = 0.01  # mm: endpoints closer than this are the same point
PLANE_TOL = 1e-6  # mm: |z| above this is off the XY plane
EXACT_TOL = 1e-7  # mm, Precision::Confusion: ends this close already join in sketch.Shape

ImportResult = namedtuple("ImportResult", "sketches construction_sketch parts construction_edges skipped")


def load_edges(path):
    """Returns (edges, off_plane_count): every XY-plane edge of the file in world coordinates.

    DXF goes through the C++ reader rather than importDXF.insert, which opens a modal
    options dialog and runs Draft post-processing under the GUI (ISK-020). The reader
    puts its layer objects in the active document, so the temp document is active for
    the read, and the caller's active document is restored afterwards: closing the temp
    document otherwise left none, which disabled every Nesting command.
    """
    import Import
    import importSVG
    prev = FreeCAD.ActiveDocument  # before newDocument, which may make the temp one active
    tmp = FreeCAD.newDocument("NWSketchImportTmp", hidden=True, temp=True)
    try:
        if path.lower().endswith(".svg"):
            importSVG.insert(path, tmp.Name)
        else:
            FreeCAD.setActiveDocument(tmp.Name)
            Import.readDXF(path, tmp.Name)
        tmp.recompute()
        edges, off_plane = [], 0
        for obj in tmp.Objects:
            if not obj.isDerivedFrom("Part::Feature") or obj.Shape.isNull():
                continue
            shape = obj.Shape.copy()
            shape.Placement = obj.getGlobalPlacement()
            for edge in shape.Edges:
                bb = edge.BoundBox
                if abs(bb.ZMin) > PLANE_TOL or abs(bb.ZMax) > PLANE_TOL:
                    off_plane += 1
                    continue
                edges.append(edge.copy())
        return edges, off_plane
    finally:
        FreeCAD.closeDocument(tmp.Name)
        if prev is not None and prev.Name in FreeCAD.listDocuments():
            FreeCAD.setActiveDocument(prev.Name)


def drop_duplicate_edges(edges):
    """Returns (kept, dropped): edges matching an earlier edge's ends and midpoint are dropped.

    CAD exports often draw a contour twice; kept, the copy became a second identical
    part that would be nested and cut twice (ISK-016). Edges are bucketed by midpoint
    cell, and the 3x3 neighbouring cells are checked, so this stays linear.
    """
    def cell(p):
        return (round(p.x / JOIN_TOL), round(p.y / JOIN_TOL))

    buckets = {}
    kept, dropped = [], 0
    for e in edges:
        a = e.valueAt(e.FirstParameter)
        b = e.valueAt(e.LastParameter)
        m = e.valueAt((e.FirstParameter + e.LastParameter) / 2)
        cx, cy = cell(m)
        near = [k for dx in (-1, 0, 1) for dy in (-1, 0, 1) for k in buckets.get((cx + dx, cy + dy), ())]
        if any(
            (m - km).Length <= JOIN_TOL and (
                ((a - ka).Length <= JOIN_TOL and (b - kb).Length <= JOIN_TOL)
                or ((a - kb).Length <= JOIN_TOL and (b - ka).Length <= JOIN_TOL)
            )
            for ka, kb, km in near
        ):
            dropped += 1
            continue
        buckets.setdefault((cx, cy), []).append((a, b, m))
        kept.append(e)
    return kept, dropped


def chain_wires(edges):
    """Wires of connected edges, joined within JOIN_TOL.

    sortEdges chains edges within the tolerance, but Part.Wire joins them at OCC's 1e-7
    vertex tolerance and silently drops what doesn't fit: a DXF whose arc ends sit 1e-6 from
    its line ends lost 3 of 8 edges and imported as no part at all (ISK-021).
    """
    chains = Part.sortEdges(edges, JOIN_TOL)
    for chain in chains:
        for edge in chain:
            edge.fixTolerance(JOIN_TOL)
    return [Part.Wire(chain) for chain in chains]


def edge_to_geometry(edge):
    """The Part.Geometry a sketch stores for edge."""
    curve = edge.Curve
    name = type(curve).__name__
    start = edge.valueAt(edge.FirstParameter)
    end = edge.valueAt(edge.LastParameter)
    if name == "Line":
        return Part.LineSegment(start, end)
    if name in ("Circle", "Ellipse"):
        circular = name == "Circle" or abs(curve.MajorRadius - curve.MinorRadius) < 1e-9
        radius = curve.Radius if name == "Circle" else curve.MajorRadius
        if edge.isClosed():
            return Part.Circle(curve.Center, FreeCAD.Vector(0, 0, 1), radius) if circular else curve.copy()
        if circular:
            mid = edge.valueAt((edge.FirstParameter + edge.LastParameter) / 2)
            return Part.Arc(start, mid, end)
        return Part.ArcOfEllipse(curve.copy(), edge.FirstParameter, edge.LastParameter)
    return curve.toBSpline(edge.FirstParameter, edge.LastParameter)


def add_wire(sketch, wire, construction):
    """Adds wire's edges to sketch in one call; returns their geometry ids.

    Each endpoint takes part in one join at most: a two-edge loop paired the same ends
    twice and made a redundant constraint (ISK-023). A loop whose joins all already
    coincide gets no constraints, since Coincident constraints made a 3000-edge sketch
    take 17 s to solve. One inexact join constrains the whole loop: the solver moving
    one arc would otherwise open that arc's other, unconstrained end.
    """
    import Sketcher
    first = sketch.GeometryCount
    sketch.addGeometry([edge_to_geometry(e) for e in wire.OrderedEdges], construction)
    ids = list(range(first, sketch.GeometryCount))
    pairs = list(zip(ids, ids[1:]))
    if wire.isClosed() and len(ids) > 1:
        pairs.append((ids[-1], ids[0]))
    used, joins = set(), []
    for a, b in pairs:
        # Position ids 1/2 as Sketcher stores them: a clockwise arc is kept
        # counter-clockwise with its ends swapped, so ends are read, not assumed.
        ends_a = [sketch.getPoint(a, 1), sketch.getPoint(a, 2)]
        ends_b = [sketch.getPoint(b, 1), sketch.getPoint(b, 2)]
        options = sorted(
            ((pa - pb).Length, i + 1, j + 1)
            for i, pa in enumerate(ends_a) for j, pb in enumerate(ends_b)
            if (a, i + 1) not in used and (b, j + 1) not in used
        )
        if options and options[0][0] <= JOIN_TOL:
            gap, pa, pb = options[0]
            used.update({(a, pa), (b, pb)})
            joins.append((gap, Sketcher.Constraint("Coincident", a, pa, b, pb)))
        else:
            nw_logger.warn(f"[SketchImport] '{sketch.Label}': edges {a} and {b} are not joined")
    if any(gap > EXACT_TOL for gap, _ in joins):
        sketch.addConstraint([c for _, c in joins])
    return ids


def import_file(doc, path):
    """Imports path into doc as one sketch per part, in one undo step; returns an ImportResult."""
    stem = os.path.splitext(os.path.basename(path))[0]
    edges, off_plane = load_edges(path)
    edges, duplicate_edges = drop_duplicate_edges(edges)
    if not edges:
        nw_logger.warn(
            f"Import '{os.path.basename(path)}': no XY-plane geometry found"
            + (f" ({off_plane} edge(s) off the XY plane)" if off_plane else "")
            + "; nothing created."
        )
        return ImportResult([], None, 0, 0, {"empty_file": 1, **({"off_plane": off_plane} if off_plane else {})})

    doc.openTransaction(f"Import sketches from {os.path.basename(path)}")
    try:
        wires = chain_wires(edges)
        closed_wires = [w for w in wires if w.isClosed()]
        open_wires = [w for w in wires if not w.isClosed()]

        polys = []
        skipped_self_intersecting = 0
        for w in closed_wires:
            ring = discretize_ring(w, 0.05)
            poly = ShapelyPolygon(ring) if ring else ShapelyPolygon()
            if not poly.is_valid:
                skipped_self_intersecting += 1
            polys.append(poly)

        parts_loops = group_loops(polys)


        # Sort parts by outline bounds (minx, then miny) for stable naming
        def _part_sort_key(part):
            bounds = polys[part.outline].bounds
            return (bounds[0], bounds[1]) if bounds else (0.0, 0.0)

        sorted_parts = sorted(parts_loops, key=_part_sort_key)

        sketches = []
        for idx, part in enumerate(sorted_parts, start=1):
            sk_name = f"{stem}_{idx:03d}"
            sk = doc.addObject("Sketcher::SketchObject", "SketchImportPart")
            sk.Label = sk_name
            # Outline
            add_wire(sk, closed_wires[part.outline], construction=False)
            # Holes
            for h_idx in part.holes:
                add_wire(sk, closed_wires[h_idx], construction=False)
            sketches.append(sk)

        # Open wires assigned to owning parts or <stem>_construction
        construction_edge_count = 0
        construction_sk = None

        for w in open_wires:
            edge_count = len(w.OrderedEdges)
            construction_edge_count += edge_count
            mid_pt = (0.0, 0.0)
            if w.OrderedEdges:
                first_e = w.OrderedEdges[0]
                mid_val = first_e.valueAt((first_e.FirstParameter + first_e.LastParameter) / 2.0)
                mid_pt = (mid_val.x, mid_val.y)
            owner_idx = owner_of_point(polys, sorted_parts, mid_pt)
            if owner_idx is not None and owner_idx < len(sketches):
                add_wire(sketches[owner_idx], w, construction=True)
            else:
                if construction_sk is None:
                    construction_sk = doc.addObject("Sketcher::SketchObject", "SketchImportConstruction")
                    construction_sk.Label = f"{stem}_construction"
                add_wire(construction_sk, w, construction=True)

        doc.recompute()
        doc.commitTransaction()

        skipped = {}
        if off_plane:
            skipped["off_plane"] = off_plane
        if skipped_self_intersecting:
            skipped["self_intersecting"] = skipped_self_intersecting
        if duplicate_edges:
            skipped["duplicate_edges"] = duplicate_edges

        # Summary info logging
        bb = FreeCAD.BoundBox()
        for sk in sketches + ([construction_sk] if construction_sk else []):
            if not sk.Shape.isNull():
                bb.add(sk.Shape.BoundBox)
        size_str = (
            f", overall size {length_to_text(bb.XLength)} x {length_to_text(bb.YLength)}"
            if bb.isValid() else ""
        )

        nw_logger.info(
            f"Imported '{os.path.basename(path)}': {len(sketches)} part sketch(es), "
            f"{construction_edge_count} construction edge(s){size_str}"
        )
        for k, count in skipped.items():
            nw_logger.warn(f"Import '{os.path.basename(path)}' skipped {count} {k} item(s)")

        return ImportResult(
            sketches=sketches,
            construction_sketch=construction_sk,
            parts=len(sketches),
            construction_edges=construction_edge_count,
            skipped=skipped,
        )
    except Exception:
        doc.abortTransaction()
        raise
