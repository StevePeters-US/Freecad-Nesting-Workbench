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
             FreeCAD.Console.PrintError("No layout group provided.\\n")
             return

        # Iterate over the layout group to find sheet groups directly
        for obj in self.layout_group.Group:
            # We assume groups starting with "Sheet_" are the sheet containers
            if obj.isDerivedFrom("App::DocumentObjectGroup") and obj.Label.startswith("Sheet_"):
                self._create_job_for_sheet(obj)


    def _create_job_for_sheet(self, sheet_group):
        """Creates a CAM job for a sheet with proper stock dimensions."""
        # Import CAM modules (FreeCAD 1.1+)
        try:
            from CAM.Path.Main import Job as PathJob
            from CAM.Path.Main import Stock as PathStock
        except ImportError as e:
            FreeCAD.Console.PrintError(f"Failed to import CAM modules. Error: {e}\\n")
            FreeCAD.Console.PrintError("Please ensure the CAM workbench is installed and enabled in FreeCAD 1.1+.\\n")
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
        
        # Collect the part_* shapes with their container placements applied
        # Create Part::Feature with shape already transformed (placement baked into geometry)
        parts_to_machine = []
        labels_to_machine = []
        
        for obj in sheet_group.Group:
            # Check for the Parts container (Shapes_X)
            if obj.Label.startswith("Shapes_") and obj.isDerivedFrom("App::DocumentObjectGroup"):
                for nested_part in obj.Group:
                    if nested_part.isDerivedFrom("App::Part") and nested_part.Label.startswith("nested_"):
                        # Get container placement
                        container_placement = nested_part.Placement
                        
                        # Find the part_* and label_* shapes inside the container
                        for child in nested_part.Group:
                            if hasattr(child, 'Shape') and child.Shape and not child.Shape.isNull():
                                # Create CAM part
                                if child.Label.startswith("part_"):
                                    cam_part_name = f"CAM_{child.Label}"
                                    cam_part = self.doc.addObject("Part::Feature", cam_part_name)
                                    
                                    # Bake the placement into the shape geometry
                                    combined_placement = container_placement.multiply(child.Shape.Placement)
                                    transformed_shape = child.Shape.copy()
                                    transformed_shape.Placement = FreeCAD.Placement()  # Reset placement
                                    transformed_shape = transformed_shape.transformGeometry(combined_placement.toMatrix())
                                    cam_part.Shape = transformed_shape
                                    
                                    parts_to_machine.append(cam_part)
                                
                                # Create CAM label
                                elif child.Label.startswith("label_"):
                                    cam_label_name = f"CAM_{child.Label}"
                                    cam_label = self.doc.addObject("Part::Feature", cam_label_name)
                                    
                                    # Bake the placement into the shape geometry
                                    combined_placement = container_placement.multiply(child.Shape.Placement)
                                    transformed_shape = child.Shape.copy()
                                    transformed_shape.Placement = FreeCAD.Placement()
                                    transformed_shape = transformed_shape.transformGeometry(combined_placement.toMatrix())
                                    cam_label.Shape = transformed_shape
                                    
                                    labels_to_machine.append(cam_label)
        
        if not parts_to_machine:
            FreeCAD.Console.PrintWarning(f"No parts found to machine in {sheet_group.Label}. Skipping.\\n")
            return
        
        # Combine parts and labels for the CAM job
        all_models = parts_to_machine + labels_to_machine
        FreeCAD.Console.PrintMessage(f"Creating CAM job with {len(parts_to_machine)} parts and {len(labels_to_machine)} labels...\\n")
        
        # Use GUI Create function which properly sets up all Model-Job linking
        try:
            import FreeCADGui
            from CAM.Path.Main.Gui import Job as PathJobGui
            
            # Use the GUI create function with openTaskPanel=False so it doesn't pop up a dialog
            job = PathJobGui.Create(all_models, None, openTaskPanel=False)
            
            if job:
                # Rename the job to our desired name
                job.Label = f"CAM_Job_{sheet_group.Label}"
                
                # CAM Model-* objects reference their base objects via Objects property
                # Keep base objects accessible but hide them from view
                base_group_name = f"CAM_BaseObjects_{sheet_group.Label}"
                base_group = self.doc.addObject("App::DocumentObjectGroup", base_group_name)
                
                # Hide the base group itself
                if hasattr(base_group, 'ViewObject') and base_group.ViewObject:
                    base_group.ViewObject.Visibility = False
                
                for cam_obj in all_models:
                    try:
                        base_group.addObject(cam_obj)
                        # Hide each base object
                        if hasattr(cam_obj, 'ViewObject') and cam_obj.ViewObject:
                            cam_obj.ViewObject.Visibility = False
                    except Exception as e:
                        FreeCAD.Console.PrintWarning(f"Could not organize CAM object {cam_obj.Name}: {e}\\n")
                
                # Add the base group under the layout group for organization
                if self.layout_group:
                    try:
                        self.layout_group.addObject(base_group)
                    except:
                        pass
                
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
                
                # Set post processor to GRBL
                try:
                    job.PostProcessor = "grbl"
                    job.PostProcessorOutputFile = ""  # Will use default naming
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"Could not set post processor: {e}\\n")
                
                # Ensure models are visible
                if hasattr(job, 'Model') and job.Model:
                    if hasattr(job.Model, 'ViewObject') and job.Model.ViewObject:
                        job.Model.ViewObject.Visibility = True
                    if hasattr(job.Model, 'Group'):
                        for model_obj in job.Model.Group:
                            if hasattr(model_obj, 'ViewObject') and model_obj.ViewObject:
                                model_obj.ViewObject.Visibility = True
                
                # Recompute to finalize the job
                self.doc.recompute()
                
                FreeCAD.Console.PrintMessage(f"Created CAM job '{job.Label}' for {sheet_group.Label} (stock: {sheet_width}x{sheet_height}x{sheet_thickness}mm)\\n")
            else:
                FreeCAD.Console.PrintError("Failed to create CAM job.\\n")
                
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error creating CAM job: {e}\\n")
            import traceback
            traceback.print_exc()

