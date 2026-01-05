
import FreeCAD
import FreeCADGui
import Part
import os
import time
import math
from PySide import QtGui
from ...datatypes.shape import Shape
from .shape_preparer import ShapePreparer

try:
    from .nesting_logic import nest, NestingDependencyError
except ImportError:
    pass

class NestingJob:
    """
    Manages a single nesting session using the Sandbox Pattern.
    - Creates a temporary environment (Layout_temp).
    - Runs the nesting algorithm.
    - Commits results to a permanent target layout or discards them on cancel.
    """
    def __init__(self, doc, target_layout, ui_params, preparer):
        self.doc = doc
        self.target_layout = target_layout
        self.params = ui_params
        self.preparer = preparer
        
        self.temp_layout = None
        self.parts_group = None # The "PartsToPlace" bin
        self.sheets = []
        
        self._init_sandbox()

    def _init_sandbox(self):
        """Creates the temporary layout and parts bin."""
        self.temp_layout = self.doc.addObject("App::DocumentObjectGroup", "Layout_temp")
        self.parts_group = self.doc.addObject("App::DocumentObjectGroup", "PartsToPlace")
        self.temp_layout.addObject(self.parts_group)
        
        if hasattr(self.temp_layout, "ViewObject"):
            self.temp_layout.ViewObject.Visibility = True
            
        FreeCAD.Console.PrintMessage(f"DEBUG: Initialized sandbox: {self.temp_layout.Name}\n")

    def run(self, quantities, master_map, rotation_params, algo_kwargs, is_simulating=False):
        """Executes the nesting logic: Prepare -> Nest -> Draw."""
        
        # 1. Prepare Shapes
        FreeCAD.Console.PrintMessage("DEBUG: Preparing shapes...\n")
        parts_to_nest = self.preparer.prepare_parts(
            self.params, quantities, master_map, self.temp_layout, self.parts_group
        )
        
        if not parts_to_nest:
            raise ValueError("No valid parts to nest.")

        # 1.5 Persist Metadata (Quantity, Rotations) to Master Containers
        self._persist_metadata(quantities, rotation_params)

        # 2. Run Algorithm
        FreeCAD.Console.PrintMessage("DEBUG: Running algorithm...\n")
        self.sheets, unplaced, steps = nest(
            parts_to_nest, 
            self.params['sheet_width'], 
            self.params['sheet_height'],
            self.params['rotation_steps'], 
            is_simulating, 
            **algo_kwargs
        )
        
        if not is_simulating:
            self._apply_placement(self.sheets, parts_to_nest)
            
        # 3. Draw Results (into Temp Layout)
        # Note: sheet.draw now handles unlinking from PartsToPlace!
        FreeCAD.Console.PrintMessage("DEBUG: Drawing results...\n")
        for sheet in self.sheets:
            sheet.draw(self.doc, self.params, self.temp_layout, parts_to_place_group=self.parts_group)
            
        return len(self.sheets), sum(len(s) for s in self.sheets)

    def _persist_metadata(self, quantities, rotation_params):
        master_group = self.temp_layout.getObject("MasterShapes")
        if not master_group: return
        
        for container in master_group.Group:
             if not hasattr(container, "Group"): continue
             
             # Find inner shape label
             shape = next((c for c in container.Group if c.Label.startswith("master_shape_")), None)
             if shape:
                 original_label = shape.Label.replace("master_shape_", "")
                 
                 # Save Quantity
                 # quantities dict is {label: (qty, rotation_steps)}
                 qty_tuple = quantities.get(original_label) 
                 if qty_tuple:
                     qty = qty_tuple[0]
                     if not hasattr(container, "Quantity"):
                         container.addProperty("App::PropertyInteger", "Quantity", "Nesting", "Part Quantity")
                     container.Quantity = qty

                 # Save Rotation Overrides
                 if original_label in rotation_params:
                     # rotation_params is {label: (val, override_bool)}
                     r_val, r_override = rotation_params[original_label]
                     
                     if not hasattr(container, "PartRotationSteps"):
                          container.addProperty("App::PropertyInteger", "PartRotationSteps", "Nesting", "Rotation steps")
                     if not hasattr(container, "PartRotationOverride"):
                          container.addProperty("App::PropertyBool", "PartRotationOverride", "Nesting", "Override global rotation")
                     
                     container.PartRotationSteps = int(r_val)
                     container.PartRotationOverride = bool(r_override)

    def commit(self):
        """Promotes the temporary results to the target layout."""
        FreeCAD.Console.PrintMessage(f"DEBUG: Committing to target {self.target_layout.Label}...\n")
        
        # 1. Clean Target of old results (Sheets)
        # We do NOT remove MasterShapes unless we have new ones to replace them?
        # Current logic: If we re-ran, we overwrite sheets.
        to_remove = []
        for child in self.target_layout.Group:
            if child.Label.startswith("Sheet_"):
                to_remove.append(child)
        
        for child in to_remove:
            self._recursive_delete(child)
            
        # 2. Check for new MasterShapes in Temp
        temp_masters = next((c for c in self.temp_layout.Group if c.Label.startswith("MasterShapes")), None)
        if temp_masters and len(temp_masters.Group) > 0:
            # We have new masters, replace old ones in Target
            old_masters = next((c for c in self.target_layout.Group if c.Label.startswith("MasterShapes")), None)
            if old_masters:
                self._recursive_delete(old_masters)
            
            # Sanitize labels before move
            temp_masters.Label = "MasterShapes"
            for m in temp_masters.Group:
                if m.Label.startswith("temp_master_"):
                    m.Label = m.Label.replace("temp_master_", "master_")
            
            self.target_layout.addObject(temp_masters)
        else:
            # No new masters, if temp has empty master group, delete it
            if temp_masters:
                self._recursive_delete(temp_masters)

        # 3. Move Sheets from Temp to Target
        sheets_to_move = [c for c in self.temp_layout.Group if c.Label.startswith("Sheet_")]
        for sheet in sheets_to_move:
            self.target_layout.addObject(sheet)
            
        # 4. Clean up Temp (Sandbox)
        # PartsToPlace should be empty of placed parts due to unlinking in Sheet.draw
        # Any unplaced parts remain there and will be deleted.
        self.cleanup()
        
        # 5. Apply Properties to Target
        self._apply_properties(self.target_layout)
        
        return self.target_layout

    def cleanup(self):
        """Destroys the sandbox."""
        FreeCAD.Console.PrintMessage("DEBUG: Cleaning up sandbox...\n")
        if self.temp_layout:
            self._recursive_delete(self.temp_layout)
            self.temp_layout = None
            
        # Safety: Check for any floating PartsToPlace that might have escaped
        # (Though with proper containment this shouldn't happen)
        match_names = []
        for obj in self.doc.Objects:
            if obj.Label.startswith("PartsToPlace") or obj.Label.startswith("Layout_temp"):
                 match_names.append(obj.Name)
        
        for name in match_names:
            o = self.doc.getObject(name)
            if o: self._recursive_delete(o)

    def _recursive_delete(self, obj):
        try:
            _ = obj.Name # Check validity
        except: return
        
        if hasattr(obj, "Group"):
             # Defensive copy of children list
             children = list(obj.Group)
             for c in children:
                 self._recursive_delete(c)
        try:
            self.doc.removeObject(obj.Name)
        except: pass

    def _apply_placement(self, sheets, parts_to_nest):
        original_parts_map = {part.id: part for part in parts_to_nest}
        for sheet in sheets:
            for i, placed_part in enumerate(sheet.parts):
                 original_part = original_parts_map[placed_part.shape.id]
                 # Calculate placement relative to sheet origin
                 sheet_origin = sheet.get_origin()
                 original_part.placement = placed_part.shape.get_final_placement(sheet_origin)
                 sheet.parts[i].shape = original_part

    def _apply_properties(self, layout_obj):
        p = self.params
        self._set_prop(layout_obj, "App::PropertyLength", "SheetWidth", p['sheet_width'])
        self._set_prop(layout_obj, "App::PropertyLength", "SheetHeight", p['sheet_height'])
        self._set_prop(layout_obj, "App::PropertyLength", "PartSpacing", p['spacing'])
        self._set_prop(layout_obj, "App::PropertyFloat", "BoundaryResolution", p['boundary_resolution'])
        self._set_prop(layout_obj, "App::PropertyFile", "FontFile", p['font_path'])
        self._set_prop(layout_obj, "App::PropertyBool", "ShowBounds", p['show_bounds'])
        self._set_prop(layout_obj, "App::PropertyBool", "AddLabels", p['add_labels'])
        self._set_prop(layout_obj, "App::PropertyLength", "LabelHeight", p['label_height'])
        self._set_prop(layout_obj, "App::PropertyInteger", "GlobalRotationSteps", p['rotation_steps'])

    def _set_prop(self, obj, type_str, name, val):
        if not hasattr(obj, name):
            obj.addProperty(type_str, name, "Layout", "")
        setattr(obj, name, val)


