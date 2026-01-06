
import math
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
                res = future.result()
                if res and res['metric'] < best_result['metric']:
                    best_result = res
                    # Call trial callback from main thread for each better result found
                    if self.trial_callback and best_result.get('x') is not None:
                        self.trial_callback(part, best_result['angle'], best_result['x'], best_result['y'])
        
        if best_result.get('x') is not None:
             part.set_rotation(best_result['angle'], reposition=False)
             curr = part.centroid
             part.move(best_result['x'] - curr.x, best_result['y'] - curr.y)
             return part
        return None

    def _evaluate_rotation(self, angle, part, placed_parts_grouped, sheet, direction):
        # 1. Get Combined NFP from Engine (Incrementally Cached on Sheet)
        nfp_entry = self.engine.get_global_nfp_for(part, angle, sheet)
        
        bin_polygon = self.engine.bin_polygon
        
        # Prepare geometry for fast containment check
        union_poly = nfp_entry['polygon']
        prepared_nfp = nfp_entry.get('prepared')
        if not prepared_nfp and not union_poly.is_empty:
             prepared_nfp = prep(union_poly)
             nfp_entry['prepared'] = prepared_nfp

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
        
        def score_point(pt):
             # A. Check NFP Collision (Fastest if cached)
             if prepared_nfp and prepared_nfp.contains(pt): 
                 return None
                 
             dx, dy = pt.x - centroid.x, pt.y - centroid.y
             
             # B. Check Bounds
             test_poly = translate(rotated_poly, xoff=dx, yoff=dy)
             if not bin_polygon.contains(test_poly): return None

             return pt.x * (-dir_x) + pt.y * (-dir_y)

        # Sort candidates (heuristic optimization)
        # ext_cands.sort(key=lambda p: p.x * (-dir_x) + p.y * (-dir_y))

        for pt in ext_cands:
            valid_metric = score_point(pt)
            if valid_metric is not None:
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
        
        # Optimization settings
        self.population_size = kwargs.get("population_size", 20)
        self.generations = kwargs.get("generations", 1)
        self.mutation_rate = 0.1
        self.elite_size = max(1, int(self.population_size * 0.1))
        
        self.log_callback = kwargs.get("log_callback")
        self.trial_callback = kwargs.get("trial_callback")  # For visualizing trial placements
        self.part_start_callback = kwargs.get("part_start_callback")  # Called when starting to place a part
        self.part_end_callback = kwargs.get("part_end_callback")  # Called after part is placed
        
        step_size = kwargs.get("step_size", 5.0) 
        self.engine = MinkowskiEngine(width, height, step_size, log_callback=self.log_callback)
        self.optimizer = PlacementOptimizer(self.engine, rotation_steps, self.search_direction, self.log_callback, self.trial_callback)

        self.parts_to_place = []
        self.sheets = []
        self.update_callback = None # Can be set externally

    def log(self, message, level="message"):
        if self.log_callback:
            self.log_callback(message)
        else:
            if level == "warning":
                FreeCAD.Console.PrintWarning(f"NESTER: {message}\n")
            else:
                FreeCAD.Console.PrintMessage(f"NESTER: {message}\n")

    def nest(self, parts, sort=True):
        """Main entry point for nesting."""
        # Cleanup
        doc = FreeCAD.ActiveDocument
        if doc and doc.getObject("MinkowskiDebug"):
            doc.removeObject("MinkowskiDebug")
            doc.recompute()

        if self.generations > 1:
            return self._nest_genetic(parts)
        else:
            return self._nest_standard(parts, sort=sort)

    def _nest_standard(self, parts, sort=True):
        """Standard greedy nesting strategy."""
        current_parts = list(parts)
        if sort:
            current_parts.sort(key=lambda p: p.area, reverse=True)
            
        sheets = []
        unplaced_parts = []
        total_parts = len(current_parts)
        
        for i, part in enumerate(current_parts):
            self.log(f"Processing part {i+1}/{total_parts}: {part.id}")
            start_part_time = datetime.now()
            placed = False
            
            # Notify start of part placement (for highlighting master shapes)
            if self.part_start_callback:
                self.part_start_callback(part)
            
            # 1. Try existing sheets
            for sheet_idx, sheet in enumerate(sheets):
                if (sheet.width * sheet.height - sheet.used_area) < part.area: continue

                if self._attempt_placement_on_sheet(part, sheet):
                    placed = True
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
                    elapsed = (datetime.now() - start_part_time).total_seconds()
                    self.log(f"  -> Placed on New Sheet {len(sheets)} ({elapsed:.4f}s)")
                    if self.update_callback: self.update_callback(part, new_sheet)
                else:
                    unplaced_parts.append(part)
                    self.log(f"  -> FAILED to place in {(datetime.now() - start_part_time).total_seconds():.4f}s")
            
            # Notify end of part placement (for unhighlighting master shapes)
            if self.part_end_callback:
                self.part_end_callback(part, placed)
        
        return sheets, unplaced_parts

    def _nest_genetic(self, parts):
        """Genetic optimization loop."""
        self.log(f"Starting Genetic Optimization with {self.generations} generations.")
        rotation_steps = self.optimizer.rotation_steps
        
        population = [genetic_utils.create_random_chromosome(parts, rotation_steps) 
                      for _ in range(self.population_size)]
        
        best_solution = None
        
        for gen in range(self.generations):
            self.log(f"Generation {gen+1}/{self.generations}...")
            ranked_population = []
            
            for chrom in population:
                fitness = self._calculate_fitness(chrom)
                ranked_population.append((fitness, chrom))
            
            ranked_population.sort(key=lambda x: x[0])
            
            if best_solution is None or ranked_population[0][0] < best_solution[0]:
                best_solution = ranked_population[0]
                self.log(f"  > New Best Fitness: {best_solution[0]:.2f}")
            
            if gen < self.generations - 1:
                next_pop = [sol[1] for sol in ranked_population[:self.elite_size]]
                while len(next_pop) < self.population_size:
                    p1 = genetic_utils.tournament_selection(ranked_population)
                    p2 = genetic_utils.tournament_selection(ranked_population)
                    child = genetic_utils.ordered_crossover(p1, p2)
                    genetic_utils.mutate_chromosome(child, self.mutation_rate, rotation_steps)
                    next_pop.append(child)
                population = next_pop

        self.log("Running final placement for best solution...")
        return self._nest_standard(best_solution[1], sort=False)

    def _calculate_fitness(self, chromosome):
        """Calculates fitness (lower is better)."""
        test_parts = [copy.deepcopy(p) for p in chromosome]
        # Run standard nesting silently (could suppress logging here if needed)
        sheets, unplaced = self._nest_standard(test_parts, sort=False)
        
        if not sheets: return float('inf')
        
        fitness = len(sheets) * self.bin_width * self.bin_height
        
        # Add used area of last sheet
        last_sheet = sheets[-1]
        if last_sheet.parts:
            min_x = min(p.shape.bounding_box()[0] for p in last_sheet.parts)
            min_y = min(p.shape.bounding_box()[1] for p in last_sheet.parts)
            max_x = max(p.shape.bounding_box()[0] + p.shape.bounding_box()[2] for p in last_sheet.parts)
            max_y = max(p.shape.bounding_box()[1] + p.shape.bounding_box()[3] for p in last_sheet.parts)
            fitness += (max_x - min_x) * (max_y - min_y)
            
        if unplaced:
            fitness += len(unplaced) * self.bin_width * self.bin_height * 10
            
        return fitness

    def _attempt_placement_on_sheet(self, part, sheet):
        """Delegates to PlacementOptimizer."""
        # Removed early unary_union calculation for optimization
        
        placed_part = self.optimizer.find_best_placement(part, sheet)
        
        if placed_part:
            # We trust the PlacementOptimizer (and NFP engine) to have found a valid spot.
            # The expensive unary_union check is unnecessary if NFP logic is correct.
            placed_part.placement = placed_part.get_final_placement(sheet.get_origin())
            sheet.add_part(PlacedPart(placed_part))
            return True
        return False