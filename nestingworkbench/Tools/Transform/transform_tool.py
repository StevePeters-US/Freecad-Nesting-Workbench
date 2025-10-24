# Nesting/nesting/drag_tool.py

"""
This module contains the DragToolObserver class, which implements a simple
drag-and-drop functionality for manually adjusting parts in a layout.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore

class TransformToolObserver:
    """
    A ViewObserver that captures mouse events to allow dragging of parts
    within a selected layout group.
    """
    def __init__(self, view):
        self.view = view
        self.pressed = False
        self.obj_to_move = None
        self.start_pos = None
        self.start_placement = None
        self.layout_group = None

        # Get the selected layout group
        selection = FreeCADGui.Selection.getSelection()
        if selection and selection[0].isDerivedFrom("App::DocumentObjectGroup") and selection[0].Label.startswith("Layout_"):
            self.layout_group = selection[0]
        else:
            FreeCAD.Console.PrintWarning("Transform Tool: Please select a Layout group first.\n")

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
                    self.save_placement()
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

    def save_placement(self):
        """Saves the new placement to the layout's OriginalPlacements property."""
        if not self.obj_to_move or not self.layout_group:
            return

        if hasattr(self.layout_group, "OriginalPlacements"):
            placements_dict = self.layout_group.OriginalPlacements
            p = self.obj_to_move.Placement
            placement_data = [p.Base.x, p.Base.y, p.Base.z, p.Rotation.Q[0], p.Rotation.Q[1], p.Rotation.Q[2], p.Rotation.Q[3]]
            placements_dict[self.obj_to_move.Name] = str(placement_data)
            self.layout_group.OriginalPlacements = placements_dict
            FreeCAD.Console.PrintMessage(f"Updated placement for {self.obj_to_move.Label}\n")
            # If sheets were stacked, this move breaks the "stacked" state
            if self.layout_group.IsStacked:
                self.layout_group.IsStacked = False
                FreeCAD.Console.PrintWarning("Layout is no longer considered stacked due to manual adjustment.\n")