import math
import random
from datetime import datetime
from shapely.geometry import Polygon, MultiPolygon, Point
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from shapely.affinity import translate, rotate, scale
from shapely.ops import unary_union, triangulate
from . import minkowski_utils

import FreeCAD
from ....datatypes.shape import Shape
from ....datatypes.sheet import Sheet
from .base_nester import BaseNester
from ....datatypes.placed_part import PlacedPart
import Part


class MinkowskiNester(BaseNester):
    """Nesting algorithm using Minkowski sums to create No-Fit Polygons."""
    def __init__(self, width, height, rotation_steps=1, discretize_edges=True, **kwargs):
        super().__init__(width, height, rotation_steps, **kwargs)
        self._debug_group = None
        self.log_callback = kwargs.get("log_callback")
        self._log_lock = Lock()

        self.discretize_edges = discretize_edges

    def nest(self, parts):
        """Overrides the base nester to add Minkowski-specific cleanup."""
        doc = FreeCAD.ActiveDocument
        if doc:
            debug_group = doc.getObject("MinkowskiDebug")
            if debug_group:
                # Using removeObject recursively is safer for groups
                doc.removeObject(debug_group.Name)
                doc.recompute()
        self._debug_group = None # Reset the handle
        return super().nest(parts)

    def _attempt_placement_on_sheet(self, part, sheet):
        """
        Overrides the base class method to use the Minkowski-specific validation
        that correctly handles placements within holes.
        """
        other_parts_polygons = [p.shape.polygon for p in sheet.parts if p.shape.polygon]
        union_of_other_parts = unary_union(other_parts_polygons) if other_parts_polygons else None
        
        placed_part_shape = self._try_place_part_on_sheet(part, sheet, union_of_other_parts)

        # Final validation using the Minkowski-aware checker.
        if placed_part_shape:
            if self._is_placement_valid_with_holes(
                placed_part_shape.polygon, sheet, union_of_other_parts
            ):
                sheet_origin = sheet.get_origin()
                placed_part_shape.placement = placed_part_shape.get_final_placement(sheet_origin)
                sheet.add_part(PlacedPart(placed_part_shape))
                return True
        
        if placed_part_shape:
            self.log(f"Nester returned an invalid placement for {part.id}. Discarding.", level="warning")
        return False

    def _draw_debug_poly(self, poly, name):
        """Helper to draw a shapely polygon for debugging."""
        pass

    def _draw_debug_point(self, x, y, z, name, color=None):
        """Helper to draw a point for debugging."""
        pass

    def log(self, message, level="message"):
        """Logs a message to the console or a UI callback if available."""
        with self._log_lock:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            log_message = f"{timestamp} MINKOWSKI: {message}"
            if self.log_callback:
                self.log_callback(log_message)
            else:
                if level == "warning":
                    FreeCAD.Console.PrintWarning(log_message + "\n")
                else:
                    FreeCAD.Console.PrintMessage(log_message + "\n")

    def _handle_first_part(self, part_to_place, sheet, angle):
        """Handles the placement of the first part on an empty sheet."""
        part_to_place.set_rotation(angle)
        # For the first part, we try to place its bottom-left corner at the origin.
        part_to_place.move_to(0, 0)
        if sheet.is_placement_valid(part_to_place):
            return part_to_place
        return None

    def _apply_nfp_to_part(self, placed_part, nfp_data):
        """Applies rotation and translation to a cached NFP for a specific placed part."""
        master_nfp = nfp_data["polygon"]
        placed_part_centroid = placed_part.shape.centroid
        xoff, yoff = placed_part_centroid.x, placed_part_centroid.y
        placed_part_angle = placed_part.angle

        # 1. Rotate the NFP by the placed part's angle (since we computed it for A@0)
        # We rotate around (0,0) because the NFP is centered relative to A's centroid at (0,0).
        rotated_nfp = rotate(master_nfp, placed_part_angle, origin=(0, 0))

        # 2. Translate to the placed part's position
        translated_nfp = translate(rotated_nfp, xoff=xoff, yoff=yoff)
        
        # Handle points (rotate then translate)
        def transform_point(p):
            # Rotate
            if placed_part_angle != 0:
                rad = math.radians(placed_part_angle)
                cos_a = math.cos(rad)
                sin_a = math.sin(rad)
                rx = p.x * cos_a - p.y * sin_a
                ry = p.x * sin_a + p.y * cos_a
                p = Point(rx, ry)
            # Translate
            return Point(p.x + xoff, p.y + yoff)

        translated_exterior_points = [transform_point(p) for p in nfp_data["exterior_points"]]
        translated_interior_points = []
        for interior_points in nfp_data["interior_points"]:
            translated_interior_points.append([transform_point(p) for p in interior_points])

        return {
            "polygon": translated_nfp,
            "exterior_points": translated_exterior_points,
            "interior_points": translated_interior_points,
        }

    def _generate_nfps(self, part_to_place, sheet, angle):
        """Generates the No-Fit Polygons for a given part and sheet, grouping by unique configuration."""
        # 1. Group parts by the cache key they would generate
        parts_by_key = {}
        part_to_place_master_label = part_to_place.source_freecad_object.Label
        
        for p in sheet.parts:
            placed_part_master_label = p.shape.source_freecad_object.Label
            placed_part_angle = p.angle
            
            # Normalize angles to relative rotation
            relative_angle = (angle - placed_part_angle) % 360.0
            relative_angle = round(relative_angle, 4)
            
            cache_key = (placed_part_master_label, part_to_place_master_label, relative_angle)
            
            if cache_key not in parts_by_key:
                parts_by_key[cache_key] = []
            parts_by_key[cache_key].append(p)

        # 2. Ensure NFPs are calculated for all unique keys (in parallel)
        unique_keys = list(parts_by_key.keys())
        
        def ensure_nfp(key):
            # Check cache first (fast check)
            if Shape.nfp_cache.get(key):
                return
            
            # If not in cache, calculate it.
            # We need actual shapes to calculate.
            # We can grab the first part from the group to get the shape_A
            first_part = parts_by_key[key][0]
            
            # We calculate for A@0 and B@relative
            self._calculate_and_cache_nfp(
                first_part.shape, 
                0.0, 
                part_to_place, 
                key[2], # relative_angle
                key
            )

        with ThreadPoolExecutor() as executor:
            list(executor.map(ensure_nfp, unique_keys))

        # 3. Now that cache is populated, generate the final translated NFPs for all parts
        results = []
        for key, parts in parts_by_key.items():
            nfp_data = Shape.nfp_cache.get(key)
            if not nfp_data: continue 
            
            for p in parts:
                res = self._apply_nfp_to_part(p, nfp_data) 
                if res: results.append(res)
                
        return results

    def _find_best_placement(
        self, part_to_place, sheet, angle, rotated_part_poly, individual_nfps, union_of_other_parts
    ):
        """Finds the best placement for a given rotation."""
        best_placement_info = {'metric': float('inf')}

        # 1. Generate candidate points from the NFPs.
        hole_candidates, external_candidates = self._get_candidate_positions(
            individual_nfps, rotated_part_poly
        )

        # We need the polygon at the current rotation to test placements.
        if not rotated_part_poly:
            return best_placement_info

        # 2. Find the best valid placement for this rotation, prioritizing holes.
        # The candidate points are for the centroid, so we need the centroid of the rotated part poly
        rotated_poly_centroid = rotated_part_poly.centroid

        # --- Stage 1: Check holes first ---
        hole_candidates.sort(key=lambda p: (p.y, p.x))
        for i, point in enumerate(hole_candidates):

            # The 'point' is where the centroid should be.
            dx = point.x - rotated_poly_centroid.x
            dy = point.y - rotated_poly_centroid.y
            test_polygon = translate(rotated_part_poly, xoff=dx, yoff=dy)

            is_valid = self._is_placement_valid_with_holes(
                test_polygon, sheet, union_of_other_parts
            )

            if is_valid:
                metric = point.y * self._bin_width + point.x
                if metric < best_placement_info['metric']:
                    best_placement_info = {
                        'x': point.x,
                        'y': point.y,
                        'angle': angle,
                        'metric': metric,
                    }
                return best_placement_info  # Found the best spot in a hole for this angle

        # --- Stage 2: If no hole fit, check external positions ---
        external_candidates.sort(key=lambda p: (p.y, p.x))
        for i, point in enumerate(external_candidates):

            # The 'point' is where the centroid should be.
            dx = point.x - rotated_poly_centroid.x
            dy = point.y - rotated_poly_centroid.y
            test_polygon = translate(rotated_part_poly, xoff=dx, yoff=dy)

            is_valid = self._is_placement_valid_with_holes(
                test_polygon, sheet, union_of_other_parts
            )

            if is_valid:
                metric = point.y * self._bin_width + point.x
                if metric < best_placement_info['metric']:
                    best_placement_info = {
                        'x': point.x,
                        'y': point.y,
                        'angle': angle,
                        'metric': metric,
                    }
                return best_placement_info  # Found the best external spot for this angle

        return best_placement_info

    def _evaluate_rotation(self, angle, part_to_place, sheet, union_of_other_parts):
        """
        Evaluates a single rotation angle to find the best possible placement.
        This is designed to be run in parallel for each angle.
        """
        # We don't move the part itself, we just get its polygon at the target rotation
        rotated_part_poly = rotate(part_to_place.original_polygon, angle, origin='centroid')

        self.log(f"  - Trying part '{part_to_place.id}' on sheet {sheet.id} at rotation {angle:.1f} degrees.")

        # 1. Compute the individual No-Fit Polygons (NFPs). This is already parallelized.
        individual_nfps = self._generate_nfps(part_to_place, sheet, angle)

        # 2. Find the best placement for this specific angle.
        placement_info = self._find_best_placement(
            part_to_place, sheet, angle, rotated_part_poly, individual_nfps, union_of_other_parts
        )

        return placement_info

    def _try_place_part_on_sheet(self, part_to_place, sheet, union_of_other_parts):
        """
        Tries to place a single shape on a given sheet using Minkowski sums.
        Returns the placed shape on success, None on failure.
        """
        
        # Ensure the part has an original_polygon to rotate from. This is critical.
        # If it's missing, the part cannot be rotated, leading to silent failure.
        if part_to_place.original_polygon is None and part_to_place.polygon is not None:
            part_to_place.original_polygon = part_to_place.polygon

        # --- Handle the first part on an empty sheet (no need for parallelization) ---
        if not sheet.parts:
            for i in range(part_to_place.rotation_steps):
                angle = i * (360.0 / part_to_place.rotation_steps) if part_to_place.rotation_steps > 1 else 0.0
                placed_part = self._handle_first_part(part_to_place, sheet, angle)
                if placed_part:
                    return placed_part
            return None # Could not place even on an empty sheet

        # --- Parallel evaluation of all rotation angles ---
        best_placement_info = {'metric': float('inf')}
        with ThreadPoolExecutor() as executor:
            angles = [i * (360.0 / part_to_place.rotation_steps) for i in range(part_to_place.rotation_steps)]
            
            future_to_angle = {executor.submit(self._evaluate_rotation, angle, part_to_place, sheet, union_of_other_parts): angle for angle in angles}

            for future in as_completed(future_to_angle):
                placement_info = future.result()
                if placement_info and placement_info.get('metric', float('inf')) < best_placement_info.get('metric', float('inf')):
                    best_placement_info = placement_info

        # After checking all rotations and their candidates, apply the best one found.
        if best_placement_info.get('x') is not None:
            best_x = best_placement_info['x']
            best_y = best_placement_info['y']
            best_angle = best_placement_info['angle']
            
            # Apply the best found transformation to the actual part object.
            part_to_place.set_rotation(best_angle, reposition=False)
            
            # Move the part so its centroid is at the best found (x, y) point.
            current_centroid = part_to_place.centroid
            dx = best_x - current_centroid.x
            dy = best_y - current_centroid.y
            part_to_place.move(dx, dy)

            return part_to_place

        return None

    def _is_placement_valid_with_holes(self, polygon_to_check, sheet, union_of_other_parts):
        """
        Custom validation function for Minkowski that correctly handles holes.
        This logic is self-contained and does not modify the Sheet class.
        """
        # 1. Check containment within sheet boundaries
        bin_polygon = Polygon([(0, 0), (sheet.width, 0), (sheet.width, sheet.height), (0, sheet.height)])
        if not bin_polygon.contains(polygon_to_check):
            return False

        # 2. Check against the pre-calculated union of other parts
        if not union_of_other_parts:
            return True # No other parts to collide with

        # --- Advanced Collision Check with Hole Support ---
        if not polygon_to_check.intersects(union_of_other_parts):
            return True # No intersection at all, definitely valid.

        # There is an intersection. Check if it's a valid placement inside a hole.
        intersection_area = polygon_to_check.intersection(union_of_other_parts).area
        if intersection_area < 1e-9:
            return True # Negligible intersection is likely just touching, which is allowed.

        # The placement is only valid if the part is entirely contained within one of the holes.
        if union_of_other_parts.geom_type == 'Polygon':
            return any(hole.contains(polygon_to_check) for hole in union_of_other_parts.interiors)
        elif union_of_other_parts.geom_type == 'MultiPolygon':
            return any(
                hole.contains(polygon_to_check)
                for poly in union_of_other_parts.geoms
                for hole in poly.interiors
            )
        return False

    def _discretize_edge(self, line):
        """Helper to discretize a line string (an edge of the polygon)."""
        from shapely.geometry import Point
        points = [Point(line.coords[0])]
        length = line.length
        if length > self.step_size:
            num_segments = int(length / self.step_size)
            for i in range(1, num_segments):
                points.append(line.interpolate(float(i) / num_segments, normalized=True))
        points.append(Point(line.coords[-1]))
        return points

    def _get_candidate_positions(self, nfps, part_to_place_poly=None):
        """
        Generates candidate placement points from the vertices of the No-Fit Polygon.
        These are the most likely points for an optimal, touching placement.
        """
        self.log("      - Getting candidate positions...")
        from shapely.geometry import Point, MultiPoint # type: ignore
        external_points = []
        hole_points = []

        # Add the sheet origin as a primary external candidate.
        # This is where the part's centroid would be placed.
        if part_to_place_poly:
            min_x, min_y, max_x, max_y = part_to_place_poly.bounds
            # Add the point that would place the part's bottom-left at the origin
            external_points.append(Point(-min_x, -min_y))

        # The vertices of each individual NFP are the primary candidates for placement of the part's reference point.
        # These represent the locations where the part can touch an existing part.
        for nfp_data in nfps:
            external_points.extend(nfp_data["exterior_points"])
            for interior_points in nfp_data["interior_points"]:
                hole_points.extend(interior_points)

        # --- Pre-filter candidate points ---
        # Remove points that are guaranteed to be outside the sheet boundaries.
        # This is a huge performance optimization.
        unique_externals = list(MultiPoint(external_points).geoms)
        valid_external_candidates = []
        for p in unique_externals:
            # A simple check: if the candidate point itself is outside the bin, it's invalid.
            if 0 <= p.x <= self._bin_width and 0 <= p.y <= self._bin_height:
                valid_external_candidates.append(p)

        unique_holes = list(MultiPoint(hole_points).geoms)
        valid_hole_candidates = []
        for p in unique_holes:
            if 0 <= p.x <= self._bin_width and 0 <= p.y <= self._bin_height:
                valid_hole_candidates.append(p)

        return valid_hole_candidates, valid_external_candidates

    def _calculate_and_cache_nfp(self, shape_A, angle_A, part_to_place, angle_B, cache_key):
        """Calculates the NFP between two polygons and stores it in the cache."""
        with Shape.nfp_cache_lock:
            # The cache might hold the data, so we check here again.
            cached_nfp_data = Shape.nfp_cache.get(cache_key)
            if cached_nfp_data:
                return cached_nfp_data

        self.log(f"      - Generating new NFP for '{shape_A.id}' ({angle_A:.1f} deg) and '{part_to_place.id}' ({angle_B:.1f} deg)")

        poly_A_master = shape_A.original_polygon
        poly_B_master = part_to_place.original_polygon
        
        nfp_exterior = minkowski_utils.minkowski_sum(poly_A_master, angle_A, False, poly_B_master, angle_B, True, self.log)
        
        nfp_interiors = []
        if poly_A_master and poly_A_master.interiors:
            poly_B_rotated = rotate(poly_B_master, angle_B, origin='centroid')
            for hole in poly_A_master.interiors:
                hole_poly_unrotated = Polygon(hole.coords)
                hole_poly_rotated = rotate(hole_poly_unrotated, angle_A, origin=poly_A_master.centroid)
                
                if (poly_B_rotated.bounds[2] - poly_B_rotated.bounds[0] < hole_poly_rotated.bounds[2] - hole_poly_rotated.bounds[0] and
                    poly_B_rotated.bounds[3] - poly_B_rotated.bounds[1] < hole_poly_rotated.bounds[3] - hole_poly_rotated.bounds[1] and
                        poly_B_rotated.area < hole_poly_rotated.area):
                    
                    ifp_raw = minkowski_utils.minkowski_difference(hole_poly_rotated, 0, poly_B_master, angle_B, self.log)
                    if ifp_raw and ifp_raw.area > 0:
                        if ifp_raw.geom_type == 'Polygon':
                            nfp_interiors.append(ifp_raw.exterior)
                        elif ifp_raw.geom_type == 'MultiPolygon':
                            for p in ifp_raw.geoms:
                                nfp_interiors.append(p.exterior)
        
        master_nfp = Polygon(nfp_exterior.exterior, nfp_interiors) if nfp_exterior and nfp_exterior.area > 0 else None
        
        nfp_data = None
        if master_nfp:
            nfp_data = {"polygon": master_nfp}
            if self.discretize_edges:
                nfp_data["exterior_points"] = self._discretize_edge(master_nfp.exterior)
                nfp_data["interior_points"] = [self._discretize_edge(interior) for interior in master_nfp.interiors]
            else:
                nfp_data["exterior_points"] = [Point(x, y) for x, y in master_nfp.exterior.coords]
                nfp_data["interior_points"] = [[Point(x, y) for x, y in interior.coords] for interior in master_nfp.interiors]

        with Shape.nfp_cache_lock:
            Shape.nfp_cache[cache_key] = nfp_data
        
        return nfp_data
