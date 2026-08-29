# SPDX-License-Identifier: LGPL-2.1-or-later

import math
import copy
import random
from shapely.geometry import Polygon
from shapely.affinity import translate, rotate
from shapely.ops import unary_union
import FreeCAD
from ....datatypes.sheet import Sheet
from ....datatypes.placed_part import PlacedPart

class BaseNester(object):
    """
    Base class for nesting algorithms. 
    It provides a common greedy loop and utility methods for placement attempts.
    """
    def __init__(self, width, height, rotation_steps=1, **kwargs):
        self._bin_width = width
        self._bin_height = height
        self.rotation_steps = max(1, rotation_steps)
        self.max_spawn_count = kwargs.get("max_spawn_count", 100)
        self.anneal_steps = kwargs.get("anneal_steps", 25)
        self.step_size = kwargs.get("step_size", 5.0)
        self.anneal_rotate_enabled = kwargs.get("anneal_rotate_enabled", True)
        self.anneal_translate_enabled = kwargs.get("anneal_translate_enabled", True)
        self.anneal_random_shake_direction = kwargs.get("anneal_random_shake_direction", False)
        self.stability_tolerance = kwargs.get("stability_tolerance", 0.01)
        self.anneal_curve = kwargs.get("anneal_curve", "Logarithmic")
        self.anneal_min_amp = kwargs.get("anneal_min_amp", 0.1)
        self.anneal_max_amp = kwargs.get("anneal_max_amp", 100.0)
        
        self.anneal_rot_steps = kwargs.get("anneal_rot_steps", 10)
        self.anneal_rot_curve = kwargs.get("anneal_rot_curve", "Logarithmic")
        self.anneal_rot_min = kwargs.get("anneal_rot_min", 1.0)
        self.anneal_rot_max = kwargs.get("anneal_rot_max", 90.0)
        self.verbose = kwargs.get("verbose", False)

        self.parts_to_place = []
        self.sheets = []
        self.update_callback = None
        self.progress_callback = None
        self.cancel_callback = kwargs.get("cancel_callback", None)
        
        self._bin_polygon = Polygon([(0, 0), (width, 0), (width, height), (0, height)])
        self._bin_boundary = self._bin_polygon.exterior

    def log(self, message):
        FreeCAD.Console.PrintMessage(f"NESTING: {message}\n")

    def nest(self, parts, sort=True):
        """
        Main greedy nesting loop.
        """
        self.parts_to_place = list(parts)
        self.sheets = []
        
        if sort:
            self.parts_to_place.sort(key=lambda p: p.area, reverse=True)
        
        unplaced_parts = []
        total_count = len(self.parts_to_place)

        for i, part in enumerate(self.parts_to_place):
            if self.cancel_callback and self.cancel_callback():
                self.log("Cancelled by user.")
                break

            if self.progress_callback:
                self.progress_callback(i + 1, total_count, f"Placing {part.id}...")

            placed = False
            for sheet in self.sheets:
                placed_shape = self._try_place_part_on_sheet(part, sheet)
                if placed_shape:
                    sheet_origin = sheet.get_origin()
                    placed_shape.placement = placed_shape.get_final_placement(sheet_origin)
                    sheet.add_part(PlacedPart(placed_shape))
                    
                    if self.update_callback:
                        self.update_callback(placed_shape, sheet)
                    
                    placed = True
                    break
            
            if not placed:
                new_sheet = Sheet(len(self.sheets), self._bin_width, self._bin_height)
                placed_shape = self._try_place_part_on_sheet(part, new_sheet)
                if placed_shape:
                    sheet_origin = new_sheet.get_origin()
                    placed_shape.placement = placed_shape.get_final_placement(sheet_origin)
                    new_sheet.add_part(PlacedPart(placed_shape))
                    self.sheets.append(new_sheet)
                    
                    if self.update_callback:
                        self.update_callback(placed_shape, new_sheet)
                    
                    placed = True
                else:
                    unplaced_parts.append(part)

        return self.sheets, unplaced_parts

    def _try_place_part_on_sheet(self, shape, sheet):
        """Must be implemented by subclasses."""
        raise NotImplementedError

    def _evaluate_placement(self, shape, direction):
        """Score placement by distance along gravity direction. Higher = better."""
        center = shape.centroid
        if not center:
            return -float('inf')
        return center.x * direction[0] + center.y * direction[1]

    def _anneal_part(self, part, sheet, direction, rotate_enabled=True, translate_enabled=True):
        """
        Opportunistic random walk.
        Shakes the part until a strictly better valid spot is found, then returns immediately.
        Returns True if a better spot was found, False otherwise.
        """
        if self.anneal_steps == 0 or (not rotate_enabled and not translate_enabled):
            return False

        initial_x, initial_y, _, _ = part.bounding_box()
        initial_angle = part.angle
        
        base_perp_dir = (-direction[1], direction[0])
        initial_side = random.choice([1, -1])

        starting_score = self._evaluate_placement(part, direction)
        best_score = starting_score
        best_state = (initial_x, initial_y, initial_angle)

        current_state = best_state
        
        # Minimum improvement required to be considered an "advantage" 
        EPSILON = self.stability_tolerance

        if self.verbose:
            self.log(f"--- Annealing Cycle Start for {part.id} ---")
            self.log(f"  Pos Steps: {self.anneal_steps}, Rot Steps per Pos: {self.anneal_rot_steps}")

        for i in range(self.anneal_steps):
            if self.cancel_callback and self.cancel_callback():
                self.log("Annealing cancelled.")
                break

            p = (i + 1) / self.anneal_steps
            amplitude = self.anneal_min_amp + (self.anneal_max_amp - self.anneal_min_amp) * self._get_curve_p(p, self.anneal_curve)
            
            part.move_to(current_state[0], current_state[1])
            part.set_rotation(current_state[2])

            if translate_enabled:
                if self.anneal_random_shake_direction:
                    rand_angle = random.uniform(0, 2 * math.pi)
                    move_dir = (math.cos(rand_angle), math.sin(rand_angle))
                else:
                    side = initial_side if i % 2 == 0 else -initial_side
                    move_dir = (base_perp_dir[0] * side, base_perp_dir[1] * side)
                
                part.move(move_dir[0] * amplitude, move_dir[1] * amplitude)

            cand_x, cand_y, _, _ = part.bounding_box()

            # Shrinks from Max down to Min as i progresses (Inverted Curve)
            rot_amp = self.anneal_rot_min + (self.anneal_rot_max - self.anneal_rot_min) * self._get_curve_p(1.0 - p, self.anneal_rot_curve)
            
            rot_loops = self.anneal_rot_steps if (rotate_enabled and self.anneal_rot_steps > 0) else 1
            
            pos_best_score = -float('inf')
            pos_best_angle = None

            for r in range(rot_loops):
                if rotate_enabled and self.anneal_rot_steps > 0:
                    jitter = random.uniform(-rot_amp, rot_amp)
                    target_angle = current_state[2] + jitter
                else:
                    target_angle = current_state[2]

                part.set_rotation(target_angle)
                
                if sheet.is_placement_valid(part, part_to_ignore=part):
                    score = self._evaluate_placement(part, direction)
                        
                    # OPPORTUNISTIC EARLY EXIT: 
                    if score > starting_score + EPSILON:
                        if self.verbose:
                            self.log(f"  Advantage found at Pos {i:2d}, Rot Jitter {r:2d} (Amp={amplitude:.1f}mm, RotAmp={rot_amp:.1f}deg): Score {score:10.6f} > {starting_score:10.6f}. EXITING EARLY.")
                        best_state = (cand_x, cand_y, target_angle)
                        part.move_to(best_state[0], best_state[1])
                        part.set_rotation(best_state[2])
                        return True
                    
                    if score > pos_best_score:
                        pos_best_score = score
                        pos_best_angle = target_angle

            if pos_best_angle is not None:
                current_state = (cand_x, cand_y, pos_best_angle)
                part.set_rotation(pos_best_angle) 
                
                if pos_best_score > best_score:
                    best_score = pos_best_score
                    best_state = current_state
                
                if self.verbose:
                    self.log(f"  PosStep {i:2d}: Pos=({cand_x:6.1f}, {cand_y:6.1f}), BestAngle={pos_best_angle:5.1f}, Score={pos_best_score:10.6f} (Wiggle)")
            else:
                part.move_to(current_state[0], current_state[1])
                part.set_rotation(current_state[2])
                if self.verbose:
                    self.log(f"  PosStep {i:2d}: Invalid")
        
        if best_score > starting_score + EPSILON:
            if self.verbose:
                self.log(f"Annealing finished with improvement. Best Score: {best_score:10.6f}")
            part.move_to(best_state[0], best_state[1])
            part.set_rotation(best_state[2])
            return True
        else:
            if self.verbose:
                self.log(f"Annealing finished (no improvement). Baseline: {starting_score:10.6f}")
            part.move_to(initial_x, initial_y)
            part.set_rotation(initial_angle)
            return False

    def _get_curve_p(self, p, curve_type):
        """Maps progress [0,1] to a curve-fitted multiplier."""
        p = max(0.0, min(1.0, p))
        if curve_type == "Logarithmic":
            return math.log10(1 + 9 * p)
        elif curve_type == "Power 1.5":
            return p ** 1.5
        elif curve_type == "Quadratic":
            return p ** 2
        elif curve_type == "Exponential":
            return (math.exp(3 * p) - 1) / (math.exp(3) - 1)
        return p # Linear
