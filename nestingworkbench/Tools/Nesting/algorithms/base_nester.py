import math
import copy
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

    def _try_rotation_shake(self, part_to_shake, sheet, initial_bl_x, initial_bl_y, initial_angle, side_direction, i, gravity_direction):
        """Helper to attempt a single rotational shake."""
        if not (self.anneal_rotate_enabled and part_to_shake.rotation_steps > 1):
            return False

        # The 'i' from the main anneal loop determines which rotation to try.
        # We try rotations alternating from the current one.
        rotation_index_offset = (i // 2) + 1
        # side_direction is +1 or -1, so we check rotations on either side of the current one.
        rotation_index = (round(initial_angle / (360.0 / part_to_shake.rotation_steps)) + rotation_index_offset * side_direction) % part_to_shake.rotation_steps
        new_angle = rotation_index * (360.0 / part_to_shake.rotation_steps)

        if abs(new_angle - initial_angle) % 360 < 1e-6:
            return False # Skip if it's the same angle

        # Reset part to its pre-shake state for this attempt
        part_to_shake.move_to(initial_bl_x, initial_bl_y)
        part_to_shake.set_rotation(new_angle, reposition=False)
        part_to_shake.move_to(initial_bl_x, initial_bl_y) # Ensure it stays in place

        if self.update_callback: self.update_callback(part_to_shake, sheet)

        is_valid_shake = sheet.is_placement_valid(part_to_shake, recalculate_union=False, part_to_ignore=part_to_shake)
        if not is_valid_shake:
            return False # This rotation is not valid.

        # The shake is valid. Now, check if the part can fall further from this new position.
        gravity_dx = gravity_direction[0] * self.step_size
        gravity_dy = gravity_direction[1] * self.step_size
        part_to_shake.move(gravity_dx, gravity_dy)
        can_fall_further = sheet.is_placement_valid(part_to_shake, recalculate_union=False, part_to_ignore=part_to_shake)
        part_to_shake.move(-gravity_dx, -gravity_dy) # Revert the temporary move

        return can_fall_further

    def _try_translation_shake(self, part_to_shake, sheet, initial_bl_x, initial_bl_y, initial_angle, current_gravity_direction, side_direction, step_index):
        """Helper to attempt a single translational shake."""
        if not self.anneal_translate_enabled:
            return False

        # Reset part to its pre-shake state for this attempt
        part_to_shake.move_to(initial_bl_x, initial_bl_y) # No UI update
        part_to_shake.set_rotation(initial_angle, reposition=False) # No UI update

        amplitude = self.step_size * (step_index + 1)

        # Determine the perpendicular direction for this shake
        perp_dir_for_shake = (-current_gravity_direction[1], current_gravity_direction[0])

        shake_dx = perp_dir_for_shake[0] * amplitude * side_direction
        shake_dy = perp_dir_for_shake[1] * amplitude * side_direction

        new_x = initial_bl_x + shake_dx
        new_y = initial_bl_y + shake_dy
        part_to_shake.move_to(new_x, new_y) # No UI update

        if self.update_callback:
            self.update_callback(part_to_shake, sheet)

        is_valid_shake = sheet.is_placement_valid(part_to_shake, recalculate_union=False, part_to_ignore=part_to_shake)
        if not is_valid_shake:
            return False

        # The shake is valid. Now, check if the part can fall further from this new position.
        # This makes the anneal "smarter" by seeking productive moves.
        gravity_dx = current_gravity_direction[0] * self.step_size
        gravity_dy = current_gravity_direction[1] * self.step_size
        part_to_shake.move(gravity_dx, gravity_dy)
        can_fall_further = sheet.is_placement_valid(part_to_shake, recalculate_union=False, part_to_ignore=part_to_shake)
        part_to_shake.move(-gravity_dx, -gravity_dy) # Revert the temporary move
        
        return can_fall_further

    def _report_anneal_success(self, part_to_shake, sheet, new_pos):
        """
        Helper function to finalize a successful anneal move. It logs the success,
        updates the UI via callback, and returns the new position and rotation.
        """
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

        if self.anneal_steps == 0 or (not self.anneal_rotate_enabled and not self.anneal_translate_enabled) or (not rotate_enabled and not translate_enabled):
            return

        num_amplitude_levels = self.anneal_steps
        for i in range(num_amplitude_levels):
            # To prevent bias where the part always "walks" in the first successful direction,
            # we can randomize the order in which we try the two shake directions.
            shake_directions = [1, -1]
            random.shuffle(shake_directions)

            for side_dir in shake_directions: # Try both directions for each amplitude
                # --- Try a translational shake ---
                if translate_enabled:
                    # This function now only checks if the shake position is valid, not if it can fall.
                    # It returns the new position if valid, otherwise None.
                    new_pos = self._try_translation_shake(part_to_shake, sheet, initial_bl_x, initial_bl_y, start_rot, current_gravity_direction, side_direction=side_dir, step_index=i)
                    
                    if new_pos:
                        # The translation was valid. From this new position, try rotating.
                        is_productive_rotation = self._try_rotation_from_new_pos(part_to_shake, sheet, current_gravity_direction)
                        if is_productive_rotation:
                            new_bl_x, new_bl_y, _, _ = part_to_shake.bounding_box()
                            self._report_anneal_success(part_to_shake, sheet, (new_bl_x, new_bl_y))
                            return # Found a productive compound move.

                # If translation is disabled or failed, try a pure rotation from the original spot.
                if rotate_enabled and self._try_rotation_shake(part_to_shake, sheet, initial_bl_x, initial_bl_y, start_rot, side_direction=side_dir, i=i, gravity_direction=current_gravity_direction):
                    new_bl_x, new_bl_y, _, _ = part_to_shake.bounding_box()
                    self._report_anneal_success(part_to_shake, sheet, (new_bl_x, new_bl_y))
                    return # Found a valid move, exit.

        # If the loop finishes, no valid shake was found.
        # Revert the part to its original state before returning the initial position.
        part_to_shake.move_to(start_pos[0], start_pos[1])
        part_to_shake.set_rotation(start_rot)
        if self.update_callback:
            self.update_callback(part_to_shake, sheet)

    def _try_rotation_from_new_pos(self, part_to_shake, sheet, gravity_direction):
        """
        After a successful translation, this function tries all possible rotations
        from that new spot to see if any allow a gravity move.
        """
        import copy
        if not (self.anneal_rotate_enabled and part_to_shake.rotation_steps > 1):
            return False

        initial_bl_x, initial_bl_y, _, _ = part_to_shake.bounding_box()
        initial_angle = part_to_shake.angle
        best_angle = None
        max_fall_distance = -1.0

        for i in range(part_to_shake.rotation_steps):
            angle = i * (360.0 / part_to_shake.rotation_steps)
            
            # Create a temporary copy to test this rotation
            test_part = copy.deepcopy(part_to_shake)
            test_part.set_rotation(angle, reposition=False)
            test_part.move_to(initial_bl_x, initial_bl_y) # Keep it in the same spot

            if sheet.is_placement_valid(test_part, recalculate_union=False, part_to_ignore=part_to_shake):
                # This rotation is valid. Now, measure how far it can fall.
                fall_distance = 0
                while True:
                    test_part.move(gravity_direction[0] * self.step_size, gravity_direction[1] * self.step_size)
                    if sheet.is_placement_valid(test_part, recalculate_union=False, part_to_ignore=part_to_shake):
                        fall_distance += self.step_size
                    else:
                        break # Collision
                
                if fall_distance > max_fall_distance:
                    max_fall_distance = fall_distance
                    best_angle = angle

        if best_angle is not None and max_fall_distance > 0:
            # A productive rotation was found. Apply it to the actual part.
            part_to_shake.set_rotation(best_angle, reposition=False)
            part_to_shake.move_to(initial_bl_x, initial_bl_y)

            if self.update_callback: self.update_callback(part_to_shake, sheet)
            return True
        else:
            # No productive rotation found, revert to the original state before this function was called.
            part_to_shake.set_rotation(initial_angle, reposition=False)
            part_to_shake.move_to(initial_bl_x, initial_bl_y)
            return False