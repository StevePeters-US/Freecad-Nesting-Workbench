import math
import copy
import pdb
import random
from shapely.geometry import Polygon
from shapely.affinity import translate, rotate
from shapely.ops import unary_union
from PySide import QtGui
import FreeCAD
from ....datatypes.sheet import Sheet
from ....datatypes.placed_part import PlacedPart

# --- Base Packer Class ---
class BaseNester(object):
    """Base class for nesting algorithms. Relies on the shapely library."""
    def __init__(self, width, height, rotation_steps=1, **kwargs):
        self._bin_width = width
        self._bin_height = height
        self.rotation_steps = rotation_steps if rotation_steps > 0 else 1
        self.max_spawn_count = kwargs.get("max_spawn_count", 100)
        self.anneal_steps = kwargs.get("anneal_steps", 100)
        self.step_size = kwargs.get("step_size", 5.0)
        self.anneal_rotate_enabled = kwargs.get("anneal_rotate_enabled", True)
        self.anneal_translate_enabled = kwargs.get("anneal_translate_enabled", True)
        self.anneal_random_shake_direction = kwargs.get("anneal_random_shake_direction", False)

        self.parts_to_place = [] # This list will hold Shape objects
        self.sheets = []
        
        self._bin_polygon = Polygon([(0, 0), (width, 0), (width, height), (0, height)])

    def nest(self, parts, update_callback=None):
        """
        Main nesting loop. Iterates through parts and calls the subclass's
        sheet nesting implementation until all parts are placed or no more
        can be placed.
        """
        self.parts_to_place = list(parts)
        self.sheets = []
        self._sort_parts_by_area() # Sorts self.parts_to_place in-place
        unplaced_shapes = []

        while self.parts_to_place:
            original_shape = self.parts_to_place.pop(0) # Get and remove the largest remaining part
            placed = False
            # Try to place on existing sheets first
            for i, sheet in enumerate(self.sheets):
                # It is CRITICAL to pass a deepcopy of the shape to the placement
                # function. This ensures that each placement attempt is independent.
                part_to_try = copy.deepcopy(original_shape)
                placed_part_shape = self._try_place_part_on_sheet(part_to_try, sheet, update_callback)
                
                # --- Safety Check ---
                # Final validation to ensure the returned part is valid before accepting it.
                if placed_part_shape and sheet.is_placement_valid(placed_part_shape, recalculate_union=False):
                        sheet.add_part(PlacedPart(placed_part_shape))
                        # Process UI events to keep FreeCAD responsive, especially during animation.
                        QtGui.QApplication.processEvents()
                        placed = True
                        break
                elif placed_part_shape:
                    # The algorithm returned a shape, but it was invalid.
                    FreeCAD.Console.PrintWarning(f"Nester algorithm returned an invalid placement for {original_shape.id}. Discarding.\n")
            
            if not placed:
                # If it didn't fit on any existing sheet, try a new one
                new_sheet_id = len(self.sheets)
                new_sheet = Sheet(new_sheet_id, self._bin_width, self._bin_height)
                part_to_try = copy.deepcopy(original_shape)
                placed_part_shape = self._try_place_part_on_sheet(part_to_try, new_sheet, update_callback)
                
                # --- Safety Check ---
                if placed_part_shape and new_sheet.is_placement_valid(placed_part_shape):
                        new_sheet.add_part(PlacedPart(placed_part_shape))
                        self.sheets.append(new_sheet)
                        # Process UI events to keep FreeCAD responsive, especially during animation.
                        QtGui.QApplication.processEvents()
                        placed = True
                elif placed_part_shape:
                    # The algorithm returned a shape, but it was invalid for a new sheet.
                    FreeCAD.Console.PrintWarning(f"Nester algorithm returned an invalid placement for {original_shape.id} on a new sheet. Discarding.\n")
                    unplaced_shapes.append(original_shape)
                else:
                    # If it can't even fit on an empty sheet, it's unplaceable
                    unplaced_shapes.append(original_shape)

        return self.sheets, unplaced_shapes


    def _sort_parts_by_area(self):
        """Sorts the list of parts to be nested in-place, largest area first."""
        self.parts_to_place.sort(key=lambda p: p.area(), reverse=True)

    def _try_spawn_part(self, shape, sheet, update_callback=None):
        """
        Tries to place a shape at a random location without initial collision.
        Returns the spawned shape on success, or None on failure.
        """
        for _ in range(self.max_spawn_count):
            # The controller sets the definitive rotation_steps on the shape.
            if shape.rotation_steps > 1:
                angle = random.randrange(shape.rotation_steps) * (360 / shape.rotation_steps)
                shape.set_rotation(angle)

            part_min_x, part_min_y, w, h = shape.bounding_box()
            
            max_target_x = self._bin_width - w
            max_target_y = self._bin_height - h
            target_x = random.uniform(0, max_target_x) if max_target_x > 0 else 0
            target_y = random.uniform(0, max_target_y) if max_target_y > 0 else 0

            # The move_to method is more direct here
            shape.move_to(target_x, target_y)

            if sheet.is_placement_valid(shape):
                if update_callback:
                    sheet_index = sheet.id if sheet else len(self.sheets)
                    # On spawn, the sheet is empty, so we only pass the new shape's bounds
                    update_callback({sheet_index: [shape.shape_bounds]}, moving_part=shape, current_sheet_id=sheet_index)
                return shape

        return None

    def _try_place_part_on_sheet(self, part_to_place, sheet, update_callback):
        """
        Subclasses must implement this. Tries to place a single shape on a given sheet.
        Returns the placed shape on success, None on failure.
        """
        raise NotImplementedError

    def _anneal_part(self, part_to_shake, sheet, current_gravity_direction, update_callback=None, rotate_enabled=True, translate_enabled=True, rotate_override=None, translate_override=None):
        """
        Attempts to "anneal" a shape out of a collision by trying small
        perpendicular and/or rotational movements. This is a local search
        mechanism to find a valid spot when a part gets stuck.
        Returns a tuple of (position, rotation) on success. If it can't find
        a valid position, it returns the starting position and rotation.
        """

        start_centroid = part_to_shake.get_centroid()
        start_pos = (start_centroid.x, start_centroid.y) if start_centroid else (0, 0)
        start_rot = part_to_shake.get_angle() # This is the angle to return if shaking fails

        # If no annealing steps are configured or both rotate and translate are disabled, return immediately.
        if self.anneal_steps == 0 or (not rotate_enabled and not translate_enabled):
            return start_pos, start_rot

        # Store the initial state of the part.
        initial_bl_x, initial_bl_y, _, _ = part_to_shake.bounding_box()
        initial_angle = part_to_shake.get_angle()

        # Determine the base perpendicular direction (relative to the current gravity)
        base_perp_dir = (-current_gravity_direction[1], current_gravity_direction[0])

        # Randomize the initial side direction to avoid bias (e.g., always trying right first)
        # Randomize the initial side direction to avoid bias (e.g., always trying right first)
        initial_side_direction = random.choice([1, -1])
        for i in range(self.anneal_steps):
            amplitude = self.step_size * (i // 2 + 1)
            side_direction = initial_side_direction if i % 2 == 0 else -initial_side_direction

            # Reset the part to its initial state (before any shaking attempts in this loop)
            part_to_shake.move_to(initial_bl_x, initial_bl_y)
            part_to_shake.set_rotation(initial_angle) # Order matters: move then rotate
            
            # Apply rotation if enabled
            if rotate_enabled and part_to_shake.rotation_steps > 1:
                # Oscillate the rotation similar to translation
                angle_step_magnitude = (360.0 / part_to_shake.rotation_steps) * (i // 2 + 1)
                
                # Use the same side_direction logic for rotation oscillation
                rotation_direction = side_direction 

                new_angle = (initial_angle + angle_step_magnitude * rotation_direction) % 360.0
                part_to_shake.set_rotation(new_angle)

            # Determine the perpendicular direction for this specific anneal attempt
            perp_dir_for_shake = (0, 0) # Default to no movement
            if translate_enabled:
                if self.anneal_random_shake_direction:
                    # Generate a completely random direction for this shake attempt
                    random_angle_rad = random.uniform(0, 2 * math.pi)
                    temp_gravity_dir = (math.cos(random_angle_rad), math.sin(random_angle_rad))
                    perp_dir_for_shake = (-temp_gravity_dir[1], temp_gravity_dir[0])
                else:
                    perp_dir_for_shake = base_perp_dir # Use the perpendicular to the current gravity direction

                shake_dx = perp_dir_for_shake[0] * amplitude * side_direction
                shake_dy = perp_dir_for_shake[1] * amplitude * side_direction
                part_to_shake.move(shake_dx, shake_dy)
            
            # Update the preview to show the current shake attempt, regardless of validity.
            if update_callback:
                sheet_index = sheet.id if sheet else len(self.sheets)
                current_bounds = [p.shape.shape_bounds for p in sheet.parts] if sheet else []
                update_callback({sheet_index: current_bounds + [part_to_shake.shape_bounds]}, moving_part=part_to_shake, current_sheet_id=sheet_index)

            if sheet.is_placement_valid(part_to_shake, recalculate_union=False, part_to_ignore=part_to_shake):
                # Found a valid position. Return its current centroid and angle.
                new_centroid = part_to_shake.get_centroid()
                new_pos = (new_centroid.x, new_centroid.y) if new_centroid else (0, 0)
                return new_pos, part_to_shake.get_angle()
            # If invalid, the loop will continue, and the next iteration will reset the part.

        # If the loop finishes, no valid shake was found.
        # Revert the part to its original state before returning the initial position.
        part_to_shake.move_to(initial_bl_x, initial_bl_y)
        part_to_shake.set_rotation(initial_angle)

        # Update the preview one last time to show the part reverted to its original (valid) position.
        if update_callback:
            sheet_index = sheet.id if sheet else len(self.sheets)
            current_bounds = [p.shape.shape_bounds for p in sheet.parts] if sheet else []
            update_callback({sheet_index: current_bounds + [part_to_shake.shape_bounds]}, moving_part=part_to_shake, current_sheet_id=sheet_index)

        return start_pos, start_rot # Could not shake free, return original state
    
    def _check_intersection(self, part1, part2):
        """Checks if two parts intersect."""
        if not part1.polygon or not part2.polygon:
            return False
        return part1.polygon.intersects(part2.polygon)

    def _get_overlap_area(self, part1, part2):
        """Calculates the overlapping area between two parts."""
        if not part1.polygon or not part2.polygon or not part1.polygon.intersects(part2.polygon):
            return 0.0
        try:
            return part1.polygon.intersection(part2.polygon).area
        except Exception:
            return 0.0 # Fallback if intersection fails

    def _get_outside_area(self, shape):
        """Calculates the area of a shape that is outside the bin."""
        if not shape.polygon or self._bin_polygon.contains(shape.polygon):
            return 0.0
        try:
            return shape.polygon.difference(self._bin_polygon).area
        except Exception:
            return 0.0 # Fallback if difference fails