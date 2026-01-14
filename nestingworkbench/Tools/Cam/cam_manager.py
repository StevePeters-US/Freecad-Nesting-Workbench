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
            from CAM.Path.Main import Stock as PathStock
        except ImportError as e:
            FreeCAD.Console.PrintError(f"Failed to import CAM modules. Error: {e}\n")
            FreeCAD.Console.PrintError("Please ensure the CAM workbench is installed and enabled in FreeCAD 1.1+.\n")
            return
        
        # Get layout parameters from layout properties (preferred) or spreadsheet (fallback)
        sheet_width = 600.0  # Default values
        sheet_height = 600.0
        sheet_thickness = 3.0
        
        # Try to read from layout group properties first (most reliable)
        if self.layout_group:
            if hasattr(self.layout_group, 'SheetWidth'):
                sheet_width = float(self.layout_group.SheetWidth)
            if hasattr(self.layout_group, 'SheetHeight'):
                sheet_height = float(self.layout_group.SheetHeight)
            if hasattr(self.layout_group, 'SheetThickness'):
                sheet_thickness = float(self.layout_group.SheetThickness)
            
            # Fallback to spreadsheet if properties don't exist
            if not hasattr(self.layout_group, 'SheetWidth'):
                spreadsheet = self.layout_group.getObject("LayoutParameters")
                if spreadsheet:
                    try:
                        # Read sheet dimensions
                        width_val = spreadsheet.get('B2')
                        if width_val:
                            sheet_width = float(width_val)
                        height_val = spreadsheet.get('B3')
                        if height_val:
                            sheet_height = float(height_val)
                        # Read sheet thickness  
                        thickness_val = spreadsheet.get('B5')
                        if thickness_val:
                            sheet_thickness = float(thickness_val)
                    except Exception as e:
                        FreeCAD.Console.PrintWarning(f"Could not read parameters from spreadsheet: {e}\\n")
        
        # Find the sheet boundary and all nested parts
        sheet_boundary = None
        parts_to_machine = []
        
        for obj in sheet_group.Group:
            # Check for Stock (Sheet Boundary)
            if obj.Label.startswith("Sheet_Boundary"):
                sheet_boundary = obj
            
            # Check for the Parts container (Shapes_X)
            elif obj.Label.startswith("Shapes_") and obj.isDerivedFrom("App::DocumentObjectGroup"):
                # Found the parts group - use the App::Part containers directly
                # These have the correct placement for the nested position
                for nested_part in obj.Group:
                    # Each nested_part is an App::Part at the correct nested location
                    if nested_part.isDerivedFrom("App::Part"):
                        parts_to_machine.append(nested_part)
                    elif hasattr(nested_part, 'Shape') and nested_part.Shape:
                        # Fallback for direct Part::Feature objects
                        parts_to_machine.append(nested_part)
        
        if not sheet_boundary:
            FreeCAD.Console.PrintError(f"Could not find sheet boundary in {sheet_group.Label}.\n")
            return

        if not parts_to_machine:
            FreeCAD.Console.PrintWarning(f"No parts found to machine in {sheet_group.Label}. Skipping.\n")
            return

        # Create a new CAM job for this sheet with all the parts
        job_name = f"CAM_Job_{sheet_group.Label}"
        job = PathJob.Create(job_name, parts_to_machine, None)
        
        # Replace the stock with a CreateBox stock matching sheet dimensions
        if job.Stock:
            old_stock = job.Stock
            self.doc.removeObject(old_stock.Name)
        
        # Create new box stock with sheet dimensions
        new_stock = PathStock.CreateBox(job)
        new_stock.Length = sheet_width
        new_stock.Width = sheet_height
        new_stock.Height = sheet_thickness
        job.Stock = new_stock
        
        # Set up the ViewProvider Proxy to enable proper tree view nesting
        try:
            import FreeCADGui
            if FreeCADGui.ActiveDocument and job.ViewObject:
                job.ViewObject.Proxy = PathJobGui.ViewProvider(job.ViewObject)
                
                # Make the models visible by default
                if job.Model and job.Model.ViewObject:
                    job.Model.ViewObject.Visibility = True
                    # Also make each model object visible
                    for model_obj in job.Model.Group:
                        if model_obj.ViewObject:
                            model_obj.ViewObject.Visibility = True
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Could not configure ViewProvider: {e}\n")

        # Recompute to finalize the job
        self.doc.recompute()
        
        FreeCAD.Console.PrintMessage(f"Created CAM job '{job_name}' for {sheet_group.Label} with {len(parts_to_machine)} parts (sheet: {sheet_width}x{sheet_height}x{sheet_thickness}mm)\n")
