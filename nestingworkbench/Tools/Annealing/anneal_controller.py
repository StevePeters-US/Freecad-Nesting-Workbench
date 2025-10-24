# Nesting/nesting/anneal_controller.py

"""
This module contains the AnnealController, which handles the logic for
optimizing an existing layout using Simulated Annealing.
"""

import FreeCAD
import FreeCADGui
import time
import random
import math
import copy
from PySide import QtGui
from ..Nesting.algorithms import shape_processor

from ... import nesting_logic

from ..Nesting.nesting_controller import NestingController # For _draw_layout and _prepare_parts_from_ui

class AnnealController:
    """
    Handles the logic for reading an existing layout, running the annealing
    optimization, and updating the layout in the document.
    """
    def __init__(self, ui_panel, layout_group):
        self.ui = ui_panel
        self.doc = FreeCAD.ActiveDocument
        self.layout_group = layout_group
        self.nesting_controller = NestingController(self.ui) # Reuse drawing and other logic

    def execute_annealing(self):
        """Main method to run the annealing optimization process."""
        start_time = time.time()
        if not self.doc or not self.layout_group:
            self.ui.status_label.setText("Error: No valid layout group provided.")
            return

        self.ui.status_label.setText("Preparing layout for annealing...")
        QtGui.QApplication.processEvents()

        # Read parameters from the layout's spreadsheet
        params = self._get_params_from_spreadsheet()
        if not params:
            self.ui.status_label.setText("Error: Could not read layout parameters from spreadsheet.")
            return

        sheet_w, sheet_h, spacing = params['width'], params['height'], params['spacing']

        # Prepare parts from the existing layout
        try:
            fixed_parts, mobile_parts, all_fc_shapes = self._prepare_parts_from_layout(spacing)
        except ValueError as e:
            self.ui.status_label.setText(f"Error: {e}")
            return

        if self.ui.consolidate_checkbox.isChecked() and not mobile_parts:
            self.ui.status_label.setText("Consolidation selected, but no parts on the last sheet to move.")
            return
        
        if not self.ui.consolidate_checkbox.isChecked():
            mobile_parts.extend(fixed_parts)
            fixed_parts = []

        self.ui.status_label.setText("Running annealing optimization...")
        QtGui.QApplication.processEvents()

        # --- Run the SA algorithm directly ---
        optimized_parts, remaining_parts = self._run_annealing_on_sheet(
            mobile_parts, fixed_parts, sheet_w, sheet_h
        )

        # --- Reconstruct and Draw ---
        # The annealing process works on a single sheet. We need to reconstruct the full layout.
        # The `fixed_parts` already have their sheet_id set correctly.
        # The `optimized_parts` were placed on the last sheet.
        last_sheet_index = 0
        if fixed_parts:
            last_sheet_index = max(p.sheet_id for p in fixed_parts)
        
        for part in optimized_parts:
            part.sheet_id = last_sheet_index

        all_placed_nesting_parts = fixed_parts + optimized_parts

        # Group parts by sheet_id to build the final list of sheets
        final_sheets_map = {}
        for part in all_placed_nesting_parts:
            final_sheets_map.setdefault(part.sheet_id, []).append(part)
        final_sheets_list = [final_sheets_map[i] for i in sorted(final_sheets_map.keys())]
        
        # Create the map of original FC shapes for the drawing function
        shape_pool = {shape.id: shape for shape in all_fc_shapes}
        self.nesting_controller.shape_pool = shape_pool

        # Remove the old layout before drawing the new one
        original_layout_name = self.layout_group.Label
        self.doc.removeObject(self.layout_group.Name)
        self.doc.recompute()

        # The drawing function from NestingController can now be used
        self.nesting_controller._draw_layout(
            list(shape_pool.values()), all_placed_nesting_parts, 
            sheet_origins=[FreeCAD.Vector(i * (sheet_w + spacing), 0, 0) for i in range(len(final_sheets_list))],
            edit_mode=True, original_layout_name=original_layout_name
        )

        end_time = time.time()
        status_text = f"Annealing complete in {end_time - start_time:.2f}s. Placed {len(all_placed_nesting_parts)} parts on {len(final_sheets_list)} sheets."
        if remaining_parts:
            status_text += f" Failed to place {len(remaining_parts)} parts."
        self.ui.status_label.setText(status_text)

    def _get_params_from_spreadsheet(self):
        """Reads layout parameters from the spreadsheet inside the layout group."""
        spreadsheet = self.layout_group.getObject("LayoutParameters")
        if not spreadsheet: return None
        try:
            return {
                "width": float(spreadsheet.get('B2')),
                "height": float(spreadsheet.get('B3')),
                "spacing": float(spreadsheet.get('B4'))
            }
        except: return None

    def _run_annealing_on_sheet(self, parts_to_anneal, fixed_parts, sheet_w, sheet_h):
        """
        Runs the simulated annealing algorithm to pack parts_to_anneal onto a
        single sheet, avoiding collisions with fixed_parts.
        """
        if not parts_to_anneal:
            return [], []

        # --- Get SA parameters from UI ---
        temp_initial = self.ui.sa_temp_initial_input.value()
        temp_final = self.ui.sa_temp_final_input.value()
        cooling_rate = self.ui.sa_cooling_rate_input.value()
        max_temp_steps = self.ui.sa_substeps_input.value()
        total_max_iterations = self.ui.sa_total_max_iter_input.value()
        rotation_steps = self.ui.rotation_steps_input.value()

        # --- The main SA packing loop ---
        current_solution = self._get_random_solution(parts_to_anneal, sheet_w, sheet_h, rotation_steps)
        best_solution = copy.deepcopy(current_solution)
        current_cost = self._calculate_cost(current_solution, parts_to_anneal, fixed_parts, sheet_w, sheet_h)
        best_cost = current_cost
        temp = temp_initial

        total_iterations = 0
        while temp > temp_final and total_iterations < total_max_iterations:
            for _ in range(max_temp_steps):
                total_iterations += 1
                new_solution = self._get_random_neighbor(current_solution, temp, temp_initial, sheet_w, sheet_h, rotation_steps)
                new_cost = self._calculate_cost(new_solution, parts_to_anneal, fixed_parts, sheet_w, sheet_h)

                delta_cost = new_cost - current_cost
                if delta_cost < 0 or random.random() < math.exp(-delta_cost / temp):
                    current_solution = new_solution
                    current_cost = new_cost

                    if current_cost < best_cost:
                        best_solution = new_solution
                        best_cost = current_cost
            
            temp *= cooling_rate

        # --- Post-process to set final placements ---
        final_parts = self._get_parts_from_placements(best_solution, parts_to_anneal)
        
        # For now, we assume all parts are placed. A more robust implementation
        # could check for parts that are still outside the boundary and return them as unplaced.
        return final_parts, []

    def _get_parts_from_placements(self, placements, source_parts):
        """Converts a list of FreeCAD.Placement objects back to ShapeBounds."""
        temp_parts = []
        for i, part in enumerate(source_parts):
            new_part = copy.deepcopy(part)
            placement = placements[i]
            
            angle = placement.Rotation.Angle * (180 / math.pi)
            x = placement.Base.x
            y = placement.Base.y

            new_part.set_placement(x, y, angle)
            temp_parts.append(new_part)
        return temp_parts

    def _get_random_solution(self, parts_to_place, bin_w, bin_h, rotation_steps):
        """Generates an initial random solution (list of placements)."""
        placements = []
        for part in parts_to_place:
            angle = 0
            if rotation_steps > 1:
                angle = random.randrange(rotation_steps) * (360 / rotation_steps)
            
            temp_part = copy.deepcopy(part)
            temp_part.set_rotation(angle)
            _, _, w, h = temp_part.bounding_box()

            max_x = bin_w - w
            max_y = bin_h - h
            
            pos_x = random.uniform(0, max_x) if max_x > 0 else 0
            pos_y = random.uniform(0, max_y) if max_y > 0 else 0

            placements.append(FreeCAD.Placement(FreeCAD.Vector(pos_x, pos_y, 0), FreeCAD.Rotation(FreeCAD.Vector(0,0,1), angle)))
        return placements

    def _get_random_neighbor(self, solution, temp, temp_initial, bin_w, bin_h, rotation_steps):
        """Generates a slightly perturbed neighboring solution."""
        neighbor = copy.deepcopy(solution)
        if not neighbor: return []

        idx = random.randrange(len(neighbor))
        
        move_dist = max(bin_w, bin_h) * (temp / temp_initial) * 0.2
        random_vec = FreeCAD.Vector(random.uniform(-1, 1), random.uniform(-1, 1), 0)
        if random_vec.Length > 0: random_vec.normalize()
        
        new_pos = neighbor[idx].Base + random_vec * move_dist
        neighbor[idx].Base = new_pos

        return neighbor

    def _calculate_cost(self, placements, source_parts, fixed_parts, bin_w, bin_h):
        """Calculates the cost of a given solution (lower is better)."""
        if not placements: return 0

        temp_parts = self._get_parts_from_placements(placements, source_parts)
        all_parts = temp_parts + fixed_parts

        # Create a temporary BasePacker to access its helper methods
        helper = nesting_logic.pack_helpers.BasePacker([], bin_w, bin_h)

        total_overlap = sum(helper._get_overlap_area(part1, part2) for i, part1 in enumerate(all_parts) for part2 in all_parts[i+1:])
        total_outside = sum(helper._get_outside_area(part) for part in all_parts)

        return total_overlap * 10000 + total_outside * 10000

    def _prepare_parts_from_layout(self, spacing):
        """Creates ShapeBounds objects from an existing layout group."""
        import copy
        
        sheet_groups = sorted([obj for obj in self.layout_group.Group if obj.Label.startswith("Sheet_")], key=lambda g: int(g.Label.split('_')[1]))
        if not sheet_groups:
            raise ValueError("No sheets found in layout.")

        fixed_parts, mobile_parts = [], []
        all_fc_shape_wrappers = []
        
        # A map to store master nesting parts so we don't re-process the same shape
        master_nesting_parts = {}
        # A map to store the original FreeCAD objects
        original_fc_objects = {}

        last_sheet_index = len(sheet_groups) - 1
        part_id_counter = 0

        for i, sheet_group in enumerate(sheet_groups):
            objects_group = sheet_group.getObject(f"Objects_{i+1}")
            if not objects_group: continue
            
            for obj in objects_group.Group:
                if not obj.Label.startswith("packed_"):
                    continue

                # Extract original label: "packed_{original_label}_{instance_num}"
                try:
                    parts = obj.Label.split('_')
                    original_label = parts[1]
                    instance_num = int(parts[2])
                except (IndexError, ValueError):
                    FreeCAD.Console.PrintWarning(f"Could not parse label for {obj.Label}. Skipping.\n")
                    continue

                # Find the original FreeCAD object in the document
                original_obj = self.doc.getObject(original_label)
                if not original_obj:
                    # If not found, it might be a compound label itself
                    # This part of the logic might need to be more robust
                    # For now, we assume simple labels.
                    FreeCAD.Console.PrintWarning(f"Could not find original object '{original_label}' for {obj.Label}. Skipping.\n")
                    continue

                # Store the original object if we haven't seen it before
                if original_label not in original_fc_objects:
                    original_fc_objects[original_label] = original_obj

                # Create the master nesting part if it doesn't exist
                if original_label not in master_nesting_parts:
                    try:
                        master_nesting_parts[original_label] = shape_processor.create_single_nesting_part(original_obj, spacing)
                    except Exception as e:
                        raise ValueError(f"Could not process shape for '{original_label}': {e}")

                # Create an instance of the nesting part
                new_nesting_part = copy.deepcopy(master_nesting_parts[original_label])
                new_nesting_part.id = part_id_counter
                new_nesting_part.name = f"{original_label}_{instance_num}"
                new_nesting_part.sheet_id = i # Store which sheet it's on

                # Create a Shape wrapper for the drawing logic
                # This was the missing piece. The shape_pool needs these wrappers.
                shape_wrapper = self.nesting_controller.shape_pool.get(f"{original_label}_{instance_num}") or self.nesting_controller.Shape(original_obj, instance_num=instance_num)
                shape_wrapper.id = part_id_counter # Match the nesting part ID
                all_fc_shape_wrappers.append(shape_wrapper)

                # Set its current placement from the object in the layout
                x = obj.Placement.Base.x
                y = obj.Placement.Base.y
                angle = obj.Placement.Rotation.Angle * (180 / math.pi)
                new_nesting_part.set_placement(x, y, angle)

                if self.ui.consolidate_checkbox.isChecked() and i == last_sheet_index:
                    mobile_parts.append(new_nesting_part)
                else:
                    fixed_parts.append(new_nesting_part)
                
                part_id_counter += 1

        return fixed_parts, mobile_parts, all_fc_shape_wrappers