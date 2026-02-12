# Nesting/nesting/exporter.py

"""
This module contains the SheetExporter class. Its function is to create 2D
projections of each sheet's layout within the active document.
"""

import FreeCAD
import Part
import os
import importDXF
from ...freecad_helpers import get_layout_group, get_sheet_groups, get_all_objects_recursive, recursive_delete

class SheetExporter:
    """
    Handles finding the layout group, iterating through sheets, and creating
    a new group containing 2D projections of each sheet's geometry.
    """
    def __init__(self, layout_group=None):
        self.doc = FreeCAD.ActiveDocument
        if layout_group:
            self.layout_group = layout_group
        else:
            self.layout_group = get_layout_group(self.doc)



    def export_sheets(self, export_dir, delete_generated_objects=True):
        """Main method to create 2D projections of the layout in a new folder."""
        if not self.layout_group:
            FreeCAD.Console.PrintMessage("No valid packed layout found to create views from.\n")
            return

        sheet_groups = get_sheet_groups(self.layout_group)
        if not sheet_groups:
            FreeCAD.Console.PrintMessage("No sheets found within the layout group.\n")
            return

        # Create a new top-level folder for the 2D views
        views_folder_name = f"{self.layout_group.Label}_2D_Views"
        
        # If a folder with this name already exists, remove it for a clean slate
        if self.doc.getObject(views_folder_name):
            self.doc.removeObject(views_folder_name)

        views_folder = self.doc.addObject("App::DocumentObjectGroup", views_folder_name)

        # Process each sheet individually
        for sheet_group in sheet_groups:
            objects_in_sheet = get_all_objects_recursive(sheet_group)
            
            # Filter for only the objects we want to project, excluding offset bounds and annotations
            objects_to_project = [
                obj for obj in objects_in_sheet 
                if (hasattr(obj, 'Shape') and obj.Shape and 
                    not obj.isDerivedFrom("Draft::Text") and
                    not obj.Label.startswith("bound_"))
            ]
            
            if not objects_to_project:
                FreeCAD.Console.PrintWarning(f"No projectable geometry found in {sheet_group.Label}. Skipping.\n")
                continue

            # Create a sub-folder for this specific sheet's views
            sheet_view_folder = self.doc.addObject("App::DocumentObjectGroup", f"{sheet_group.Label}_Views")
            views_folder.addObject(sheet_view_folder)

            try:
                # Add a 2D projection of each object to the new sub-folder
                for obj in objects_to_project:
                    # Get the base shape, which is defined at the origin
                    base_shape = obj.Shape.copy()
                    
                    # Project the base shape to a 2D entity at the origin
                    # ShapeStrings are Compounds, so we check for that type as well. 
                    if isinstance(base_shape, (Part.Wire, Part.Face, Part.Compound, Part.Solid)):
                        shape_2d = base_shape
                    else:
                        shape_2d = base_shape.toShape2D()
                    
                    # Create the new 2D object
                    new_2d_obj = self.doc.addObject("Part::Part2DObject", f"{obj.Name}_2D")
                    new_2d_obj.Shape = shape_2d
                    
                    # Apply the original object's placement to the new 2D object
                    new_2d_obj.Placement = obj.Placement
                    
                    # Add the new 2D object to this sheet's view folder
                    sheet_view_folder.addObject(new_2d_obj)
                
                FreeCAD.Console.PrintMessage(f"Successfully created 2D views for {sheet_group.Label}\n")

            except Exception as e:
                FreeCAD.Console.PrintError(f"An error occurred during view creation for {sheet_group.Label}: {e}\n")

        FreeCAD.Console.PrintMessage(f"Finished creating 2D views in folder: {views_folder.Label}\n")

        # Export to DXF
        for sheet_view_folder in views_folder.Group:
            if sheet_view_folder.isDerivedFrom("App::DocumentObjectGroup"):
                filename = f"{self.doc.Name}_{sheet_view_folder.Label}.dxf"
                filepath = os.path.join(export_dir, filename)
                importDXF.export(sheet_view_folder.Group, filepath)
                FreeCAD.Console.PrintMessage(f"Exported {sheet_view_folder.Label} to {filepath}\n")

        # Delete the generated 2D views if requested
        if delete_generated_objects:
            recursive_delete(self.doc, views_folder)
            FreeCAD.Console.PrintMessage("Deleted temporary 2D views folder.\n")


