
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
from ...freecad_helpers import recursive_delete

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

    @classmethod
    def from_ga_result(cls, doc, target_layout, params, preparer, layout_group, parts_group, sheets):
        """Creates a NestingJob from a completed GA layout, bypassing sandbox creation."""
        job = cls.__new__(cls)
        job.doc = doc
        job.target_layout = target_layout
        job.params = params
        job.preparer = preparer
        job.temp_layout = layout_group
        job.parts_group = parts_group
        job.sheets = sheets
        return job

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
        
        # Warn about unplaced parts
        if unplaced:
            unplaced_ids = [p.id for p in unplaced]
            FreeCAD.Console.PrintWarning(f"WARNING: {len(unplaced)} part(s) could not be placed: {unplaced_ids}\n")
        
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
            recursive_delete(self.doc, child)
            
        # 2. Check for new MasterShapes in Temp
        temp_masters = next((c for c in self.temp_layout.Group if c.Label.startswith("MasterShapes")), None)
        
        if temp_masters and len(temp_masters.Group) > 0:
            # We have new masters, replace old ones in Target
            old_masters = next((c for c in self.target_layout.Group if c.Label.startswith("MasterShapes")), None)
            if old_masters:
                recursive_delete(self.doc, old_masters)
            
            # Sanitize labels before move
            temp_masters.Label = "MasterShapes"
            for m in temp_masters.Group:
                if m.Label.startswith("temp_master_"):
                    m.Label = m.Label.replace("temp_master_", "master_")
            
            self.target_layout.addObject(temp_masters)
            
        else:
            # No new masters, if temp has empty master group, delete it
            if temp_masters:
                recursive_delete(self.doc, temp_masters)

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
            recursive_delete(self.doc, self.temp_layout)
            self.temp_layout = None
            
        # Safety: Check for any floating PartsToPlace that might have escaped
        # (Though with proper containment this shouldn't happen)
        match_names = []
        for obj in self.doc.Objects:
            if obj.Label.startswith("PartsToPlace") or obj.Label.startswith("Layout_temp"):
                 match_names.append(obj.Name)
        
        for name in match_names:
            o = self.doc.getObject(name)
            if o: recursive_delete(self.doc, o)



    def _apply_placement(self, sheets, parts_to_nest):
        original_parts_map = {part.id: part for part in parts_to_nest}
        for sheet in sheets:
            for i, placed_part in enumerate(sheet.parts):
                 original_part = original_parts_map[placed_part.shape.id]
                 # Calculate placement relative to sheet origin
                 # Calculate placement relative to sheet origin
                 sheet_origin = sheet.get_origin()
                 original_part.placement = placed_part.shape.get_final_placement(sheet_origin)
                 sheet.parts[i].shape = original_part

    def _apply_properties(self, layout_obj):
        p = self.params
        self._set_prop(layout_obj, "App::PropertyLength", "SheetWidth", p['sheet_width'])
        self._set_prop(layout_obj, "App::PropertyLength", "SheetHeight", p['sheet_height'])
        self._set_prop(layout_obj, "App::PropertyLength", "PartSpacing", p['spacing'])
        self._set_prop(layout_obj, "App::PropertyLength", "SheetThickness", p['sheet_thickness'])
        self._set_prop(layout_obj, "App::PropertyFloat", "DeflectionAngle", p.get('deflection_angle', 30))  # Save angle in degrees
        self._set_prop(layout_obj, "App::PropertyFloat", "Simplification", p.get('simplification', 1.0))
        self._set_prop(layout_obj, "App::PropertyFile", "FontFile", p['font_path'])
        self._set_prop(layout_obj, "App::PropertyBool", "ShowBounds", p['show_bounds'])
        self._set_prop(layout_obj, "App::PropertyBool", "AddLabels", p['add_labels'])
        self._set_prop(layout_obj, "App::PropertyLength", "LabelHeight", p['label_height'])
        self._set_prop(layout_obj, "App::PropertyFloat", "LabelSize", p['label_size'])
        self._set_prop(layout_obj, "App::PropertyInteger", "GlobalRotationSteps", p['rotation_steps'])
        self._set_prop(layout_obj, "App::PropertyInteger", "Generations", p.get('generations', 1))
        self._set_prop(layout_obj, "App::PropertyInteger", "PopulationSize", p.get('population_size', 1))

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

        # Clear stale caches from previous nesting runs
        Shape.clear_caches()

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
        
        # 3. Execute nesting using unified GA path
        # (population=1, generations=1 is equivalent to standard nesting)
        self._execute_ga_nesting(target_layout, ui_params, quantities, master_map, 
                                 rotation_params, algo_kwargs, is_simulating)
    
    def load_selection(self):
        FreeCAD.Console.PrintMessage("Loading selection via Controller...\n")
        selection = FreeCADGui.Selection.getSelection()
        self.ui.shape_table.setRowCount(0)

        if not selection:
            FreeCAD.Console.PrintMessage("  -> No selection found.\n")
            self.ui.status_label.setText("Warning: No shapes selected.")
            self.ui.nest_button.setEnabled(False)
            return

        # Check if a layout group is selected
        first_selected = selection[0]
        if first_selected.isDerivedFrom("App::DocumentObjectGroup") and first_selected.Label.startswith("Layout_"):
            FreeCAD.Console.PrintMessage(f"  -> Detected layout selection: {first_selected.Label}\n")
            self.load_layout(first_selected)
        else:
            FreeCAD.Console.PrintMessage(f"  -> Detected {len(selection)} shapes.\n")
            self.load_shapes(selection)

    def load_layout(self, layout_group):
        """Loads the parameters and shapes from a layout group."""
        self.ui.current_layout = layout_group
        self.ui.nest_button.setEnabled(True)
        self.ui.selected_shapes_to_process = []
        self.ui.hidden_originals = []

        # Read parameters directly from the layout group's properties
        if hasattr(layout_group, 'SheetWidth'):
            self.ui.sheet_width_input.setValue(layout_group.SheetWidth)
        if hasattr(layout_group, 'SheetHeight'):
            self.ui.sheet_height_input.setValue(layout_group.SheetHeight)
        if hasattr(layout_group, 'PartSpacing'):
            self.ui.part_spacing_input.setValue(layout_group.PartSpacing)
        if hasattr(layout_group, 'SheetThickness'):
            self.ui.sheet_thickness_input.setValue(layout_group.SheetThickness)
        
        # Load deflection angle (new format) or convert old deflection mm to angle
        if hasattr(layout_group, 'DeflectionAngle'):
            self.ui.deflection_input.setValue(layout_group.DeflectionAngle)
        elif hasattr(layout_group, 'Deflection'):
            # Backward compatibility: convert old Deflection (mm) to angle
            deflection_angle = layout_group.Deflection * 200.0
            self.ui.deflection_input.setValue(deflection_angle)
            
        if hasattr(layout_group, 'Simplification'):
            self.ui.simplification_input.setValue(layout_group.Simplification)
        if hasattr(layout_group, 'FontFile') and os.path.exists(layout_group.FontFile):
            self.ui.selected_font_path = layout_group.FontFile
            self.ui.font_label.setText(os.path.basename(layout_group.FontFile))
        if hasattr(layout_group, 'LabelSize'):
            self.ui.label_size_input.setValue(layout_group.LabelSize)
        if hasattr(layout_group, 'Generations'):
            self.ui.minkowski_generations_input.setValue(layout_group.Generations)
        if hasattr(layout_group, 'PopulationSize'):
            self.ui.minkowski_population_size_input.setValue(layout_group.PopulationSize)

        # Get the shapes from the layout
        master_shapes_group = None
        for child in layout_group.Group:
            if child.Label.startswith("MasterShapes"):
                master_shapes_group = child
                break
        
        if master_shapes_group:
            # The master shapes are now copies inside this group.
            # We need to find the actual ShapeObject inside each 'master_' container,
            # as that is what the processing logic expects.
            shapes_to_load = []
            quantities = {}
            rotation_overrides = {}
            rotation_steps_map = {}
            up_directions = {}
            fill_sheet_map = {}
            
            for master_container in master_shapes_group.Group:
                # Use a relaxed check for the container to ensure robust loading.
                if hasattr(master_container, "Group"):
                    # The object to load is the 'master_shape_...' object inside the container.
                    shape_obj = next((child for child in master_container.Group if child.Label.startswith("master_shape_")), None)
                    if shape_obj and hasattr(shape_obj, "Shape"):
                        shapes_to_load.append(shape_obj)
                        
                        # Recover properties from container
                        quantities[shape_obj.Label] = getattr(master_container, "Quantity", 1)
                        
                        if hasattr(master_container, "PartRotationOverride"):
                            rotation_overrides[shape_obj.Label] = master_container.PartRotationOverride
                        if hasattr(master_container, "PartRotationSteps"):
                            rotation_steps_map[shape_obj.Label] = master_container.PartRotationSteps
                        if hasattr(master_container, "UpDirection"):
                            up_directions[shape_obj.Label] = master_container.UpDirection
                        if hasattr(master_container, "FillSheet"):
                            fill_sheet_map[shape_obj.Label] = master_container.FillSheet
            
            self.load_shapes(
                shapes_to_load, 
                is_reloading_layout=True, 
                initial_quantities=quantities,
                initial_overrides=rotation_overrides,
                initial_rotation_steps=rotation_steps_map,
                initial_up_directions=up_directions,
                initial_fill_sheet=fill_sheet_map
            )
            
            # Load Global Rotation Steps if present
            if hasattr(layout_group, "GlobalRotationSteps"):
                self.ui.rotation_steps_spinbox.setValue(layout_group.GlobalRotationSteps)
        else:
            FreeCAD.Console.PrintMessage(f"  WARNING: No MasterShapes group found!\n")
            self.ui.status_label.setText("Warning: Could not find 'MasterShapes' group in the selected layout.")

    def _extract_parts_from_selection(self, selection):
        """
        Extracts parts from Assembly containers only.
        Regular Part objects are used directly without extracting children.
        """
        parts = []
        
        def is_assembly(obj):
            """Check if object is an Assembly container (not a regular Part/Body)."""
            type_id = obj.TypeId if hasattr(obj, 'TypeId') else ''
            if 'Assembly' in type_id:
                return True
            if type_id == 'App::Part' and hasattr(obj, 'Group'):
                for child in obj.Group:
                    child_type = child.TypeId if hasattr(child, 'TypeId') else ''
                    if 'Link' in child_type or 'Assembly' in child_type:
                        return True
            return False
        
        def extract_from_assembly(obj):
            """Recursively extract nestable parts from an assembly."""
            if hasattr(obj, 'Group'):
                for child in obj.Group:
                    child_type = child.TypeId if hasattr(child, 'TypeId') else ''
                    if 'Constraint' in child_type or 'Origin' in child_type:
                        continue
                    if hasattr(child, 'LinkedObject') and child.LinkedObject:
                        linked = child.LinkedObject
                        if hasattr(linked, 'Shape') and linked.Shape and not linked.Shape.isNull():
                            parts.append(linked)
                    elif hasattr(child, 'Shape') and child.Shape and not child.Shape.isNull():
                        if is_assembly(child):
                            extract_from_assembly(child)
                        else:
                            parts.append(child)
        
        for obj in selection:
            if is_assembly(obj):
                extract_from_assembly(obj)
            else:
                parts.append(obj)
        
        return parts

    def load_shapes(self, selection, is_reloading_layout=False, initial_quantities=None, 
                     initial_overrides=None, initial_rotation_steps=None,
                     initial_up_directions=None, initial_fill_sheet=None):
        """Loads a selection of shapes into the UI."""
        self.ui.nest_button.setEnabled(True)
        
        # Extract individual parts from assemblies/groups
        if not is_reloading_layout:
            extracted = self._extract_parts_from_selection(selection)
            if extracted:
                selection = extracted
                FreeCAD.Console.PrintMessage(f"  -> Extracted {len(selection)} parts from selection.\n")
        
        # Keep unique, preserve order
        self.ui.selected_shapes_to_process = list(dict.fromkeys(selection)) 
        
        if not is_reloading_layout:
            self.ui.current_layout = None
            self.ui.hidden_originals = list(self.ui.selected_shapes_to_process)
        
        self.ui.shape_table.setRowCount(len(self.ui.selected_shapes_to_process))
        for i, obj in enumerate(self.ui.selected_shapes_to_process):
            # Clean up label if it's a master shape
            display_label = obj.Label
            if display_label.startswith("master_shape_"):
                display_label = display_label.replace("master_shape_", "")
            
            qty = 1
            if initial_quantities and obj.Label in initial_quantities:
                qty = initial_quantities[obj.Label]
                
            steps = 4
            override = False
            if initial_rotation_steps and obj.Label in initial_rotation_steps:
                steps = initial_rotation_steps[obj.Label]
            if initial_overrides and obj.Label in initial_overrides:
                override = initial_overrides[obj.Label]
            
            up_dir = "Z+"
            if initial_up_directions and obj.Label in initial_up_directions:
                up_dir = initial_up_directions[obj.Label]
                
            fill = False
            if initial_fill_sheet and obj.Label in initial_fill_sheet:
                fill = initial_fill_sheet[obj.Label]

            # We assume _add_part_row is still on UI or moved to a public method on UI
            # Plan says it stays on UI but exposed. Let's assume it's publicly accessible as add_part_row now.
            if hasattr(self.ui, 'add_part_row'):
                 self.ui.add_part_row(i, display_label, quantity=qty, rotation_steps=steps, 
                                      override_rotation=override, up_direction=up_dir, fill_sheet=fill)
            elif hasattr(self.ui, '_add_part_row'):
                 # Fallback if I haven't renamed it yet (I should rename it in next step)
                 self.ui._add_part_row(i, display_label, quantity=qty, rotation_steps=steps, 
                                      override_rotation=override, up_direction=up_dir, fill_sheet=fill)
        
        self.ui.shape_table.resizeColumnsToContents()
        self.ui.status_label.setText(f"{len(selection)} unique object(s) selected. Specify quantities and nest.")

    def add_selected_shapes(self):
        """Adds the currently selected FreeCAD objects to the shape table."""
        selection = FreeCADGui.Selection.getSelection()
        if not selection:
            self.ui.status_label.setText("Select shapes in the 3D view or tree to add them.")
            return

        existing_labels = [self.ui.shape_table.item(row, 0).text() for row in range(self.ui.shape_table.rowCount())]
        
        added_count = 0
        for obj in selection:
            if obj.Label not in existing_labels:
                row_position = self.ui.shape_table.rowCount()
                self.ui.shape_table.insertRow(row_position)
                
                if hasattr(self.ui, 'add_part_row'):
                    self.ui.add_part_row(row_position, obj.Label)
                elif hasattr(self.ui, '_add_part_row'):
                    self.ui._add_part_row(row_position, obj.Label)
                    
                self.ui.selected_shapes_to_process.append(obj)
                added_count += 1
        
        self.ui.shape_table.resizeColumnsToContents()
        self.ui.status_label.setText(f"Added {added_count} new shape(s).")

        # Enable the nest button if any shapes are now in the table
        if self.ui.shape_table.rowCount() > 0:
            self.ui.nest_button.setEnabled(True)

    def remove_selected_shapes(self):
        """Removes the selected rows from the shape table."""
        selected_items = self.ui.shape_table.selectedItems()
        selected_rows = sorted(list(set(item.row() for item in selected_items)), reverse=True)
        for row in selected_rows:
            label_to_remove = self.ui.shape_table.item(row, 0).text()
            self.ui.selected_shapes_to_process = [obj for obj in self.ui.selected_shapes_to_process if obj.Label != label_to_remove]
            self.ui.shape_table.removeRow(row)
        self.ui.status_label.setText(f"Removed {len(selected_rows)} shape(s).")

        # Disable the nest button if the table is now empty
        if self.ui.shape_table.rowCount() == 0:
            self.ui.nest_button.setEnabled(False)

    

    
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
        total_nesting_time = 0
        
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
                    
                    # Run nesting
                    sheets, unplaced, _, elapsed = nest(
                        layout.parts,
                        ui_params['sheet_width'],
                        ui_params['sheet_height'],
                        rotation_steps,
                        is_simulating,
                        **algo_kwargs
                    )

                    # FIX: If not simulating, we need to manually apply the placement
                    # from the nested copies back to the original layout.parts
                    # because GA nesting bypasses NestingJob.run
                    if not is_simulating:
                         original_parts_map = {p.id: p for p in layout.parts}
                         for s in sheets:
                             for i, placed_part in enumerate(s.parts):
                                  original_part = original_parts_map[placed_part.shape.id]
                                  original_part.placement = placed_part.shape.get_final_placement(s.get_origin())
                                  s.parts[i].shape = original_part
                    total_nesting_time += elapsed
                    
                    layout.sheets = sheets
                    layout.unplaced = unplaced  # Track unplaced parts
                    
                    # Calculate efficiency
                    fitness, efficiency = layout_manager.calculate_efficiency(
                        layout, ui_params['sheet_width'], ui_params['sheet_height']
                    )
                    
                    # Penalize unplaced parts
                    if unplaced:
                        layout.fitness += len(unplaced) * ui_params['sheet_width'] * ui_params['sheet_height'] * 10
                        unplaced_ids = [p.id for p in unplaced]
                        FreeCAD.Console.PrintWarning(f"    -> WARNING: {len(unplaced)} part(s) could not be placed: {unplaced_ids}\n")
                    
                    FreeCAD.Console.PrintMessage(f"    -> Efficiency: {efficiency:.1f}%\n")
                    
                    # Draw the layout (no offset - we'll delete non-winners)
                    for sheet in sheets:
                        sheet.draw(self.doc, ui_params, layout.layout_group, 
                                   parts_to_place_group=layout.parts_group)
                    
                    # Hide completed layout to reduce visual clutter (when population > 1)
                    if population_size > 1 and layout.layout_group and hasattr(layout.layout_group, "ViewObject"):
                        layout.layout_group.ViewObject.Visibility = False
                    
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
                    
                    import random
                    for i in range(population_size - 1):
                        new_layout = layout_manager.create_layout(
                            f"Layout_GA_{gen+2}_{i+1}",
                            master_map, quantities, ui_params
                        )
                        # Shuffle and mutate
                        if new_layout.parts:
                            random.shuffle(new_layout.parts)
                            if rotation_steps > 1:
                                genetic_utils.mutate_chromosome(new_layout.parts, mutation_rate, rotation_steps)
                        layouts.append(new_layout)
                else:
                    # Last generation - just keep the winner
                    layouts = [best_layout]
            
            # STEP 4: Final result - winner becomes Layout_temp
            FreeCAD.Console.PrintMessage(f"\n=== Best Solution: {best_efficiency:.1f}% efficiency ===\n")
            
            # Show and rename best layout, set as current job's temp_layout
            if best_layout:
                # Make winner visible
                if best_layout.layout_group and hasattr(best_layout.layout_group, "ViewObject"):
                    best_layout.layout_group.ViewObject.Visibility = True
                
                # Hide MasterShapes group to keep view clean
                if best_layout.layout_group and hasattr(best_layout.layout_group, "Group"):
                    for child in best_layout.layout_group.Group:
                        if child.Label.startswith("MasterShapes") and hasattr(child, "ViewObject"):
                            child.ViewObject.Visibility = False
                
                best_layout.layout_group.Label = "Layout_temp"
                self.current_job = NestingJob.from_ga_result(
                    doc=self.doc,
                    target_layout=target_layout,
                    params=ui_params,
                    preparer=self.shape_preparer,
                    layout_group=best_layout.layout_group,
                    parts_group=best_layout.parts_group,
                    sheets=best_layout.sheets
                )
                
                # Print final summary at the very end of the report
                c_score = f", Contact: {best_layout.contact_score:.1f}" if hasattr(best_layout, 'contact_score') else ""
                unplaced_count = len(getattr(best_layout, 'unplaced', []) or [])
                placed_count = sum(len(s) for s in best_layout.sheets)
                unplaced_msg = f", {unplaced_count} UNPLACED" if unplaced_count > 0 else ""
                msg = f"GA Complete: {best_efficiency:.1f}% efficiency, {len(best_layout.sheets)} sheets, {placed_count} placed{unplaced_msg}{c_score}, Time: {total_nesting_time:.2f}s"
                self.ui.status_label.setText(msg)
                FreeCAD.Console.PrintMessage(f"{msg}\n")
                if unplaced_count > 0:
                    unplaced_ids = [p.id for p in best_layout.unplaced]
                    FreeCAD.Console.PrintWarning(f"WARNING: {unplaced_count} part(s) could not be placed: {unplaced_ids}\n")
                FreeCAD.Console.PrintMessage(f"--- NESTING DONE ---\n")
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
                    # Check if target layout is empty (newly created, never committed to)
                    has_content = any(
                        child.Label.startswith("Sheet_") or child.Label.startswith("MasterShapes")
                        for child in (target.Group if hasattr(target, "Group") else [])
                    )
                    
                    if not has_content:
                        # Empty layout - was created by _ensure_target_layout but never used
                        recursive_delete(self.doc, target)
                        if hasattr(self.ui, 'current_layout') and self.ui.current_layout == target:
                            self.ui.current_layout = None
                        FreeCAD.Console.PrintMessage("Removed empty target layout.\n")
                    else:
                        # Has content - restore visibility
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
                except Exception: pass
            
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
            except Exception: target = None
            
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
        # Convert deflection angle (degrees) to linear deflection (mm)
        # Formula: deflection_mm = angle / 200.0
        # This gives: 10° → 0.05mm, 20° → 0.1mm, 40° → 0.2mm
        deflection_angle = self.ui.deflection_input.value()
        deflection_mm = deflection_angle / 200.0
        
        settings_dict = {
            'sheet_width': self.ui.sheet_width_input.value(),
            'sheet_height': self.ui.sheet_height_input.value(),
            'spacing': self.ui.part_spacing_input.value(),
            'sheet_thickness': self.ui.sheet_thickness_input.value(),
            'deflection': deflection_mm,  # Linear deflection for processing
            'deflection_angle': deflection_angle,  # Angle for persistence
            'simplification': self.ui.simplification_input.value(),
            'rotation_steps': self.ui.rotation_steps_spinbox.value(),
            'add_labels': self.ui.add_labels_checkbox.isChecked(),
            'font_path': getattr(self.ui, 'selected_font_path', None),
            'show_bounds': self.ui.show_bounds_checkbox.isChecked(),
            'label_height': self.ui.label_height_input.value(),
            'label_size': self.ui.label_size_input.value(),
            'generations': self.ui.minkowski_generations_input.value(),
            'population_size': self.ui.minkowski_population_size_input.value()
        }
        
        # Save persistence
        self.save_settings(settings_dict)
        
        return settings_dict

    def save_settings(self, settings):
        """Saves current UI settings to FreeCAD preferences."""
        prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/NestingWorkbench")
        prefs.SetFloat("SheetWidth", float(settings['sheet_width']))
        prefs.SetFloat("SheetHeight", float(settings['sheet_height']))
        prefs.SetFloat("PartSpacing", float(settings['spacing']))
        prefs.SetFloat("SheetThickness", float(settings['sheet_thickness']))
        prefs.SetFloat("DeflectionAngle", float(settings.get('deflection_angle', 10)))  # Save angle, not mm
        prefs.SetFloat("Simplification", float(settings['simplification']))
        prefs.SetInt("RotationSteps", int(settings['rotation_steps']))
        prefs.SetBool("AddLabels", bool(settings['add_labels']))
        prefs.SetBool("ShowBounds", bool(settings['show_bounds']))
        prefs.SetFloat("LabelHeight", float(settings['label_height']))
        prefs.SetFloat("LabelSize", float(settings['label_size']))
        if settings['font_path']:
             prefs.SetString("FontPath", str(settings['font_path']))

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
                
                # Get new parameters
                up_dir_combo = self.ui.shape_table.cellWidget(row, 4)
                up_direction = up_dir_combo.currentText() if up_dir_combo else "Z+"
                
                fill_checkbox = self.ui.shape_table.cellWidget(row, 5)
                fill_sheet = fill_checkbox.isChecked() if fill_checkbox else False
                
                # Store quantity with effective rotation (based on override) and new params
                quantities[label] = {
                    'quantity': qty,
                    'rotation_steps': rot_val if override else global_rot,
                    'up_direction': up_direction,
                    'fill_sheet': fill_sheet
                }
                
                # Store rotation params (value AND override flag) for persistence
                rotation_params[label] = (rot_val, override)
            except Exception: continue
            
        # Map objects
        for obj in self.ui.selected_shapes_to_process:
             try:
                 lbl = obj.Label.replace("master_shape_", "")
                 if lbl in quantities:
                     master_map[obj.Label] = obj
             except Exception: pass
             
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
        algo_kwargs['clear_nfp_cache'] = self.ui.clear_cache_checkbox.isChecked()
        
        if hasattr(self.ui, 'log_message'):
            algo_kwargs['log_callback'] = self.ui.log_message
            
        return algo_kwargs
