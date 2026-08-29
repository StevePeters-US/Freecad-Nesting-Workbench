# SPDX-License-Identifier: LGPL-2.1-or-later
import random
import math
import FreeCAD
from .base_nester import BaseNester

class PhysicsNester(BaseNester):
    """
    A packer that uses a simple physics simulation.
    Parts are spawned at a random location and then moved in a specified
    direction until they collide with the sheet edge or another part.
    """

    def __init__(self, width, height, rotation_steps=1, **kwargs):
        super().__init__(width, height, rotation_steps, **kwargs)
        self.physics_direction = kwargs.get("physics_direction", (0, -1))
        self.max_spawn_count = kwargs.get("max_spawn_count", 100)
        self.max_nesting_steps = kwargs.get("max_nesting_steps", 500)

    def _spawn_part_on_sheet(self, shape, sheet):
        """Tries to place a shape at a random location without initial collision."""
        for _ in range(self.max_spawn_count):
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
        """Tries to place a single shape on a given sheet using physics movement."""
        spawned_part = self._spawn_part_on_sheet(part_to_place, sheet)
        
        if spawned_part:
            if self.physics_direction is None:
                angle_rad = random.uniform(0, 2 * math.pi)
                part_direction = (math.cos(angle_rad), math.sin(angle_rad))
            else:
                part_direction = self.physics_direction
            
            return self._move_until_collision(spawned_part, sheet, part_direction)
        else:
            return None

    def _apply_physics_to_part(self, part, sheet, direction):
        """Moves a part in a direction until it hits an obstacle."""
        for _ in range(self.max_nesting_steps):
            if self.cancel_callback and self.cancel_callback():
                break

            dx = direction[0] * self.step_size
            dy = direction[1] * self.step_size

            part.move(dx, dy)

            if self.update_callback:
                self.update_callback(part, sheet)

            is_valid = sheet.is_placement_valid(part, part_to_ignore=part)
            if not is_valid:
                part.move(-dx, -dy)
                if self.update_callback:
                    self.update_callback(part, sheet)
                break

    def _move_until_collision(self, part, sheet, direction):
        """Orchestrates physics movement and annealing ('shaking') to find a dense spot."""
        cycle = 0
        while cycle < self.max_nesting_steps:
            if self.cancel_callback and self.cancel_callback():
                break

            cycle += 1

            pre_physics_x, pre_physics_y, _, _ = part.bounding_box()
            self._apply_physics_to_part(part, sheet, direction)
            post_physics_x, post_physics_y, _, _ = part.bounding_box()
            
            # Check if physics movement actually changed position
            physics_moved = abs(post_physics_x - pre_physics_x) > 1e-6 or abs(post_physics_y - pre_physics_y) > 1e-6

            if physics_moved:
                cycle = 0 # Reset iteration count because we made progress
                continue

            # If physics alone can't move the part, try to shake it free (annealing)
            # This now returns True if a breakthrough (better spot) was found.
            shake_moved = self._anneal_part(part, sheet, direction, 
                             rotate_enabled=self.anneal_rotate_enabled, 
                             translate_enabled=self.anneal_translate_enabled)
            
            if shake_moved:
                cycle = 0 # Reset iteration count because the wiggle found a better opening
                continue

            # No movement possible after both physics and annealing
            break

        return part
