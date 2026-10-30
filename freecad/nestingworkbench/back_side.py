# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/back_side.py
"""A flipped sheet's back side: the board turned over to machine its back.

Turning a board over is a rotation by FLIP_ANGLE (180 degrees) about the flip
axis: a line along X or Y through the sheet's centre, at the middle of the
parts' Z range (SVF decision 7). The angle is a parameter because indexed
rotary machining may later use other angles. Parts and outlines are rotated;
labels only move to their rotated position, so they stay readable.

Everything is placed from the front and back boundaries' positions
(front_base, back_base), so it works stacked or unstacked.
"""

from collections import namedtuple

# Guarded like datatypes/sheet.py, which imports this module: GA worker processes
# import sheet.py without FreeCAD.
try:
    import FreeCAD
except ImportError:
    FreeCAD = None

try:
    import Part
except ImportError:
    Part = None

from freecad.nestingworkbench import nw_logger, nw_settings
from .constants import (
    BACK_BOUNDARY_PREFIX, BACK_GROUP_PREFIX, FLIPPED_PREFIX,
    PREF_BACK_SHEET_COLOR,
    PROP_FLIP_ANGLE, PROP_FLIP_AXIS, PROP_FLIP_Z_MID, PROP_FLOAT, PROP_STRING,
)
from .freecad_helpers import (
    create_part_feature, find_back_boundary, find_back_group, find_sheet_boundary,
    get_nested_containers,
)

# How one back side is laid out. axis: "x" or "y"; angle: degrees; z_mid: height
# of the rotation axis; front_base/back_base: the two boundaries' Placement.Base;
# width/height: the sheet's size.
BackFrame = namedtuple("BackFrame", "axis angle z_mid front_base back_base width height")


def axis_rotation(axis, angle, width, height, z_mid):
    """Sheet-local Placement rotating by *angle* degrees about the sheet's centre
    line along *axis* ("x": through (*, H/2, z_mid); "y": through (W/2, *, z_mid)).
    With 180, "x" maps (x, y, z) -> (x, H - y, 2*z_mid - z). Raises ValueError for
    any other axis."""
    if axis == "x":
        centre, direction = FreeCAD.Vector(0, height / 2, z_mid), FreeCAD.Vector(1, 0, 0)
    elif axis == "y":
        centre, direction = FreeCAD.Vector(width / 2, 0, z_mid), FreeCAD.Vector(0, 1, 0)
    else:
        raise ValueError(f"flip axis must be 'x' or 'y', not {axis!r}")
    return FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), FreeCAD.Rotation(direction, angle), centre)


def to_back(frame):
    """World Placement taking a front-side position to its back-side position:
    into the front sheet's frame, axis_rotation, out into the back sheet's frame."""
    return (FreeCAD.Placement(FreeCAD.Vector(frame.back_base.x, frame.back_base.y, 0), FreeCAD.Rotation())
            .multiply(axis_rotation(frame.axis, frame.angle, frame.width, frame.height, frame.z_mid))
            .multiply(FreeCAD.Placement(FreeCAD.Vector(-frame.front_base.x, -frame.front_base.y, 0),
                                        FreeCAD.Rotation())))


def back_label_placement(label_shape, world_placement, frame):
    """World Placement for a readable back-side copy of a label.

    The label keeps its orientation and Z and is NOT rotated. It is only moved
    in XY, so that its bounding-box centre lands on the XY of where to_back
    takes the front label's centre."""
    placed = label_shape.copy()
    placed.Placement = world_placement
    c = placed.BoundBox.Center
    target = to_back(frame).multVec(c)
    shift = FreeCAD.Vector(target.x - c.x, target.y - c.y, 0)
    return FreeCAD.Placement(shift, FreeCAD.Rotation()).multiply(world_placement)


def _flippable(obj):
    return hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull()


def _back_frame(sheet_group, back_group):
    """The BackFrame recorded on *back_group*, or None with an error logged."""
    front = find_sheet_boundary(sheet_group)
    back = find_back_boundary(back_group)
    if front is None or back is None:
        nw_logger.error(f"Back side of '{sheet_group.Label}': a sheet or back boundary is missing.")
        return None
    bb = front.Shape.BoundBox
    return BackFrame(getattr(back_group, PROP_FLIP_AXIS), float(getattr(back_group, PROP_FLIP_ANGLE)),
                     float(getattr(back_group, PROP_FLIP_Z_MID)), front.Placement.Base,
                     back.Placement.Base, bb.XLength, bb.YLength)


