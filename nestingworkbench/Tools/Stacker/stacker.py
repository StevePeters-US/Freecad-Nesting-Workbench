# Nesting/nesting/stacker.py

"""
This module contains the SheetStacker class, which handles the logic for 
finding, stacking, and unstacking the generated sheet layouts.
"""

import FreeCAD
import ast

class SheetStacker:
    """Handles the logic for finding, stacking, and unstacking sheet layouts."""
    def __init__(self, layout_group=None):
        self.doc = FreeCAD.ActiveDocument
        if layout_group:
            self.layout_group = layout_group
        else:
            self.layout_group = self._get_layout_group()

    def _get_layout_group(self):
        """Finds the most relevant layout group in the active document."""
        if not self.doc:
            return None
        
        # Prioritize the temporary group as it's the one being actively worked on
        temp_group = self.doc.getObject("__temp_Layout")
        if temp_group:
            return temp_group
        
        # If no temp group, find the most recently created final layout group
        groups = [o for o in self.doc.Objects if o.isDerivedFrom("App::DocumentObjectGroup")]
        packed_groups = sorted([g for g in groups if g.Label.startswith("Layout_")], key=lambda x: x.Name)
        if packed_groups:
            return packed_groups[-1]
            
        return None

    def _get_sheet_groups(self):
        """Gets all the direct child Sheet groups from the main layout group."""
        if not self.layout_group:
            return []
        
        sheet_groups = [obj for obj in self.layout_group.Group if obj.Label.startswith("Sheet_")]
        # Sort them numerically by their label to ensure correct order
        sheet_groups.sort(key=lambda g: int(g.Label.split('_')[1]))
        return sheet_groups

    def _get_all_objects_recursive(self, group):
        """Recursively finds all objects within a group and its subgroups."""
        all_objects = []
        for obj in group.Group:
            if obj.isDerivedFrom("App::DocumentObjectGroup"):
                all_objects.extend(self._get_all_objects_recursive(obj))
            else:
                all_objects.append(obj)
        return all_objects

    def _get_params_from_layout_group(self):
        """Reads layout parameters from the properties of the layout group."""
        if not self.layout_group:
            return None

        try:
            # Check for properties directly on the group object
            if hasattr(self.layout_group, 'SheetWidth') and hasattr(self.layout_group, 'PartSpacing'):
                return {"width": self.layout_group.SheetWidth, "spacing": self.layout_group.PartSpacing}
            else:
                FreeCAD.Console.PrintWarning("Could not find parameter properties on the layout group. Stacking may be inaccurate.\n")
                return None
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error reading from spreadsheet: {e}\n")
            return None

    def toggle_stack(self):
        """Public method to stack or unstack the sheets."""
        if not self.layout_group:
            FreeCAD.Console.PrintMessage("No valid packed layout found to stack/unstack.\n")
            return
        
        if not hasattr(self.layout_group, "IsStacked"):
             self.layout_group.addProperty("App::PropertyBool", "IsStacked", "Nesting")
             self.layout_group.IsStacked = False
        
        if self.layout_group.IsStacked:
            self._unstack()
        else:
            self._stack()
        
        self.doc.recompute()

    def _stack(self):
        """Moves all objects in sheets 2 and higher to overlay sheet 1."""
        params = self._get_params_from_layout_group()
        if not params:
            FreeCAD.Console.PrintError("Could not retrieve sheet parameters. Stacking aborted.\n")
            return

        # Before any movement, store the current state of all objects in the layout.
        # This ensures that unstacking will always restore to the state right before stacking.
        if not hasattr(self.layout_group, "OriginalPlacements"):
            self.layout_group.addProperty("App::PropertyMap", "OriginalPlacements", "Nesting")

        placements_dict = {}
        all_objects = self._get_all_objects_recursive(self.layout_group)
        for obj in all_objects:
            if not hasattr(obj, 'Placement'):
                continue
            # Store placement as a string representation of a tuple:
            # (Base.x, Base.y, Base.z, Rotation.Q[0], Rotation.Q[1], Rotation.Q[2], Rotation.Q[3])
            p = obj.Placement
            placement_str = str((p.Base.x, p.Base.y, p.Base.z, p.Rotation.Q[0], p.Rotation.Q[1], p.Rotation.Q[2], p.Rotation.Q[3]))
            placements_dict[obj.Name] = placement_str
        self.layout_group.OriginalPlacements = placements_dict

        total_sheet_width = params["width"] + params["spacing"]
        sheet_groups = self._get_sheet_groups()

        if len(sheet_groups) < 2:
            FreeCAD.Console.PrintMessage("Stacking requires two or more sheets.\n")
            return

        # The target position is the origin (0,0,0)
        target_pos = FreeCAD.Vector(0, 0, 0)
        
        # Iterate through all subsequent sheets and move them
        for i in range(1, len(sheet_groups)):
            sheet_group = sheet_groups[i]
            
            # The original position of this sheet determines how much it needs to move
            original_pos = FreeCAD.Vector(i * total_sheet_width, 0, 0)
            move_vec = target_pos - original_pos
            
            # Apply this transformation to all objects within this sheet's group
            objects_to_move = self._get_all_objects_recursive(sheet_group)
            for obj in objects_to_move:
                new_placement = FreeCAD.Placement(move_vec, FreeCAD.Rotation()).multiply(obj.Placement)
                obj.Placement = new_placement

        self.layout_group.IsStacked = True
        FreeCAD.Console.PrintMessage("Sheets are now stacked.\n")
        
    def _unstack(self):
        """Restores all objects in the layout to their original positions."""
        if not hasattr(self.layout_group, "OriginalPlacements"):
            FreeCAD.Console.PrintError("Original placement data not found. Cannot unstack.\n")
            return
            
        placements_dict = self.layout_group.OriginalPlacements
        all_objects = self._get_all_objects_recursive(self.layout_group)
        for obj in all_objects:
            if not hasattr(obj, 'Placement'):
                continue
            if obj.Name in placements_dict:
                placement_str = placements_dict[obj.Name]
                try:
                    # Use ast.literal_eval for safely evaluating the string representation of the tuple
                    data = ast.literal_eval(placement_str)
                except (ValueError, SyntaxError):
                    FreeCAD.Console.PrintWarning(f"Could not parse placement data for '{obj.Name}'. Skipping.\n")
                    continue
                base = FreeCAD.Vector(data[0], data[1], data[2])
                rot = FreeCAD.Rotation(data[3], data[4], data[5], data[6])
                obj.Placement = FreeCAD.Placement(base, rot)
        
        self.layout_group.IsStacked = False
        FreeCAD.Console.PrintMessage("Sheets are now unstacked.\n")
