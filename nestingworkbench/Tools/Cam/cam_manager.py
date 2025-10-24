# Nesting/nesting/cam_manager.py

"""
This module contains the CAMManager class, which is responsible for creating
and managing CAM jobs from the nested layouts.
"""

import FreeCAD
import Path

class CAMManager:
    """Manages the creation of FreeCAD CAM jobs from nested layouts."""
    def __init__(self, layout_group):
        self.doc = FreeCAD.ActiveDocument
        self.layout_group = layout_group

    def create_cam_job(self):
        """Main method to create the CAM job."""
        views_folder_name = f"{self.layout_group.Label}_2D_Views"
        views_folder = self.doc.getObject(views_folder_name)

        if not views_folder:
            FreeCAD.Console.PrintError(f"Could not find the 2D views folder: {views_folder_name}\n")
            return

        for sheet_view_folder in views_folder.Group:
            if sheet_view_folder.isDerivedFrom("App::DocumentObjectGroup"):
                self._create_job_for_sheet(sheet_view_folder)

    def _create_job_for_sheet(self, sheet_view_folder):
        """Creates a CAM job for a single sheet."""
        # Get the sheet number from the label
        sheet_num = int(sheet_view_folder.Label.split('_')[1])

        # Find the original sheet group
        sheet_group_name = f"Sheet_{sheet_num}"
        sheet_group = self.layout_group.getObject(sheet_group_name)

        if not sheet_group:
            FreeCAD.Console.PrintError(f"Could not find the original sheet group: {sheet_group_name}\n")
            return

        # Find the sheet object
        sheet_object = None
        for obj in sheet_group.Group:
            if obj.isDerivedFrom("Part::Box"):
                sheet_object = obj
                break

        if not sheet_object:
            FreeCAD.Console.PrintError(f"Could not find the sheet object in group: {sheet_group_name}\n")
            return

        # Create a new job
        job = Path.Create('Job')
        job.Label = f"CAM_Job_{sheet_view_folder.Label}"

        # Set the stock
        stock = job.Stock
        stock.Base = sheet_object
        stock.ExtXneg = 0
        stock.ExtXpos = 0
        stock.ExtYneg = 0
        stock.ExtYpos = 0
        stock.ExtZneg = 0
        stock.ExtZpos = 0

        # Create a tool controller and tool
        tool_controller = job.Tools.addToolController('Default')
        tool = tool_controller.addTool('EndMill', 'Tool')
        tool.Diameter = 3.175

        # Create profile operations
        for obj in sheet_view_folder.Group:
            if obj.isDerivedFrom("Part::Part2DObject"):
                op = Path.Create('Profile')
                op.ToolController = tool_controller
                op.Base = (obj, [])
                job.Operations.addOperation(op)

        self.doc.recompute()
