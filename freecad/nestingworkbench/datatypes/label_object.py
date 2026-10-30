# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/datatypes/label_object.py

"""
This module defines a custom FreeCAD scripted object for representing a text label.
"""

import math
import os

try:
    import FreeCAD
except ImportError:
    FreeCAD = None

try:
    import Part
except ImportError:
    Part = None

from freecad.nestingworkbench import nw_logger

_FONT_SUFFIXES = (".ttc", ".ttf", ".otf", ".pfb")

# Label glyph geometry, keyed by (font_path, font file mtime, size). Each entry is
# {"fill": bool, "cap_height": float, "glyphs": {char: (shapes, x_min, left)}}: the
# character's shapes already scaled to the label size, the unscaled XMin of its wires
# where it was first built, and the scaled shapes' optimal XMin (None when it has no
# shapes). Only Sheet.draw uses it, on the main thread, so it has no lock. It lives for
# the session and is bounded by fonts × sizes × distinct characters. A font file
# rewritten in place gets a new mtime, and therefore a new entry.
_glyph_cache = {}

class LabelObject:
    """A scripted object representing a text label."""

    def __init__(self, obj):
        """Called when a new object is created."""
        obj.Proxy = self
        obj.ViewObject.Proxy = 0 # Use the default view provider

    def execute(self, fp):
        """Called on recompute. Does nothing as shape is set externally."""
        pass

class ViewProviderLabel:
    """A view provider for the LabelObject."""

    def __init__(self, vobj):
        """Called when the view object is created."""
        vobj.Proxy = self

    def attach(self, vobj):
        """Set up the view object's properties."""
        vobj.ShapeColor = (0.9, 0.9, 0.9) # Light gray


def _make_faces(wires):
    """Faces for one character's wires. Same face makers, order and orientation fix as
    Draft's ShapeString.make_faces (FreeCAD 26.3), so labels keep Draft's geometry."""
    wirelist = []
    for wire in wires:
        connected = Part.Compound(wire.Edges).connectEdgesToWires()
        if connected.Wires[0].isClosed():
            wirelist.append(connected.Wires[0])
    if not wirelist:
        nw_logger.warn("Label: face creation failed for one character")
        return []
    for maker in (
        "Part::FaceMakerBullseye",
        "Part::FaceMakerCheese",
        "Part::FaceMakerSimple",
        "Part::FaceMakerUnified",
    ):
        try:
            faces = Part.makeFace(wirelist, maker).Faces
            for face in faces:
                face.validate()
            break
        except (Part.OCCError, ValueError):
            continue
    else:
        nw_logger.warn("Label: face creation failed for one character")
        return []
    for face in faces:
        try:
            if face.normalAt(0, 0).z < 0:
                face.reverse()
        except Exception as e:
            nw_logger.debug(f"[Label] face orientation check skipped: {e}")
    return faces


def _font_entry(font_path, size):
    """The glyph-cache entry for this font and size, created on first use. Returns None
    when Draft's ShapeString would reject the font file."""
    if (not font_path
            or os.path.splitext(font_path)[1].lower() not in _FONT_SUFFIXES
            or not os.path.isfile(font_path)):
        return None
    key = (font_path, os.path.getmtime(font_path), size)
    entry = _glyph_cache.get(key)
    if entry is None:
        # Stick-font probe, as in ShapeString.execute: a font whose "L" doesn't make
        # solid faces keeps its characters as wires.
        probe = Part.makeWireString("L", font_path, 1, 0)[0]
        probe_faces = _make_faces(probe)
        fill = bool(probe_faces)
        if fill:
            probe_box = Part.Compound(probe).BoundBox
            factor = 1 / probe_box.YLength
            fill = (sum(face.Area for face in probe_faces) > 0.03 / factor ** 2
                    and math.isclose(probe_box.DiagonalLength,
                                     Part.Compound(probe_faces).BoundBox.DiagonalLength,
                                     rel_tol=1e-7))
        cap_height = Part.Compound(Part.makeWireString("M", font_path, size, 0)[0]).BoundBox.YMax
        entry = _glyph_cache[key] = {"fill": fill, "cap_height": cap_height, "glyphs": {}}
    return entry


