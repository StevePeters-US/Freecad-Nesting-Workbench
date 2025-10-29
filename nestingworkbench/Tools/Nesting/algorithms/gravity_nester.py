import random
import math
#import pdb
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

    def _spawn_part_on_sheet(self, shape, sheet):
        """
        Tries to place a shape at a random location without initial collision.
        Returns the spawned shape on success, or None on failure.
        """
        for _ in range(self.max_spawn_count):
            # The controller sets the definitive rotation_steps on the shape.
            if shape.rotation_steps > 1:
                angle = random.randrange(shape.rotation_steps) * (360 / shape.rotation_steps)
                shape.set_rotation(angle)

            _, _, w, h = shape.bounding_box()
            
            max_target_x = self._bin_width - w
            max_target_y = self._bin_height - h
            target_x = random.uniform(0, max_target_x) if max_target_x > 0 else 0
            target_y = random.uniform(0, max_target_y) if max_target_y > 0 else 0
            shape.move_to(target_x, target_y)

            if sheet.is_placement_valid(shape):
                return shape
        return None

    def _try_place_part_on_sheet(self, part_to_place, sheet):
        """
        Tries to place a single part on the given sheet using gravity simulation.
        Returns the placed part on success, None on failure.
        """
        spawned_part = self._spawn_part_on_sheet(part_to_place, sheet)
        
        if spawned_part:
            if self.gravity_direction is None: # None indicates random direction
                angle_rad = random.uniform(0, 2 * math.pi)
                part_direction = (math.cos(angle_rad), math.sin(angle_rad))
            else:
                part_direction = self.gravity_direction
            
            # The spawned part is now moved until it collides with something.
            return self._move_until_collision(spawned_part, sheet, part_direction)
        else:
            return None

    def _apply_gravity_to_part(self, part, sheet, direction):
        """Helper to move a part in a given direction until it collides."""
        # FreeCAD.Console.PrintMessage(f"DEBUG: Gravity: Moving part '{part.id}' (id={id(part)}). fc_object is {'set' if part.fc_object else 'None'}.\n")
        for _ in range(self.max_nesting_steps):
            # Record the last valid position's bottom-left corner
            last_valid_x, last_valid_y, _, _ = part.bounding_box()
            part.move(direction[0] * self.step_size, direction[1] * self.step_size)
            if self.update_callback:
                self.update_callback(part, sheet) # Force UI update for simulation
            
            if not sheet.is_placement_valid(part, recalculate_union=False, part_to_ignore=part):
                # Collision detected. Revert to the last valid position.
                part.move_to(last_valid_x, last_valid_y)
                break # Part has stopped moving.

    def _move_until_collision(self, part, sheet, direction):
        """
        Moves a part in the gravity direction step-by-step until it hits
        the bin edge or another placed part.
        If a collision occurs, it attempts to "shake" the part free once.
        """
        # --- Phase 1: Initial Gravity Movement ---
        self._apply_gravity_to_part(part, sheet, direction)
        
        # --- Phase 2: Shake on Collision ---
        # The part is now at its final resting place from gravity. Try to shake it.
        pre_shake_centroid = part.centroid
        pre_shake_pos = (pre_shake_centroid.x, pre_shake_centroid.y) if pre_shake_centroid else (0, 0)
        
        # --- Step 2a: Try rotation-only annealing ---
        # We check the UI setting from the controller before attempting the rotation shake.
        if self.anneal_rotate_enabled:
            rot_pos, rot_rot = self._anneal_part(part, sheet, direction, rotate_enabled=True, translate_enabled=False)
        else:
            rot_pos = pre_shake_pos # If rotation is disabled, it didn't move.
        
        # Check if rotation found a valid spot. If not, try translation.
        moved_distance_sq_rot = (rot_pos[0] - pre_shake_pos[0])**2 + (rot_pos[1] - pre_shake_pos[1])**2
        if math.isclose(moved_distance_sq_rot, 0.0):
            # --- Step 2b: Try translation-only annealing ---
            # We check the UI setting from the controller before attempting the translation shake.
            if self.anneal_translate_enabled:
                self._anneal_part(part, sheet, direction, rotate_enabled=False, translate_enabled=True)
        
        # --- Phase 3: Final Gravity Movement ---
        # After shaking, try one last gravity move to see if a new path opened up.
        self._apply_gravity_to_part(part, sheet, direction)
                
        return part