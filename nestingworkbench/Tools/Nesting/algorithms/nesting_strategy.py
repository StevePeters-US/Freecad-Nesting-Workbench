
import math
import os
import random
import copy
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from shapely.geometry import Polygon, Point

from shapely.prepared import prep
from shapely.affinity import rotate, translate

import FreeCAD
from ....datatypes.sheet import Sheet
from ....datatypes.placed_part import PlacedPart
from . import genetic_utils
from .minkowski_engine import MinkowskiEngine

class PlacementOptimizer:
    """
    Handles the geometric logic of finding the best position for a part on a sheet.
    """
    def __init__(self, engine, rotation_steps, search_direction, log_callback=None, trial_callback=None):
        self.engine = engine
        self.rotation_steps = max(1, rotation_steps)
        self.search_direction = search_direction
        self.log_callback = log_callback
        self.trial_callback = trial_callback  # Called for each trial placement in simulation mode

    def log(self, message):
        if self.log_callback:
            self.log_callback(message)

    def find_best_placement(self, part, sheet):
        """
        Parallel evaluation of rotations to find best spot.
        """
        if part.original_polygon is None and part.polygon is not None:
            part.original_polygon = part.polygon
            
        # Pre-group placed parts by (master_label, angle)
        placed_parts_grouped = defaultdict(list)
        for p in sheet.parts:
            key = (p.shape.source_freecad_object.Label, p.angle)
            placed_parts_grouped[key].append(p)
            
        direction = self.search_direction
        if direction is None:
             angle_rad = random.uniform(0, 2 * math.pi)
             direction = (math.cos(angle_rad), math.sin(angle_rad))

        best_result = {'metric': float('inf')}
        
        # Use per-part rotation_steps if available, otherwise use global
        part_rotation_steps = getattr(part, 'rotation_steps', None)
        if part_rotation_steps is None or part_rotation_steps < 1:
            part_rotation_steps = self.rotation_steps
        part_rotation_steps = max(1, part_rotation_steps)
        
        # Parallel execution
        with ThreadPoolExecutor() as executor:
            angles = [i * (360.0 / part_rotation_steps) for i in range(part_rotation_steps)]
            futures = {
                executor.submit(self._evaluate_rotation, angle, part, placed_parts_grouped, sheet, direction): angle 
                for angle in angles
            }
            
            for future in as_completed(futures):
                try:
                    res = future.result()
                    if res and res['metric'] < best_result['metric']:
                        best_result = res
                        # Call trial callback from main thread for each better result found
                        if self.trial_callback and best_result.get('x') is not None:
                            self.trial_callback(part, best_result['angle'], best_result['x'], best_result['y'])
                except Exception as e:
                    self.log(f"Error in rotation evaluation thread: {e}")
        
        if best_result.get('x') is not None:
             part.set_rotation(best_result['angle'], reposition=False)
             curr = part.centroid
             part.move(best_result['x'] - curr.x, best_result['y'] - curr.y)
             return part
        return None

    def _evaluate_rotation(self, angle, part, placed_parts_grouped, sheet, direction):
        # 1. Get Combined NFP from Engine (Incrementally Cached on Sheet)
        nfp_entry = self.engine.get_global_nfp_for(part, angle, sheet)
        
        # Check for NFP calculation failure
        if nfp_entry is None:
            return {'metric': float('inf')}
        
        bin_polygon = self.engine.bin_polygon
        
        # Prepare geometry for fast containment check
        union_poly = nfp_entry['polygon']
        prepared_nfp = nfp_entry.get('prepared')
        if not prepared_nfp and not union_poly.is_empty:
             prepared_nfp = prep(union_poly)
             with sheet.nfp_cache_lock:
                 # Double-check: another thread may have set it
                 if not nfp_entry.get('prepared'):
                     nfp_entry['prepared'] = prepared_nfp
                 else:
                     prepared_nfp = nfp_entry['prepared']

        # 2. Generate Candidates
        rotated_poly = rotate(part.original_polygon, angle, origin='centroid')
        if not rotated_poly: return {'metric': float('inf')}

        # A. Bin Candidates (Corners of part vs Corners of bin)
        min_x, min_y, max_x, max_y = rotated_poly.bounds
        ext_cands = []
        w_bin, h_bin = self.engine.bin_width, self.engine.bin_height
        
        # Essential placement points
        # Bottom-Left at (0,0) -> (-min_x, -min_y)
        ext_cands.append(Point(-min_x, -min_y)) 
        ext_cands.append(Point(w_bin - max_x, -min_y))
        ext_cands.append(Point(-min_x, h_bin - max_y))
        ext_cands.append(Point(w_bin - max_x, h_bin - max_y))

        # B. NFP Boundary Candidates
        # Filter points that are within bin bounds
        valid_points = []
        for p in nfp_entry['points']:
             if 0 <= p.x <= w_bin and 0 <= p.y <= h_bin:
                 valid_points.append(p)
        ext_cands.extend(valid_points)

        # 3. Score Candidates
        centroid = rotated_poly.centroid
        dir_x, dir_y = direction
        
        best = {'metric': float('inf')}
        valid_count = 0
        rejected_nfp = 0
        rejected_bounds = 0
        
        def score_point(pt):
             nonlocal rejected_nfp, rejected_bounds
             # A. Check NFP Collision (Fastest if cached)
             if prepared_nfp and prepared_nfp.contains(pt): 
                 rejected_nfp += 1
                 return None
                 
             dx, dy = pt.x - centroid.x, pt.y - centroid.y
             
             # B. Check Bounds
             test_poly = translate(rotated_poly, xoff=dx, yoff=dy)
             if not bin_polygon.contains(test_poly):
                 rejected_bounds += 1
                 return None

             return pt.x * (-dir_x) + pt.y * (-dir_y)

        # Sort candidates (heuristic optimization)
        # ext_cands.sort(key=lambda p: p.x * (-dir_x) + p.y * (-dir_y))

        for pt in ext_cands:
            valid_metric = score_point(pt)
            if valid_metric is not None:
                valid_count += 1
                if valid_metric < best['metric']:
                    best = {'x': pt.x, 'y': pt.y, 'angle': angle, 'metric': valid_metric}
        
        return best


