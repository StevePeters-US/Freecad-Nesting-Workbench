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

class AnnealController:
    """
    Handles the logic for reading an existing layout, running the annealing
    optimization, and updating the layout in the document.
    """
    def __init__(self, ui_panel, layout_group):
        self.ui = ui_panel
        self.doc = FreeCAD.ActiveDocument
        self.layout_group = layout_group

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
            fixed_parts, mobile_parts = self._prepare_parts_from_layout(spacing)
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
        
        # Remove the old layout before drawing the new one
        original_layout_name = self.layout_group.Label
        self.doc.removeObject(self.layout_group.Name)

        # --- Draw the new layout ---
        new_layout_obj = self.doc.addObject("App::DocumentObjectGroup", original_layout_name)
        if FreeCAD.GuiUp: new_layout_obj.ViewObject.Visibility = True

        from ...datatypes.sheet import Sheet
        sheets_to_draw = []
        for i, parts_on_sheet in enumerate(final_sheets_list):
            sheet = Sheet(i, sheet_w, sheet_h)
            sheet.parts = parts_on_sheet
            sheet_origin = sheet.get_origin(spacing)
            # Manually set final placement on each part before drawing
            for part in sheet.parts:
                part.shape.placement = part.shape.get_final_placement(sheet_origin)
            sheet.draw(self.doc, sheet_origin, self.nesting_controller.last_run_ui_params, new_layout_obj)

        end_time = time.time()
        status_text = f"Annealing complete in {end_time - start_time:.2f}s. Placed {len(all_placed_nesting_parts)} parts on {len(final_sheets_list)} sheets."
        if remaining_parts:
            status_text += f" Failed to place {len(remaining_parts)} parts."
        self.ui.status_label.setText(status_text)

    def _get_params_from_spreadsheet(self):
        """Reads layout parameters from the properties of the layout group."""
        if not self.layout_group: return None
        try:
            if hasattr(self.layout_group, 'SheetWidth') and hasattr(self.layout_group, 'SheetHeight') and hasattr(self.layout_group, 'PartSpacing'):
                return {
                    "width": self.layout_group.SheetWidth,
                    "height": self.layout_group.SheetHeight,
                    "spacing": self.layout_group.PartSpacing
                }
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error reading properties from layout group: {e}\n")
        return None

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
        all_parts_on_sheet = temp_parts + fixed_parts

        total_overlap = sum(self._get_overlap_area(part1, part2) for i, part1 in enumerate(all_parts_on_sheet) for part2 in all_parts_on_sheet[i+1:])
        total_outside = sum(self._get_outside_area(part, bin_w, bin_h) for part in all_parts_on_sheet)

        return total_overlap * 10000 + total_outside * 10000

    def _get_overlap_area(self, part1, part2):
        """Calculates the overlapping area between two parts."""
        if not part1.polygon or not part2.polygon or not part1.polygon.intersects(part2.polygon):
            return 0.0
        try:
            return part1.polygon.intersection(part2.polygon).area
        except Exception:
            return 0.0 # Fallback if intersection fails

    def _get_outside_area(self, shape, bin_w, bin_h):
        """Calculates the area of a shape that is outside the bin."""
        from shapely.geometry import Polygon
        bin_polygon = Polygon([(0, 0), (bin_w, 0), (bin_w, bin_h), (0, bin_h)])
        
        if not shape.polygon or bin_polygon.contains(shape.polygon):
            return 0.0
        try:
            return shape.polygon.difference(bin_polygon).area
        except Exception:
            return 0.0 # Fallback if difference fails

    def _prepare_parts_from_layout(self, spacing):
        """Creates ShapeBounds objects from an existing layout group."""
        import copy
        from ...datatypes.shape import Shape

        # --- Step 1: Load Master Shapes ---
        # The MasterShapes group contains the original, processed shapes.
        master_shapes_group = self.layout_group.getObject("MasterShapes")
        if not master_shapes_group:
            raise ValueError("Could not find 'MasterShapes' group in the selected layout.")

        master_shape_map = {}
        for master_obj in master_shapes_group.Group:
            if master_obj.Label.startswith("master_"):
                try:
                    # Re-create the in-memory Shape object from the master FreeCAD object.
                    # This is fast as we are not re-running the expensive geometry processing.
                    original_label = master_obj.Label.replace("master_", "")
                    master_shape_instance = Shape(master_obj)
                    shape_processor.create_single_nesting_part(master_shape_instance, master_obj, spacing, 75) # Resolution is not critical here
                    master_shape_map[original_label] = master_shape_instance
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"Could not re-process master shape '{master_obj.Label}': {e}\n")

        if not master_shape_map:
            raise ValueError("No valid master shapes found in the 'MasterShapes' group.")

        # --- Step 2: Recreate Placed Part Instances ---
        sheet_groups = sorted([obj for obj in self.layout_group.Group if obj.Label.startswith("Sheet_")], key=lambda g: int(g.Label.split('_')[1]))
        if not sheet_groups:
            raise ValueError("No sheets found in layout.")

        fixed_parts, mobile_parts = [], []
        last_sheet_index = len(sheet_groups) - 1

        for i, sheet_group in enumerate(sheet_groups):
            shapes_group = sheet_group.getObject(f"Shapes_{i+1}")
            if not shapes_group: continue
            
            for obj in shapes_group.Group:
                if not obj.Label.startswith("nested_"):
                    continue

                try:
                    # Label is "nested_{original_label}_{instance_num}"
                    label_parts = obj.Label.split('_')
                    original_label = label_parts[1]
                    instance_num = int(label_parts[2])
                    
                    master_shape = master_shape_map.get(original_label)
                    if not master_shape: continue

                    # Create a deep copy of the master shape for this instance
                    part_instance = copy.deepcopy(master_shape)
                    part_instance.sheet_id = i # Store which sheet it's on
                    part_instance.set_rotation(obj.Placement.Rotation.Angle * (180 / math.pi))
                    part_instance.move_to(obj.Placement.Base.x, obj.Placement.Base.y)

                    if self.ui.consolidate_checkbox.isChecked() and i == last_sheet_index:
                        mobile_parts.append(part_instance)
                    else:
                        fixed_parts.append(part_instance)

                except (IndexError, ValueError):
                    FreeCAD.Console.PrintWarning(f"Could not parse label for {obj.Label}. Skipping.\n")
                    continue

        return fixed_parts, mobile_parts