import math
import random
from shapely.geometry import Polygon
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
        self.spacing = kwargs.get("spacing", 0)
        self.max_spawn_count = kwargs.get("max_spawn_count", 100)
        self.anneal_steps = kwargs.get("anneal_steps", 100)
        self.step_size = kwargs.get("step_size", 5.0)
        self.anneal_rotate_enabled = kwargs.get("anneal_rotate_enabled", True)
        self.anneal_translate_enabled = kwargs.get("anneal_translate_enabled", True)
        self.anneal_random_shake_direction = kwargs.get("anneal_random_shake_direction", False)
        self.update_callback = kwargs.get("update_callback", None)

        self.parts_to_place = [] # This list will hold Shape objects
        self.sheets = []
        
        self._bin_polygon = Polygon([(0, 0), (width, 0), (width, height), (0, height)])

    def _attempt_placement_on_sheet(self, part, sheet):
        """
        Attempts to place a part on a sheet, and if successful, finalizes
        its placement and adds it to the sheet.
        Returns True on success, False on failure.
        """
        placed_part_shape = self._try_place_part_on_sheet(part, sheet)
        
        # Final validation to ensure the returned part is valid before accepting it.
        if placed_part_shape and sheet.is_placement_valid(placed_part_shape, recalculate_union=False):
            sheet_origin = sheet.get_origin()
            placed_part_shape.placement = placed_part_shape.get_final_placement(sheet_origin)
            sheet.add_part(PlacedPart(placed_part_shape))
            return True
        elif placed_part_shape:
            FreeCAD.Console.PrintWarning(f"Nester algorithm returned an invalid placement for {part.id}. Discarding.\n")
        
        return False

    def nest(self, parts):
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
            for sheet in self.sheets:
                if self._attempt_placement_on_sheet(original_shape, sheet):
                    placed = True
                    break
            
            if not placed:
                # If it didn't fit on any existing sheet, try a new one
                new_sheet_id = len(self.sheets)
                new_sheet = Sheet(new_sheet_id, self._bin_width, self._bin_height, spacing=self.spacing) # Create a new sheet
                
                if self._attempt_placement_on_sheet(original_shape, new_sheet):
                    self.sheets.append(new_sheet)
                    placed = True
                else:
                    # If it can't even fit on an empty sheet, it's unplaceable
                    unplaced_shapes.append(original_shape)

        return self.sheets, unplaced_shapes


    def _sort_parts_by_area(self):
        """Sorts the list of parts to be nested in-place, largest area first."""
        self.parts_to_place.sort(key=lambda p: p.area, reverse=True)

    def _try_place_part_on_sheet(self, part_to_place, sheet):
        """
        Subclasses must implement this. Tries to place a single shape on a given sheet.
        Returns the placed shape on success, None on failure.
        """
        raise NotImplementedError

    def _try_rotation_shake(self, part_to_shake, sheet, initial_bl_x, initial_bl_y, initial_angle, side_direction, i):
        """Helper to attempt a single rotational shake."""
        if not (self.anneal_rotate_enabled and part_to_shake.rotation_steps > 1):
            return False

        # Reset part to its pre-shake state for this attempt
        part_to_shake.move_to(initial_bl_x, initial_bl_y) # No UI update needed here
        part_to_shake.set_rotation(initial_angle, reposition=False) # No UI update

        # Oscillate the rotation
        angle_step_magnitude = (360.0 / part_to_shake.rotation_steps) * (i // 2 + 1)
        rotation_direction = side_direction
        new_angle = (initial_angle + angle_step_magnitude * rotation_direction) % 360.0
        part_to_shake.set_rotation(new_angle) # No UI update

        if self.update_callback:
            self.update_callback(part_to_shake, sheet)

        return sheet.is_placement_valid(part_to_shake, recalculate_union=False, part_to_ignore=part_to_shake)

    def _try_translation_shake(self, part_to_shake, sheet, initial_bl_x, initial_bl_y, initial_angle, current_gravity_direction, side_direction, step_index):
        """Helper to attempt a single translational shake."""
        import copy
        FreeCAD.Console.PrintMessage(f"        ANNEAL_DEBUG: _try_translation_shake (amplitude level {step_index}): side_dir={side_direction}\n")
        if not self.anneal_translate_enabled:
            FreeCAD.Console.PrintMessage("        ANNEAL_DEBUG:  -> translate disabled, skipping.\n")
            return False

        # Reset part to its pre-shake state for this attempt
        part_to_shake.move_to(initial_bl_x, initial_bl_y) # No UI update
        part_to_shake.set_rotation(initial_angle, reposition=False) # No UI update
        FreeCAD.Console.PrintMessage(f"        ANNEAL_DEBUG:  -> Reset to ({initial_bl_x:.2f}, {initial_bl_y:.2f}) @ {initial_angle:.1f} deg\n")

        amplitude = self.step_size * (step_index + 1)

        # Determine the perpendicular direction for this shake
        if self.anneal_random_shake_direction:
            random_angle_rad = random.uniform(0, 2 * math.pi)
            temp_gravity_dir = (math.cos(random_angle_rad), math.sin(random_angle_rad))
            perp_dir_for_shake = (-temp_gravity_dir[1], temp_gravity_dir[0])
        else:
            perp_dir_for_shake = (-current_gravity_direction[1], current_gravity_direction[0])

        FreeCAD.Console.PrintMessage(f"        ANNEAL_DEBUG:  -> Amplitude: {amplitude:.2f}, Perp Dir: ({perp_dir_for_shake[0]:.2f}, {perp_dir_for_shake[1]:.2f})\n")
        shake_dx = perp_dir_for_shake[0] * amplitude * side_direction
        shake_dy = perp_dir_for_shake[1] * amplitude * side_direction

        # We must use move_to with absolute coordinates. The part was reset to initial_bl_x/y,
        # so we calculate the new absolute position from there.
        new_x = initial_bl_x + shake_dx
        new_y = initial_bl_y + shake_dy
        part_to_shake.move_to(new_x, new_y) # No UI update

        post_shake_x, post_shake_y, _, _ = part_to_shake.bounding_box()
        FreeCAD.Console.PrintMessage(f"        ANNEAL_DEBUG:  -> Shaking by ({shake_dx:.2f}, {shake_dy:.2f}). New pos check...\n")
        FreeCAD.Console.PrintMessage(f"        ANNEAL_DEBUG:  -> Position after shake: ({post_shake_x:.2f}, {post_shake_y:.2f})\n")
        if self.update_callback:
            self.update_callback(part_to_shake, sheet)

        is_valid_shake = sheet.is_placement_valid(part_to_shake, recalculate_union=False, part_to_ignore=part_to_shake)
        FreeCAD.Console.PrintMessage(f"        ANNEAL_DEBUG:  -> Shake pos valid? {is_valid_shake}\n")
        if not is_valid_shake:
            return False

        # The shake is valid. Now, check if the part can fall further from this new position.
        # This makes the anneal "smarter" by seeking productive moves.
        FreeCAD.Console.PrintMessage(f"        ANNEAL_DEBUG:  -> Shake position is valid. Checking for subsequent gravity move...\n")
        gravity_part_copy = copy.deepcopy(part_to_shake)
        gravity_part_copy.move(current_gravity_direction[0] * self.step_size, current_gravity_direction[1] * self.step_size)
        
        can_fall_further = sheet.is_placement_valid(gravity_part_copy, recalculate_union=False, part_to_ignore=part_to_shake)
        FreeCAD.Console.PrintMessage(f"        ANNEAL_DEBUG:  -> Pos after gravity ({gravity_part_copy.bounding_box()[0]:.2f}, {gravity_part_copy.bounding_box()[1]:.2f}) valid? {can_fall_further}\n")

        if can_fall_further:
            FreeCAD.Console.PrintMessage(f"        ANNEAL_DEBUG:  -> SUCCESS: Gravity move is possible after shake.\n")
        
        return can_fall_further

    def _report_anneal_success(self, part_to_shake, sheet, new_pos):
        """
        Helper function to finalize a successful anneal move. It logs the success,
        updates the UI via callback, and returns the new position and rotation.
        """
        FreeCAD.Console.PrintMessage(f"      ANNEAL_DEBUG: SUCCESS! Found valid shake at pos ({new_pos[0]:.2f}, {new_pos[1]:.2f}).\n")
        if self.update_callback:
            self.update_callback(part_to_shake, sheet)
        # This function no longer needs to return anything.
        # The calling function will check the part's final position.

    def _anneal_part(self, part_to_shake, sheet, current_gravity_direction, rotate_enabled=True, translate_enabled=True):
        """ 
        Attempts to "anneal" a shape out of a collision by trying small
        perpendicular and/or rotational movements. This is a local search
        mechanism to find a valid spot when a part gets stuck.
        Returns a tuple of (position, rotation) on success. If it can't find
        a valid position, it reverts the part to its starting state.
        """

        # We must use the bottom-left corner for position, as this is what `move_to` uses.
        # Using the centroid was causing a coordinate mismatch.
        initial_bl_x, initial_bl_y, _, _ = part_to_shake.bounding_box()
        start_pos = (initial_bl_x, initial_bl_y)
        start_rot = part_to_shake.angle # This is the angle to return if shaking fails

        FreeCAD.Console.PrintMessage(f"      ANNEAL_DEBUG: --- Entering _anneal_part for '{part_to_shake.id}' ---\n")
        FreeCAD.Console.PrintMessage(f"      ANNEAL_DEBUG: Start pos: ({start_pos[0]:.2f}, {start_pos[1]:.2f}), Angle: {start_rot:.1f}\n")
        FreeCAD.Console.PrintMessage(f"      ANNEAL_DEBUG: Anneal steps: {self.anneal_steps}, Rotate: {rotate_enabled}, Translate: {translate_enabled}\n")

        if self.anneal_steps == 0 or (not self.anneal_rotate_enabled and not self.anneal_translate_enabled) or (not rotate_enabled and not translate_enabled):
            return

        num_amplitude_levels = self.anneal_steps
        for i in range(num_amplitude_levels):
            if not translate_enabled: break

            # To prevent bias where the part always "walks" in the first successful direction,
            # we can randomize the order in which we try the two shake directions.
            shake_directions = [1, -1]
            random.shuffle(shake_directions)

            for side_dir in shake_directions: # Try both directions for each amplitude
                FreeCAD.Console.PrintMessage(f"      ANNEAL_DEBUG: Trying translation shake for amplitude level {i+1} (direction {side_dir}).\n")
                is_valid = self._try_translation_shake(part_to_shake, sheet, initial_bl_x, initial_bl_y, start_rot, current_gravity_direction, side_direction=side_dir, step_index=i)
                if is_valid:
                    new_bl_x, new_bl_y, _, _ = part_to_shake.bounding_box()
                    self._report_anneal_success(part_to_shake, sheet, (new_bl_x, new_bl_y))
                    # Found a valid shake, so we are done. Exit immediately.
                    return

        # If the loop finishes, no valid shake was found.
        # Revert the part to its original state before returning the initial position.
        FreeCAD.Console.PrintMessage(f"      ANNEAL_DEBUG: FAILED. No valid shake found after {self.anneal_steps} steps. Reverting part.\n")
        part_to_shake.move_to(start_pos[0], start_pos[1])
        part_to_shake.set_rotation(start_rot)
        if self.update_callback:
            self.update_callback(part_to_shake, sheet)