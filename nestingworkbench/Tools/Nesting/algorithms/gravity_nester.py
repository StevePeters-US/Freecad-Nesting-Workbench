import random
import copy
import math
import FreeCAD
from .base_nester import BaseNester

class GravityNester(BaseNester):
    """
    A packer that uses a simple physics-inspired "gravity" simulation.
    Parts are spawned at a random location and then moved in a specified
    direction until they collide with the sheet edge or another part.
    """

    def __init__(self, width, height, rotation_steps=1, **kwargs):
        super().__init__(width, height, rotation_steps, **kwargs)
        # --- Algorithm-specific parameters ---
        self.gravity_direction = kwargs.get("gravity_direction", (0, -1))
        self.max_spawn_count = kwargs.get("max_spawn_count", 100)
        self.max_nesting_steps = kwargs.get("max_nesting_steps", 500)

    def _try_place_part_on_sheet(self, part_to_place, sheet, update_callback):
        """
        Tries to place a single part on the given sheet using gravity simulation.
        Returns the placed part on success, None on failure.
        """
        spawned_part = self._try_spawn_part(part_to_place, sheet)
        
        if spawned_part:
            if self.gravity_direction is None: # None indicates random direction
                angle_rad = random.uniform(0, 2 * math.pi)
                part_direction = (math.cos(angle_rad), math.sin(angle_rad))
            else:
                part_direction = self.gravity_direction
            
            return self._move_until_collision(spawned_part, sheet, part_direction, update_callback)
        else:
            return None

    def _try_spawn_part(self, part, sheet):
        """
        Tries to place a part at a random location without initial collision.
        Returns the spawned part on success, or None on failure.
        """
        for _ in range(self.max_spawn_count):
            return super()._try_spawn_part(part, sheet)
        return None

    def _move_until_collision(self, part, sheet, direction, update_callback=None):
        """
        Moves a part in the gravity direction step-by-step until it hits
        the bin edge or another placed part.
        """
        can_shake = True # The part is allowed to shake on its first collision.

        for _ in range(self.max_nesting_steps):
            if update_callback:
                sheet_index = sheet.id if sheet else len(self.sheets)
                current_bounds = [p.shape.shape_bounds for p in sheet.parts] if sheet else []
                update_callback({sheet_index: current_bounds + [part.shape_bounds]}, moving_part=part, current_sheet_id=sheet_index)

            # Record the last valid position's bottom-left corner
            last_valid_x, last_valid_y, _, _ = part.bounding_box()
            part.move(direction[0] * self.step_size, direction[1] * self.step_size)

            if not sheet.is_placement_valid(part, recalculate_union=False, part_to_ignore=part):
                # Collision detected. Revert to the last valid position.
                part.move_to(last_valid_x, last_valid_y)

                if can_shake:
                    # We are allowed to shake. Let's try it.
                    can_shake = False # Disarm shaking until a successful gravity move.
                    pre_shake_centroid = part.get_centroid()
                    pre_shake_pos = (pre_shake_centroid.x, pre_shake_centroid.y) if pre_shake_centroid else (0, 0)
                    new_pos = pre_shake_pos # Start with the current position
                    
                    # --- Step 1: Try rotation-only annealing ---
                    rot_pos, rot_rot = self._anneal_part(part, sheet, direction, update_callback, rotate_override=True, translate_override=False)
                    
                    # Check if rotation found a valid spot. If not, try translation.
                    moved_distance_sq_rot = (rot_pos[0] - pre_shake_pos[0])**2 + (rot_pos[1] - pre_shake_pos[1])**2
                    if math.isclose(moved_distance_sq_rot, 0.0):
                        # --- Step 2: Try translation-only annealing ---
                        new_pos, new_rot = self._anneal_part(part, sheet, direction, update_callback, rotate_override=False, translate_override=True)
                    else:
                        new_pos = rot_pos

                    # Check if the shake was successful (i.e., it moved a significant distance).
                    moved_distance_sq = (new_pos[0] - pre_shake_pos[0])**2 + (new_pos[1] - pre_shake_pos[1])**2
                    min_movement_threshold_sq = (self.step_size * 0.1)**2

                    if not (math.isclose(moved_distance_sq, 0.0) or moved_distance_sq < min_movement_threshold_sq):
                        continue # Shake was successful, continue the main loop to try another gravity move.
                    else:
                        break # Shake failed to move the part, so it's stuck.
                else:
                    # We collided, but we were not allowed to shake. This means we are stuck.
                    break
            else:
                # The move in the gravity direction was successful.
                # This means we can "re-arm" the ability to shake on the *next* collision.
                can_shake = True

        return part