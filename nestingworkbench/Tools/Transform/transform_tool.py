# Nesting/nestingworkbench/Tools/Transform/transform_tool.py

"""
This module contains the TransformToolObserver class, which implements a
simple drag-and-drop functionality for manually transforming parts in a layout.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore
import time
import math
import traceback
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
        self.callback_ids = []  # Store callback IDs for cleanup
        self.last_log_time = 0
        self.selected_obj = None
        self.mode = "IDLE" # IDLE, TRANSLATE, ROTATE
        self.is_mouse_down = False
        self.is_implicit_drag = False
        self.drag_start_screen_pos = (0,0)

        # Get the selected layout group
        selection = FreeCADGui.Selection.getSelection()
        if selection and selection[0].isDerivedFrom("App::DocumentObjectGroup") and selection[0].Label.startswith("Layout_"):
            self.layout_group = selection[0]
        else:
            FreeCAD.Console.PrintWarning("Transform Tool: Please select a Layout group first.\n")
            return

        # Store original placements and manage visibility
        # print(f"DEBUG: Traversing Layout Group: {self.layout_group.Label}")
        for sheet_group in self.layout_group.Group:
            # print(f"DEBUG: Checking sheet_group: {sheet_group.Label} (Type: {sheet_group.TypeId})")
            if sheet_group.isDerivedFrom("App::DocumentObjectGroup"):
                # Ensure sheet boundary is visible
                sheet_boundary = next((obj for obj in sheet_group.Group if obj.Label.startswith("Sheet_Boundary_")), None)
                if sheet_boundary and hasattr(sheet_boundary, "ViewObject"):
                    self.original_visibilities[sheet_boundary] = sheet_boundary.ViewObject.Visibility
                    sheet_boundary.ViewObject.Visibility = True
                
                # print(f"DEBUG: Sheet Group content count: {len(sheet_group.Group)}")
                for sub_group in sheet_group.Group: # e.g., Shapes_1, Text_1
                    # print(f"DEBUG: Checking sub_group: {sub_group.Label} (Type: {sub_group.TypeId})")
                    if sub_group.isDerivedFrom("App::DocumentObjectGroup"):
                        if sub_group.Label.startswith("Shapes_"):
                            # print(f"DEBUG: Found Shapes group: {sub_group.Label}")
                            for obj in sub_group.Group: # e.g., nested_PartA_1
                                # print(f"DEBUG: Inspecting obj: {obj.Label} Proxy: {obj.Proxy.__class__.__name__ if hasattr(obj, 'Proxy') else 'None'}")
                                
                                has_shape_proxy = hasattr(obj, "Proxy") and isinstance(obj.Proxy, object) and obj.Proxy.__class__.__name__ == "ShapeObject"
                                # Relaxed check: allow if it has the proxy OR if it's in the Shapes group (likely a nested part without proxy)
                                if has_shape_proxy or True: 
                                    # print(f"DEBUG: Tracking ShapeObject: {obj.Label}")
                                    self.original_placements[obj] = obj.Placement.copy()
                                    if hasattr(obj, "ViewObject"):
                                        self.original_visibilities[obj] = obj.ViewObject.Visibility
                                        
                                        # Manage visibility of linked objects (BoundaryObject and LabelObject)
                                        replacement_shown = False
                                        if hasattr(obj, "BoundaryObject") and obj.BoundaryObject and hasattr(obj.BoundaryObject, "ViewObject"):
                                            self.original_visibilities[obj.BoundaryObject] = obj.BoundaryObject.ViewObject.Visibility
                                            obj.BoundaryObject.ViewObject.Visibility = True # Always show bounds in transform mode
                                            replacement_shown = True
                                        if hasattr(obj, "LabelObject") and obj.LabelObject and hasattr(obj.LabelObject, "ViewObject"):
                                            self.original_visibilities[obj.LabelObject] = obj.LabelObject.ViewObject.Visibility
                                            obj.LabelObject.ViewObject.Visibility = True # Always show label in transform mode
                                            replacement_shown = True
                                        
                                        # Only hide the original 3D shape if we are showing a replacement (Boundary/Label)
                                        if replacement_shown:
                                            obj.ViewObject.Visibility = False 

                        elif sub_group.Label.startswith("Text_"):
                             # print(f"DEBUG: Found Text group: {sub_group.Label}")
                             for label_obj in sub_group.Group: # e.g., label_unplaced_PartB
                                if hasattr(label_obj, "Proxy") and isinstance(label_obj.Proxy, object) and label_obj.Proxy.__class__.__name__ == "LabelObject":
                                    # print(f"DEBUG: Tracking LabelObject: {label_obj.Label}")
                                    self.original_placements[label_obj] = label_obj.Placement.copy()
                                    if hasattr(label_obj, "ViewObject"):
                                        self.original_visibilities[label_obj] = label_obj.ViewObject.Visibility
                                        label_obj.ViewObject.Visibility = True # Ensure standalone labels are visible

        # After changing visibilities, we need to update the GUI to reflect them.
        FreeCADGui.updateGui()
        
        # Debug: list tracked objects
        print("DEBUG: Tracked objects in original_placements:")
        for obj in self.original_placements:
           print(f"  - {obj.Label if hasattr(obj, 'Label') else obj} (Name: {obj.Name if hasattr(obj, 'Name') else 'No Name'})")

        # Register event callbacks for mouse interaction
        if self.layout_group:
            cb_id = self.view.addEventCallback("SoMouseButtonEvent", self._make_callback("SoMouseButtonEvent"))
            self.callback_ids.append(("SoMouseButtonEvent", cb_id))
            cb_id = self.view.addEventCallback("SoLocation2Event", self._make_callback("SoLocation2Event"))
            self.callback_ids.append(("SoLocation2Event", cb_id))
            cb_id = self.view.addEventCallback("SoKeyboardEvent", self._make_callback("SoKeyboardEvent"))
            self.callback_ids.append(("SoKeyboardEvent", cb_id))
            FreeCAD.Console.PrintMessage("Transform Tool: Activated. Click and drag parts to move them.\n")


    def eventCallback(self, event_type, event_dict):
        """The main callback method for handling mouse and keyboard events."""
        try:
            if not self.layout_group:
                return False 

            # event = event_dict["Event"] # ERROR: Dictionary does not contain 'Event' key wrapper
            
            # --- KEYBOARD HANDLING ---
            if event_type == "SoKeyboardEvent" and event_dict["State"] == "DOWN":
                key = event_dict["Key"]
                
                # Handling Key Strings from FreeCAD
                key = str(key).upper()
                
                # ESC - Cancel
                if key == "ESCAPE": 
                    self.cancel_operation()
                    return True
                
                # G - Grab/Translate
                if key == "G": 
                    if self.selected_obj:
                        self.set_mode("TRANSLATE")
                        return True
                
                # R - Rotate
                if key == "R": 
                    if self.selected_obj:
                        self.set_mode("ROTATE")
                        return True
                    
                # ENTER or RETURN - Confirm
                if key in ["RETURN", "ENTER"]: 
                    self.finish_operation()
                    return True

            # --- MOUSE BUTTON HANDLING ---
            if event_type == "SoMouseButtonEvent":
                if event_dict["Button"] == "BUTTON1": # Left Button
                    pos = event_dict["Position"] 
                    
                    if event_dict["State"] == "DOWN":
                        self.handle_click(pos)
                        return True
                    else: # UP
                        self.handle_release()
                        return True

            # --- MOUSE MOVE HANDLING ---
            elif event_type == "SoLocation2Event":
                pos = event_dict["Position"]
                snap = event_dict.get("Ctrl", False) or event_dict.get("Control", False)
                self.handle_move(pos, snap)
                
                if self.mode != "IDLE": return True
            
            return False

        except Exception:
            # traceback.print_exc()
            return False

    def handle_click(self, pos):
        """On mouse down: Select object and start interaction."""
        
        # If we are already in a mode (G/R active), a click confirms it
        if self.mode in ["TRANSLATE", "ROTATE"]:
             self.finish_operation()
             return

        clicked_obj = self.pick_object(pos)
        
        if clicked_obj:
            self.selected_obj = clicked_obj
            FreeCAD.Console.PrintMessage(f"Selected: {clicked_obj.Label}\n")
            
            # Prepare for potential drag
            self.drag_start_screen_pos = pos
            self.start_pos = self.view.getPoint(pos[0], pos[1]) # 3D point
            self.start_placement = self.selected_obj.Placement.copy()
            self.is_mouse_down = True
            self.is_implicit_drag = False # Will become true if moved
                
        else:
            # Clicked on empty space
            self.selected_obj = None
            self.is_mouse_down = True # Track even if no object

    def handle_move(self, pos, snap=False):
        if self.mode == "IDLE":
             if self.is_mouse_down and self.selected_obj:
                 # Check drag threshold
                 dx = pos[0] - self.drag_start_screen_pos[0]
                 dy = pos[1] - self.drag_start_screen_pos[1]
                 dist = math.sqrt(dx*dx + dy*dy)
                 if dist > 5: # 5 pixels threshold
                     self.set_mode("TRANSLATE")
                     self.is_implicit_drag = True
        
        if not self.selected_obj: return
        
        if self.mode == "TRANSLATE":
            if not self.start_pos: return
            
            current_pos = self.view.getPoint(pos[0], pos[1])
            move_vec = current_pos - self.start_pos
            move_vec.z = 0 # Project to XY plane for 2D nesting
            
            # TODO: Add Translation Snapping (Grid) if requested later
            
            new_placement = self.start_placement.copy()
            new_placement.Base += move_vec
            self.selected_obj.Placement = new_placement
            
        elif self.mode == "ROTATE":
            if not self.start_placement: return
            
            # Calculate rotation based on X delta from click
            delta_x = pos[0] - self.drag_start_screen_pos[0]
            sensitivity = 0.5 # Degrees per pixel
            angle_deg = -delta_x * sensitivity # Inverted to match standard expectation
            
            # Snap logic (CTRL key)
            if snap:
                step = 45.0
                angle_deg = round(angle_deg / step) * step
            
            rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), angle_deg)
            
            # Apply individual rotation
            orig_rot = self.start_placement.Rotation
            new_rot = rot.multiply(orig_rot)
            
            new_placement = self.start_placement.copy()
            new_placement.Rotation = new_rot
            self.selected_obj.Placement = new_placement

    def handle_release(self):
        self.is_mouse_down = False
        
        if self.is_implicit_drag:
            # If we were dragging with mouse down, release confirms it
            self.finish_operation()
            self.is_implicit_drag = False

    def set_mode(self, mode):
        self.mode = mode
        if mode in ["TRANSLATE", "ROTATE"]:
             FreeCAD.Console.PrintMessage(f"Mode: {mode} (Click/Enter to Confirm, Esc to Cancel)\n")
             # Setup start state if not already
             if not hasattr(self, 'start_placement') or not self.start_placement:
                 if self.selected_obj:
                    self.start_placement = self.selected_obj.Placement.copy()
             
             # Capture screen pos if not set (e.g. key press without click)
             if not hasattr(self, 'drag_start_screen_pos') or not self.drag_start_screen_pos:
                 if hasattr(self, 'last_known_screen_pos'):
                     self.drag_start_screen_pos = self.last_known_screen_pos
                 else:
                     self.drag_start_screen_pos = (0,0) # Fallback

    def cancel_operation(self):
        if self.selected_obj and hasattr(self, 'start_placement') and self.start_placement:
             self.selected_obj.Placement = self.start_placement
             FreeCAD.Console.PrintMessage("Operation Cancelled.\n")
        
        self.mode = "IDLE"
        self.start_placement = None
        self.start_pos = None
        self.is_implicit_drag = False
        self.is_mouse_down = False

    def finish_operation(self):
        if self.selected_obj:
            # FreeCAD.Console.PrintMessage(f"Operation Confirmed for {self.selected_obj.Label}.\n")
            pass
        self.mode = "IDLE"
        self.start_placement = None
        self.start_pos = None
        self.is_implicit_drag = False

    def pick_object(self, pos):
        """Helper to find the draggable object at screen pos."""
        info = self.view.getObjectInfo(pos)
        if info and "Object" in info:
             clicked_obj = info["Object"]
             # Resolve strings
             if isinstance(clicked_obj, str):
                 if self.layout_group and hasattr(self.layout_group, 'Document'):
                     clicked_obj = self.layout_group.Document.getObject(clicked_obj)
                 else:
                     clicked_obj = FreeCAD.ActiveDocument.getObject(clicked_obj)
             
             parent_obj_from_click = info.get("ParentObject")
             return self.get_draggable_parent(clicked_obj, parent_obj_from_click)
        return None

    def _make_callback(self, event_type):
        """Creates a callback wrapper that passes the event type to eventCallback."""
        def callback(event_dict):
            return self.eventCallback(event_type, event_dict)
        return callback

    def get_draggable_parent(self, obj, parent_obj_from_click=None):
        """
        Determines the actual object to drag based on what was clicked.
        Supports:
        1. Direct match with tracked object.
        2. Linked object matching (App::Link).
        3. Parent containment matching (App::Part containing clicked object).
        4. Matching via 'ParentObject' from click info.
        """        
        # Check if the clicked object is a linked boundary or label of a ShapeObject
        for potential_parent_shape_obj in self.original_placements.keys():
            if hasattr(potential_parent_shape_obj, "Proxy") and isinstance(potential_parent_shape_obj.Proxy, object) and potential_parent_shape_obj.Proxy.__class__.__name__ == "ShapeObject":
                if hasattr(potential_parent_shape_obj, "BoundaryObject") and potential_parent_shape_obj.BoundaryObject == obj:
                    return potential_parent_shape_obj # Drag the ShapeObject parent
                if hasattr(potential_parent_shape_obj, "LabelObject") and potential_parent_shape_obj.LabelObject == obj:
                    return potential_parent_shape_obj # Drag the ShapeObject parent
        
        # Check if the clicked object itself is tracked
        if obj in self.original_placements:
            return obj

        # Check against tracked objects with advanced logic
        for tracked_obj in self.original_placements.keys():
            if tracked_obj == obj:
                 return tracked_obj
            
            # LINK MATCHing
            if hasattr(tracked_obj, "LinkedObject") and tracked_obj.LinkedObject == obj:
                # print(f"DEBUG: Matched via LinkedObject: {tracked_obj.Label} -> {obj.Label}")
                return tracked_obj

            # PARENT CONTAINMENT MATCHING (For App::Part)
            # Check if clicked object is inside tracked_obj's Group
            if hasattr(tracked_obj, "Group") and obj in tracked_obj.Group:
                 # print(f"DEBUG: Matched via Group Containment: {tracked_obj.Label} contains {obj.Label}")
                 return tracked_obj
            
            # PARENT OBJECT FROM CLICK INFO MATCHING
            if parent_obj_from_click and tracked_obj == parent_obj_from_click:
                 # print(f"DEBUG: Matched via ParentObject info: {tracked_obj.Label}")
                 return tracked_obj

            # NAME MATCHING (Fallback)
            if hasattr(tracked_obj, 'Name') and hasattr(obj, 'Name') and tracked_obj.Name == obj.Name:
                # print(f"DEBUG: Matched by Name: {tracked_obj.Name}")
                return tracked_obj
        
        return None

    def is_object_in_layout(self, obj):
        """Check if an object is a child of the selected layout group."""
        # This method is now primarily used to check if a clicked object is *part* of the layout,
        # not necessarily if it's directly draggable. get_draggable_parent handles that.
        for sheet_group in self.layout_group.Group:
            if sheet_group.isDerivedFrom("App::DocumentObjectGroup"):
                for sub_group in sheet_group.Group: # e.g., Shapes_1, Text_1
                    if sub_group.isDerivedFrom("App::DocumentObjectGroup") and obj in sub_group.Group:
                        return True
        return False

    def save_placements(self): # This method is now part of the TransformToolObserver
        """Saves the new placements to the layout's OriginalPlacements property."""
        if not self.layout_group:
            return

        # The placements are already applied to the objects.
        FreeCAD.Console.PrintMessage(f"Saved new placements for transformed objects.\n")
        # If sheets were stacked, this move breaks the "stacked" state
        if hasattr(self.layout_group, 'IsStacked') and self.layout_group.IsStacked:
            self.layout_group.IsStacked = False
            FreeCAD.Console.PrintWarning("Layout is no longer considered stacked due to manual adjustment.\n")

    def cancel(self): # This method is now part of the TransformToolObserver
        """Reverts any changes made to the object placements."""
        if self.original_placements:
            for obj, placement in self.original_placements.items():
                if obj: # Check if object still exists
                    obj.Placement = placement
            FreeCAD.Console.PrintMessage("Transformations cancelled.\n")

    def cleanup(self): # This method is now part of the TransformToolObserver
        """Removes the event callbacks from the view and restores original visibilities."""
        for event_type, callback_id in self.callback_ids:
            try:
                self.view.removeEventCallback(event_type, callback_id)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Could not remove {event_type} callback: {e}\n")
        self.callback_ids = []
        
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