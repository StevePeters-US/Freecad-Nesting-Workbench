
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
    Encapsulates a single run of the nesting algorithm.
    Manages the temporary layout and its lifecycle (create, commit, abort).
    """
    def __init__(self, doc, ui_params, target_layout=None):
        self.doc = doc
        self.params = ui_params
        self.target_layout = target_layout # Can be None (New Layout)
        self.temp_layout = None
        self.sheets = []
        self.unplaced_parts = []
        
        self._init_temp_layout()

    def _init_temp_layout(self):
        self.temp_layout = self.doc.addObject("App::DocumentObjectGroup", "Layout_temp")
        if hasattr(self.temp_layout, "ViewObject"):
            self.temp_layout.ViewObject.Visibility = True
        
        FreeCAD.Console.PrintMessage(f"DEBUG: Created temp layout {self.temp_layout.Name}\n")
        self._debug_print_tree("After Init Temp Layout")
            
        # Write params to temp layout essentially "staging" the commit
        self._apply_properties(self.temp_layout)

    def _debug_print_tree(self, phase):
        FreeCAD.Console.PrintMessage(f"DEBUG_TREE [{phase}]:\n")
        if not self.doc: return
        for obj in self.doc.Objects:
             FreeCAD.Console.PrintMessage(f"  - {obj.Name} ({obj.Label})\n")
        FreeCAD.Console.PrintMessage("\n")

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
        self._set_prop(layout_obj, "App::PropertyBool", "IsStacked", False)

    def _set_prop(self, obj, type_str, name, val):
        if not hasattr(obj, name):
            obj.addProperty(type_str, name, "Layout", "")
        setattr(obj, name, val)

    def cleanup(self):
        """Aborts the job and deletes the temporary layout."""
        FreeCAD.Console.PrintMessage("DEBUG: Starting Cleanup...\n")
        if not self.temp_layout: return
        
        # Recursive delete of temp layout contents
        self._recursive_delete(self.temp_layout)
        self.temp_layout = None
        
        # Safety cleanup of any stragglers
        # Collect names first to avoid iterating over a modifying list or accessing dead wrappers
        candidate_names = []
        for obj in self.doc.Objects:
            try:
                if obj.Label.startswith("Layout_temp") or obj.Label.startswith("PartsToPlace"): # Check partial match for PartsToPlace too (e.g. PartsToPlace001)
                     candidate_names.append(obj.Name)
            except (ReferenceError, Exception):
                pass
                
        for name in candidate_names:
            obj = self.doc.getObject(name)
            if obj:
                 self._recursive_delete(obj)
        
        self._debug_print_tree("After Cleanup")

    def commit(self):
        """Moves results from temp to target (or finalizes temp as new)."""
        if not self.temp_layout: return None
        
        final_layout = self.target_layout
        
        # With _determine_target_layout ensuring a layout, this should always be true.
        # Added a fallback for robustness.
        if not final_layout:
            FreeCAD.Console.PrintError("Error: No target layout found during commit. Creating fallback.\n")
            final_layout = self.doc.addObject("App::DocumentObjectGroup", "Layout_Fallback")
            final_layout.Label = "Layout_Fallback" # Ensure it has a label
        
        FreeCAD.Console.PrintMessage(f"DEBUG: Updating existing layout: {final_layout.Label} ({final_layout.Name})\n")
        # Updating existing
        self._merge_into_target(final_layout)
        self.cleanup() # Delete the now-empty temp shell
            
        if hasattr(final_layout, "ViewObject"):
            final_layout.ViewObject.Visibility = True
            
        # Ensure MasterShapes derived property is hidden
        for child in final_layout.Group:
            if child.Label.startswith("MasterShapes") and hasattr(child, "ViewObject"):
                 child.ViewObject.Visibility = False
                 
        self._debug_print_tree("After Commit")
        return final_layout

    def _merge_into_target(self, target):
        temp = self.temp_layout
        
        # 1. Check for new masters
        temp_masters = next((c for c in temp.Group if c.Label.startswith("MasterShapes")), None)
        has_new_masters = temp_masters and len(temp_masters.Group) > 0
        
        # 2. Identify removals
        to_remove = []
        to_keep = []
        
        for child in target.Group:
            if child.Label.startswith("Sheet_") or child.Label.startswith("PartsToPlace"):
                to_remove.append(child)
            elif child.Label.startswith("MasterShapes"):
                if has_new_masters:
                    to_remove.append(child)
                else:
                    to_keep.append(child)
            else:
                to_keep.append(child)
                
        # 3. Execute removals
        for child in to_remove:
            # Crucial: If deleting a PartsToPlace bin from the TARGET, we must empty it first 
            # if it happens to contain anything we want to keep (though ideally it shouldn't).
            # But more importantly, if we delete a container, its children might be deleted if they have no other owners.
            # The placed parts (in Sheets) reference these objects.
            # Wait, PartsToPlace in TARGET contains *copies* from previous run? No, they were moved to Sheets?
            # Actually, standard FreeCAD behavior: if object A is in Group B, and A is also linked by C.
            # Deleting B does NOT delete A.
            # BUT, if A was created inside B using addObject, and never moved?
            # In our logic, placed parts are in Sheets. Sheets are in Target.
            # If PartsToPlace is ALSO in Target, it's just an empty group usually.
            # However, to be SAFE and avoid "Scope" errors:
            if child.Label.startswith("PartsToPlace"):
                child.Group = []
            
            self._recursive_delete(child)
            
        # 4. Move children
        to_move = []
        for child in list(temp.Group):
            # If we are keeping old masters, don't move the empty/temp master group
            if child.Label.startswith("MasterShapes") and not has_new_masters:
                # Just delete the temp master group container
                try: self.doc.removeObject(child.Name)
                except: pass
                continue
                
            if child.Label.startswith("MasterShapes"):
                child.Label = "MasterShapes"
                # Sanitize inner labels
                for m in child.Group:
                     if m.Label.startswith("temp_master_"):
                         m.Label = m.Label.replace("temp_master_", "master_")
            
            to_move.append(child)
            
        # 5. Commit Group
        temp.Group = [] # Detach from temp
        target.Group = to_keep + to_move
        
        # 6. Update Properties
        self._apply_properties(target)

    def _recursive_delete(self, obj):
         # Robustness check: Ensure object is still valid before access
         try:
             _ = obj.Name
         except (ReferenceError, Exception):
             return

         if hasattr(obj, "Group"):
             children = []
             try: children = list(obj.Group)
             except: pass
             
             for c in children: 
                 self._recursive_delete(c)
                 
         try: self.doc.removeObject(obj.Name)
         except: pass


class NestingController:
    """
    Main controller.
    """
    def __init__(self, ui_panel):
        self.ui = ui_panel
        self.doc = FreeCAD.ActiveDocument
        self.current_job = None
        self.shape_preparer = ShapePreparer(self.doc, {})
        
        # Directly set the default font path. The UI can override this if the user selects a different font.
        font_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'fonts'))
        default_font = os.path.join(font_dir, 'PoiretOne-Regular.ttf')
        self.ui.selected_font_path = default_font
        if hasattr(self.ui, 'font_label'):
            self.ui.font_label.setText(os.path.basename(default_font))

    def _create_default_layout(self, base_name="Layout"):
        i = 0
        existing_labels = [o.Label for o in self.doc.Objects]
        while f"{base_name}_{i:03d}" in existing_labels:
            i += 1
        new_layout = self.doc.addObject("App::DocumentObjectGroup", f"{base_name}_{i:03d}")
        new_layout.Label = f"{base_name}_{i:03d}" # Ensure label is set
        return new_layout

    def _determine_target_layout(self):
        target_layout = getattr(self.ui, 'current_layout', None)
        
        # 1. Check Explicit Selection from UI (self.ui.current_layout)
        if target_layout:
            try:
                # Validate if the object still exists in the document
                _ = target_layout.Name
                if target_layout not in self.doc.Objects:
                    raise ReferenceError("Object not in document")
            except (ReferenceError, AttributeError, ValueError):
                # If the object is invalid or deleted, clear it
                target_layout = None
                self.ui.current_layout = None

        # 2. Check Inferred from Selection (parent layout of selected master shapes)
        if not target_layout and hasattr(self.ui, 'selected_shapes_to_process') and self.ui.selected_shapes_to_process:
            # Assuming selected_shapes_to_process contains FreeCAD objects
            # This logic attempts to find a parent layout if a master shape is selected
            try:
                # Check if the first selected shape is a master_shape_ and try to find its parent layout
                first_shape = self.ui.selected_shapes_to_process[0]
                if first_shape.Label.startswith("master_shape_"):
                    # Master shapes are typically inside a container, which is inside MasterShapes group, which is inside a Layout
                    if hasattr(first_shape, 'InList') and first_shape.InList:
                        master_container = first_shape.InList[0] # e.g., "MasterShape_001" container
                        if hasattr(master_container, 'InList') and master_container.InList:
                            master_group = master_container.InList[0] # "MasterShapes" group
                            if hasattr(master_group, 'InList') and master_group.InList:
                                layout_candidate = master_group.InList[0] # The actual Layout object
                                if layout_candidate and hasattr(layout_candidate, 'Group'): # Ensure it's a group
                                    target_layout = layout_candidate
                                    self.ui.current_layout = target_layout # Update UI's current_layout
                                    FreeCAD.Console.PrintMessage(f"DEBUG: Inferred target layout from selection: {target_layout.Label}\n")
            except Exception as e:
                FreeCAD.Console.PrintMessage(f"DEBUG: Could not infer target layout from selection: {e}\n")
            
        # 3. Create Default if Missing
        if not target_layout:
            target_layout = self._create_default_layout("Layout")
            self.ui.current_layout = target_layout # Update UI's current_layout
            FreeCAD.Console.PrintMessage(f"DEBUG: Created default target layout: {target_layout.Label}\n")
            
        return target_layout

    def execute_nesting(self):
        FreeCAD.Console.PrintMessage("\n--- NESTING START ---\n")
        
        # 1. Cleanup old jobs
        if self.current_job:
            self.current_job.cleanup()
            self.current_job = None
            
        # 2. Hide existing layouts (Visual cleanup)
        self._hide_all_layouts()
        
        # 3. Collect Settings
        ui_params = self._collect_ui_params()
        
        # 4. Identify Target
        target_layout = self._determine_target_layout()
        if target_layout and hasattr(target_layout, "ViewObject"):
            target_layout.ViewObject.Visibility = False
            
        # 5. Start Job
        self.current_job = NestingJob(self.doc, ui_params, target_layout)
        
        # 6. Run Nesting Logic
        # (Prepare Parts, Run Algo, Draw)
        try:
            self._run_job_logic(self.current_job)
        except Exception as e:
            FreeCAD.Console.PrintError(f"Nesting Failed: {e}\n")
            self.ui.status_label.setText(f"Error: {e}")
            self.cancel_job()

    def _run_job_logic(self, job):
        # ... logic moved here ...
        # Create PartsToPlace
        parts_group = self.doc.addObject("App::DocumentObjectGroup", "PartsToPlace")
        job.temp_layout.addObject(parts_group)
        
        # Prepare
        ui_settings, quantities, master_map, rotation_params = self._collect_job_parameters()
        
        start_time = time.time()
        self.ui.status_label.setText("Preparing shapes...")
        QtGui.QApplication.processEvents()
        
        parts_to_nest = self.shape_preparer.prepare_parts(
            ui_settings, quantities, master_map, job.temp_layout, parts_group
        )
        
        if not parts_to_nest:
            self.ui.status_label.setText("Error: No valid parts to nest.")
            return

        # Persist Rotations
        self._persist_rotation_state(job.temp_layout, rotation_params)

        # Run Algo
        self.ui.status_label.setText("Running nesting algorithm...")
        algo_kwargs = self._prepare_algo_kwargs(ui_settings)
        
        try:
             # Check for early abort
             if not job.temp_layout: return

             is_simulating = self.ui.simulate_nesting_checkbox.isChecked()
             sheets, remaining, steps = nest(
                parts_to_nest, ui_settings['sheet_width'], ui_settings['sheet_height'],
                ui_settings['rotation_steps'], is_simulating, **algo_kwargs
             )
             
             if not is_simulating:
                 self._apply_placement(sheets, parts_to_nest)
                 
             # Draw
             # Verify job still active
             if not job.temp_layout: return
             
             for sheet in sheets:
                 sheet.draw(self.doc, ui_settings, job.temp_layout, parts_to_place_group=parts_group)
                 
             # Cleanup specific sheets overshoot
             # (Handled by job commit merge usually, but we can prevent it here visually)
             
             # Status
             placed = sum(len(s) for s in sheets)
             msg = f"Placed {placed} shapes on {len(sheets)} sheets. ({(time.time()-start_time):.2f}s)"
             self.ui.status_label.setText(msg)
             FreeCAD.Console.PrintMessage(f"{msg}\n--- NESTING DONE ---\n")
             if self.ui.sound_checkbox.isChecked(): QtGui.QApplication.beep()
             
        except NestingDependencyError as e:
            self.ui.status_label.setText(str(e))


    def cancel_job(self):
        if self.current_job:
            self.current_job.cleanup()
            
            # Restore target visibility
            if self.current_job.target_layout and hasattr(self.current_job.target_layout, "ViewObject"):
                self.current_job.target_layout.ViewObject.Visibility = True
            
            self.current_job = None
        
        FreeCAD.Console.PrintMessage("Job Cancelled.\n")

    def finalize_job(self):
        if self.current_job:
            final_obj = self.current_job.commit()
            self.ui.current_layout = final_obj
            self.current_job = None
            FreeCAD.Console.PrintMessage("Job Finalized.\n")
            self.doc.recompute()

    # ... Include helpers (_collect_ui_params, _prepare_algo_kwargs, etc) adapted from old ...
    
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

    def _apply_placement(self, sheets, parts_to_nest):
        original_parts_map = {part.id: part for part in parts_to_nest}
        for sheet in sheets:
            for i, placed_part in enumerate(sheet.parts):
                sheet_origin = sheet.get_origin() 
                original_part = original_parts_map[placed_part.shape.id]
                original_part.placement = placed_part.shape.get_final_placement(sheet_origin)
                sheet.parts[i].shape = original_part

    def _prepare_algo_kwargs(self, ui_settings):
        algo_kwargs = {}
        if self.ui.minkowski_random_checkbox.isChecked():
            algo_kwargs['search_direction'] = None
        else:
            angle_deg = (270 - self.ui.minkowski_direction_dial.value()) % 360
            angle_rad = math.radians(angle_deg)
            algo_kwargs['search_direction'] = (math.cos(angle_rad), math.sin(angle_rad))
        
        algo_kwargs['population_size'] = self.ui.minkowski_population_size_input.value()
        algo_kwargs['generations'] = self.ui.minkowski_generations_input.value()
        algo_kwargs['spacing'] = ui_settings['spacing']

        if hasattr(self.ui, 'log_message'):
            algo_kwargs['log_callback'] = self.ui.log_message
            
        return algo_kwargs

    def _persist_rotation_state(self, layout_obj, rotation_params):
        master_shapes_group = layout_obj.getObject("MasterShapes")
        if master_shapes_group:
            for container in master_shapes_group.Group:
                if not hasattr(container, "Group"): continue
                
                inner_shape = next((c for c in container.Group if c.Label.startswith("master_shape_")), None)
                if inner_shape:
                    original_label = inner_shape.Label.replace("master_shape_", "")
                    if original_label in rotation_params:
                        r_steps, r_override = rotation_params[original_label]
                        if not hasattr(container, "PartRotationSteps"):
                             container.addProperty("App::PropertyInteger", "PartRotationSteps", "Nesting", "Rotation steps")
                        if not hasattr(container, "PartRotationOverride"):
                             container.addProperty("App::PropertyBool", "PartRotationOverride", "Nesting", "Override global rotation")
                        container.PartRotationSteps = r_steps
                        container.PartRotationOverride = r_override

    def _collect_job_parameters(self):
        ui_settings = self._collect_ui_params()
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
        
        is_reloading = False
        if self.ui.selected_shapes_to_process and self.ui.selected_shapes_to_process[0].Label.startswith("master_shape_"):
            is_reloading = True
            
        for obj in self.ui.selected_shapes_to_process:
            try:
                _ = obj.Name 
                if obj not in self.doc.Objects: continue
                
                display_label = obj.Label
                if display_label.startswith("master_shape_"):
                    display_label = display_label.replace("master_shape_", "")
                
                if display_label in ui_row_data:
                    # Relaxed check for reloading
                    if not is_reloading or obj.Label.startswith("master_shape_"):
                         quantities[display_label] = ui_row_data[display_label]
                         master_shapes_from_ui[obj.Label] = obj
                         
            except (ReferenceError, AttributeError):
                continue

        return ui_settings, quantities, master_shapes_from_ui, rotation_params

    def _determine_target_layout(self):
        target_layout = getattr(self.ui, 'current_layout', None)
        
        if target_layout:
            try:
                _ = target_layout.Name
                if target_layout not in self.doc.Objects:
                    raise ReferenceError("Object not in document")
            except (ReferenceError, AttributeError, ValueError):
                target_layout = None
                self.ui.current_layout = None

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
            except Exception:
                pass
        return target_layout
        
    def _hide_all_layouts(self):
        for obj in self.doc.Objects:
            if (obj.Name.startswith("Layout_") or 
                obj.Name in ["PartsToPlace", "MasterShapes"]):
                if hasattr(obj, "ViewObject") and obj.ViewObject.Visibility:
                    obj.ViewObject.Visibility = False

    def toggle_bounds_visibility(self):
        is_visible = self.ui.show_bounds_checkbox.isChecked()
        toggled_count = 0
        for obj in self.doc.Objects:
            if obj.Label.startswith("nested_") and obj.isDerivedFrom("App::Part"):
                shape_child = next((child for child in obj.Group if hasattr(child, "Proxy") and child.Proxy.__class__.__name__ == "ShapeObject"), None)
                if shape_child and hasattr(shape_child, "ShowBounds"):
                    shape_child.ShowBounds = is_visible
                    toggled_count += 1
        
        if toggled_count > 0:
            self.ui.status_label.setText(f"Toggled bounds visibility for {toggled_count} shapes.")