class NestingController:
    """
    Main controller for the Nesting Workbench.
    Handles UI interaction, Job creation, and Layout management.
    """
    def __init__(self, ui_panel):
        self.ui = ui_panel
        self.doc = FreeCAD.ActiveDocument
        self.current_job = None
        self.shape_preparer = ShapePreparer(self.doc, {})
        
        # Initialize default fonts
        font_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'fonts'))
        default_font = os.path.join(font_dir, 'PoiretOne-Regular.ttf')
        self.ui.selected_font_path = default_font
        if hasattr(self.ui, 'font_label'):
            self.ui.font_label.setText(os.path.basename(default_font))

    def execute_nesting(self):
        FreeCAD.Console.PrintMessage("\n--- NESTING START ---\n")
        
        if self.current_job:
            self.current_job.cleanup()
            self.current_job = None

        # 1. Ensure Target Layout Exists (Create default if needed)
        target_layout = self._ensure_target_layout()
        if not target_layout:
             return # Standard error handled in helper
             
        # Hide target during operation
        if hasattr(target_layout, "ViewObject"):
            target_layout.ViewObject.Visibility = False

        # 2. Collect Parameters
        ui_params = self._collect_ui_params()
        ui_params, quantities, master_map, rotation_params = self._collect_job_parameters(ui_params)
        
        # 3. Create Job
        self.current_job = NestingJob(self.doc, target_layout, ui_params, self.shape_preparer)
        
        # 4. Run Job
        try:
            self.ui.status_label.setText("Running nesting...")
            QtGui.QApplication.processEvents()
            
            algo_kwargs = self._prepare_algo_kwargs(ui_params)
            is_simulating = self.ui.simulate_nesting_checkbox.isChecked()
            
            num_sheets, num_parts = self.current_job.run(quantities, master_map, rotation_params, algo_kwargs, is_simulating)
            
            msg = f"Placed {num_parts} parts on {num_sheets} sheets."
            self.ui.status_label.setText(msg)
            FreeCAD.Console.PrintMessage(f"{msg}\n--- NESTING DONE ---\n")
            if self.ui.sound_checkbox.isChecked(): QtGui.QApplication.beep()
            
            if is_simulating:
                 # Simulation doesn't commit automatically? 
                 # Usually simulation is just visual.
                 pass
                 
        except Exception as e:
            FreeCAD.Console.PrintError(f"Nesting Error: {e}\n")
            self.ui.status_label.setText(f"Error: {e}")
            self.cancel_job()

    def finalize_job(self):
        """Called when User clicks OK."""
        if self.current_job:
            self.current_job.commit()
            self.current_job = None
            FreeCAD.Console.PrintMessage("Job Finalized & Committed.\n")
            self.doc.recompute()

    def cancel_job(self):
        """Called when User clicks Cancel."""
        if self.current_job:
            self.current_job.cleanup()
            # Restore visibility of original target
            if self.current_job.target_layout and hasattr(self.current_job.target_layout, "ViewObject"):
                self.current_job.target_layout.ViewObject.Visibility = True
            
            self.current_job = None
            FreeCAD.Console.PrintMessage("Job Cancelled.\n")

    def toggle_bounds_visibility(self):
        is_visible = self.ui.show_bounds_checkbox.isChecked()
        current_layout = getattr(self.ui, 'current_layout', None)
        
        if not current_layout: return

        # Recursively find parts with ShowBounds property
        def set_show_bounds(obj):
            if hasattr(obj, "ShowBounds"):
                obj.ShowBounds = is_visible
            if hasattr(obj, "Group"):
                for child in obj.Group:
                    set_show_bounds(child)
                    
        set_show_bounds(current_layout)
        self.doc.recompute()

    def _ensure_target_layout(self):
        """Determines the target layout, creating a default one if none exists."""
        target = getattr(self.ui, 'current_layout', None)
        
        # Validate existing
        if target:
            try:
                if target not in self.doc.Objects: target = None
            except: target = None
            
        # Infer from selection
        if not target and hasattr(self.ui, 'selected_shapes_to_process') and self.ui.selected_shapes_to_process:
             # Logic to find parent layout derived previously...
             # Simplified for brevity/robustness
             pass 

        # Create Default
        if not target:
            base_name = "Layout"
            i = 0
            existing_labels = [o.Label for o in self.doc.Objects]
            while f"{base_name}_{i:03d}" in existing_labels: i += 1
            target = self.doc.addObject("App::DocumentObjectGroup", f"{base_name}_{i:03d}")
            target.Label = f"{base_name}_{i:03d}"
            self.ui.current_layout = target
            FreeCAD.Console.PrintMessage(f"DEBUG: Created default layout: {target.Label}\n")
            
        return target

    def _collect_ui_params(self):
        return {
            'sheet_width': self.ui.sheet_width_input.value(),
            'sheet_height': self.ui.sheet_height_input.value(),
            'spacing': self.ui.part_spacing_input.value(),
            'boundary_resolution': self.ui.boundary_resolution_input.value(),
            'rotation_steps': self.ui.rotation_steps_spinbox.value(),
            'add_labels': self.ui.add_labels_checkbox.isChecked(),
            'font_path': getattr(self.ui, 'selected_font_path', None),
            'show_bounds': self.ui.show_bounds_checkbox.isChecked(),
            'label_height': self.ui.label_height_input.value()
        }

    def _collect_job_parameters(self, ui_settings):
        # Re-implementation of collecting quantities and master map from UI table
        # Simplified for brevity - assumes standard UI state
        quantities = {}
        master_map = {}
        rotation_params = {}
        
        global_rot = ui_settings['rotation_steps']
        
        for row in range(self.ui.shape_table.rowCount()):
            try:
                label = self.ui.shape_table.item(row, 0).text()
                qty = self.ui.shape_table.cellWidget(row, 1).value()
                
                rot_widget = self.ui.shape_table.cellWidget(row, 2)
                rot_val = rot_widget.findChild(QtGui.QSpinBox).value()
                override = self.ui.shape_table.cellWidget(row, 3).isChecked()
                
                quantities[label] = (qty, rot_val if override else global_rot)
            except: continue
            
        # Map objects
        for obj in self.ui.selected_shapes_to_process:
             try:
                 lbl = obj.Label.replace("master_shape_", "")
                 if lbl in quantities:
                     master_map[obj.Label] = obj
             except: pass
             
        return ui_settings, quantities, master_map, rotation_params

    def _prepare_algo_kwargs(self, ui_params):
        algo_kwargs = {}
        if self.ui.minkowski_random_checkbox.isChecked():
            algo_kwargs['search_direction'] = None
        else:
            angle_deg = (270 - self.ui.minkowski_direction_dial.value()) % 360
            angle_rad = math.radians(angle_deg)
            algo_kwargs['search_direction'] = (math.cos(angle_rad), math.sin(angle_rad))
        
        algo_kwargs['population_size'] = self.ui.minkowski_population_size_input.value()
        algo_kwargs['generations'] = self.ui.minkowski_generations_input.value()
        algo_kwargs['spacing'] = ui_params['spacing']
        
        if hasattr(self.ui, 'log_message'):
            algo_kwargs['log_callback'] = self.ui.log_message
            
        return algo_kwargs
