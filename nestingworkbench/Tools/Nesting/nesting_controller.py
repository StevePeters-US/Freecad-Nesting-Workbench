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
from .layout_controller import LayoutController # This is a placeholder, will be replaced by the correct import
from .algorithms import shape_processor
from ...datatypes.shape import Shape

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
        self.preview_sheet_layouts = {} # Persistent state for preview
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

    def execute_nesting(self):
        """Main method to run the entire nesting process."""
        start_time = time.time()
        if not self.doc:
            return

        # Check if we are in edit mode
        selection = FreeCADGui.Selection.getSelection()
        edit_mode = False
        original_layout_name = None
        if selection:
            first_selected = selection[0]
            if first_selected.isDerivedFrom("App::DocumentObjectGroup") and first_selected.Label.startswith("Layout_"):
                edit_mode = True
                original_layout_name = first_selected.Label
                self.doc.removeObject(first_selected.Name)
                self.doc.recompute()

        # Check if a font is needed and has been selected
        font_path = getattr(self.ui, 'selected_font_path', None)
        if self.ui.add_labels_checkbox.isChecked() and not font_path:
            self.ui.status_label.setText("Error: Could not find a valid font file for labels.")
            return

        # --- Pre-emptive Cleanup -- - 
        # Remove all existing boundary objects from any previous run to prevent clutter.
        # This is more robust than relying on group-based cleanup.
        for obj in list(self.doc.Objects): # Iterate over a copy
            if obj.Label.startswith("bound_"):
                try: self.doc.removeObject(obj.Name)
                except Exception: pass
        
        # --- Robust Preview Cleanup -- - 
        # Directly find and delete any old preview group to ensure a clean state.
        # This is more reliable than relying on the UI's cleanup method.
        preview_group = self.doc.getObject(self.ui.preview_group_name)
        if preview_group:
            self.doc.removeObject(preview_group.Name)
            self.doc.recompute()
        self.ui.cleanup_preview()

        for obj in self.ui.hidden_originals:
            if hasattr(obj, "ViewObject"):
                obj.ViewObject.Visibility = False
        
        self.ui.status_label.setText("Preparing shapes...")
        QtGui.QApplication.processEvents()

        parts_to_nest = self._prepare_parts_from_ui(self.ui.part_spacing_input.value(), self.ui.boundary_resolution_input.value())

        if not parts_to_nest:
            self.ui.status_label.setText("Error: No valid parts to nest.")
            return

        self.ui.status_label.setText("Running nesting algorithm...")
        QtGui.QApplication.processEvents()
        
        sheet_w = self.ui.sheet_width_input.value()
        sheet_h = self.ui.sheet_height_input.value()
        spacing = self.ui.part_spacing_input.value()
        algorithm = self.ui.algorithm_dropdown.currentText()

        algo_kwargs = {}
        if algorithm == 'Gravity':
            if self.ui.gravity_random_checkbox.isChecked():
                # Let the packer handle generating a random vector
                algo_kwargs['gravity_direction'] = None 
            else:
                # Convert dial angle to a direction vector
                # User wants 0=Down, 90=Right, 180=Up, 270=Left.
                # We use (270 - angle) to map the dial value to the standard math unit circle.
                angle_deg = (270 - self.ui.gravity_direction_dial.value()) % 360
                angle_rad = math.radians(angle_deg)
                algo_kwargs['gravity_direction'] = (math.cos(angle_rad), math.sin(angle_rad))

            algo_kwargs['step_size'] = self.ui.gravity_step_size_input.value() # Maps to BaseNester's step_size
            algo_kwargs['anneal_rotate_enabled'] = self.ui.anneal_rotate_checkbox.isChecked() # This widget is in NestingPanel
            algo_kwargs['anneal_translate_enabled'] = self.ui.anneal_translate_checkbox.isChecked() # This widget is in NestingPanel
            algo_kwargs['anneal_random_shake_direction'] = self.ui.anneal_random_shake_checkbox.isChecked() # This widget is in NestingPanel
            algo_kwargs['max_spawn_count'] = self.ui.gravity_max_spawn_input.value()
            algo_kwargs['anneal_steps'] = self.ui.gravity_anneal_steps_input.value() # This widget is in NestingPanel
            algo_kwargs['max_nesting_steps'] = self.ui.gravity_max_nesting_steps_input.value() # This widget is in NestingPanel

        if algorithm == 'Genetic':
            algo_kwargs['population_size'] = self.ui.genetic_population_size_input.value()
            algo_kwargs['generations'] = self.ui.genetic_generations_input.value()
            # Could add mutation rate to UI later if needed

        self.preview_sheet_layouts.clear() # Reset preview state for new run

        # --- Prepare UI parameters for controllers ---
        global_rotation_steps = self.ui.rotation_steps_spinbox.value() # This widget is in NestingPanel
        self.last_run_ui_params = {
            'sheet_w': sheet_w,
            'sheet_h': sheet_h,
            'spacing': spacing,
            'font_path': self.ui.selected_font_path,
            'show_bounds': self.ui.show_bounds_checkbox.isChecked(),
            'add_labels': self.ui.add_labels_checkbox.isChecked(),
            'label_height': self.ui.label_height_input.value(), # This widget is in NestingPanel
            'edit_mode': edit_mode,
            'original_layout_name': original_layout_name
        }
        # Explicitly reset the preview controller and its state for the new run.
        self.preview_sheet_layouts.clear()
        preview_controller = LayoutController(self.doc, [], self.last_run_ui_params, self.ui.preview_group_name)
        preview_controller.draw_preview({}, self.last_run_ui_params) # Call with empty dict to ensure a clean state

        # --- Preview Callback ---
        def full_layout_preview_callback(sheet_layouts_dict, moving_part=None, current_sheet_id=None, grid_info=None):
            # Update the persistent state with the new data
            self.preview_sheet_layouts.update(sheet_layouts_dict)
            # On every animation frame, update the UI parameters in case they changed (e.g., grid resolution slider)
            self.last_run_ui_params['show_bounds'] = self.ui.show_bounds_checkbox.isChecked()
            
            preview_controller.draw_preview(self.preview_sheet_layouts, self.last_run_ui_params, moving_part, current_sheet_id, grid_info) # This is a placeholder, will be replaced by the correct import

        update_callback = full_layout_preview_callback if self.ui.animate_nesting_checkbox.isChecked() else None # This widget is in NestingPanel

        try:
            sheets, remaining_parts_to_nest, total_steps = nest(
                parts_to_nest,
                sheet_w, sheet_h,
                global_rotation_steps, algorithm,
                update_callback=update_callback,
                **algo_kwargs
            )
        except NestingDependencyError as e:
            self.ui.status_label.setText(f"Error: {e}")
            # The dialog is already shown by nesting_logic, so we just stop.
            return

        # Store the results for later use (e.g., by the bounds toggle)
        self.last_run_sheets = sheets
        self.last_run_unplaced_parts = remaining_parts_to_nest

        # Draw the final state of the preview if animation was off
        if not self.ui.animate_nesting_checkbox.isChecked(): # This widget is in NestingPanel
            preview_layouts = {s.id: [p.shape.shape_bounds for p in s.parts] for s in sheets}
            preview_controller.draw_preview(preview_layouts, self.last_run_ui_params)
        QtGui.QApplication.processEvents()

        # Final cleanup before drawing the final layout
        self.ui.cleanup_preview()

        # The LayoutController now handles all aspects of creating the final layout objects
        layout_controller = LayoutController(self.doc, sheets, self.last_run_ui_params, unplaced_parts=remaining_parts_to_nest) 

        placed_count = sum(len(s) for s in sheets)
        status_text = f"Placed {placed_count} shapes on {len(sheets)} sheets."

        if remaining_parts_to_nest:
            status_text += f" Could not place {len(remaining_parts_to_nest)} shapes."
        
        # Calculate fill percentage for the status message
        # by calling the new method on the layout_controller instance.
        sheet_fills = layout_controller.calculate_sheet_fills()
        
        if sheets:
            avg_fill = sum(sheet_fills) / len(sheet_fills)
            fills_str = ", ".join([f"{fill:.2f}%" for fill in sheet_fills])
            status_text += f" (Sheet fills: {fills_str}; Avg: {avg_fill:.2f}%)"
        
        end_time = time.time()
        duration = end_time - start_time
        status_text += f" (Took {duration:.2f} seconds)."
        self.ui.status_label.setText(status_text)
        
        if self.ui.sound_checkbox.isChecked():
            QtGui.QApplication.beep() # type: ignore
        
        # Finally, draw the layout
        layout_controller.draw()

    def toggle_bounds_visibility(self):
        """Toggles the visibility of boundary objects by creating or deleting them directly."""
        if not self.doc:
            return

        if not self.last_run_sheets:
            self.ui.status_label.setText("No layout data found. Please run nesting first.")
            return
        
        is_visible = self.ui.show_bounds_checkbox.isChecked()

        # Update the data model first, so it's consistent for any future operations.
        for sheet in self.last_run_sheets:
            for part in sheet.parts:
                part.shape.set_bounds_visibility(is_visible)

        if is_visible:
            # Create the bounds if they don't exist.
            self.ui.status_label.setText("Creating boundary objects...")
            spacing = self.last_run_ui_params.get('spacing', 0)
            for sheet in self.last_run_sheets:
                sheet_origin = sheet.get_origin(spacing)
                # Find the correct group to add the bounds to.
                objects_group = self.doc.getObject(f"Objects_{sheet.id+1}")
                if not objects_group: continue

                for placed_part in sheet.parts:
                    shape = placed_part.shape
                    bound_obj_name = f"bound_{shape.id}"
                    # Only draw if the bound object doesn't already exist.
                    if not self.doc.getObject(bound_obj_name):
                        if shape.shape_bounds and shape.shape_bounds.polygon:
                            shape.draw_bounds(self.doc, sheet_origin, objects_group)
            self.ui.status_label.setText("Boundary objects are now visible.")
        else:
            # Find and delete all existing boundary objects.
            self.ui.status_label.setText("Removing boundary objects...")
            bounds_to_delete = [obj for obj in self.doc.Objects if obj.Label.startswith("bound_")]
            for obj in bounds_to_delete:
                try:
                    self.doc.removeObject(obj.Name)
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"Could not remove boundary object {obj.Label}: {e}\n")
            self.ui.status_label.setText("Boundary objects have been hidden.")

        self.doc.recompute()

    def _prepare_parts_from_ui(self, spacing, boundary_resolution):
        """Reads the UI table and creates a list of Shape objects to be nested."""
        global_rotation_steps = self.ui.rotation_steps_spinbox.value() # This widget is in NestingPanel
        quantities = {}
        for row in range(self.ui.shape_table.rowCount()):
            try:
                label = self.ui.shape_table.item(row, 0).text()
                quantity = self.ui.shape_table.cellWidget(row, 1).value()
                # The widget in column 2 is a QWidget containing a layout with a spinbox
                rotation_widget = self.ui.shape_table.cellWidget(row, 2) # type: ignore
                rotation_value = rotation_widget.findChild(QtGui.QSpinBox).value() # This widget is in NestingPanel
                override_enabled = self.ui.shape_table.cellWidget(row, 3).isChecked()
                
                # Centralize rotation logic here. The nester will use part.rotation_steps directly.
                part_rotation_steps = rotation_value if override_enabled else global_rotation_steps
                quantities[label] = (quantity, part_rotation_steps)
            except (ValueError, AttributeError):
                FreeCAD.Console.PrintWarning(f"Skipping row {row} due to invalid data.\n")
                continue

        master_shapes_from_ui = {obj.Label: obj for obj in self.ui.selected_shapes_to_process if obj.Label in quantities}
        
        parts_to_nest = [] # This will be a list of disposable Shape object copies
        
        # --- Label Creation ---
        add_labels = self.ui.add_labels_checkbox.isChecked()
        font_path = getattr(self.ui, 'selected_font_path', None)

        for label, master_obj in master_shapes_from_ui.items():
            # For each unique shape, create a master Shape object with its bounds calculated once.
            try:
                master_shape_instance = Shape(master_obj, instance_num=1)
                bounds = shape_processor.create_single_nesting_part(
                    master_obj, 
                    spacing, 
                    boundary_resolution
                )
                master_shape_instance.set_shape_bounds(bounds)
            except Exception as e:
                FreeCAD.Console.PrintError(f"Could not create boundary for '{master_obj.Label}', it will be skipped. Error: {e}\n")
                continue

            # Then, create the required number of disposable copies for the nesting algorithm.
            quantity, part_rotation_steps = quantities.get(label, (0, global_rotation_steps))
            for i in range(quantity):
                shape_instance = copy.deepcopy(master_shape_instance)
                shape_instance.instance_num = i + 1
                shape_instance.id = f"{shape_instance.source_freecad_object.Label}_{shape_instance.instance_num}"
                shape_instance.rotation_steps = part_rotation_steps
                shape_instance.set_bounds_visibility(self.ui.show_bounds_checkbox.isChecked())

                if add_labels and Draft and font_path:
                    # Store the label text, not the FreeCAD object itself.
                    # The FreeCAD object will be created during the final drawing phase.
                    shape_instance.label_text = shape_instance.id

                parts_to_nest.append(shape_instance)
        
        return parts_to_nest
