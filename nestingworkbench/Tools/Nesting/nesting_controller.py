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

        # Persistent cache for processed shape geometry across runs.
        # Format: { (object_name, spacing, boundary_resolution) : ShapeObject }
        self.processed_shape_cache = {}

    def execute_nesting(self):
        """Main method to run the entire nesting process."""
        FreeCAD.Console.PrintMessage("\n--- NESTING START ---\n")
        
        # Conditionally clear the NFP cache
        if hasattr(self.ui, 'clear_cache_checkbox') and self.ui.clear_cache_checkbox.isChecked():
            FreeCAD.Console.PrintMessage("Clearing NFP and Geometry caches...\n")
            Shape.nfp_cache.clear()
            self.processed_shape_cache.clear()
        
        start_time = time.time()
        if not self.doc:
            return

        # Hide previous layouts
        # Also hide any leftover temporary groups from previous runs.
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

        for obj in self.ui.hidden_originals:
            if hasattr(obj, "ViewObject"):
                obj.ViewObject.Visibility = False
        
        self.ui.status_label.setText("Preparing shapes...")
        QtGui.QApplication.processEvents()

        # --- Create or Retrieve the LayoutObject ---
        FreeCAD.Console.PrintMessage(f"   [TIMING] Prep & UI: {time.time() - start_time:.4f}s\n")
        step_start_time = time.time()
        
        FreeCAD.Console.PrintMessage("1. Creating/Updating LayoutObject...\n")
        
        # 0. Check if UI already has a tracked layout (e.g. from previous run)
        layout_obj = getattr(self.ui, 'current_layout', None)
        
        # 1. Helper to traverse up: master_shape -> master_container -> MasterShapes -> Layout
        if not layout_obj and self.ui.selected_shapes_to_process and self.ui.selected_shapes_to_process[0].Label.startswith("master_shape_"):
            try:
                first_shape = self.ui.selected_shapes_to_process[0]
                if first_shape.InList:
                    master_container = first_shape.InList[0]
                    if master_container.InList:
                        master_group = master_container.InList[0]
                        if master_group.InList and master_group.Label == "MasterShapes":
                             layout_candidate = master_group.InList[0]
                             if layout_candidate.Label.startswith("Layout_"):
                                 layout_obj = layout_candidate
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"   -> Could not determine parent layout: {e}\n")

        if layout_obj:
             FreeCAD.Console.PrintMessage(f"   -> Updating existing layout: {layout_obj.Label}\n")
             # Clean up old results. We ONLY remove 'PartsToPlace' here. 
             # We rely on Sheet.draw reusing existing 'Sheet_X' groups, 
             # and we will delete any excess sheets at the end.
             for child in list(layout_obj.Group):
                 if child.Label == "PartsToPlace":
                     self.doc.removeObject(child.Name)
        else:
            base_name, i = "Layout", 0
            existing_labels = [o.Label for o in self.doc.Objects]
            while f"{base_name}_{i:03d}" in existing_labels:
                i += 1
            layout_name = f"{base_name}_{i:03d}"
            layout_obj = self.doc.addObject("App::DocumentObjectGroup", layout_name)
        
        # Track this layout in the UI so subsequent clicks reuse it
        self.ui.current_layout = layout_obj
        # --- Store UI Parameters on the Layout Object ---
        def set_or_add_property(obj, prop_type, prop_name, prop_group, prop_desc, value):
            if not hasattr(obj, prop_name):
                 obj.addProperty(prop_type, prop_name, prop_group, prop_desc)
            setattr(obj, prop_name, value)

        set_or_add_property(layout_obj, "App::PropertyLength", "SheetWidth", "Layout", "Width of the nested sheets", self.ui.sheet_width_input.value())
        set_or_add_property(layout_obj, "App::PropertyLength", "SheetHeight", "Layout", "Height of the nested sheets", self.ui.sheet_height_input.value())
        set_or_add_property(layout_obj, "App::PropertyLength", "PartSpacing", "Layout", "Spacing between parts", self.ui.part_spacing_input.value())
        set_or_add_property(layout_obj, "App::PropertyFile", "FontFile", "Layout", "Font file used for labels", self.ui.selected_font_path)
        set_or_add_property(layout_obj, "App::PropertyBool", "ShowBounds", "Layout", "Visibility of part boundaries", self.ui.show_bounds_checkbox.isChecked())
        set_or_add_property(layout_obj, "App::PropertyBool", "AddLabels", "Layout", "Whether part labels are enabled", self.ui.add_labels_checkbox.isChecked())
        set_or_add_property(layout_obj, "App::PropertyLength", "LabelHeight", "Layout", "Height of the part labels", self.ui.label_height_input.value())
        set_or_add_property(layout_obj, "App::PropertyBool", "IsStacked", "Layout", "Whether sheets are stacked", False)

        QtGui.QApplication.processEvents()
        # Ensure the new layout group is visible by default.
        if FreeCAD.GuiUp and hasattr(layout_obj, "ViewObject"): layout_obj.ViewObject.Visibility = True

        # --- Create a temporary, visible group for the parts to be placed ---
        # This must be created BEFORE calling _prepare_parts_from_ui.
        parts_to_place_group = self.doc.addObject("App::DocumentObjectGroup", "PartsToPlace")
        layout_obj.addObject(parts_to_place_group)

        # --- Prepare Parts and Master Shapes ---
        parts_to_nest = self._prepare_parts_from_ui(
            self.ui.part_spacing_input.value(),
            self.ui.boundary_resolution_input.value(),
            layout_obj # Pass the layout group to add master shapes to.
        )
        
        FreeCAD.Console.PrintMessage(f"   [TIMING] Shape Processing: {time.time() - step_start_time:.4f}s\n")
        step_start_time = time.time()

        if not parts_to_nest:
            self.ui.status_label.setText("Error: No valid parts to nest.")
            self.doc.removeObject(layout_obj.Name) # Clean up the empty layout group
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

        # Add the UI logger callback
        if hasattr(self.ui, 'log_message'):
            algo_kwargs['log_callback'] = self.ui.log_message

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

    def _prepare_parts_from_ui(self, spacing, boundary_resolution, layout_obj):
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

        # Determine if we are reloading a layout. This is true if the first selected object is a master shape.
        is_reloading = False
        if self.ui.selected_shapes_to_process and self.ui.selected_shapes_to_process[0].Label.startswith("master_shape_"):
            is_reloading = True

        # If reloading, only use the master shapes from the selected layout.
        # Otherwise, use the original user-selected shapes.
        master_shapes_from_ui = {obj.Label: obj for obj in self.ui.selected_shapes_to_process if obj.Label in quantities and (not is_reloading or obj.Label.startswith("master_shape_"))}

        # --- Create or Retrieve the hidden MasterShapes group ---
        master_shapes_group = None
        for child in layout_obj.Group:
             if child.Label == "MasterShapes":
                 master_shapes_group = child
                 break
        
        if not master_shapes_group:
            master_shapes_group = self.doc.addObject("App::DocumentObjectGroup", "MasterShapes")
            layout_obj.addObject(master_shapes_group)
        # The master shapes group is for internal reference and should not be visible.
        if hasattr(master_shapes_group, "ViewObject"):
            master_shapes_group.ViewObject.Visibility = False

        parts_to_nest = []
        master_shape_obj_map = {} # Maps original FreeCAD object ID to the new master ShapeObject
        master_geometry_cache = {} # Maps original FreeCAD object ID to the processed Shape wrapper

        # This list will hold tuples of (master_container, temp_shape_wrapper) to be sorted later.
        masters_to_place = []

        # --- Variables for placing master shapes in a line ---
        master_placement_x = 0
        max_master_height = 0

        # --- Step 1: Create the FreeCAD "master" objects for each unique part. ---
        for label, master_obj in master_shapes_from_ui.items():
            try:
                # Cache Key: (Object Name, Spacing, Resolution)
                # We use Name because it is unique and persistent.
                cache_key = (master_obj.Name, spacing, boundary_resolution)
                
                # Check if we represent a reloading state
                is_reloading = master_obj.Label.startswith("master_shape_")
                
                temp_shape_wrapper = None
                
                # Check Cache
                if cache_key in self.processed_shape_cache:
                    # FreeCAD.Console.PrintMessage(f"DEBUG: Geometry cache HIT for '{label}'\n")
                    temp_shape_wrapper = copy.deepcopy(self.processed_shape_cache[cache_key])
                    # Update the source object reference to the current live object
                    temp_shape_wrapper.source_freecad_object = master_obj
                else:
                    # FreeCAD.Console.PrintMessage(f"DEBUG: Geometry cache MISS for '{label}'\n")
                    pass
                
                if is_reloading:
                    # We are reloading. The master_obj is the ShapeObject. Its container is the master_container.
                    master_shape_obj = master_obj
                    master_container = master_shape_obj.InList[0]
                    
                    # Update quantity on existing container
                    quantity, _ = quantities.get(label.replace("master_shape_", ""), (1, 1))
                    
                    if hasattr(master_container, "Quantity"):
                        master_container.Quantity = quantity
                    else:
                        master_container.addProperty("App::PropertyInteger", "Quantity", "Nest", "Number of instances").Quantity = quantity

                    # If not in cache, process now
                    if not temp_shape_wrapper:
                        temp_shape_wrapper = Shape(master_shape_obj)
                        shape_processor.create_single_nesting_part(temp_shape_wrapper, master_shape_obj, spacing, boundary_resolution)
                        self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)
                        
                else:
                    # This is a fresh run. Create new master objects.
                    if not temp_shape_wrapper:
                        temp_shape_wrapper = Shape(master_obj)
                        shape_processor.create_single_nesting_part(temp_shape_wrapper, master_obj, spacing, boundary_resolution)
                        self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)

                    master_container = self.doc.addObject("App::Part", f"master_{label}")
                    
                    # Store quantity on the container for later retrieval
                    quantity, _ = quantities.get(label, (1, 1))
                    master_container.addProperty("App::PropertyInteger", "Quantity", "Nest", "Number of instances").Quantity = quantity

                    # Hide the master container immediately upon creation.
                    if hasattr(master_container, "ViewObject"):
                        master_container.ViewObject.Visibility = False
                    master_shape_obj = create_shape_object(f"master_shape_{label}")
                    master_shape_obj.Shape = master_obj.Shape.copy()

                    if temp_shape_wrapper.source_centroid:
                        master_shape_obj.Placement = FreeCAD.Placement(temp_shape_wrapper.source_centroid.negative(), FreeCAD.Rotation())
                    master_container.addObject(master_shape_obj)

                    if temp_shape_wrapper.polygon:
                        boundary_obj = temp_shape_wrapper.draw_bounds(self.doc, FreeCAD.Vector(0,0,0), None)
                        if boundary_obj:
                            master_container.addObject(boundary_obj)
                            boundary_obj.Placement = FreeCAD.Placement()
                            master_shape_obj.BoundaryObject = boundary_obj
                            master_shape_obj.ShowBounds = False
                            if hasattr(boundary_obj, "ViewObject"): boundary_obj.ViewObject.Visibility = False
                
                master_shapes_group.addObject(master_container)
                master_shape_obj_map[id(master_obj)] = master_shape_obj
                master_geometry_cache[id(master_obj)] = temp_shape_wrapper
                masters_to_place.append((master_container, temp_shape_wrapper))

            except Exception as e:
                FreeCAD.Console.PrintError(f"Could not create boundary for '{master_obj.Label}', it will be skipped. Error: {e}\n")
                continue
        
        # --- Step 1.5: Sort masters by area (largest first) and position them in a line ---
        masters_to_place.sort(key=lambda item: item[1].area, reverse=True)

        # Calculate max height from all sorted masters
        if masters_to_place:
            max_master_height = max(item[1].bounding_box()[3] for item in masters_to_place if item[1].polygon)

        current_x = 0
        y_offset = -max_master_height - spacing * 4 # Place masters below the sheets area
        for container, shape_wrapper in masters_to_place:
            container.Placement = FreeCAD.Placement(FreeCAD.Vector(current_x, y_offset, 0), FreeCAD.Rotation())
            # Use the bounding box of the container's contents for the next position.
            # This ensures even spacing regardless of the shape's internal origin.
            current_x += container.Shape.BoundBox.XLength + spacing * 2

        # --- Step 2: Create in-memory Shape instances and temporary FreeCAD copies for nesting. ---
        add_labels = self.ui.add_labels_checkbox.isChecked()
        font_path = getattr(self.ui, 'selected_font_path', None)
        parts_to_place_group = self.doc.getObject("PartsToPlace")

        for label, original_obj in master_shapes_from_ui.items():
            quantity, part_rotation_steps = quantities.get(label, (0, global_rotation_steps))
            master_shape_obj = master_shape_obj_map.get(id(original_obj))
            master_wrapper = master_geometry_cache.get(id(original_obj))
            
            if not master_shape_obj or not master_wrapper: continue

            for i in range(quantity):
                # Create the in-memory Shape object for the algorithm.
                shape_instance = Shape(original_obj) # Source is the original user-selected object
                
                # Copy geometric properties from the cached master wrapper instead of re-calculating
                shape_instance.polygon = master_wrapper.polygon
                shape_instance.original_polygon = master_wrapper.original_polygon
                shape_instance.unbuffered_polygon = master_wrapper.unbuffered_polygon
                shape_instance.source_centroid = master_wrapper.source_centroid
                shape_instance.spacing = spacing
                
                shape_instance.instance_num = i + 1
                shape_instance.id = f"{shape_instance.source_freecad_object.Label}_{shape_instance.instance_num}"
                shape_instance.rotation_steps = part_rotation_steps

                # Create a temporary FreeCAD object copy for this instance and link it.
                part_copy = self.doc.copyObject(master_shape_obj, True)
                part_copy.Label = f"part_{shape_instance.id}"
                parts_to_place_group.addObject(part_copy)
                shape_instance.fc_object = part_copy

                # Apply the master's final placement to the temporary copy.
                # This ensures the simulation starts with parts in the correct initial positions.
                if master_shape_obj.InList:
                    master_container = master_shape_obj.InList[0]
                    part_copy.Placement = master_shape_obj.getGlobalPlacement() # This is correct for the shape proxy
                    # Also update the placement of the boundary object for correct simulation display.
                    # The boundary's geometry is centered, so its placement should match the master container's placement.
                    if hasattr(part_copy, 'BoundaryObject') and part_copy.BoundaryObject:
                        part_copy.BoundaryObject.Placement = master_container.getGlobalPlacement()

                # Set visibility for simulation.
                part_copy.ShowBounds = True
                part_copy.ShowShape = False

                # Store the label text to be created later, not the FreeCAD object itself.
                if add_labels and Draft and font_path:
                    shape_instance.label_text = shape_instance.id

                parts_to_nest.append(shape_instance)

        return parts_to_nest