def _scaled_glyph(shapes, x_min, scale):
    """A glyph-cache value: copies of `shapes` scaled by `scale` about the origin, the
    unscaled XMin of the character's wires, and the copies' optimal XMin (None when the
    character has no shapes)."""
    scaled = []
    for shape in shapes:
        copy = shape.copy()
        copy.scale(scale)
        scaled.append(copy)
    left = min(copy.optimalBoundingBox().XMin for copy in scaled) if scaled else None
    return scaled, x_min, left


def _build_uncached(chars, entry, size):
    """The label built without the glyph cache, for a makeWireString result that doesn't
    hold one wire list per character: the whole compound scaled, then shifted so its
    optimal XMin is 0. Returns an empty Part.Shape() when no character has geometry."""
    shapes = []
    for wires in chars:
        if wires:
            shapes.extend(_make_faces(wires) if entry["fill"] else wires)
    if not shapes:
        return Part.Shape()
    compound = Part.Compound(shapes)
    compound.scale(size / entry["cap_height"])
    left_margin = FreeCAD.Vector(-compound.optimalBoundingBox().XMin, 0, 0)
    sub_shapes = compound.SubShapes
    for sub_shape in sub_shapes:
        sub_shape.translate(left_margin)
    return Part.Compound(sub_shapes)


def build_label_shape(text, font_path, size):
    """The label for `text` as a Part compound: faces, or wires for a stick font.

    Equal to the Shape of a Draft ShapeString with default properties (Bottom-Left,
    Cap Height reference, ScaleToSize, no tracking, no fuse), without creating a
    document object. Each character is built and scaled once per font and size; later
    uses place the cached shapes with translated(), which shares their geometry, at the
    x position Part.makeWireString gives, shifted so the label's optimal XMin is 0.
    Returns an empty Part.Shape() when the font is unusable or no character has
    geometry, as Draft did.
    """
    entry = _font_entry(font_path, size)
    if entry is None:
        nw_logger.error(f"Label font not usable: {font_path}")
        return Part.Shape()
    chars = Part.makeWireString(text, font_path, size, 0)
    # makeWireString returns one wire list per character of `text`. If that ever
    # doesn't hold, characters can't be keyed, so build this label uncached.
    if len(chars) != len(text):
        return _build_uncached(chars, entry, size)
    scale = size / entry["cap_height"]
    placed = []
    for char, wires in zip(text, chars):
        if not wires:
            continue
        x_min = Part.Compound(wires).BoundBox.XMin
        glyph = entry["glyphs"].get(char)
        if glyph is None:
            shapes = _make_faces(wires) if entry["fill"] else list(wires)
            glyph = entry["glyphs"][char] = _scaled_glyph(shapes, x_min, scale)
        glyph_shapes, glyph_x, glyph_left = glyph
        if not glyph_shapes:
            continue
        # Single-line text: characters differ only in x, scaled with everything else.
        placed.append((glyph_shapes, scale * (x_min - glyph_x), glyph_left))
    if not placed:
        return Part.Shape()
    left_margin = -min(offset + left for _, offset, left in placed)
    shapes = []
    for glyph_shapes, offset, _ in placed:
        move = FreeCAD.Vector(offset + left_margin, 0, 0)
        # translated() returns a moved copy and leaves the cached shape where it is.
        shapes.extend(shape.translated(move) for shape in glyph_shapes)
    return Part.Compound(shapes)


def create_label_object(doc, name="Label"):
    """Helper function to create a new LabelObject in doc."""
    obj = doc.addObject("Part::FeaturePython", name)
    LabelObject(obj)
    if FreeCAD.GuiUp:
        ViewProviderLabel(obj.ViewObject)
    return obj