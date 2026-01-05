
import math
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from shapely.geometry import Polygon, MultiPolygon, Point, MultiPoint
from shapely.affinity import translate, rotate
from shapely.ops import unary_union
from . import minkowski_utils
from ....datatypes.shape import Shape

class MinkowskiEngine:
    """
    Handles geometric operations for Minkowski nesting, such as NFP generation,
    candidate point finding, and placement validation.
    """
    def __init__(self, bin_width, bin_height, step_size, discretize_edges=True, log_callback=None):
        self.bin_width = bin_width
        self.bin_height = bin_height
        self.step_size = step_size
        self.discretize_edges = discretize_edges
        self.log_callback = log_callback
        self._log_lock = Lock()

    def log(self, message):
        if self.log_callback:
            with self._log_lock:
                self.log_callback("MINKOWSKI_ENGINE: " + message)
        else:
             # Fallback to FreeCAD console if no callback is wired
             import FreeCAD
             FreeCAD.Console.PrintMessage(f"MINKOWSKI_ENGINE: {message}\n")

    def is_placement_valid_with_holes(self, polygon_to_check, sheet, union_of_other_parts):
        """
        Custom validation function for Minkowski that correctly handles holes.
        """
        # 1. Check containment within sheet boundaries
        bin_polygon = Polygon([(0, 0), (self.bin_width, 0), (self.bin_width, self.bin_height), (0, self.bin_height)])
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

    def get_candidate_positions(self, nfps, part_to_place_poly=None):
        """
        Generates candidate placement points from the vertices of the No-Fit Polygon.
        """
        external_points = []
        hole_points = []

        # Add the sheet origin as a primary external candidate.
        if part_to_place_poly:
            min_x, min_y, max_x, max_y = part_to_place_poly.bounds
            
            # 1. Bottom-Left: Part's min_x, min_y at 0,0
            external_points.append(Point(-min_x, -min_y))
            
            w_bin = self.bin_width
            h_bin = self.bin_height
            
            external_points.append(Point(w_bin - max_x, -min_y)) # Bottom-Right
            external_points.append(Point(-min_x, h_bin - max_y)) # Top-Left
            external_points.append(Point(w_bin - max_x, h_bin - max_y)) # Top-Right

        for nfp_data in nfps:
            external_points.extend(nfp_data["exterior_points"])
            for interior_points in nfp_data["interior_points"]:
                hole_points.extend(interior_points)

        # Pre-filter candidate points
        unique_externals = list(MultiPoint(external_points).geoms)
        valid_external_candidates = []
        for p in unique_externals:
            if 0 <= p.x <= self.bin_width and 0 <= p.y <= self.bin_height:
                valid_external_candidates.append(p)

        unique_holes = list(MultiPoint(hole_points).geoms)
        valid_hole_candidates = []
        for p in unique_holes:
            if 0 <= p.x <= self.bin_width and 0 <= p.y <= self.bin_height:
                valid_hole_candidates.append(p)

        return valid_hole_candidates, valid_external_candidates

    def generate_nfps(self, part_to_place, placed_parts_grouped, angle):
        """
        Generates the No-Fit Polygons for a given part and existing placed parts.
        
        Args:
            part_to_place: The Shape object being placed.
            placed_parts_grouped: A dict mapping (master_label, placed_angle) -> list of PlacedPart objects.
            angle: The current rotation angle of part_to_place.
        """
        results = []
        part_to_place_master_label = part_to_place.source_freecad_object.Label

        # Iterate over uniqueness of placed parts instead of every instance
        for (placed_label, placed_angle), parts in placed_parts_grouped.items():
            
            # Normalize angles to relative rotation
            relative_angle = (angle - placed_angle) % 360.0
            relative_angle = round(relative_angle, 4)
            
            # Key includes spacing and resolution to ensure complete uniqueness of geometry
            cache_key = (
                placed_label, 
                part_to_place_master_label, 
                relative_angle, 
                part_to_place.spacing,
                part_to_place.resolution
            )
            
            # 1. Retrieve or Calculate NFP
            nfp_data = Shape.nfp_cache.get(cache_key)
            if not nfp_data:
                # self.log(f"DEBUG: NFP cache MISS for {cache_key}")
                # Calculate synchronously to avoid nested thread pool overhead
                # The outer loop in Nester is already parallelized
                first_part = parts[0]
                nfp_data = self._calculate_and_cache_nfp(
                    first_part.shape, 
                    0.0, 
                    part_to_place, 
                    relative_angle, 
                    cache_key
                )
            # else:
            #     self.log(f"DEBUG: NFP cache HIT for {cache_key}")
            
            if not nfp_data:
                continue

            # 2. Apply NFP to all instances in this group
            for p in parts:
                res = self._apply_nfp_to_part(p, nfp_data) 
                if res: results.append(res)
                
        return results

    def _calculate_and_cache_nfp(self, shape_A, angle_A, part_to_place, angle_B, cache_key):
        with Shape.nfp_cache_lock:
            cached_nfp_data = Shape.nfp_cache.get(cache_key)
            if cached_nfp_data:
                return cached_nfp_data

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

    def _apply_nfp_to_part(self, placed_part, nfp_data):
        master_nfp = nfp_data["polygon"]
        placed_part_centroid = placed_part.shape.centroid
        xoff, yoff = placed_part_centroid.x, placed_part_centroid.y
        placed_part_angle = placed_part.angle

        rotated_nfp = rotate(master_nfp, placed_part_angle, origin=(0, 0))
        translated_nfp = translate(rotated_nfp, xoff=xoff, yoff=yoff)
        
        def transform_point(p):
            if placed_part_angle != 0:
                rad = math.radians(placed_part_angle)
                cos_a = math.cos(rad)
                sin_a = math.sin(rad)
                rx = p.x * cos_a - p.y * sin_a
                ry = p.x * sin_a + p.y * cos_a
                p = Point(rx, ry)
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

    def _discretize_edge(self, line):
        points = [Point(line.coords[0])]
        length = line.length
        if length > self.step_size:
            num_segments = int(length / self.step_size)
            for i in range(1, num_segments):
                points.append(line.interpolate(float(i) / num_segments, normalized=True))
        points.append(Point(line.coords[-1]))
        return points
