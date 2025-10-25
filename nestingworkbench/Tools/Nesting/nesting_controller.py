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
from ...datatypes.layout_object import create_layout_object
from .algorithms import shape_processor
from ...datatypes.shape_object import create_shape_object
from ...datatypes.sheet_object import create_sheet
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
        FreeCAD.Console.PrintMessage("\n--- NESTING START ---\n")
        start_time = time.time()
        if not self.doc:
            return

        # Check if a font is needed and has been selected
        font_path = getattr(self.ui, 'selected_font_path', None)
        if self.ui.add_labels_checkbox.isChecked() and not font_path:
            self.ui.status_label.setText("Error: Could not find a valid font file for labels.")
            return

        for obj in self.ui.hidden_originals:
            if hasattr(obj, "ViewObject"):
                obj.ViewObject.Visibility = False
        
        self.ui.status_label.setText("Preparing shapes...")
        QtGui.QApplication.processEvents()

        # --- Create the final LayoutObject FIRST ---
        FreeCAD.Console.PrintMessage("1. Creating LayoutObject...\n")
        base_name, i = "Layout", 0
        existing_labels = [o.Label for o in self.doc.Objects]
        while f"{base_name}_{i:03d}" in existing_labels:
            i += 1
        layout_name = f"{base_name}_{i:03d}"

        layout_obj = create_layout_object(layout_name)
        layout_proxy = layout_obj.Proxy
        QtGui.QApplication.processEvents()
        # Ensure the new layout group is visible by default.
        if hasattr(layout_obj, "ViewObject"):
            layout_obj.ViewObject.Visibility = True

        # --- Prepare Parts and Master Shapes ---
        # This creates the in-memory representations of the parts to be nested
        # and the master shapes that will be drawn.
        parts_to_nest, master_shape_wrappers = self._prepare_parts_from_ui(self.ui.part_spacing_input.value(), self.ui.boundary_resolution_input.value())

        if not parts_to_nest:
            self.ui.status_label.setText("Error: No valid parts to nest.")
            return

        # --- Create the hidden MasterShapes group directly, before nesting ---
        master_shapes_group = self.doc.addObject("App::DocumentObjectGroup", "MasterShapes")
        layout_obj.addObject(master_shapes_group)

        for shape_wrapper in master_shape_wrappers:
            original_obj = shape_wrapper.source_freecad_object
            # Create a ShapeObject to store as the master, leaving the original untouched.
            master_obj = create_shape_object(f"master_{original_obj.Label.replace('master_', '').replace('nested_', '')}")
            
            # The shape_bounds.source_centroid contains the necessary offset to align
            # the shape with its origin-centered boundary polygon.
            shape_copy = original_obj.Shape.copy()
            if shape_wrapper.shape_bounds and hasattr(shape_wrapper.shape_bounds, 'source_centroid') and shape_wrapper.shape_bounds.source_centroid:
                shape_copy.translate(-shape_wrapper.shape_bounds.source_centroid) # This is the correct alignment logic.
            master_obj.Shape = shape_copy

            master_shapes_group.addObject(master_obj)

            # Now, draw the bounds for this master shape and link them.
            # The bounds are drawn at the document origin (0,0,0) as they are just for reference.
            if shape_wrapper.shape_bounds and shape_wrapper.shape_bounds.polygon:
                # We pass the master_shapes_group to ensure the boundary object is added to it.
                boundary_obj = shape_wrapper.draw_bounds(self.doc, FreeCAD.Vector(0,0,0), master_shapes_group)
                if boundary_obj:
                    # The 'BoundaryObject' and 'ShowBounds' properties are already added by the ShapeObject class.
                    # We just need to set their values.
                    master_obj.BoundaryObject = boundary_obj
                    master_obj.ShowBounds = False # Master shape bounds should always be off by default.
                    if hasattr(boundary_obj, "ViewObject"):
                        boundary_obj.ViewObject.Visibility = False

            # --- Create Label for Master Shape ---
            if self.ui.add_labels_checkbox.isChecked() and Draft and self.ui.selected_font_path and hasattr(shape_wrapper, 'label_text') and shape_wrapper.label_text:
                label_obj = self.doc.addObject("Part::Feature", f"label_master_{original_obj.Label.replace('master_', '')}")
                
                shapestring_geom = Draft.make_shapestring(
                    String=shape_wrapper.label_text,
                    FontFile=self.ui.selected_font_path,
                    Size=self.ui.part_spacing_input.value() * 0.6
                )
                label_obj.Shape = shapestring_geom.Shape
                self.doc.removeObject(shapestring_geom.Name)

                shapestring_bb = label_obj.Shape.BoundBox
                shapestring_center = shapestring_bb.Center
                master_shape_center = master_obj.Shape.BoundBox.Center
                master_shape_center.z += self.ui.label_height_input.value()
                label_placement_base = master_shape_center - shapestring_center
                label_obj.Placement = FreeCAD.Placement(label_placement_base, FreeCAD.Rotation())

                master_shapes_group.addObject(label_obj)
                # The 'LabelObject' property is already added by the ShapeObject class.
                master_obj.LabelObject = label_obj
                if hasattr(label_obj, "ViewObject"):
                    label_obj.ViewObject.Visibility = self.ui.add_labels_checkbox.isChecked()
        
        # Hide the group itself
        if FreeCAD.GuiUp and hasattr(master_shapes_group, "ViewObject"):
            master_shapes_group.ViewObject.Visibility = False
        # self.doc.recompute() # This is not needed as we are just creating simple objects. The final recompute will handle everything.

        # --- Create a temporary, visible group of the parts to be placed for debugging ---
        parts_to_place_group = self.doc.addObject("App::DocumentObjectGroup", "PartsToPlace")
        layout_obj.addObject(parts_to_place_group)
        for part_instance in parts_to_nest:
            # Find the corresponding master object in the MasterShapes group
            master_label = f"master_{part_instance.source_freecad_object.Label.replace('master_', '').replace('nested_', '')}"
            master_obj = master_shapes_group.getObject(master_label)
            
            if master_obj:
                # Create a copy of the master shape and its boundary for this instance
                part_copy = self.doc.copyObject(master_obj, False)
                part_copy.Label = f"part_{part_instance.id}"
                parts_to_place_group.addObject(part_copy)
                # Link the physical FreeCAD object back to the in-memory Shape object.
                part_instance.fc_object = part_copy
                
                if master_obj.BoundaryObject:
                    bounds_copy = self.doc.copyObject(master_obj.BoundaryObject, False)
                    bounds_copy.Label = f"bounds_{part_instance.id}"
                    parts_to_place_group.addObject(bounds_copy)
                    part_copy.BoundaryObject = bounds_copy # Link the bound to the part copy
                    # Make the bounds visible during the nesting process
                    if hasattr(bounds_copy, "ViewObject"):
                        bounds_copy.ViewObject.Visibility = True
        
        self.ui.status_label.setText("Running nesting algorithm...")
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
            'sheet_w': self.ui.sheet_width_input.value(),
            'sheet_h': self.ui.sheet_height_input.value(),
            'spacing': spacing,
            'font_path': self.ui.selected_font_path,
            'show_bounds': self.ui.show_bounds_checkbox.isChecked(),
            'add_labels': self.ui.add_labels_checkbox.isChecked(),
            'label_height': self.ui.label_height_input.value(), # This widget is in NestingPanel
        }

        try:
            sheets, remaining_parts_to_nest, total_steps = nest(
                parts_to_nest,
                self.ui.sheet_width_input.value(), self.ui.sheet_height_input.value(),
                global_rotation_steps, algorithm,
                **algo_kwargs
            )
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
            sheet_origin = sheet.get_origin(spacing)
            sheet.draw(
                self.doc,
                sheet_origin,
                self.last_run_ui_params,
                layout_obj # The parent group is the layout object itself
            )
        
        # Now that the recompute is finished and parts are moved, it is safe to remove the empty group.
        self.doc.removeObject(parts_to_place_group.Name)
        
        placed_count = sum(len(s) for s in sheets)
        status_text = f"Placed {placed_count} shapes on {len(sheets)} sheets."

        if remaining_parts_to_nest:
            status_text += f" Could not place {len(remaining_parts_to_nest)} shapes."
        
        # Calculate fill percentage for the status message
        # by calling the new method on the layout_controller instance.
        sheet_fills = layout_proxy.calculate_sheet_fills()
        
        end_time = time.time()
        duration = end_time - start_time
        status_text += f" (Took {duration:.2f} seconds)."
        self.ui.status_label.setText(status_text)

        if self.ui.sound_checkbox.isChecked(): QtGui.QApplication.beep()

    def toggle_bounds_visibility(self):
        """Toggles the 'ShowBounds' property on all nested ShapeObjects in the document."""
        is_visible = self.ui.show_bounds_checkbox.isChecked()
        
        # Find all ShapeObjects in the document and toggle their property.
        # The ShapeObject's onChanged method will handle the visibility change.
        toggled_count = 0
        for obj in self.doc.Objects:
            # Only toggle bounds for the final nested parts, which are prefixed with "nested_".
            # Master shapes are prefixed with "master_" and will be ignored.
            if obj.Label.startswith("nested_") and hasattr(obj, "ShowBounds"):
                obj.ShowBounds = is_visible
                toggled_count += 1
        
        self.ui.status_label.setText(f"Toggled bounds visibility for {toggled_count} shapes.")

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
        unique_master_shape_wrappers = [] # To store the unique Shape objects for the MasterShapes group
        
        # --- Step 1: Create a master Shape object for each unique part and generate its bounds once. ---
        master_shape_map = {}
        for label, master_obj in master_shapes_from_ui.items():
            try:
                master_shape_instance = Shape(master_obj)
                master_shape_instance.generate_bounds(shape_processor, spacing, boundary_resolution)
                master_shape_map[label] = master_shape_instance
            except Exception as e:
                FreeCAD.Console.PrintError(f"Could not create boundary for '{master_obj.Label}', it will be skipped. Error: {e}\n")
                continue
        
        # This list contains one unique, processed Shape object for each master part.
        unique_master_shape_wrappers = list(master_shape_map.values())

        # --- Step 2: Create deep copies of the master shapes for the nesting algorithm based on quantity. ---
        add_labels = self.ui.add_labels_checkbox.isChecked()
        font_path = getattr(self.ui, 'selected_font_path', None)

        # Iterate through the master shapes we just created.
        for label, master_shape_instance in master_shape_map.items():
            quantity, part_rotation_steps = quantities.get(label, (0, global_rotation_steps))
            for i in range(quantity):
                # For each quantity, create a deep copy. This is critical.
                shape_instance = copy.deepcopy(master_shape_instance)
                shape_instance.instance_num = i + 1
                shape_instance.id = f"{shape_instance.source_freecad_object.Label}_{shape_instance.instance_num}"
                shape_instance.rotation_steps = part_rotation_steps
                shape_instance.fc_object = None # Initialize link to physical object as None
                shape_instance.show_bounds = self.ui.show_bounds_checkbox.isChecked()

                # Store the label text to be created later, not the FreeCAD object itself.
                if add_labels and Draft and font_path:
                    shape_instance.label_text = shape_instance.id

                parts_to_nest.append(shape_instance)
        
        return parts_to_nest, unique_master_shape_wrappers
