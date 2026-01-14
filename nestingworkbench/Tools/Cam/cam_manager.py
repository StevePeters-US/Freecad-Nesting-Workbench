# Nesting/nesting/cam_manager.py

"""
This module contains the CAMManager class, which is responsible for creating
and managing CAM jobs from the nested layouts.
"""

import FreeCAD

class CAMManager:
    """Manages the creation of FreeCAD CAM jobs from nested layouts."""
    def __init__(self, layout_group):
        self.doc = FreeCAD.ActiveDocument
        self.layout_group = layout_group

    def create_cam_job(self):
        """Main method to create the CAM job."""
        if not self.layout_group:
             FreeCAD.Console.PrintError("No layout group provided.\n")
             return

        # Iterate over the layout group to find sheet groups directly
        for obj in self.layout_group.Group:
            # We assume groups starting with "Sheet_" are the sheet containers
            if obj.isDerivedFrom("App::DocumentObjectGroup") and obj.Label.startswith("Sheet_"):
                self._create_job_for_sheet(obj)

    def _create_job_for_sheet(self, sheet_group):
        """Creates a CAM job for a single sheet using 3D parts."""
        # Import CAM modules (FreeCAD 1.1+)
        try:
            from CAM.Path.Main import Job as PathJob
            from CAM.Path.Main.Gui import Job as PathJobGui
        except ImportError as e:
            FreeCAD.Console.PrintError(f"Failed to import CAM modules. Error: {e}\n")
            FreeCAD.Console.PrintError("Please ensure the CAM workbench is installed and enabled in FreeCAD 1.1+.\n")
            return
        
        # Get material thickness from layout spreadsheet
        material_thickness = 3.0  # Default value
        if self.layout_group:
            spreadsheet = self.layout_group.getObject("LayoutParameters")
            if spreadsheet:
                try:
                    thickness_value = spreadsheet.get('B5')
                    if thickness_value:
                        material_thickness = float(thickness_value)
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"Could not read material thickness from spreadsheet: {e}\n")
        
        # Find the sheet object (Stock)
        sheet_object = None
        
        for obj in sheet_group.Group:
            if obj.Label.startswith("Sheet_Boundary"):
                 sheet_object = obj
                 break
        
        if not sheet_object:
            FreeCAD.Console.PrintError(f"Could not find sheet object in {sheet_group.Label}.\n")
            return

        # Create a new CAM job for this sheet
        job_name = f"CAM_Job_{sheet_group.Label}"
        job = PathJob.Create(job_name, [sheet_object], None)
        
        # Configure the stock to match the sheet dimensions
        stock = job.Stock
        stock.ExtXneg = 0
        stock.ExtXpos = 0
        stock.ExtYneg = 0
        stock.ExtYpos = 0
        stock.ExtZneg = material_thickness
        stock.ExtZpos = 0

        # Set up the ViewProvider Proxy to enable proper tree view nesting
        # This is critical - without this, the tree view won't show children
        try:
            import FreeCADGui
            if FreeCADGui.ActiveDocument and job.ViewObject:
                # Assign the ViewProvider class to enable claimChildren()
                job.ViewObject.Proxy = PathJobGui.ViewProvider(job.ViewObject)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Could not set ViewProvider: {e}\n")

        # Recompute to finalize the job
        self.doc.recompute()
        
        FreeCAD.Console.PrintMessage(f"Created CAM job '{job_name}' for {sheet_group.Label} (thickness: {material_thickness}mm)\n")
