# SPDX-License-Identifier: LGPL-2.1-or-later

import FreeCAD
import FreeCADGui
import Part
import os
import time
import math
import threading
from PySide import QtWidgets
from PySide.QtCore import QThread, Signal
from ...datatypes.shape import Shape
from .shape_preparer import ShapePreparer
from .layout_manager import LayoutManager, Layout
from .ga_coordinator import GACoordinator
from ...freecad_helpers import recursive_delete
from ...constants import *
from .nesting_job import NestingJob
from ... import DEFAULT_FONT

try:
    from .nesting_logic import nest, NestingDependencyError
    from .visualization_manager import VisualizationManager
except ImportError:
    pass  # Optional dependency missing, proceed without visualization

class NestingWorker(QThread):
    """Runs nesting computation on a background thread.
    
    Communicates with main thread via Qt signals for:
    - Status/progress updates (non-blocking)
    - Document operations like sheet.draw() (blocking synchronization)
    - Completion/error reporting
    """
    status_changed = Signal(str)
    progress_updated = Signal(int, int, str)   # current, total, message
    draw_requested = Signal(object)             # payload dict for main-thread drawing
    finished_signal = Signal(object)            # NestingJob result (or None)
    error_signal = Signal(str)                  # error message + traceback
    
    def __init__(self, coordinator, run_args, cancel_check_fn, parent=None):
        """
        Args:
            coordinator: GACoordinator instance
            run_args: tuple of (target_layout, ui_params, quantities, master_map,
                      rotation_params, algo_kwargs, is_simulating, viz_manager)
            cancel_check_fn: callable that returns True if cancel requested
        """
        super().__init__(parent)
        self.coordinator = coordinator
        self.run_args = run_args
        self.cancel_check_fn = cancel_check_fn
        self._draw_event = threading.Event()
    
    def run(self):
        """Execute nesting on worker thread."""
        try:
            (target_layout, ui_params, quantities, master_map,
             rotation_params, algo_kwargs, is_simulating, viz_manager) = self.run_args
            
            job = self.coordinator.run(
                target_layout, ui_params, quantities, master_map,
                rotation_params, algo_kwargs, is_simulating, viz_manager=viz_manager
            )
            self.finished_signal.emit(job)
        except Exception as e:
            import traceback
            self.error_signal.emit(f"{e}\n{traceback.format_exc()}")
    
    def request_draw_on_main_thread(self, payload):
        """Called from worker thread. Emits signal and blocks until main thread draws."""
        self._draw_event.clear()
        self.draw_requested.emit(payload)
        self._draw_event.wait()
    
    def notify_draw_complete(self):
        """Called from main thread after draw finishes."""
        self._draw_event.set()

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
        self.is_running = False
        self.cancel_requested = False
        
        self.viz_manager = VisualizationManager()
        
        # Initialize default fonts
        default_font = DEFAULT_FONT
        self.ui.selected_font_path = default_font
        if hasattr(self.ui, 'font_label'):
            self.ui.font_label.setText(os.path.basename(default_font))

    def execute_nesting(self):
        FreeCAD.Console.PrintMessage("\n--- NESTING START ---\n")
        
        if self.current_job:
            self.current_job.cleanup()
            self.current_job = None

        Shape.clear_caches()

        target_layout = self._ensure_target_layout()
        if not target_layout:
             return # Standard error handled in helper
             
        is_simulating = self.ui.simulate_nesting_checkbox.isChecked()
        if hasattr(target_layout, "ViewObject"):
            target_layout.ViewObject.Visibility = False

        ui_params = self._collect_ui_params()
        ui_params, quantities, master_map, rotation_params = self._collect_job_parameters(ui_params)
        
        algo_kwargs = self._prepare_algo_kwargs(ui_params)
        verbose = self.ui.verbose_logging_checkbox.isChecked()
        algo_kwargs['verbose'] = verbose
        
        rot_steps = ui_params.get('rotation_steps', 1)
        ann_steps = algo_kwargs.get('anneal_steps', 25) if ui_params.get('algorithm') == 'Physics' else 0
        FreeCAD.Console.PrintMessage(f"Algorithm Selected: {ui_params.get('algorithm', 'Unknown')}\n")
        FreeCAD.Console.PrintMessage(f"  -> UI Rotation Steps Slider: {rot_steps}\n")
        FreeCAD.Console.PrintMessage(f"  -> UI Anneal Steps Input: {ann_steps}\n")
        
        algo_kwargs['cancel_callback'] = self._check_cancel
        
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        prefs.SetBool("VerboseLogging", verbose)
        
        
        def progress_cb(current, total, message=None):
            try:
                self.ui.update_progress(current, total, message)
            except RuntimeError: pass  # Widget deleted (panel closed)
            
        self.ui.reset_progress()
        algo_kwargs['progress_callback'] = progress_cb
        
        self.is_running = True
        self.cancel_requested = False
        self.ui.nest_button.setEnabled(False)
        self.ui.cancel_button.setEnabled(True)
        self._execute_ga_nesting(target_layout, ui_params, quantities, master_map, 
                                 rotation_params, algo_kwargs, is_simulating, self.viz_manager)
    
    def load_selection(self):
        FreeCAD.Console.PrintMessage("Loading selection via Controller...\n")
        selection = FreeCADGui.Selection.getSelection()
        self.ui.shape_table.setRowCount(0)

        if not selection:
            FreeCAD.Console.PrintMessage("  -> No selection found.\n")
            self.ui.status_label.setText("Warning: No shapes selected.")
            self.ui.nest_button.setEnabled(False)
            return

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

        self._load_params_from_layout(layout_group)
        
        self._load_shapes_from_layout(layout_group)

    def _load_params_from_layout(self, layout_group):
        """Extracts algorithm parameters from layout properties."""
        props_map = {
            PROP_SHEET_WIDTH: self.ui.sheet_width_input,
            PROP_SHEET_HEIGHT: self.ui.sheet_height_input,
            PROP_PART_SPACING: self.ui.part_spacing_input,
            PROP_SHEET_THICKNESS: self.ui.sheet_thickness_input,
            PROP_SIMPLIFICATION: self.ui.simplification_input,
            PROP_LABEL_SIZE: self.ui.label_size_input,
            PROP_GENERATIONS: self.ui.minkowski_generations_input,
            PROP_POPULATION_SIZE: self.ui.minkowski_population_size_input,
            PROP_NESTING_DIRECTION: self.ui.minkowski_direction_dial,
        }
        
        for prop, widget in props_map.items():
            val = getattr(layout_group, prop, None)
            if val is not None: widget.setValue(val)
            
        deflection_angle = getattr(layout_group, PROP_DEFLECTION_ANGLE, None)
        if deflection_angle is not None:
            self.ui.deflection_input.setValue(deflection_angle)
        elif hasattr(layout_group, 'Deflection'):
            self.ui.deflection_input.setValue(layout_group.Deflection * 200.0)
            

        
        font_path = getattr(layout_group, PROP_FONT_FILE, None)
        if font_path and os.path.exists(font_path):
            self.ui.selected_font_path = font_path
            self.ui.font_label.setText(os.path.basename(font_path))

        steps = getattr(layout_group, PROP_GLOBAL_ROTATION_STEPS, 0)
        if steps > 0:
            target_angle = 360.0 / steps
            algo = getattr(layout_group, "Algorithm", "Minkowski")
            self.ui.algorithm_dropdown.setCurrentText(algo)
            
            angles = PHYSICS_ROTATION_PRESETS if algo == "Physics" else self.ui.rotation_angles
            slider = self.ui.physics_rotation_steps_slider if algo == "Physics" else self.ui.minkowski_rotation_steps_slider
            
            closest_idx = 0
            min_diff = float('inf')
            for i, angle in enumerate(angles):
                diff = abs(angle - target_angle)
                if diff < min_diff:
                    min_diff, closest_idx = diff, i
            slider.setValue(closest_idx)

    def _load_shapes_from_layout(self, layout_group):
        """Identifies master shapes and their quantities/overrides."""
        master_shapes_group = next((c for c in layout_group.Group if c.Label.startswith("MasterShapes")), None)
        
        if not master_shapes_group:
            FreeCAD.Console.PrintWarning(f"  WARNING: No MasterShapes group found in '{layout_group.Label}'\n")
            self.ui.status_label.setText("Warning: Could not find 'MasterShapes' group.")
            return

        shapes_to_load = []
        quantities, overrides, steps_map, up_dirs, fill_map = {}, {}, {}, {}, {}
        
        for master in master_shapes_group.Group:
            if not hasattr(master, "Group"): continue
            
            shape_obj = next((child for child in master.Group if child.Label.startswith("master_shape_")), None)
            if shape_obj and hasattr(shape_obj, "Shape"):
                shapes_to_load.append(shape_obj)
                label = shape_obj.Label
                
                quantities[label] = getattr(master, "Quantity", 1)
                
                overrides[label] = getattr(master, "PartRotationOverride", [])
                steps_map[label] = getattr(master, "PartRotationSteps", 0)
                up_dirs[label] = getattr(master, "UpDirection", "Z+")
                fill_map[label] = getattr(master, "FillSheet", False)
        
        self.load_shapes(
            shapes_to_load, is_reloading_layout=True, initial_quantities=quantities,
            initial_overrides=overrides, initial_rotation_steps=steps_map,
            initial_up_directions=up_dirs, initial_fill_sheet=fill_map
        )

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
        
        selection_counts = {}

        if not is_reloading_layout:
            extracted = self._extract_parts_from_selection(selection)
            if extracted:
                # Count occurrences before deduping
                for obj in extracted:
                    if obj in selection_counts:
                        selection_counts[obj] += 1
                    else:
                        selection_counts[obj] = 1
                
                selection = extracted
                FreeCAD.Console.PrintMessage(f"  -> Extracted {len(selection)} parts from selection.\n")
        
        self.ui.selected_shapes_to_process = list(dict.fromkeys(selection)) 
        
        if not is_reloading_layout:
            self.ui.current_layout = None
            self.ui.hidden_originals = list(self.ui.selected_shapes_to_process)
        
        self.ui.shape_table.setRowCount(len(self.ui.selected_shapes_to_process))
        for i, obj in enumerate(self.ui.selected_shapes_to_process):
            display_label = obj.Label
            if display_label.startswith("master_shape_"):
                display_label = display_label.replace("master_shape_", "")
            
            qty = selection_counts.get(obj, 1)
            
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

            add_row_fn = getattr(self.ui, 'add_part_row', getattr(self.ui, '_add_part_row', None))
            if add_row_fn:
                 add_row_fn(i, display_label, quantity=qty, rotation_steps=steps, 
                            override_rotation=override, up_direction=up_dir, fill_sheet=fill)
        
        self.ui.shape_table.resizeColumnsToContents()
        self.ui.status_label.setText(f"{len(selection)} unique object(s) selected. Specify quantities and nest.")

    def add_selected_shapes(self):
        """Adds the currently selected FreeCAD objects to the shape table."""
        selection = FreeCADGui.Selection.getSelection()
        if not selection:
            self.ui.status_label.setText("Select shapes in the 3D view or tree to add them.")
            return

        extracted = self._extract_parts_from_selection(selection)
        selection_counts = {}
        if extracted:
            for obj in extracted:
                selection_counts[obj] = selection_counts.get(obj, 0) + 1
            selection = extracted

        existing_labels = [self.ui.shape_table.item(row, 0).text() for row in range(self.ui.shape_table.rowCount())]
        
        added_count = 0
        
        unique_selection = list(dict.fromkeys(selection))
        
        for obj in unique_selection:
            if obj.Label not in existing_labels:
                row_position = self.ui.shape_table.rowCount()
                self.ui.shape_table.insertRow(row_position)
                
                qty = selection_counts.get(obj, 1)
                
                add_row_fn = getattr(self.ui, 'add_part_row', getattr(self.ui, '_add_part_row', None))
                if add_row_fn:
                    add_row_fn(row_position, obj.Label, quantity=qty)
                    
                self.ui.selected_shapes_to_process.append(obj)
                added_count += 1
        
        self.ui.shape_table.resizeColumnsToContents()
        self.ui.status_label.setText(f"Added {added_count} new shape(s).")

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

        if self.ui.shape_table.rowCount() == 0:
            self.ui.nest_button.setEnabled(False)

    def _get_rotation_steps(self):
        """Returns the orientation count based on algorithm-specific angle mapping."""
        algo = self.ui.algorithm_dropdown.currentText()
        
        if algo == "Physics":
            idx = self.ui.physics_rotation_steps_slider.value()
            # Mapping: 360(1), 90(4), 45(8), 30(12), 15(24), 10(36), 5(72), 2(180), 1(360)
            angles = PHYSICS_ROTATION_PRESETS
            if idx < len(angles):
                angle = angles[idx]
                return int(360 / angle)
        else:
            idx = self.ui.minkowski_rotation_steps_slider.value()
            angles = self.ui.rotation_angles
            if idx < len(angles):
                angle = angles[idx]
                return int(360 / angle)
        return 1

    def _execute_ga_nesting(self, target_layout, ui_params, quantities, master_map, 
                            rotation_params, algo_kwargs, is_simulating, viz_manager=None):
        """GA optimization on a background thread."""
        
        self._worker = NestingWorker(
            coordinator=None, # Set below
            run_args=(target_layout, ui_params, quantities, master_map,
                      rotation_params, algo_kwargs, is_simulating, viz_manager),
            cancel_check_fn=self._check_cancel,
            parent=None
        )
        
        coordinator = GACoordinator(
            doc=self.doc,
            shape_preparer=self.shape_preparer,
            ui_callbacks={
                'set_status': lambda msg: self.ui.status_label.setText(msg),
                'update_progress': lambda c, t, m: self.ui.update_progress(c, t, m),
                'reset_progress': lambda: self.ui.reset_progress(),
                'play_sound': lambda: QtWidgets.QApplication.beep() if self.ui.sound_checkbox.isChecked() else None,
            },
            draw_callback=self._worker.request_draw_on_main_thread,
            worker=self._worker
        )
        self._worker.coordinator = coordinator
        
        self._worker.status_changed.connect(lambda msg: self.ui.status_label.setText(msg))
        self._worker.progress_updated.connect(lambda c, t, m: self.ui.update_progress(c, t, m))
        self._worker.draw_requested.connect(self._handle_draw_request)
        self._worker.finished_signal.connect(self._on_nesting_finished)
        self._worker.error_signal.connect(self._on_nesting_error)
        
        self._worker.start()

    def _handle_draw_request(self, payload):
        """Main-thread handler for draw requests from worker."""
        try:
            if payload.get('updateGui_only'):
                FreeCADGui.updateGui()
            elif payload.get('create_population'):
                layouts = self._worker.coordinator.layout_manager.create_ga_population(
                    payload['master_map'], payload['quantities'], 
                    payload['ui_params'], payload['population_size'],
                    payload['rotation_steps'], verbose=payload.get('verbose', False)
                )
                self._worker.coordinator._pending_layouts = layouts
            elif payload.get('build_next_generation'):
                layouts = self._worker.coordinator._build_next_generation(
                    payload['gen'], payload['layouts'], payload['elites'], 
                    payload['master_map'], payload['quantities'], payload['ui_params'], 
                    payload['rotation_steps'], payload['mutation_rate'], 
                    payload['immigrant_ratio'], payload.get('verbose', False)
                )
                self._worker.coordinator._pending_layouts = layouts
            elif payload.get('spawn_fill_part'):
                payload['result_holder'][0] = payload['spawn_fn']()
            elif payload.get('cleanup_layouts'):
                for layout in payload['layouts']:
                    if layout != payload['best_layout']:
                        self._worker.coordinator.layout_manager.delete_layout(layout, verbose=payload.get('verbose', False))
            elif payload.get('sheets'):
                for sheet in payload['sheets']:
                    sheet.draw(payload['doc'], payload['ui_params'], payload['layout_group'],
                              parts_to_place_group=payload['parts_group'],
                              verbose=payload.get('verbose', False))
                if payload.get('hide_layout'):
                    lg = payload['layout_group']
                    if lg and hasattr(lg, "ViewObject"):
                        lg.ViewObject.Visibility = False
                FreeCADGui.updateGui()
            elif payload.get('ga_finalize'):
                # _finalize() and doc.recompute() must run on main thread (ViewObject + recompute)
                coordinator = self._worker.coordinator
                job = coordinator._finalize(
                    payload['best_layout'], payload['best_efficiency'],
                    payload['total_time'], payload['target_layout'], payload['ui_params']
                )
                payload['result_holder'][0] = job
                coordinator.doc.recompute()
            elif payload.get('doc_recompute_only'):
                self._worker.coordinator.doc.recompute()
        finally:
            self._worker.notify_draw_complete()

    def _on_nesting_finished(self, job):
        """Main-thread handler for nesting completion."""
        if self.cancel_requested:
            if job:
                job.cleanup()
            self.cancel_job()
        else:
            self.current_job = job
            
        self.is_running = False
        self.cancel_requested = False
        self.ui.nest_button.setEnabled(True)
        self.ui.cancel_button.setEnabled(False)
        self.ui.reset_progress()
        self._worker = None

    def _on_nesting_error(self, error_msg):
        """Main-thread handler for nesting errors."""
        FreeCAD.Console.PrintError(f"Nesting Error: {error_msg}\n")
        self.ui.status_label.setText(f"Error: {error_msg.split(chr(10))[0]}")
        self.is_running = False
        self.cancel_requested = False
        self.ui.nest_button.setEnabled(True)
        self.ui.cancel_button.setEnabled(False)
        self.ui.reset_progress()
        self._worker = None
    
    def finalize_job(self):
        """Called when User clicks OK."""
        if self.current_job:
            final_layout = self.current_job.commit()
            
            self.ui.current_layout = final_layout
            
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

    def request_cancel(self):
        """Called when the custom Cancel Nesting button is clicked."""
        if self.is_running:
            self.cancel_requested = True
            try:
                self.ui.status_label.setText("Cancelling... Please wait.")
                self.ui.cancel_button.setEnabled(False) # Prevent double-click
            except Exception:
                pass  # UI widget may be destroyed if user closed panel while cancelling
            
            # If worker is running, also unblock it from any draw wait
            if hasattr(self, '_worker') and self._worker:
                self._worker.notify_draw_complete() # Unblock if waiting for draw
        else:
            self.cancel_job()
            if hasattr(self.ui, 'reject'):
                self.ui.reject()

    def _check_cancel(self):
        return self.cancel_requested

    def cancel_job(self):
        """Called when User clicks Cancel."""
        if self.current_job:
            target = self.current_job.target_layout
            
            self.current_job.cleanup()
            
            if target:
                try: 
                    has_content = any(
                        child.Label.startswith("Sheet_") or child.Label.startswith("MasterShapes")
                        for child in (target.Group if hasattr(target, "Group") else [])
                    )
                    
                    if not has_content:
                        recursive_delete(self.doc, target)
                        if hasattr(self.ui, 'current_layout') and self.ui.current_layout == target:
                            self.ui.current_layout = None
                        FreeCAD.Console.PrintMessage("Removed empty target layout.\n")
                    else:
                        if hasattr(target, "ViewObject"):
                            target.ViewObject.Visibility = True
                        
                        if hasattr(target, "Group"):
                            for child in target.Group:
                                if child.Label.startswith("Sheet_") and hasattr(child, "ViewObject"):
                                    child.ViewObject.Visibility = True
                                if child.Label.startswith("MasterShapes") and hasattr(child, "ViewObject"):
                                    child.ViewObject.Visibility = False
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"[NestingController] Cancel cleanup failed for child: {e}\n")
            
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
            
            if obj.Label.startswith("boundary_"):
                found_count += 1
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.Visibility = is_visible
                    
            if hasattr(obj, "BoundaryObject") and obj.BoundaryObject:
                found_count += 1
                if hasattr(obj.BoundaryObject, "ViewObject"):
                    obj.BoundaryObject.ViewObject.Visibility = is_visible
                
            if hasattr(obj, "Group"):
                for child in obj.Group:
                    set_show_bounds(child, depth + 1)
                    
        set_show_bounds(target_layout)
        self.doc.recompute()

    def _ensure_target_layout(self):
        """Determines the target layout, creating a default one if none exists."""
        target = getattr(self.ui, 'current_layout', None)
        
        if target:
            try:
                if target not in self.doc.Objects: target = None
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"[NestingController] Target validation failed: {e}\n")
                target = None
            
        if not target and hasattr(self.ui, 'selected_shapes_to_process') and self.ui.selected_shapes_to_process:
             # Logic to find parent layout derived previously...
             # Simplified for brevity/robustness
             pass 

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
        deflection_angle = self.ui.deflection_input.value()
        deflection_mm = deflection_angle / 200.0
        
        settings_dict = {
            'sheet_width': self.ui.sheet_width_input.value(),
            'sheet_height': self.ui.sheet_height_input.value(),
            'spacing': self.ui.part_spacing_input.value(),
            'sheet_thickness': self.ui.sheet_thickness_input.value(),
            'deflection': deflection_mm,
            'deflection_angle': deflection_angle,
            'simplification': self.ui.simplification_input.value(),
            'rotation_steps': self._get_rotation_steps(),
            'add_labels': self.ui.add_labels_checkbox.isChecked(),
            'font_path': getattr(self.ui, 'selected_font_path', None),
            'show_bounds': self.ui.show_bounds_checkbox.isChecked(),
            'label_height': self.ui.label_height_input.value(),
            'label_size': self.ui.label_size_input.value(),
            'generations': self.ui.minkowski_generations_input.value(),
            'population_size': self.ui.minkowski_population_size_input.value(),
            'compactness_weight': self.ui.minkowski_compactness_input.value(),
            'verbose': self.ui.verbose_logging_checkbox.isChecked(),
            'nesting_direction': self.ui.minkowski_direction_dial.value(),
            'algorithm': self.ui.algorithm_dropdown.currentText(),
            'use_random_direction': (self.ui.physics_random_checkbox.isChecked() if self.ui.algorithm_dropdown.currentText() == 'Physics' else self.ui.minkowski_random_checkbox.isChecked()),
            'stability_tolerance': self.ui.physics_improvement_threshold_input.value(),
            'anneal_curve': self.ui.physics_anneal_curve_type.currentText(),
            'anneal_min_amp': self.ui.physics_anneal_min_amp.value(),
            'anneal_max_amp': self.ui.physics_anneal_max_amp.value(),
            'anneal_rot_steps': self.ui.physics_anneal_rot_steps.value(),
            'anneal_rot_curve': self.ui.physics_anneal_rot_curve_type.currentText(),
            'anneal_rot_min': self.ui.physics_anneal_rot_min.value(),
            'anneal_rot_max': self.ui.physics_anneal_rot_max.value()
        }
        
        self.save_settings(settings_dict)
        
        return settings_dict

    def save_settings(self, settings):
        """Saves current UI settings to FreeCAD preferences."""
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        prefs.SetFloat(PROP_SHEET_WIDTH, float(settings['sheet_width']))
        prefs.SetFloat(PROP_SHEET_HEIGHT, float(settings['sheet_height']))
        prefs.SetFloat(PROP_PART_SPACING, float(settings['spacing']))
        prefs.SetFloat(PROP_SHEET_THICKNESS, float(settings['sheet_thickness']))
        prefs.SetFloat(PROP_DEFLECTION_ANGLE, float(settings.get('deflection_angle', 10)))  # Save angle, not mm
        prefs.SetFloat(PROP_SIMPLIFICATION, float(settings['simplification']))
        prefs.SetFloat("GACompactnessWeight", float(settings['compactness_weight']))
        
        mink_steps = int(360 / self.ui.rotation_angles[self.ui.minkowski_rotation_steps_slider.value()])
        prefs.SetInt("MinkowskiRotationSteps", mink_steps)
        
        phys_angles = PHYSICS_ROTATION_PRESETS
        phys_steps = int(360 / phys_angles[self.ui.physics_rotation_steps_slider.value()])
        prefs.SetInt("PhysicsRotationSteps", phys_steps)

        prefs.SetBool(PROP_ADD_LABELS, bool(settings['add_labels']))
        prefs.SetBool(PROP_SHOW_BOUNDS, bool(settings['show_bounds']))
        prefs.SetFloat(PROP_LABEL_HEIGHT, float(settings['label_height']))
        prefs.SetFloat(PROP_LABEL_SIZE, float(settings['label_size']))
        prefs.SetFloat("PhysicsStabilityTolerance", float(settings.get('stability_tolerance', 0.01)))
        prefs.SetString("PhysicsAnnealCurveType", str(settings.get('anneal_curve', "Logarithmic")))
        prefs.SetFloat("PhysicsAnnealMinAmp", float(settings.get('anneal_min_amp', 0.1)))
        prefs.SetFloat("PhysicsAnnealMaxAmp", float(settings.get('anneal_max_amp', 100.0)))
        
        prefs.SetInt("PhysicsAnnealRotSteps", int(settings.get('anneal_rot_steps', 10)))
        prefs.SetString("PhysicsAnnealRotCurveType", str(settings.get('anneal_rot_curve', "Logarithmic")))
        prefs.SetFloat("PhysicsAnnealRotMin", float(settings.get('anneal_rot_min', 1.0)))
        prefs.SetFloat("PhysicsAnnealRotMax", float(settings.get('anneal_rot_max', 90.0)))
        if settings['font_path']:
             prefs.SetString("FontPath", str(settings['font_path']))

    def _collect_job_parameters(self, ui_settings):
        quantities = {}
        master_map = {}
        rotation_params = {}
        
        global_rot = ui_settings['rotation_steps']
        
        for row in range(self.ui.shape_table.rowCount()):
            try:
                label = self.ui.shape_table.item(row, 0).text()
                qty = self.ui.shape_table.cellWidget(row, 1).value()
                
                rot_widget = self.ui.shape_table.cellWidget(row, 2)
                rot_val = rot_widget.findChild(QtWidgets.QSpinBox).value()
                override = self.ui.shape_table.cellWidget(row, 3).isChecked()
                
                up_dir_combo = self.ui.shape_table.cellWidget(row, 4)
                up_direction = up_dir_combo.currentText() if up_dir_combo else "Z+"
                
                fill_checkbox = self.ui.shape_table.cellWidget(row, 5)
                fill_sheet = fill_checkbox.isChecked() if fill_checkbox else False
                
                quantities[label] = {
                    'quantity': qty,
                    'rotation_steps': rot_val if override else global_rot,
                    'up_direction': up_direction,
                    'fill_sheet': fill_sheet
                }
                
                rotation_params[label] = (rot_val, override)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"[NestingController] Skipping row {row} in shape table: {e}\n")
                continue
            
        for obj in self.ui.selected_shapes_to_process:
             try:
                 lbl = obj.Label.replace("master_shape_", "")
                 if lbl in quantities:
                     master_map[obj.Label] = obj
             except Exception as e:
                 FreeCAD.Console.PrintWarning(f"[NestingController] Failed to map object {obj.Label if hasattr(obj, 'Label') else 'unknown'}: {e}\n")
             
        return ui_settings, quantities, master_map, rotation_params

    def _prepare_algo_kwargs(self, ui_params):
        algo_kwargs = {}
        algorithm = ui_params.get('algorithm', 'Minkowski')
        
        if algorithm == 'Physics':
            if self.ui.physics_random_checkbox.isChecked():
                algo_kwargs['physics_direction'] = None 
            else:
                # Dial value is CCW from 6 o'clock. 
                # 0=Down(0,-1), 90=Left(-1,0), 180=Up(0,1), 270=Right(1,0)
                angle_deg = (270 - self.ui.physics_direction_dial.value()) % 360
                angle_rad = math.radians(angle_deg)
                algo_kwargs['physics_direction'] = (math.cos(angle_rad), math.sin(angle_rad))
            
            algo_kwargs['step_size'] = self.ui.physics_step_size_input.value()
            algo_kwargs['max_spawn_count'] = self.ui.physics_max_spawn_input.value()
            algo_kwargs['max_nesting_steps'] = self.ui.physics_max_nesting_steps_input.value()
            algo_kwargs['anneal_steps'] = self.ui.physics_anneal_steps_input.value()
            algo_kwargs['anneal_rotate_enabled'] = self.ui.anneal_rotate_checkbox.isChecked()
            algo_kwargs['anneal_translate_enabled'] = self.ui.anneal_translate_checkbox.isChecked()
            algo_kwargs['anneal_random_shake_direction'] = self.ui.anneal_random_shake_checkbox.isChecked()
            algo_kwargs['stability_tolerance'] = ui_params.get('stability_tolerance', 0.01)
            algo_kwargs['anneal_curve'] = ui_params.get('anneal_curve', "Logarithmic")
            algo_kwargs['anneal_min_amp'] = ui_params.get('anneal_min_amp', 0.1)
            algo_kwargs['anneal_max_amp'] = ui_params.get('anneal_max_amp', 100.0)
            algo_kwargs['anneal_rot_steps'] = ui_params.get('anneal_rot_steps', 10)
            algo_kwargs['anneal_rot_curve'] = ui_params.get('anneal_rot_curve', "Logarithmic")
            algo_kwargs['anneal_rot_min'] = ui_params.get('anneal_rot_min', 1.0)
            algo_kwargs['anneal_rot_max'] = ui_params.get('anneal_rot_max', 90.0)
        else:
            if self.ui.minkowski_random_checkbox.isChecked():
                algo_kwargs['search_direction'] = None
            else:
                angle_deg = (270 - self.ui.minkowski_direction_dial.value()) % 360
                angle_rad = math.radians(angle_deg)
                algo_kwargs['search_direction'] = (math.cos(angle_rad), math.sin(angle_rad))
            
            algo_kwargs['population_size'] = self.ui.minkowski_population_size_input.value()
            algo_kwargs['generations'] = self.ui.minkowski_generations_input.value()
            algo_kwargs['clear_nfp_cache'] = self.ui.clear_cache_checkbox.isChecked()

        algo_kwargs['spacing'] = ui_params['spacing']
        algo_kwargs['random_seed'] = ui_params.get('random_seed')
        
        if hasattr(self.ui, 'log_message'):
            algo_kwargs['log_callback'] = self.ui.log_message
            
        return algo_kwargs



