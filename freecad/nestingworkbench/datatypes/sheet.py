# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/datatypes/sheet.py

"""
This module contains the Sheet class, which represents a single bin or sheet
in the nesting layout.
"""

try:
    import FreeCAD
except ImportError:
    FreeCAD = None

try:
    import Part
except ImportError:
    Part = None
import threading

try:
    from shapely.geometry import Polygon
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

from .. import back_side
from ..constants import (
    FLIP_ANGLE, OFFCUT_COLOR, OFFCUT_GROUP_PREFIX, OFFCUT_LINE_COLOR, OFFCUT_PREFIX, OFFCUT_Z,
    PREF_FRONT_SHEET_COLOR,
)
from .label_object import build_label_shape, create_label_object
from ..freecad_helpers import calculate_label_placement, create_part_feature, recursive_delete, write_sheet_instance
from freecad.nestingworkbench import nw_logger, nw_settings

class Sheet:
    """
    Represents a single sheet (or bin) in the nesting layout. It contains
    the parts that have been placed on it.
    """
    def __init__(self, sheet_id, width, height, spacing=0):
        self.id = sheet_id
        self.width = width
        self.height = height
        self.used_area = 0.0 # Track area usage for fast filtering
        self.parts = [] # List of PlacedPart objects
        self.spacing = spacing
        # Which sequence row this sheet was cut from; survives renumbering.
        self.spec_index = sheet_id
        # World X of the sheet's left edge. The default is right only when every
        # sheet is this size; mixed sequences set it via sheet_sequence.
        self.origin_x = sheet_id * (width + spacing)
        # The sequence row (write_sheet_instance dict) this sheet is cut from,
        # set before the final draw; None during nesting.
        self.stock = None
        self.parent_group_name = None # Will store the name of the top-level layout group

    def __repr__(self):
        return f"<Sheet id={self.id}, parts={len(self.parts)}>"

    def __iter__(self):
        """Allows iterating directly over the parts on the sheet."""
        return iter(self.parts)

    def __len__(self):
        """Returns the number of parts on the sheet."""
        return len(self.parts)

    def add_part(self, placed_part):
        """Adds a part to the sheet."""
        self.parts.append(placed_part)
        self.used_area += placed_part.shape.area

    def get_origin(self):
        """
        Returns the stored world origin (bottom-left corner) of this sheet in a layout.

        Returns:
            FreeCAD.Vector or tuple: The origin vector (origin_x, 0, 0).
        """
        if FreeCAD:
            return FreeCAD.Vector(self.origin_x, 0, 0)
        return (self.origin_x, 0, 0)

    def is_placement_valid(self, shape_to_check, part_to_ignore=None):
        """
        Checks if a shape's placement is valid on this sheet, considering both
        containment and collision with existing parts.

        Args:
            shape_to_check (Shape): The shape instance with its bounds polygon at the desired location.
            part_to_ignore (Shape, optional): A specific shape to exclude from the collision check.

        Returns:
            bool: True if the placement is valid, False otherwise.
        """
        if not SHAPELY_AVAILABLE: return False
        if not shape_to_check.polygon: return False

        bin_polygon = Polygon([(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)])
        if not bin_polygon.contains(shape_to_check.polygon):
            return False

        for placed_part in self.parts:
            if placed_part.shape != part_to_ignore and placed_part.shape and placed_part.shape.polygon:
                if shape_to_check.polygon.intersects(placed_part.shape.polygon):
                    return False
        
        return True

    def draw(self, doc, ui_params, parent_group=None, transient_part=None, parts_to_place_group=None, x_offset=0, verbose=False):
        """
        Draws the sheet and its contents into the FreeCAD document.

        Args:
            doc (FreeCAD.Document): The active document.
            ui_params (dict): A dictionary of parameters from the UI.
            parent_group (App.DocumentObjectGroup): Optional. The main layout group to add this sheet to.
            transient_part (Shape): Optional. If given (and self.parts is empty), draws this single
                                     part in isolation instead of the sheet's placed parts.
            parts_to_place_group (App.DocumentObjectGroup): Optional. The temporary group where parts were created.
                                                            Required to safely remove parts from it to prevent deletion.
            x_offset (float): Optional X offset for placing layouts side by side in GA mode.
            verbose (bool): Optional. If True, enables detailed drawing logs.
        """
        # verbose is passed as an argument
        sheet_origin = self.get_origin()
        # Apply X offset for GA layout visualization
        if x_offset != 0:
            sheet_origin = FreeCAD.Vector(sheet_origin.x + x_offset, sheet_origin.y, sheet_origin.z)

        if parent_group:
            self.parent_group_name = parent_group.Name
            # Create or Retrieve the group structure for this sheet
            sheet_group_name = f"Sheet_{self.id+1}"
            
            # Find existing sheet group in parent's children (getObject doesn't work on groups)
            sheet_group = None
            if hasattr(parent_group, 'Group'):
                for child in parent_group.Group:
                    if child.Label == sheet_group_name:
                        sheet_group = child
                        break
            
            if not sheet_group:
                 sheet_group = doc.addObject("App::DocumentObjectGroup", sheet_group_name)
                 parent_group.addObject(sheet_group)
            if self.stock is not None:
                write_sheet_instance(sheet_group, self.stock)
            if hasattr(sheet_group, 'Group') and sheet_group.Group:
                # Clear existing children (recursively — containers live
                # inside the Shapes_ subgroup)
                for child in list(sheet_group.Group):
                    try:
                        recursive_delete(doc, child)
                    except Exception as e:
                        nw_logger.debug(f"[Sheet] recursive_delete skipped during child cleanup: {e}")

            shapes_group_name = f"Shapes_{self.id+1}"
            
            shapes_group = doc.addObject("App::DocumentObjectGroup", shapes_group_name)
            
            sheet_group.addObject(shapes_group)

            # Draw sheet boundary - always create a new one (FreeCAD will auto-rename if collision)
            sheet_boundary_name = f"Sheet_Boundary_{self.id+1}"
            sheet_obj = create_part_feature(
                doc, sheet_boundary_name, Part.makePlane(self.width, self.height), group=sheet_group, visible=True
            )
            sheet_obj.Placement = FreeCAD.Placement(sheet_origin, FreeCAD.Rotation())
            if FreeCAD.GuiUp and hasattr(sheet_obj, "ViewObject") and sheet_obj.ViewObject:
                sheet_obj.ViewObject.Transparency = 75
                sheet_obj.ViewObject.ShapeColor = nw_settings.get_color(PREF_FRONT_SHEET_COLOR)

            # Draw the parts placed on this sheet
            for placed_part in self.parts:
                self._draw_single_part(doc, placed_part.shape, sheet_origin, ui_params, shapes_group, parts_to_place_group, verbose=verbose)

            self._draw_offcuts(doc, sheet_group, sheet_origin, ui_params)

            if self.stock is not None and self.stock["flip"] != "none":
                back_side.draw_back_side(doc, sheet_group, self.id + 1, self.stock["flip"],
                                         FLIP_ANGLE, self.spacing)

        elif transient_part:
            # Draw/update sheet boundary during simulation
            sim_boundary_name = f"sim_sheet_boundary_{self.id}"
            sim_boundary = doc.getObject(sim_boundary_name)
            if not sim_boundary:
                sim_boundary = create_part_feature(
                    doc, sim_boundary_name, Part.makePlane(self.width, self.height), visible=True
                )
                if FreeCAD.GuiUp and hasattr(sim_boundary, "ViewObject") and sim_boundary.ViewObject:
                    sim_boundary.ViewObject.Transparency = 75
                    sim_boundary.ViewObject.ShapeColor = nw_settings.get_color(PREF_FRONT_SHEET_COLOR)
                    sim_boundary.ViewObject.DisplayMode = "Flat Lines"
            else:
                bb = getattr(getattr(sim_boundary, "Shape", None), "BoundBox", None)
                x_len = getattr(bb, "XLength", None)
                y_len = getattr(bb, "YLength", None)
                if bb is None or x_len is None or y_len is None \
                        or abs(float(x_len) - self.width) > 1e-6 \
                        or abs(float(y_len) - self.height) > 1e-6:
                    sim_boundary.Shape = Part.makePlane(self.width, self.height)
            sim_boundary.Placement = FreeCAD.Placement(sheet_origin, FreeCAD.Rotation())
            if FreeCAD.GuiUp and hasattr(sim_boundary, "ViewObject"):
                sim_boundary.ViewObject.Visibility = True
            
            # Draw all parts already placed on this sheet
            for placed_part in self.parts:
                self._draw_single_part(doc, placed_part.shape, sheet_origin, ui_params, verbose=verbose)
            
            # Draw the current transient part being placed
            self._draw_single_part(doc, transient_part, sheet_origin, ui_params, verbose=verbose)

    def _draw_offcuts(self, doc, sheet_group, sheet_origin, ui_params):
        """Draw this sheet's offcuts (NLA ruling 20) into an Offcuts_<n> subgroup.

        The rectangles are offcuts.free_rectangles over the placed parts at the panel's Minimum
        Offcut, the pieces the Offcuts score counts. Each carries its size, for a later
        add-to-library step (LKS-006).
        """
        from ..Tools.Nesting.algorithms.fitness import MIN_OFFCUT_DEFAULT
        from ..Tools.Nesting.algorithms.offcuts import free_rectangles
        n = self.id + 1
        rects = free_rectangles(self.parts, self.width, self.height,
                                ui_params.get('min_offcut', MIN_OFFCUT_DEFAULT))
        if not rects:
            return
        group = doc.addObject("App::DocumentObjectGroup", f"{OFFCUT_GROUP_PREFIX}{n}")
        sheet_group.addObject(group)
        visible = bool(ui_params.get('show_offcuts', True))
        for k, (x0, y0, x1, y1) in enumerate(rects, start=1):
            obj = create_part_feature(doc, f"{OFFCUT_PREFIX}{n}_{k}",
                                      Part.makePlane(x1 - x0, y1 - y0), group=group, visible=visible)
            obj.Placement = FreeCAD.Placement(sheet_origin + FreeCAD.Vector(x0, y0, OFFCUT_Z),
                                              FreeCAD.Rotation())
            obj.addProperty("App::PropertyLength", "OffcutWidth", "Offcut", "Width along the sheet's X")
            obj.addProperty("App::PropertyLength", "OffcutHeight", "Offcut", "Height along the sheet's Y")
            obj.OffcutWidth, obj.OffcutHeight = x1 - x0, y1 - y0
            if FreeCAD.GuiUp and getattr(obj, "ViewObject", None):
                obj.ViewObject.ShapeColor = OFFCUT_COLOR
                obj.ViewObject.LineColor = OFFCUT_LINE_COLOR
                obj.ViewObject.Transparency = 50

    def _draw_single_part(self, doc, shape, sheet_origin, ui_params, shapes_group=None, parts_to_place_group=None, verbose=False):
        """Helper to draw a single part, dispatching to final or simulation path."""
        if not shape:
            return

        # For final drawing, placement is pre-calculated. For simulation, we calculate it now.
        final_placement = shape.placement if shape.placement else shape.get_final_placement(sheet_origin)

        shape_obj = shape.fc_object
        if not shape_obj:
            return

        if shapes_group:
            self._draw_final_part(doc, shape, shape_obj, final_placement, ui_params, shapes_group, parts_to_place_group, verbose)
        else:
            self._draw_simulation_part(shape_obj, final_placement)

    def _draw_final_part(self, doc, shape, shape_obj, final_placement, ui_params, shapes_group, parts_to_place_group=None, verbose=False):
        """Draw a part for final placement: create container, add labels, set properties."""
        # Create a NEW container to hold the part.
        # Do NOT try to reuse an existing container by name, as it might belong to
        # an old layout that is about to be deleted.
        # FreeCAD will automatically handle name collisions (e.g. nested_O_1001).
        container = doc.addObject("App::Part", f"nested_{shape.id}")
        container.Label = f"nested_{shape.id}" # Ensure label matches intended ID

        # Add ShowBounds property
        container.addProperty("App::PropertyBool", "ShowBounds", "Nesting", "Show the boundary check logic used")
        container.ShowBounds = ui_params.get('show_bounds', False)

        shapes_group.addObject(container)

        # Place the boundary object at the container's origin. It is the reference.
        boundary_obj = shape_obj.BoundaryObject
        if boundary_obj:
            boundary_obj.Placement = FreeCAD.Placement()
            container.addObject(boundary_obj)

            if hasattr(boundary_obj, "ViewObject"):
                boundary_obj.ViewObject.Visibility = container.ShowBounds
                # Set red color for bounds
                boundary_obj.ViewObject.LineColor = (1.0, 0.0, 0.0)  # Red
                boundary_obj.ViewObject.LineWidth = 2.0

        # The shape_obj already has the correct placement (centered + rotated)
        # from shape_preparer, so we don't touch it. Just add to container.

        if hasattr(shape_obj, "ViewObject"):
            shape_obj.ViewObject.Visibility = True
        container.addObject(shape_obj)

        if hasattr(container, "ViewObject"):
            container.ViewObject.Visibility = True

        # Unlink from the temporary "PartsToPlace" bin so cleanup doesn't delete them
        if parts_to_place_group:
            try:
                if boundary_obj:
                    parts_to_place_group.removeObject(boundary_obj)
                parts_to_place_group.removeObject(shape_obj)
            except Exception as e:
                nw_logger.debug(f"[Sheet] parts_to_place_group removeObject skipped: {e}")

        # Apply the final nesting placement to the CONTAINER.
        container.Placement = final_placement

        if verbose:
            # Positions, to diagnose a bounds-to-shape offset
            shape_bb = shape_obj.Shape.BoundBox
            shape_visual_center = FreeCAD.Vector(
                shape_obj.Placement.Base.x + (shape_bb.XMin + shape_bb.XMax) / 2,
                shape_obj.Placement.Base.y + (shape_bb.YMin + shape_bb.YMax) / 2,
                0
            )
            bound_center_str = "N/A"
            if boundary_obj:
                bbb = boundary_obj.Shape.BoundBox
                bound_center_str = f"({(bbb.XMin + bbb.XMax)/2:.2f}, {(bbb.YMin + bbb.YMax)/2:.2f})"
            nw_logger.info(
                f"  DRAW {shape.id}: shape_visual=({shape_visual_center.x:.2f}, {shape_visual_center.y:.2f})"
                f" bounds_center={bound_center_str}"
                f" shape_plc=({shape_obj.Placement.Base.x:.2f}, {shape_obj.Placement.Base.y:.2f})"
                f" container_plc=({final_placement.Base.x:.2f}, {final_placement.Base.y:.2f})"
            )

        if ui_params.get('add_labels', False) and ui_params.get('font_path') and hasattr(shape, 'label_text') and shape.label_text:
            label_name = f"label_{shape.id}"
            # Allow FreeCAD to auto-rename if collision exists (e.g. label_Part001)
            # Do NOT delete existing objects by name as they might belong to other layouts.
            label_obj = create_label_object(doc, label_name)

            label_obj.Shape = build_label_shape(
                shape.label_text, ui_params['font_path'], ui_params.get('label_size', 10.0))

            # Add label to the CONTAINER (same scope as part)
            container.addObject(label_obj)

            # Calculate local placement relative to the part inside the container
            # using utility function to keep text horizontal and centered
            label_obj.Placement = calculate_label_placement(
                label_obj.Shape.BoundBox.Center,
                final_placement.Rotation,
                ui_params.get('label_height', 0.1)
            )

            # Link label to shape (add property if needed for plain Part::Feature)
            if not hasattr(shape_obj, "LabelObject"):
                shape_obj.addProperty("App::PropertyLink", "LabelObject", "Nesting", "Link to label")
            shape_obj.LabelObject = label_obj

        # Set visibility on the main shape object (add properties if needed)
        if not hasattr(shape_obj, "ShowShape"):
            shape_obj.addProperty("App::PropertyBool", "ShowShape", "Nesting", "Show shape geometry")
        if not hasattr(shape_obj, "ShowBounds"):
            shape_obj.addProperty("App::PropertyBool", "ShowBounds", "Nesting", "Show bounds")
        if not hasattr(shape_obj, "ShowLabel"):
            shape_obj.addProperty("App::PropertyBool", "ShowLabel", "Nesting", "Show label")

        shape_obj.ShowShape = True
        shape_obj.ShowBounds = ui_params.get('show_bounds', False)
        shape_obj.ShowLabel = ui_params.get('add_labels', False)

    def _draw_simulation_part(self, shape_obj, final_placement):
        """Draw a part for simulation/preview: move the boundary object and hide the shape."""
        if hasattr(shape_obj, 'ViewObject'):
            shape_obj.ViewObject.Visibility = False

        if hasattr(shape_obj, 'BoundaryObject') and shape_obj.BoundaryObject:
            boundary = shape_obj.BoundaryObject
            boundary.Placement = final_placement
            if hasattr(boundary, 'ViewObject'):
                boundary.ViewObject.Visibility = True
                boundary.ViewObject.LineColor = (0.0, 0.7, 0.0)  # Green for simulation
                boundary.ViewObject.LineWidth = 2.0