class Nester:
    """
    The main nesting algorithm class. 
    It orchestrates the nesting process using PlacementOptimizer and MinkowskiEngine.
    """
    def __init__(self, width, height, rotation_steps=1, **kwargs):
        self.bin_width = width
        self.bin_height = height
        self.spacing = kwargs.get("spacing", 0)
        self.search_direction = kwargs.get("search_direction", (0, -1)) # Default Down
        
        # Optimization settings (kept for backwards compatibility, GA now in controller)
        self.population_size = kwargs.get("population_size", 1)
        self.generations = kwargs.get("generations", 1)
        self.mutation_rate = 0.1
        self.elite_size = max(1, int(self.population_size * 0.1))
        
        # Logging control
        self.quiet = kwargs.get("quiet", False)  # If True, suppress per-part logs
        self.log_callback = kwargs.get("log_callback")
        self.trial_callback = kwargs.get("trial_callback")  # For visualizing trial placements
        self.part_start_callback = kwargs.get("part_start_callback")  # Called when starting to place a part
        self.part_end_callback = kwargs.get("part_end_callback")  # Called after part is placed
        self.progress_callback = kwargs.get("progress_callback") # Called with (current, total)
        
        step_size = kwargs.get("step_size", 5.0) 
        self.engine = MinkowskiEngine(width, height, step_size, log_callback=self.log_callback)
        self.optimizer = PlacementOptimizer(self.engine, rotation_steps, self.search_direction, self.log_callback, self.trial_callback)

        self.parts_to_place = []
        self.sheets = []
        self.update_callback = None # Can be set externally

        # Background NFP pre-computation
        self._precompute_pool = ThreadPoolExecutor(max_workers=os.cpu_count())
        self._precomputed_keys = set()

    def log(self, message, level="message"):
        if self.log_callback:
            self.log_callback(message)
        else:
            if level == "warning":
                FreeCAD.Console.PrintWarning(f"NESTER: {message}\n")
            else:
                FreeCAD.Console.PrintMessage(f"NESTER: {message}\n")

    def nest(self, parts, sort=True):
        """
        Main entry point for nesting.
        
        NOTE: GA optimization is now handled at the controller level using LayoutManager.
        This method just runs standard greedy nesting.
        """
        # Cleanup debug objects
        doc = FreeCAD.ActiveDocument
        if doc and doc.getObject("MinkowskiDebug"):
            doc.removeObject("MinkowskiDebug")
            doc.recompute()

        return self._nest_standard(parts, sort=sort)

    def _nest_standard(self, parts, sort=True, quiet=None):
        """
        Standard greedy nesting strategy.
        
        Args:
            parts: List of parts to nest
            sort: Whether to sort by area (largest first)
            quiet: If True, suppresses logging and callbacks. Defaults to self.quiet.
        """
        # Use instance quiet setting if not explicitly passed
        if quiet is None:
            quiet = self.quiet
        current_parts = list(parts)
        if sort:
            current_parts.sort(key=lambda p: p.area, reverse=True)
            
        sheets = []
        unplaced_parts = []
        total_parts = len(current_parts)
        
        for i, part in enumerate(current_parts):
            if not quiet:
                self.log(f"Processing part {i+1}/{total_parts}: {part.id}")
                if self.progress_callback:
                    self.progress_callback(i + 1, total_parts, f"Placing {part.id}...")
            start_part_time = datetime.now()
            placed = False
            
            # Notify start of part placement (for highlighting master shapes)
            if not quiet and self.part_start_callback:
                self.part_start_callback(part)
            
            # 1. Try existing sheets
            for sheet_idx, sheet in enumerate(sheets):
                if (sheet.width * sheet.height - sheet.used_area) < part.area: continue

                if self._attempt_placement_on_sheet(part, sheet):
                    placed = True
                    if not quiet:
                        elapsed = (datetime.now() - start_part_time).total_seconds()
                        self.log(f"  -> Placed on Sheet {sheet_idx+1} ({elapsed:.4f}s)")
                        if self.update_callback: self.update_callback(part, sheet)
                    break
            
            # 2. Try new sheet
            if not placed:
                new_sheet = Sheet(len(sheets), self.bin_width, self.bin_height, spacing=self.spacing)
                if self._attempt_placement_on_sheet(part, new_sheet):
                    sheets.append(new_sheet)
                    placed = True
                    if not quiet:
                        elapsed = (datetime.now() - start_part_time).total_seconds()
                        self.log(f"  -> Placed on New Sheet {len(sheets)} ({elapsed:.4f}s)")
                        if self.update_callback: self.update_callback(part, new_sheet)
                else:
                    unplaced_parts.append(part)
                    if not quiet:
                        self.log(f"  -> FAILED to place in {(datetime.now() - start_part_time).total_seconds():.4f}s")
            
            # Notify end of part placement (for unhighlighting master shapes)
            if not quiet and self.part_end_callback:
                self.part_end_callback(part, placed)
            
            # Submit background NFP pre-computation for remaining parts
            if placed and i < total_parts - 1:
                self._submit_precomputation(sheets, current_parts[i+1:])
        
        # Shut down precompute pool (don't wait for pending futures)
        self._precompute_pool.shutdown(wait=False)
        self._precomputed_keys.clear()
        return sheets, unplaced_parts

    def _attempt_placement_on_sheet(self, part, sheet):
        """Delegates to PlacementOptimizer."""
        placed_part = self.optimizer.find_best_placement(part, sheet)
        
        if placed_part:
            # We trust the PlacementOptimizer (and NFP engine) to have found a valid spot.
            placed_part.placement = placed_part.get_final_placement(sheet.get_origin())
            new_placed_part = PlacedPart(placed_part)
            sheet.add_part(new_placed_part)
            return True
        return False

    def _submit_precomputation(self, sheets, remaining_parts):
        """Submit background NFP computations for remaining parts against all placed parts.
        
        While the main thread is placing the current part, background threads
        compute master NFPs that will be needed for future parts. When those
        parts are actually placed, their NFPs are already cached.
        """
        from ....datatypes.shape import Shape
        
        # Collect all placed parts across all sheets
        placed_parts = []
        for sheet in sheets:
            for pp in sheet.parts:
                placed_parts.append(pp)
        
        if not placed_parts:
            return
        
        for remaining in remaining_parts:
            # Per-part rotation steps
            rot_steps = getattr(remaining, 'rotation_steps', None)
            if rot_steps is None or rot_steps < 1:
                rot_steps = self.optimizer.rotation_steps
            rot_steps = max(1, rot_steps)
            angles = [i * (360.0 / rot_steps) for i in range(rot_steps)]
            
            for placed in placed_parts:
                for angle in angles:
                    relative_angle = (angle - placed.angle) % 360.0
                    if abs(relative_angle - 360.0) < 1e-5:
                        relative_angle = 0.0
                    relative_angle = round(relative_angle, 4)
                    
                    cache_key = (
                        placed.shape.source_freecad_object.Label,
                        remaining.source_freecad_object.Label,
                        relative_angle,
                        remaining.spacing,
                        remaining.deflection,
                        remaining.simplification
                    )
                    
                    # Skip if already submitted or cached
                    if cache_key in self._precomputed_keys:
                        continue
                    self._precomputed_keys.add(cache_key)
                    
                    with Shape.nfp_cache_lock:
                        if cache_key in Shape.nfp_cache:
                            continue
                    
                    # Fire-and-forget: compute in background, result goes to cache
                    self._precompute_pool.submit(
                        self.engine._calculate_and_cache_nfp,
                        placed.shape, 0.0, remaining, relative_angle, cache_key
                    )