# Nesting/nestingworkbench/Tools/Transform/transform_tool.py

"""
This module contains the TransformToolObserver class, which implements a
simple drag-and-drop functionality for manually transforming parts in a layout.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore
from .ui_transform import TransformToolUI

class TransformToolObserver:
    """
    A ViewObserver that captures mouse events to allow transforming (dragging)
    of parts within a selected layout group.
    """
    def __init__(self, view, panel_manager):
        self.panel_manager = panel_manager
        self.view = view
        self.pressed = False
        self.obj_to_move = None
        self.start_pos = None
        self.start_placement = None
        self.layout_group = None
        self.original_placements = {}
        self.original_visibilities = {}

        # Get the selected layout group
        selection = FreeCADGui.Selection.getSelection()
        if selection and selection[0].isDerivedFrom("App::DocumentObjectGroup") and selection[0].Label.startswith("Layout_"):
            self.layout_group = selection[0]
        else:
            FreeCAD.Console.PrintWarning("Transform Tool: Please select a Layout group first.\n")
            return

        # Store original placements and manage visibility
        for sheet_group in self.layout_group.Group:
            if sheet_group.isDerivedFrom("App::DocumentObjectGroup"):
                # Ensure sheet boundary is visible
                sheet_boundary = next((obj for obj in sheet_group.Group if obj.Label.startswith("SheetBoundary")), None)
                if sheet_boundary and hasattr(sheet_boundary, "ViewObject"):
                    self.original_visibilities[sheet_boundary] = sheet_boundary.ViewObject.Visibility
                    sheet_boundary.ViewObject.Visibility = True

                for sub_group in sheet_group.Group: # e.g., "Objects_1", "Bounds_1"
                    if sub_group.isDerivedFrom("App::DocumentObjectGroup"):
                        for obj in sub_group.Group: # e.g., "packed_Part_1"
                            self.original_placements[obj] = obj.Placement.copy()
                            # Manage visibility
                            if hasattr(obj, "ViewObject"):
                                self.original_visibilities[obj] = obj.ViewObject.Visibility
                                # Hide packed parts, show bounds/labels
                                if obj.Label.startswith("packed_"):
                                    obj.ViewObject.Visibility = False
                                elif obj.Label.startswith("bound_") or obj.isDerivedFrom("Draft::ShapeString"):
                                    obj.ViewObject.Visibility = True
        
        # After changing visibilities, we need to update the GUI to reflect them.
        FreeCADGui.updateGui()


    def eventCallback(self, event_type, event):
        """The main callback method for handling mouse events."""
        if not self.layout_group:
            return False # Do not handle events if no layout is selected

        if event_type == "SoMouseButtonEvent":
            if event["State"] == "DOWN" and event["Button"] == "BUTTON1":
                pos = event["Position"]
                info = self.view.getObjectInfo((pos.x(), pos.y()))
                if info and "Object" in info:
                    clicked_obj = info["Object"]
                    # Check if the clicked object is part of the selected layout
                    if self.is_object_in_layout(clicked_obj):
                        self.pressed = True
                        self.obj_to_move = clicked_obj
                        self.start_pos = self.view.getPoint(pos.x(), pos.y())
                        self.start_placement = self.obj_to_move.Placement.copy()
                        return True # Event handled

            elif event["State"] == "UP" and event["Button"] == "BUTTON1":
                if self.pressed and self.obj_to_move:
                    self.pressed = False
                    self.obj_to_move = None
                    self.start_pos = None
                    self.start_placement = None
                    return True # Event handled

        elif event_type == "SoLocation2Event":
            if self.pressed and self.obj_to_move:
                pos = event["Position"]
                current_pos = self.view.getPoint(pos.x(), pos.y())
                
                # Project movement onto the XY plane
                move_vec = current_pos - self.start_pos
                move_vec.z = 0

                new_placement = self.start_placement.copy()
                new_placement.Base += move_vec
                self.obj_to_move.Placement = new_placement
                return True # Event handled

        return False # Event not handled

    def is_object_in_layout(self, obj):
        """Check if an object is a child of the selected layout group."""
        for sheet_group in self.layout_group.Group:
            if sheet_group.isDerivedFrom("App::DocumentObjectGroup"):
                for sub_group in sheet_group.Group:
                    if obj in sub_group.Group:
                        return True
        return False

    def save_placements(self):
        """Saves the new placements to the layout's OriginalPlacements property."""
        if not self.layout_group:
            return

        # This logic is now handled by the panel manager's accept() method.
        # We can add properties to the layout group to store changes if needed,
        # but for simple drag/drop, the change is already applied to the object's Placement.
        FreeCAD.Console.PrintMessage(f"Saved new placements for transformed objects.\n")
        # If sheets were stacked, this move breaks the "stacked" state
        if hasattr(self.layout_group, 'IsStacked') and self.layout_group.IsStacked:
            self.layout_group.IsStacked = False
            FreeCAD.Console.PrintWarning("Layout is no longer considered stacked due to manual adjustment.\n")

    def cancel(self):
        """Reverts any changes made to the object placements."""
        if self.original_placements:
            for obj, placement in self.original_placements.items():
                if obj: # Check if object still exists
                    obj.Placement = placement
            FreeCAD.Console.PrintMessage("Transformations cancelled.\n")

    def cleanup(self):
        """Removes the event callbacks from the view."""
        try:
            self.view.removeEventCallback("SoMouseButtonEvent", self.eventCallback)
            self.view.removeEventCallback("SoLocation2Event", self.eventCallback)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Could not remove transform observer callbacks: {e}\n")
        self.original_placements = {}
        # Restore original visibility
        for obj, is_visible in self.original_visibilities.items():
            try:
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.Visibility = is_visible
            except Exception:
                pass # Object may have been deleted
        self.original_visibilities = {}
        
        # After restoring visibilities, update the GUI again.
        FreeCADGui.updateGui()
        self.layout_group = None