def _add_flipped(doc, back_group, container, child, frame):
    """Add the back-side copy of one part_, outline_, boundary_ or label_ object; others are skipped."""
    world = container.Placement.multiply(child.Placement)
    name = f"{FLIPPED_PREFIX}{child.Label}"
    if child.Label.startswith(("part_", "outline_")):
        obj = create_part_feature(doc, name, child.Shape.copy(), group=back_group, visible=True)
        obj.Placement = to_back(frame).multiply(world)
    elif child.Label.startswith("boundary_"):
        # The Show Bounds outline: shown or hidden with its container, red like the front one.
        obj = create_part_feature(doc, name, child.Shape.copy(), group=back_group,
                                  visible=bool(getattr(container, "ShowBounds", False)))
        obj.Placement = to_back(frame).multiply(world)
        if FreeCAD.GuiUp and getattr(obj, "ViewObject", None):
            obj.ViewObject.LineColor = (1.0, 0.0, 0.0)
            obj.ViewObject.LineWidth = 2.0
    elif child.Label.startswith("label_"):
        obj = create_part_feature(doc, name, child.Shape.copy(), group=back_group, visible=True)
        obj.Placement = back_label_placement(child.Shape, world, frame)


def draw_back_side(doc, sheet_group, number, axis, angle, gap):
    """Create Back_<number> inside a freshly drawn Sheet_<number>; returns the group.

    The Back_Boundary_<number> plane, tinted with the Sheet back side color setting, is directly above
    the sheet boundary (+Y, *gap* up). Every part_ and outline_ object in the
    sheet's nested containers gets a copy rotated by *angle* about *axis*
    (to_back), every boundary_ a copy whose visibility follows the container's
    ShowBounds, and every label_ a readable moved copy (back_label_placement).
    The group records FlipAxis, FlipAngle and FlipZMid, so refresh_back_outlines
    places outlines the same way.
    """
    front = find_sheet_boundary(sheet_group)
    bb = front.Shape.BoundBox
    width, height = bb.XLength, bb.YLength
    front_base = front.Placement.Base
    back_base = FreeCAD.Vector(front_base.x, front_base.y + height + gap, 0)

    pairs = [(c, child) for c in get_nested_containers(sheet_group)
             for child in c.Group if _flippable(child)]
    boxes = []
    for container, child in pairs:
        if child.Label.startswith("part_"):
            placed = child.Shape.copy()
            placed.Placement = container.Placement.multiply(child.Placement)
            boxes.append(placed.BoundBox)
    z_mid = (min(b.ZMin for b in boxes) + max(b.ZMax for b in boxes)) / 2 if boxes else 0.0

    back_group = doc.addObject("App::DocumentObjectGroup", f"{BACK_GROUP_PREFIX}{number}")
    sheet_group.addObject(back_group)
    back_group.addProperty(PROP_STRING, PROP_FLIP_AXIS, "Nesting", "Axis the sheet is turned over about")
    back_group.addProperty(PROP_FLOAT, PROP_FLIP_ANGLE, "Nesting", "Rotation about that axis, in degrees")
    back_group.addProperty(PROP_FLOAT, PROP_FLIP_Z_MID, "Nesting", "Height of the rotation axis")
    setattr(back_group, PROP_FLIP_AXIS, axis)
    setattr(back_group, PROP_FLIP_ANGLE, float(angle))
    setattr(back_group, PROP_FLIP_Z_MID, z_mid)

    boundary = create_part_feature(doc, f"{BACK_BOUNDARY_PREFIX}{number}",
                                   Part.makePlane(width, height), group=back_group, visible=True)
    boundary.Placement = FreeCAD.Placement(back_base, FreeCAD.Rotation())
    if FreeCAD.GuiUp and hasattr(boundary, "ViewObject") and boundary.ViewObject:
        boundary.ViewObject.Transparency = 75
        boundary.ViewObject.ShapeColor = nw_settings.get_color(PREF_BACK_SHEET_COLOR)

    frame = BackFrame(axis, float(angle), z_mid, front_base, back_base, width, height)
    for container, child in pairs:
        _add_flipped(doc, back_group, container, child, frame)
    return back_group


def refresh_back_outlines(doc, sheet_group):
    """Rebuild the back side's flip_outline_* objects from the sheet's current
    outline_* objects. Does nothing for a sheet with no back side. Uses the axis,
    angle and axis height recorded on Back_<n> (_back_frame) and the boundaries'
    current positions, so it is right whether the layout is stacked or not."""
    back_group = find_back_group(sheet_group)
    if back_group is None:
        return
    frame = _back_frame(sheet_group, back_group)
    if frame is None:
        return
    for obj in list(back_group.Group):
        if obj.Label.startswith(f"{FLIPPED_PREFIX}outline_"):
            doc.removeObject(obj.Name)
    for container in get_nested_containers(sheet_group):
        for child in container.Group:
            if child.Label.startswith("outline_") and _flippable(child):
                _add_flipped(doc, back_group, container, child, frame)
