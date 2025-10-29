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

        # Hide previous layouts
        for obj in self.doc.Objects:
            if obj.Name.startswith("Layout_"):
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.Visibility = False

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

        layout_obj = self.doc.addObject("App::DocumentObjectGroup", layout_name)
        # --- Store UI Parameters on the Layout Object ---
        layout_obj.addProperty("App::PropertyLength", "SheetWidth", "Layout", "Width of the nested sheets").SheetWidth = self.ui.sheet_width_input.value()
        layout_obj.addProperty("App::PropertyLength", "SheetHeight", "Layout", "Height of the nested sheets").SheetHeight = self.ui.sheet_height_input.value()
        layout_obj.addProperty("App::PropertyLength", "PartSpacing", "Layout", "Spacing between parts").PartSpacing = self.ui.part_spacing_input.value()
        layout_obj.addProperty("App::PropertyFile", "FontFile", "Layout", "Font file used for labels").FontFile = self.ui.selected_font_path
        layout_obj.addProperty("App::PropertyBool", "ShowBounds", "Layout", "Visibility of part boundaries").ShowBounds = self.ui.show_bounds_checkbox.isChecked()
        layout_obj.addProperty("App::PropertyBool", "AddLabels", "Layout", "Whether part labels are enabled").AddLabels = self.ui.add_labels_checkbox.isChecked()
        layout_obj.addProperty("App::PropertyLength", "LabelHeight", "Layout", "Height of the part labels").LabelHeight = self.ui.label_height_input.value()

        QtGui.QApplication.processEvents()
        # Ensure the new layout group is visible by default.
        if FreeCAD.GuiUp and hasattr(layout_obj, "ViewObject"): layout_obj.ViewObject.Visibility = True

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

        # This map is crucial for robustly linking shape instances to their masters,
        # avoiding reliance on potentially non-unique or stale labels.
        master_obj_map = {}
        for shape_wrapper in master_shape_wrappers:
            original_obj = shape_wrapper.source_freecad_object

            # Create a container for the master shape, mirroring the final nested part structure.
            master_container = self.doc.addObject("App::Part", f"master_{original_obj.Label}")
            master_obj_map[id(original_obj)] = master_container
            master_shapes_group.addObject(master_container)

            # Create the master ShapeObject that will live inside the container.
            master_shape_obj = create_shape_object(f"master_shape_{original_obj.Label}")
            shape_copy = original_obj.Shape.copy()
            master_shape_obj.Shape = shape_copy
            
            # The shape object is placed inside the container, offset by its centroid.
            # This aligns its geometry's reference point (the centroid) with the container's origin.
            if shape_wrapper.source_centroid:
                master_shape_obj.Placement = FreeCAD.Placement(shape_wrapper.source_centroid.negative(), FreeCAD.Rotation())
            else:
                master_shape_obj.Placement = FreeCAD.Placement()
            master_container.addObject(master_shape_obj)

            # Now, draw the bounds for this master shape and link them.
            # The bounds are drawn at the document origin and placed inside the container without any offset.
            if shape_wrapper.polygon:
                # We pass the master_container as the group to add the boundary to.
                boundary_obj = shape_wrapper.draw_bounds(self.doc, FreeCAD.Vector(0,0,0), master_shapes_group)
                if boundary_obj:
                    master_container.addObject(boundary_obj) # Add the bounds to the container
                    boundary_obj.Placement = FreeCAD.Placement() # Place at container origin
                    master_shape_obj.BoundaryObject = boundary_obj # Link it to the shape object
                    master_shape_obj.ShowBounds = False # Master shape bounds should always be off by default.
                    if hasattr(boundary_obj, "ViewObject"):
                        boundary_obj.ViewObject.Visibility = False

            # --- Create Label for Master Shape ---
            if self.ui.add_labels_checkbox.isChecked() and Draft and self.ui.selected_font_path and hasattr(shape_wrapper, 'label_text') and shape_wrapper.label_text:
                # The logic for creating labels for the final parts is complex and tied to final placement.
                # Since master shapes are hidden and just templates, we can skip adding a label object
                # to them to simplify the structure. The label text is still stored on the in-memory
                # `shape_wrapper` and will be used when drawing the final nested parts.
                pass
        
        # Hide the group itself
        if FreeCAD.GuiUp and hasattr(master_shapes_group, "ViewObject"):
            master_shapes_group.ViewObject.Visibility = False
        # self.doc.recompute() # This is not needed as we are just creating simple objects. The final recompute will handle everything.

        # --- Create a temporary, visible group of the parts to be placed for debugging ---
        parts_to_place_group = self.doc.addObject("App::DocumentObjectGroup", "PartsToPlace")
        layout_obj.addObject(parts_to_place_group)
        FreeCAD.Console.PrintMessage(f"DEBUG: --- Linking FreeCAD objects to in-memory Shape objects --- \n")
        for part_instance in parts_to_nest:
            # Find the corresponding master object using the robust map.
            original_obj_id = id(part_instance.source_freecad_object)
            master_container = master_obj_map.get(original_obj_id)
            
            if master_container:
                # Create a copy of the master shape and its boundary for this instance
                FreeCAD.Console.PrintMessage(f"DEBUG: Creating copy for Shape '{part_instance.id}' (id={id(part_instance)}). Master container: '{master_container.Label}'\n")
                
                # Find the actual ShapeObject inside the master container to link to the part instance.
                master_shape_obj = next((child for child in master_container.Group if hasattr(child, "Proxy") and child.Proxy.__class__.__name__ == "ShapeObject"), None)
                if not master_shape_obj: continue

                # Create a copy of the master ShapeObject for this instance.
                part_copy = self.doc.copyObject(master_shape_obj, True) # Deep copy to get linked objects like bounds
                part_copy.Label = f"part_{part_instance.id}" # This is the ShapeObject
                parts_to_place_group.addObject(part_copy) # Add the copy to the transient group

                # Link the physical FreeCAD object back to the in-memory Shape object.
                part_instance.fc_object = part_copy
                FreeCAD.Console.PrintMessage(f"DEBUG:   Shape '{part_instance.id}' (id={id(part_instance)}) linked to FreeCAD object '{part_copy.Label}' (id={id(part_copy)}). fc_object is now {'set' if part_instance.fc_object else 'None'}.\n")

                # For simulation, we want to see the bounds, not the shape.
                # Set these properties on the temporary copy.
                part_copy.ShowBounds = True
                part_copy.ShowShape = False
        
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

        FreeCAD.Console.PrintMessage(f"DEBUG: --- State of parts_to_nest before calling nest() --- \n")
        for part in parts_to_nest:
            FreeCAD.Console.PrintMessage(f"DEBUG:   Part '{part.id}' (id={id(part)}). fc_object is {'set' if part.fc_object else 'None'}.\n")
        FreeCAD.Console.PrintMessage(f"DEBUG: ---------------------------------------------------- \n")

        is_simulating = self.ui.simulate_nesting_checkbox.isChecked()

        try:
            sheets, remaining_parts_to_nest, total_steps = nest(
                parts_to_nest,
                self.ui.sheet_width_input.value(), self.ui.sheet_height_input.value(),
                global_rotation_steps, algorithm, is_simulating,
                **algo_kwargs
            )

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
        
        # Now that the recompute is finished and parts are moved, it is safe to remove the empty group.
        self.doc.removeObject(parts_to_place_group.Name)
        
        placed_count = sum(len(s) for s in sheets)
        # FreeCAD.Console.PrintMessage(f"DEBUG: Successfully placed {placed_count} shapes.\n") # Revert this too

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
            FreeCAD.Console.PrintMessage(f"DEBUG: Preparing master shape for label '{label}'. Source FreeCAD object id: {id(master_obj)}\n")
            try:
                # This creates a new in-memory Shape object for the master.
                master_shape_instance = Shape(master_obj)
                shape_processor.create_single_nesting_part(master_shape_instance, master_obj, spacing, boundary_resolution)
                FreeCAD.Console.PrintMessage(f"Prepared master shape {label} with area {master_shape_instance.area}\n") # DEBUG
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
                FreeCAD.Console.PrintMessage(f"DEBUG: Deep copying master shape '{label}' (id={id(master_shape_instance)}) for instance {i+1}.\n")
                shape_instance = copy.deepcopy(master_shape_instance)
                shape_instance.instance_num = i + 1
                shape_instance.id = f"{shape_instance.source_freecad_object.Label}_{shape_instance.instance_num}"
                shape_instance.spacing = spacing # Explicitly set spacing on each instance
                shape_instance.rotation_steps = part_rotation_steps
                shape_instance.fc_object = None # Initialize link to physical object as None

                # Store the label text to be created later, not the FreeCAD object itself.
                if add_labels and Draft and font_path:
                    shape_instance.label_text = shape_instance.id

                parts_to_nest.append(shape_instance)

        return parts_to_nest, unique_master_shape_wrappers
