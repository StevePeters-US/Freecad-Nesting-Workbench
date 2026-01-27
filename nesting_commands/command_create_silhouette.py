# nesting_commands/command_create_silhouette.py

"""
FreeCAD command to create 2D silhouettes from selected 3D objects.

Supports:
- Layout groups: Creates silhouettes for all parts in all sheets
- Nested containers (App::Part like nested_Side_1): Creates silhouette for the part inside
- Individual Part::Feature objects: Creates silhouette directly
"""

import FreeCAD
import FreeCADGui
import os


class CreateSilhouetteCommand:
    """Command to create a 2D silhouette from a selected 3D object."""
    
    def GetResources(self):
        """Defines the command's appearance in FreeCAD."""
        return {
            'Pixmap': 'Silhouette_Icon.svg',
            'MenuText': 'Create Silhouette',
            'ToolTip': 'Creates a 2D silhouette (outline) from the selected 3D object(s).'
        }
    
    def Activated(self):
        """Executed when the command is activated."""
        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError("No active document\n")
            return
        
        # Get selected objects
        sel = FreeCADGui.Selection.getSelection()
        if not sel:
            FreeCAD.Console.PrintWarning("Please select objects, nested containers, or a Layout group.\n")
            return
        
        # Import the silhouette creator functions
        from nestingworkbench.Tools.Silhouette.silhouette_creator import (
            is_layout_group,
            create_silhouettes_for_layout,
            create_silhouette_for_container,
            create_silhouette_for_part,
            is_nested_container
        )
        
        created_count = 0
        
        # Process each selected object
        for obj in sel:
            # Check if this is a Layout group
            if is_layout_group(obj):
                FreeCAD.Console.PrintMessage(f"Processing Layout: {obj.Label}\n")
                silhouettes = create_silhouettes_for_layout(doc, obj)
                created_count += len(silhouettes) if silhouettes else 0
                
            # Check if this is a nested container (App::Part like nested_Side_1)
            elif is_nested_container(obj):
                FreeCAD.Console.PrintMessage(f"Processing container: {obj.Label}\n")
                silhouette = create_silhouette_for_container(doc, obj)
                if silhouette:
                    created_count += 1
                    
            # Try as a direct part
            elif hasattr(obj, "Shape") and not obj.Shape.isNull():
                FreeCAD.Console.PrintMessage(f"Processing part: {obj.Label}\n")
                silhouette = create_silhouette_for_part(doc, obj)
                if silhouette:
                    created_count += 1
            else:
                FreeCAD.Console.PrintWarning(f"Skipping '{obj.Label}': Not a valid shape\n")
        
        if created_count > 0:
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Created {created_count} silhouette(s)\n")
        else:
            FreeCAD.Console.PrintWarning("No silhouettes created. Check selection.\n")
    
    def IsActive(self):
        """Command is active when a document is open and objects are selected."""
        if FreeCAD.ActiveDocument is None:
            return False
        sel = FreeCADGui.Selection.getSelection()
        return len(sel) > 0


# Register the command
if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_CreateSilhouette', CreateSilhouetteCommand())

