# Nesting/nesting/nesting_controller.py

"""
This module contains the NestingController, which is the "brain" of the 
nesting operation. It reads the UI, runs the algorithm, and draws the result.
"""

import FreeCAD
import FreeCADGui
import Part
import copy
import math
import os
import time

# Import QtGui for UI event processing
from PySide import QtGui

# Import other necessary modules from the workbench
from ...datatypes.shape import Shape
from .shape_preparer import ShapePreparer

try:
    from .nesting_logic import nest, NestingDependencyError
    import Draft
except ImportError:
    Draft = None

try:
    from shapely.affinity import translate
except ImportError:
    translate = None


class NestingController:
    """
    Handles the core logic of preparing shapes, running the nesting
    algorithm, and drawing the final layout in the document.
    """
    def __init__(self, ui_panel):
        self.ui = ui_panel
        self.doc = FreeCAD.ActiveDocument
        self.last_run_sheets = [] # Store the result of the last nesting run
        self.last_run_ui_params = {} # Store the UI params from the last run
        self.last_run_unplaced_parts = [] # Store unplaced parts from the last run
        
        # Directly set the default font path. The UI can override this if the user selects a different font.
        font_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'fonts'))
        default_font = os.path.join(font_dir, 'PoiretOne-Regular.ttf')
        self.ui.selected_font_path = default_font
        
        # Also update the UI label to show the default font is selected.
        if hasattr(self.ui, 'font_label'):
            self.ui.font_label.setText(os.path.basename(default_font))

        # Persistent cache for processed shape geometry across runs.
        # Persistent cache for processed shape geometry across runs.
        # Format: { (object_name, spacing, boundary_resolution) : ShapeObject }
        self.processed_shape_cache = {}
        self.shape_preparer = ShapePreparer(self.doc, self.processed_shape_cache)

        self.target_layout = None
        self.temp_layout = None

    def execute_nesting(self):
        """Main method to run the entire nesting process."""
        FreeCAD.Console.PrintMessage("\n--- NESTING START ---\n")
        
        # Cleanup any previous runs first
        self._cleanup_temp_layout()

        # Conditionally clear the NFP cache
        if hasattr(self.ui, 'clear_cache_checkbox') and self.ui.clear_cache_checkbox.isChecked():
            FreeCAD.Console.PrintMessage("Clearing NFP and Geometry caches...\n")
            Shape.nfp_cache.clear()
            self.processed_shape_cache.clear()
        
        start_time = time.time()
        if not self.doc:
            return

        # Hide previous layouts
        for obj in self.doc.Objects:
            if (obj.Name.startswith("Layout_") or 
                obj.Name in ["PartsToPlace", "MasterShapes"]):
                if hasattr(obj, "ViewObject") and obj.ViewObject.Visibility:
                    obj.ViewObject.Visibility = False

        # Check if a font is needed and has been selected
        font_path = getattr(self.ui, 'selected_font_path', None)
        if self.ui.add_labels_checkbox.isChecked() and not font_path:
            self.ui.status_label.setText("Error: Could not find a valid font file for labels.")
            return
        
        self.ui.status_label.setText("Preparing shapes...")
        QtGui.QApplication.processEvents()

        FreeCAD.Console.PrintMessage(f"   [TIMING] Prep & UI: {time.time() - start_time:.4f}s\n")
        step_start_time = time.time()
        
        FreeCAD.Console.PrintMessage("1. Creating/Updating LayoutObject...\n")
        
        # --- Identify Target Layout (Existing) ---
        target_layout = getattr(self.ui, 'current_layout', None)
        
        # Validate target_layout reference
        if target_layout:
            try:
                # Check if object is valid by accessing a property
                _ = target_layout.Name
                # Also check if it exists in document (ReferenceError usually covers C++ deletion)
                if target_layout not in self.doc.Objects:
                    raise ReferenceError("Object not in document")
            except (ReferenceError, AttributeError, ValueError):
                FreeCAD.Console.PrintWarning("Tracked layout object is invalid or deleted. Treating as New Layout.\n")
                target_layout = None
                self.ui.current_layout = None

        # Try to infer layout if selecting master shapes
        if not target_layout and self.ui.selected_shapes_to_process and self.ui.selected_shapes_to_process[0].Label.startswith("master_shape_"):
            try:
                first_shape = self.ui.selected_shapes_to_process[0]
                if first_shape.InList:
                    master_container = first_shape.InList[0]
                    if master_container.InList:
                        master_group = master_container.InList[0]
                        if master_group.InList and master_group.Label == "MasterShapes":
                             layout_candidate = master_group.InList[0]
                             if layout_candidate.Label.startswith("Layout_"):
                                 target_layout = layout_candidate
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"   -> Could not determine parent layout: {e}\n")

        self.target_layout = target_layout
        if self.target_layout:
             # If we are updating an existing layout, hide it during the operation
             try:
                 if hasattr(self.target_layout, "ViewObject"): 
                     self.target_layout.ViewObject.Visibility = False
             except ReferenceError:
                 self.target_layout = None # Abort update if it died right now
                 self.ui.current_layout = None

        # --- Create Temporary Working Layout ---
        # We ALWAYS work on a temp object to support atomic Cancel/Undo
        self.temp_layout = self.doc.addObject("App::DocumentObjectGroup", "Layout_temp")
        layout_obj = self.temp_layout # Use this as the working object
        
        # --- Store UI Parameters on the Temp Layout Object ---
        def set_or_add_property(obj, prop_type, prop_name, prop_group, prop_desc, value):
            if not hasattr(obj, prop_name):
                 obj.addProperty(prop_type, prop_name, prop_group, prop_desc)
            setattr(obj, prop_name, value)

        set_or_add_property(layout_obj, "App::PropertyLength", "SheetWidth", "Layout", "Width of the nested sheets", self.ui.sheet_width_input.value())
        set_or_add_property(layout_obj, "App::PropertyLength", "SheetHeight", "Layout", "Height of the nested sheets", self.ui.sheet_height_input.value())
        set_or_add_property(layout_obj, "App::PropertyLength", "PartSpacing", "Layout", "Spacing between parts", self.ui.part_spacing_input.value())
        set_or_add_property(layout_obj, "App::PropertyFloat", "BoundaryResolution", "Layout", "Resolution for boundary approximation", self.ui.boundary_resolution_input.value())
        set_or_add_property(layout_obj, "App::PropertyFile", "FontFile", "Layout", "Font file used for labels", self.ui.selected_font_path)
        set_or_add_property(layout_obj, "App::PropertyBool", "ShowBounds", "Layout", "Visibility of part boundaries", self.ui.show_bounds_checkbox.isChecked())
        set_or_add_property(layout_obj, "App::PropertyBool", "AddLabels", "Layout", "Whether part labels are enabled", self.ui.add_labels_checkbox.isChecked())
        set_or_add_property(layout_obj, "App::PropertyLength", "LabelHeight", "Layout", "Height of the part labels", self.ui.label_height_input.value())
        set_or_add_property(layout_obj, "App::PropertyInteger", "GlobalRotationSteps", "Layout", "Global rotation steps for nesting", self.ui.rotation_steps_spinbox.value())
        set_or_add_property(layout_obj, "App::PropertyBool", "IsStacked", "Layout", "Whether sheets are stacked", False)

        QtGui.QApplication.processEvents()
        if hasattr(layout_obj, "ViewObject"): layout_obj.ViewObject.Visibility = True

        # --- Create a temporary, visible group for the parts to be placed ---
        # This must be created BEFORE calling _prepare_parts_from_ui.
        parts_to_place_group = self.doc.addObject("App::DocumentObjectGroup", "PartsToPlace")
        layout_obj.addObject(parts_to_place_group)

        # --- Prepare Parts and Master Shapes ---
        # Gather UI parameters
        ui_settings, quantities, master_map, rotation_params = self._collect_job_parameters()
        
        # Add layout-specific settings that might be needed by the preparer (like spacing/res which are in ui_settings)
        # But we need to make sure _collect_job_parameters returns what we need.
        # Actually, let's just pass what we have.
        
        parts_to_nest = self.shape_preparer.prepare_parts(
            ui_settings,
            quantities,
            master_map,
            layout_obj
        )
        
        FreeCAD.Console.PrintMessage(f"   [TIMING] Shape Processing: {time.time() - step_start_time:.4f}s\n")
        step_start_time = time.time()

        if not parts_to_nest:
            msg = "Error: No valid parts to nest."
            if self.ui.selected_shapes_to_process:
                 msg += " Source shapes might be deleted."
            self.ui.status_label.setText(msg)
            self._cleanup_temp_layout() 
            return
        
        self.ui.status_label.setText("Running nesting algorithm...")
        spacing = self.ui.part_spacing_input.value()
        algo_kwargs = {}
        if self.ui.minkowski_random_checkbox.isChecked():
            algo_kwargs['search_direction'] = None
        else:
            # Same coordinate logic as gravity: 0=Down, 90=Right, etc.
            angle_deg = (270 - self.ui.minkowski_direction_dial.value()) % 360
            angle_rad = math.radians(angle_deg)
            algo_kwargs['search_direction'] = (math.cos(angle_rad), math.sin(angle_rad))
        
        algo_kwargs['population_size'] = self.ui.minkowski_population_size_input.value()
        algo_kwargs['generations'] = self.ui.minkowski_generations_input.value()

        # --- Prepare UI parameters for controllers ---
        global_rotation_steps = self.ui.rotation_steps_spinbox.value() # This widget is in NestingPanel
        self.last_run_ui_params = {
            'spacing': spacing,
            'sheet_w': self.ui.sheet_width_input.value(),
            'sheet_h': self.ui.sheet_height_input.value(),
            'spacing': spacing,
            'font_path': self.ui.selected_font_path,
            'show_bounds': self.ui.show_bounds_checkbox.isChecked(),
            'add_labels': self.ui.add_labels_checkbox.isChecked(),
            'label_height': self.ui.label_height_input.value(), # This widget is in NestingPanel
        }

        # Add spacing to algo_kwargs so the nester can use it for sheet origin calculations
        algo_kwargs['spacing'] = spacing

        if hasattr(self.ui, 'log_message'):
            algo_kwargs['log_callback'] = self.ui.log_message

        # --- Persist Rotation State ---
        # We need to save the rotation overrides to the Master Containers so they can be reloaded.
        master_shapes_group = layout_obj.getObject("MasterShapes")
        if master_shapes_group:
            for container in master_shapes_group.Group:
                # container is 'master_O_1' etc.
                # Find the inner shape to identify it
                inner_shape = next((c for c in container.Group if c.Label.startswith("master_shape_")), None)
                if inner_shape:
                    original_label = inner_shape.Label.replace("master_shape_", "")
                    
                    # Look up params
                    if original_label in rotation_params:
                        r_steps, r_override = rotation_params[original_label]
                        set_or_add_property(container, "App::PropertyInteger", "PartRotationSteps", "Nesting", "Rotation steps", r_steps)
                        set_or_add_property(container, "App::PropertyBool", "PartRotationOverride", "Nesting", "Override global rotation", r_override)

        try:
            is_simulating = self.ui.simulate_nesting_checkbox.isChecked()
            sheets, remaining_parts_to_nest, total_steps = nest(
                parts_to_nest,
                self.ui.sheet_width_input.value(), self.ui.sheet_height_input.value(),
                global_rotation_steps, is_simulating,
                **algo_kwargs
            )
            FreeCAD.Console.PrintMessage(f"   [TIMING] Nesting Algorithm: {time.time() - step_start_time:.4f}s\n")
            step_start_time = time.time()

            # If not simulating, the `sheets` object contains deep copies of the parts.
            # We must replace these copies with the original parts that are linked to the
            # FreeCAD objects, and transfer the final placement data.
            if not is_simulating:
                original_parts_map = {part.id: part for part in parts_to_nest}
                for sheet in sheets:
                    for i, placed_part in enumerate(sheet.parts):
                        sheet_origin = sheet.get_origin() # Get the origin for the current sheet
                        original_part = original_parts_map[placed_part.shape.id]
                        original_part.placement = placed_part.shape.get_final_placement(sheet_origin)
                        sheet.parts[i].shape = original_part # Replace the copied shape with the original
        except NestingDependencyError as e:
            self.ui.status_label.setText(f"Error: {e}")
            # The dialog is already shown by nesting_logic, so we just stop.
            return

        # Store the results for later use (e.g., by the bounds toggle)
        self.last_run_sheets = sheets
        self.last_run_unplaced_parts = remaining_parts_to_nest

        # --- Draw the final layout directly, without recompute ---
        # The LayoutController's execute method is no longer used.
        # We perform all drawing actions imperatively here.
        for sheet in sheets:
            sheet.draw(
                self.doc,
                # sheet_origin is now calculated inside draw()
                self.last_run_ui_params,
                layout_obj # The parent group is the layout object itself
            )
            
        FreeCAD.Console.PrintMessage(f"   [TIMING] Drawing Sheets: {time.time() - step_start_time:.4f}s\n")
        
        # --- Cleanup Excess Sheets ---
        # If the previous run had more sheets (e.g. 5) than this run (e.g. 3),
        # we need to remove Sheet_4 and Sheet_5.
        num_sheets = len(sheets)
        for child in list(layout_obj.Group):
            if child.Label.startswith("Sheet_"):
                try:
                    # Extract ID from "Sheet_X"
                    sheet_id = int(child.Label.split('_')[1])
                    if sheet_id > num_sheets:
                        FreeCAD.Console.PrintMessage(f"Cleaning up unused sheet: {child.Label}\n")
                        self.doc.removeObject(child.Name)
                except (ValueError, IndexError):
                    pass # Ignore if label format doesn't match
        
        # Now that the recompute is finished and parts are moved, it is safe to remove the empty group.
        # Check if it still exists (it might have been removed via layout cleanup already if Logic reuse/naming was mismatched)
        parts_group = self.doc.getObject("PartsToPlace")
        if parts_group:
             self.doc.removeObject(parts_group.Name)
        
        placed_count = sum(len(s) for s in sheets)

        status_text = f"Placed {placed_count} shapes on {len(sheets)} sheets."

        if remaining_parts_to_nest:
            status_text += f" Could not place {len(remaining_parts_to_nest)} shapes."
        
        # Calculate fill percentage for the status message
        sheet_fills = [s.calculate_fill_percentage() for s in sheets]
        
        end_time = time.time()
        duration = end_time - start_time
        status_text += f" (Took {duration:.2f} seconds)."
        self.ui.status_label.setText(status_text)

        if self.ui.sound_checkbox.isChecked(): QtGui.QApplication.beep()
        self._debug_dump_doc()

    def toggle_bounds_visibility(self):
        """Toggles the 'ShowBounds' property on all nested ShapeObjects in the document."""
        is_visible = self.ui.show_bounds_checkbox.isChecked()
        
        # Find all ShapeObjects in the document and toggle their property.
        # The ShapeObject's onChanged method will handle the visibility change.
        toggled_count = 0
        for obj in self.doc.Objects:
            # Only toggle bounds for the final nested parts, which are prefixed with "nested_".
            # The "nested_" object is an App::Part container. We need to find the
            # ShapeObject inside it to toggle the property, as the onChanged logic
            # is on the ShapeObject's proxy.
            if obj.Label.startswith("nested_") and obj.isDerivedFrom("App::Part"):
                # Find the ShapeObject within the container group
                shape_child = next((child for child in obj.Group if hasattr(child, "Proxy") and child.Proxy.__class__.__name__ == "ShapeObject"), None)
                if shape_child and hasattr(shape_child, "ShowBounds"):
                    shape_child.ShowBounds = is_visible
                    toggled_count += 1
        
        if toggled_count > 0:
            self.ui.status_label.setText(f"Toggled bounds visibility for {toggled_count} shapes.")

    def _collect_job_parameters(self):
        """Reads the UI table and returns job configuration."""
        ui_settings = {
            'spacing': self.ui.part_spacing_input.value(),
            'boundary_resolution': self.ui.boundary_resolution_input.value(),
            'rotation_steps': self.ui.rotation_steps_spinbox.value(),
            'add_labels': self.ui.add_labels_checkbox.isChecked(),
            'font_path': getattr(self.ui, 'selected_font_path', None)
        }
        
        global_rotation_steps = ui_settings['rotation_steps']
        
        # 1. Collect raw data from UI Table (Key = Display Label)
        ui_row_data = {}
        rotation_params = {} # Key = Display Label
        
        for row in range(self.ui.shape_table.rowCount()):
            try:
                display_label = self.ui.shape_table.item(row, 0).text()
                quantity = self.ui.shape_table.cellWidget(row, 1).value()
                
                rot_widget = self.ui.shape_table.cellWidget(row, 2)
                rotation_value = rot_widget.findChild(QtGui.QSpinBox).value()
                override_enabled = self.ui.shape_table.cellWidget(row, 3).isChecked()
                
                part_rotation_steps = rotation_value if override_enabled else global_rotation_steps
                
                ui_row_data[display_label] = (quantity, part_rotation_steps)
                rotation_params[display_label] = (rotation_value, override_enabled)
            except (ValueError, AttributeError):
                continue

        # 2. Map Objects to collected Data
        quantities = {}
        master_shapes_from_ui = {}
        
        # Check reload state
        is_reloading = False
        if self.ui.selected_shapes_to_process and self.ui.selected_shapes_to_process[0].Label.startswith("master_shape_"):
            is_reloading = True
            
        for obj in self.ui.selected_shapes_to_process:
            try:
                # Validation
                _ = obj.Name 
                if obj not in self.doc.Objects: continue
                
                # Resolve key
                display_label = obj.Label
                if display_label.startswith("master_shape_"):
                    display_label = display_label.replace("master_shape_", "")
                
                if display_label in ui_row_data:
                    # Filter logic: if reloading, only accept actual master shapes?
                    if not is_reloading or obj.Label.startswith("master_shape_"):
                         quantities[display_label] = ui_row_data[display_label]
                         master_shapes_from_ui[obj.Label] = obj
                         
            except (ReferenceError, AttributeError):
                continue

        return ui_settings, quantities, master_shapes_from_ui, rotation_params

    def _cleanup_temp_layout(self):
        """Helper to recursively delete the temporary layout group."""
        if not self.doc: return
        
        # 1. Delete explicitly tracked temp layout
        if self.temp_layout:
             # Standard recursive delete
             def delete_recursive(obj):
                 if hasattr(obj, "Group"):
                     # Iterate over copy because list might change? Actually Group is property.
                     # But safer to cast to list.
                     children = list(obj.Group) 
                     for child in children:
                         delete_recursive(child)
                 try:
                     self.doc.removeObject(obj.Name)
                 except: pass

             try:
                 delete_recursive(self.temp_layout)
             except (ReferenceError, AttributeError):
                 pass
             self.temp_layout = None
        
        # 2. Safety net: Search for ANY object starting with "Layout_temp"
        # This catches stragglers if the controller was re-instantiated or state was lost.
        objects_to_delete = []
        for obj in self.doc.Objects:
            if obj.Label.startswith("Layout_temp"):
                 objects_to_delete.append(obj)
        
        for obj in objects_to_delete:
            # Recursive delete might be needed if they are groups and we missed them
             if hasattr(obj, "Group"):
                 for child in list(obj.Group):
                     try: self.doc.removeObject(child.Name)
                     except: pass
             try: self.doc.removeObject(obj.Name)
             except: pass

    def cancel_job(self):
        """Called by UI when Cancel is clicked."""
        FreeCAD.Console.PrintMessage("Job Cancelled by User.\n")
        self._cleanup_temp_layout()
        
        # Restore target visibility
        if self.target_layout:
             if hasattr(self.target_layout, "ViewObject"):
                 self.target_layout.ViewObject.Visibility = True
                 
        self.target_layout = None
        
        # Cleanup any unplaced parts visual artifacts
        # (Already handled by cleanup_temp mostly, but PartsToPlace is inside temp)

    def _debug_dump_doc(self):
        msg = "\n--- DEBUG DOCUMENT STRUCTURE ---\n"
        for obj in self.doc.Objects:
            if obj.isDerivedFrom("App::Origin") or obj.isDerivedFrom("App::Line") or obj.isDerivedFrom("App::Plane") or obj.isDerivedFrom("App::Point"):
                 continue
            msg += f"Object: {obj.Name} ({obj.Label}) Type: {obj.TypeId}\n"
            if hasattr(obj, "Group"):
                 msg += f"  Group children: {[c.Name for c in obj.Group]}\n"
        msg += "--------------------------------\n"
        FreeCAD.Console.PrintMessage(msg)

    def finalize_job(self):
        """Called by UI when OK is clicked."""
        self._debug_dump_doc()
        FreeCAD.Console.PrintMessage("DEBUG: Finalizing Job...\n")
        if not self.temp_layout: return

        if self.target_layout:
            FreeCAD.Console.PrintMessage(f"DEBUG: Updating Target Layout {self.target_layout.Label}\n")
            
            # Check if we have new masters in temp
            temp_masters_group = None
            current_temp_group = list(self.temp_layout.Group)
            for child in current_temp_group:
                if child.Label.startswith("MasterShapes"):
                    temp_masters_group = child
                    break
            
            has_new_masters = False
            if temp_masters_group and len(temp_masters_group.Group) > 0:
                has_new_masters = True
            
            # 1. Identify children to remove vs keep
            children_to_remove = []
            children_to_keep = []
            
            for child in self.target_layout.Group:
                if child.Label.startswith("Sheet_") or child.Label == "PartsToPlace":
                    children_to_remove.append(child)
                elif child.Label.startswith("MasterShapes"):
                    if has_new_masters:
                        children_to_remove.append(child)
                    else:
                        children_to_keep.append(child)
                        FreeCAD.Console.PrintMessage("DEBUG: Preserving existing MasterShapes.\n")
                else:
                    children_to_keep.append(child)

            # 2. Delete removals
            for child in children_to_remove:
                def delete_recursive(obj):
                     if hasattr(obj, "Group"):
                         for c in list(obj.Group): delete_recursive(c)
                     try: self.doc.removeObject(obj.Name)
                     except Exception as e: FreeCAD.Console.PrintWarning(f"Failed to remove {obj.Name}: {e}\n")
                delete_recursive(child)
            
            # 3. Move from Temp to Target
            children_to_move = []
            for child in current_temp_group:
                if child.Label.startswith("MasterShapes") and not has_new_masters:
                    try: self.doc.removeObject(child.Name)
                    except: pass
                    continue
                
                # Enforce clean label for new MasterShapes
                if child.Label.startswith("MasterShapes"):
                    child.Label = "MasterShapes"
                    
                children_to_move.append(child)
            
            # 4. Set Group
            # Detach from Temp first to avoid "Object can only be in a single Group" error
            self.temp_layout.Group = []
            self.target_layout.Group = children_to_keep + children_to_move
            
            # 5. Properties
            def ensure_prop(obj, name, val, type_str="App::PropertyLength"):
                if not hasattr(obj, name):
                    obj.addProperty(type_str, name, "Layout", "")
                setattr(obj, name, val)

            ensure_prop(self.target_layout, "SheetWidth", self.temp_layout.SheetWidth)
            ensure_prop(self.target_layout, "SheetHeight", self.temp_layout.SheetHeight)
            ensure_prop(self.target_layout, "PartSpacing", self.temp_layout.PartSpacing)
            
            if hasattr(self.temp_layout, "BoundaryResolution"):
                ensure_prop(self.target_layout, "BoundaryResolution", self.temp_layout.BoundaryResolution, "App::PropertyFloat")
            
            if hasattr(self.temp_layout, "FontFile"):
                ensure_prop(self.target_layout, "FontFile", self.temp_layout.FontFile, "App::PropertyFile")
             
            FreeCAD.Console.PrintMessage("DEBUG: Target Layout Updated Successfully.\n")
            
            # CRITICAL: Clear Temp Layout Group before deleting to avoid issues
            try: FreeCAD.Console.PrintMessage(f"DEBUG: Removing Temp Layout: {self.temp_layout.Name} ({self.temp_layout.Label}). Children: {[c.Name for c in self.temp_layout.Group]}\n")
            except: pass
            self.temp_layout.Group = []

            # 6. Delete Temp Layout
            try:
                self.doc.removeObject(self.temp_layout.Name)
                FreeCAD.Console.PrintMessage("DEBUG: Temp Layout Object Removed.\n")
            except Exception as e: 
                FreeCAD.Console.PrintWarning(f"DEBUG: Failed to remove Temp Layout Object: {e}\n")
            
            if hasattr(self.target_layout, "ViewObject"):
                self.target_layout.ViewObject.Visibility = True
            
            self.ui.current_layout = self.target_layout

        else:
            # --- New Layout ---
            base_name, i = "Layout", 0
            existing_labels = [o.Label for o in self.doc.Objects]
            while f"{base_name}_{i:03d}" in existing_labels:
                i += 1
            final_name = f"{base_name}_{i:03d}"
            
            self.temp_layout.Label = final_name
            self.ui.current_layout = self.temp_layout
            FreeCAD.Console.PrintMessage(f"DEBUG: Finalized New Layout {final_name}\n")

        # Ensure MasterShapes are hidden
        if self.ui.current_layout:
            for child in self.ui.current_layout.Group:
                if child.Label.startswith("MasterShapes") and hasattr(child, "ViewObject"):
                     child.ViewObject.Visibility = False

        self.target_layout = None
        self.temp_layout = None
        self.doc.recompute()
        FreeCAD.Console.PrintMessage("Nesting Job Finalized.\n")
        self._debug_dump_doc()
