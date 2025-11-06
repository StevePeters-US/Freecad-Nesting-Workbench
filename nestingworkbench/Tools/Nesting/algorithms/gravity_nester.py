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
        for _ in range(self.max_nesting_steps):
            dx = direction[0] * self.step_size
            dy = direction[1] * self.step_size

            # Move the actual part to the next position.
            part.move(dx, dy)

            # Update the UI to show the new test position.
            if self.update_callback:
                self.update_callback(part, sheet)

            # Check if the new position is valid.
            is_valid = sheet.is_placement_valid(part, recalculate_union=False, part_to_ignore=part)
            if not is_valid:
                # Collision detected. Move the part back to its last valid position.
                part.move(-dx, -dy)
                # Update the UI again to show the reverted position.
                if self.update_callback:
                    self.update_callback(part, sheet)
                # Stop moving.
                break
            else:
                # Position is valid, continue to the next step in the loop.
                pass

    def _move_until_collision(self, part, sheet, direction):
        """
        Moves a part in the gravity direction step-by-step until it hits
        the bin edge or another placed part.
        If a collision occurs, it attempts to "shake" the part free once.
        """
        cycle = 0
        # Loop until the part is fully settled (cannot move via gravity or annealing).
        while cycle < self.max_nesting_steps: # Use max_nesting_steps as a safeguard
            cycle += 1

            # --- 1. Apply Gravity ---
            pre_gravity_x, pre_gravity_y, _, _ = part.bounding_box()
            self._apply_gravity_to_part(part, sheet, direction)
            post_gravity_x, post_gravity_y, _, _ = part.bounding_box()
            gravity_moved = abs(post_gravity_x - pre_gravity_x) > 1e-6 or abs(post_gravity_y - pre_gravity_y) > 1e-6

            if gravity_moved:
                continue # Part is still falling, loop again.

            # --- 2. Anneal (if gravity failed) ---
            pre_anneal_x, pre_anneal_y, _, _ = part.bounding_box()
            self._anneal_part(part, sheet, direction, rotate_enabled=self.anneal_rotate_enabled, translate_enabled=self.anneal_translate_enabled)
            post_anneal_x, post_anneal_y, _, _ = part.bounding_box()
            shake_moved = abs(post_anneal_x - pre_anneal_x) > 1e-6 or abs(post_anneal_y - pre_anneal_y) > 1e-6

            if shake_moved:
                # The part was shaken into a new position. We must immediately check
                # if it can now fall further under gravity from this new spot.
                # A successful shake now guarantees that a gravity move is possible, so we just continue the loop.
                continue

            # If we reach here, it means either:
            # 1. The shake failed to move the part.
            # 2. The shake succeeded, but the part could not fall any further from its new position.
            # In either case, the part is now considered fully settled.
            break # Exit the loop.

        return part