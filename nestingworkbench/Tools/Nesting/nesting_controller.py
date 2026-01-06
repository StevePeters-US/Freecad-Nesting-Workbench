
import FreeCAD
import FreeCADGui
import Part
import os
import time
import math
from PySide import QtGui
from ...datatypes.shape import Shape
from .shape_preparer import ShapePreparer
from .layout_manager import LayoutManager, Layout

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
            


    def run(self, quantities, master_map, rotation_params, algo_kwargs, is_simulating=False):
        """Executes the nesting logic: Prepare -> Nest -> Draw."""
        
        # 1. Prepare Shapes
        parts_to_nest = self.preparer.prepare_parts(
            self.params, quantities, master_map, self.temp_layout, self.parts_group
        )
        
        if not parts_to_nest:
            raise ValueError("No valid parts to nest.")

        # 1.5 Persist Metadata (Quantity, Rotations) to Master Containers
        self._persist_metadata(quantities, rotation_params)

        # 2. Run Algorithm
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
        
        # DEBUG: What's in temp_masters before commit?
        if temp_masters:
            FreeCAD.Console.PrintMessage(f"\n=== DEBUG: Commit - temp_masters has {len(temp_masters.Group)} containers ===\n")
            for container in temp_masters.Group:
                children = [c.Label for c in container.Group] if hasattr(container, 'Group') else []
                FreeCAD.Console.PrintMessage(f"  {container.Label}: {children}\n")
        
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
            
            # DEBUG: What's in target after commit?
            final_masters = next((c for c in self.target_layout.Group if c.Label.startswith("MasterShapes")), None)
            if final_masters:
                FreeCAD.Console.PrintMessage(f"\n=== DEBUG: After commit - final_masters has {len(final_masters.Group)} containers ===\n")
                for container in final_masters.Group:
                    children = [c.Label for c in container.Group] if hasattr(container, 'Group') else []
                    FreeCAD.Console.PrintMessage(f"  {container.Label}: {children}\n")
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
            if self.target_layout and obj.Name == self.target_layout.Name: return
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
        
        algo_kwargs = self._prepare_algo_kwargs(ui_params)
        is_simulating = self.ui.simulate_nesting_checkbox.isChecked()
        
        # Check if GA mode is enabled
        generations = algo_kwargs.get('generations', 1)
        population_size = algo_kwargs.get('population_size', 1)
        
        if generations > 1 and population_size > 1:
            # GA Mode: Use LayoutManager for multiple layouts
            self._execute_ga_nesting(target_layout, ui_params, quantities, master_map, 
                                     rotation_params, algo_kwargs, is_simulating)
        else:
            # Standard Mode: Single job
            self._execute_standard_nesting(target_layout, ui_params, quantities, master_map, 
                                           rotation_params, algo_kwargs, is_simulating)
    
    def _execute_standard_nesting(self, target_layout, ui_params, quantities, master_map, 
                                   rotation_params, algo_kwargs, is_simulating):
        """Standard single-layout nesting."""
        # Create Job
        self.current_job = NestingJob(self.doc, target_layout, ui_params, self.shape_preparer)
        
        try:
            self.ui.status_label.setText("Running nesting...")
            QtGui.QApplication.processEvents()
            
            num_sheets, num_parts = self.current_job.run(quantities, master_map, rotation_params, algo_kwargs, is_simulating)
            
            msg = f"Placed {num_parts} parts on {num_sheets} sheets."
            self.ui.status_label.setText(msg)
            FreeCAD.Console.PrintMessage(f"{msg}\n--- NESTING DONE ---\n")
            if self.ui.sound_checkbox.isChecked(): QtGui.QApplication.beep()
                 
        except Exception as e:
            FreeCAD.Console.PrintError(f"Nesting Error: {e}\n")
            self.ui.status_label.setText(f"Error: {e}")
            self.cancel_job()
    
    def _execute_ga_nesting(self, target_layout, ui_params, quantities, master_map, 
                            rotation_params, algo_kwargs, is_simulating):
        """GA optimization using multiple layouts."""
        from .algorithms import genetic_utils
        
        generations = algo_kwargs.get('generations', 1)
        population_size = algo_kwargs.get('population_size', 1)
        rotation_steps = ui_params.get('rotation_steps', 1)
        elite_count = max(1, population_size // 5)  # Keep top 20%
        mutation_rate = 0.1
        early_stop_threshold = 5
        
        FreeCAD.Console.PrintMessage(f"GA Mode: {generations} generations, {population_size} population\n")
        
        # Create LayoutManager
        layout_manager = LayoutManager(self.doc, self.shape_preparer.processed_shape_cache)
        
        # STEP 1: Create initial population of layouts
        self.ui.status_label.setText(f"Creating {population_size} layouts...")
        QtGui.QApplication.processEvents()
        
        layouts = layout_manager.create_ga_population(
            master_map, quantities, ui_params, population_size, rotation_steps
        )
        
        best_layout = None
        best_efficiency = 0
        generations_without_improvement = 0
        
        try:
            for gen in range(generations):
                FreeCAD.Console.PrintMessage(f"\n=== Generation {gen+1}/{generations} ===\n")
                self.ui.status_label.setText(f"Generation {gen+1}/{generations}...")
                QtGui.QApplication.processEvents()
                
                # Debug: show all layouts with their part counts
                FreeCAD.Console.PrintMessage(f"  Layouts to evaluate: {len(layouts)}\n")
                for i, lay in enumerate(layouts):
                    part_ids = [p.id for p in lay.parts] if lay.parts else []
                    FreeCAD.Console.PrintMessage(f"    {i+1}. {lay.name}: {part_ids}\n")
                
                # Run nesting on each layout
                for idx, layout in enumerate(layouts):
                    FreeCAD.Console.PrintMessage(f"  [Gen {gen+1}] Layout {idx+1}/{len(layouts)}: {layout.name}\n")
                    
                    # Store genes (ordering and rotations) for this layout
                    layout.genes = [(p.id, getattr(p, '_angle', 0)) for p in layout.parts] if layout.parts else []
                    
                    # Skip if already nested (e.g., winner from previous generation)
                    if layout.sheets:
                        FreeCAD.Console.PrintMessage(f"    -> Already nested (winner from previous gen), efficiency: {layout.efficiency:.1f}%\n")
                        continue
                    
                    if not layout.parts:
                        layout.fitness = float('inf')
                        layout.efficiency = 0
                        continue
                    
                    # Run nesting with quiet mode (suppress per-part logs)
                    sheets, unplaced, _ = nest(
                        layout.parts,
                        ui_params['sheet_width'],
                        ui_params['sheet_height'],
                        rotation_steps,
                        is_simulating,
                        quiet=True,  # Suppress per-part logging in GA mode
                        **algo_kwargs
                    )
                    
                    layout.sheets = sheets
                    
                    # Calculate efficiency
                    fitness, efficiency = layout_manager.calculate_efficiency(
                        layout, ui_params['sheet_width'], ui_params['sheet_height']
                    )
                    
                    # Penalize unplaced parts
                    if unplaced:
                        layout.fitness += len(unplaced) * ui_params['sheet_width'] * ui_params['sheet_height'] * 10
                    
                    FreeCAD.Console.PrintMessage(f"    -> Efficiency: {efficiency:.1f}%\n")
                    
                    # Draw the layout (no offset - we'll delete non-winners)
                    for sheet in sheets:
                        sheet.draw(self.doc, ui_params, layout.layout_group, 
                                   parts_to_place_group=layout.parts_group)
                    
                    QtGui.QApplication.processEvents()
                
                # Sort by fitness (lower is better)
                layouts.sort(key=lambda l: l.fitness)
                
                current_best = layouts[0]
                if best_layout is None or current_best.fitness < best_layout.fitness:
                    best_layout = current_best
                    best_efficiency = current_best.efficiency
                    generations_without_improvement = 0
                    FreeCAD.Console.PrintMessage(f"\n>>> New Best: {best_efficiency:.1f}% efficiency <<<\n")
                    FreeCAD.Console.PrintMessage(f"    Best genes: {best_layout.genes[:5]}... ({len(best_layout.genes)} total)\n")
                    if hasattr(best_layout, 'contact_score'):
                        FreeCAD.Console.PrintMessage(f"    Contact score: {best_layout.contact_score:.1f}\n")
                else:
                    generations_without_improvement += 1
                    FreeCAD.Console.PrintMessage(f"\nNo improvement ({generations_without_improvement}/{early_stop_threshold})\n")
                
                # Early stopping
                if generations_without_improvement >= early_stop_threshold:
                    FreeCAD.Console.PrintMessage(f"Early stopping: no improvement for {early_stop_threshold} generations\n")
                    break
                
                # Hide winner (we'll show it at the end)
                if best_layout and best_layout.layout_group:
                    if hasattr(best_layout.layout_group, "ViewObject"):
                        best_layout.layout_group.ViewObject.Visibility = False
                
                # STEP 2: Delete all non-winner layouts from this generation
                FreeCAD.Console.PrintMessage(f"  Deleting {len(layouts) - 1} non-winning layouts...\n")
                for layout in layouts:
                    if layout != best_layout:
                        layout_manager.delete_layout(layout)
                
                # STEP 3: Create new layouts for next generation (if not last)
                if gen < generations - 1:
                    layouts = [best_layout]  # Start with the winner
                    
                    for i in range(population_size - 1):
                        new_layout = layout_manager.create_layout(
                            f"Layout_GA_{gen+2}_{i+1}",
                            master_map, quantities, ui_params
                        )
                        # Shuffle and mutate
                        if new_layout.parts:
                            import random
                            random.shuffle(new_layout.parts)
                            if rotation_steps > 1:
                                genetic_utils.mutate_chromosome(new_layout.parts, mutation_rate, rotation_steps)
                        layouts.append(new_layout)
                else:
                    # Last generation - just keep the winner
                    layouts = [best_layout]
            
            # STEP 4: Final result - winner becomes Layout_temp
            FreeCAD.Console.PrintMessage(f"\n=== Best Solution: {best_efficiency:.1f}% efficiency ===\n")
            if hasattr(best_layout, 'contact_score'):
                FreeCAD.Console.PrintMessage(f"    Contact score: {best_layout.contact_score:.1f}\n")
            
            # Show and rename best layout, set as current job's temp_layout
            if best_layout:
                # Make winner visible
                if best_layout.layout_group and hasattr(best_layout.layout_group, "ViewObject"):
                    best_layout.layout_group.ViewObject.Visibility = True
                
                best_layout.layout_group.Label = "Layout_temp"
                self.current_job = NestingJob.__new__(NestingJob)
                self.current_job.doc = self.doc
                self.current_job.target_layout = target_layout
                self.current_job.params = ui_params
                self.current_job.preparer = self.shape_preparer
                self.current_job.temp_layout = best_layout.layout_group
                self.current_job.parts_group = best_layout.parts_group
                self.current_job.sheets = best_layout.sheets
                
                msg = f"GA Complete: {best_efficiency:.1f}% efficiency, {len(best_layout.sheets)} sheets"
                self.ui.status_label.setText(msg)
                FreeCAD.Console.PrintMessage(f"{msg}\n--- NESTING DONE ---\n")
                if self.ui.sound_checkbox.isChecked(): QtGui.QApplication.beep()
            
            self.doc.recompute()
        except Exception as e:
            FreeCAD.Console.PrintError(f"GA Nesting Error: {e}\n")
            self.ui.status_label.setText(f"Error: {e}")
            # Cleanup all remaining layouts on error
            for layout in layouts:
                layout_manager.delete_layout(layout)
            self.doc.recompute()
    
    def finalize_job(self):
        """Called when User clicks OK."""
        if self.current_job:
            final_layout = self.current_job.commit()
            
            # Update UI reference so toggle_bounds works on the new layout
            self.ui.current_layout = final_layout
            
            # Ensure layout is visible and MasterShapes is hidden
            if final_layout and hasattr(final_layout, "ViewObject"):
                final_layout.ViewObject.Visibility = True
                
            if final_layout and hasattr(final_layout, "Group"):
                for child in final_layout.Group:
                    if child.Label.startswith("MasterShapes") and hasattr(child, "ViewObject"):
                        child.ViewObject.Visibility = False
                    elif child.Label.startswith("Sheet_") and hasattr(child, "ViewObject"):
                        child.ViewObject.Visibility = True
            
            self.current_job = None
            FreeCAD.Console.PrintMessage("Job Finalized & Committed.\n")
            self.doc.recompute()

    def cancel_job(self):
        """Called when User clicks Cancel."""
        if self.current_job:
            # Capture target and ensure it's not deleted during cleanup
            target = self.current_job.target_layout
            
            # Run cleanup
            self.current_job.cleanup()
            
            # Restore visibility of original target
            if target:
                try: 
                    # Force visibility on target
                    if hasattr(target, "ViewObject"):
                        target.ViewObject.Visibility = True
                    
                    if hasattr(target, "Group"):
                        for child in target.Group:
                            # Show Sheets
                            if child.Label.startswith("Sheet_") and hasattr(child, "ViewObject"):
                                child.ViewObject.Visibility = True
                            # Hide MasterShapes
                            if child.Label.startswith("MasterShapes") and hasattr(child, "ViewObject"):
                                child.ViewObject.Visibility = False
                except: pass
            
            self.current_job = None
            FreeCAD.Console.PrintMessage("Job Cancelled.\n")
            self.doc.recompute()
    
    def toggle_bounds_visibility(self):
        is_visible = self.ui.show_bounds_checkbox.isChecked()
        
        # If a job is active, use its temp_layout (where current results are)
        # Otherwise use the committed current_layout
        if self.current_job and self.current_job.temp_layout:
            target_layout = self.current_job.temp_layout
        else:
            target_layout = getattr(self.ui, 'current_layout', None)
        
        if not target_layout: 
            return
        
        found_count = 0
        
        # Recursively find and toggle bounds visibility
        def set_show_bounds(obj, depth=0):
            nonlocal found_count
            indent = "  " * depth
            
            # Check for boundary objects that are children (by label)
            if obj.Label.startswith("boundary_"):
                found_count += 1
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.Visibility = is_visible
                    
            # Check for linked BoundaryObject property
            if hasattr(obj, "BoundaryObject") and obj.BoundaryObject:
                found_count += 1
                if hasattr(obj.BoundaryObject, "ViewObject"):
                    obj.BoundaryObject.ViewObject.Visibility = is_visible
                
            # Recurse into children
            if hasattr(obj, "Group"):
                for child in obj.Group:
                    set_show_bounds(child, depth + 1)
                    
        set_show_bounds(target_layout)
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
                
                # Store quantity with effective rotation (based on override)
                quantities[label] = (qty, rot_val if override else global_rot)
                
                # Store rotation params (value AND override flag) for persistence
                rotation_params[label] = (rot_val, override)
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
