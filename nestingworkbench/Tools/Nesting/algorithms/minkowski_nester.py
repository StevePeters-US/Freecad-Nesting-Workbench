import math
import pdb
import random
from shapely.geometry import Polygon, MultiPolygon
from shapely.affinity import translate, rotate, scale
from shapely.ops import unary_union
from PySide import QtGui

import FreeCAD
from ....datatypes.sheet import Sheet
from .base_nester import BaseNester

class MinkowskiNester(BaseNester):
    """Nesting algorithm using Minkowski sums to create No-Fit Polygons."""
    def __init__(self, width, height, rotation_steps=1, **kwargs):
        super().__init__(width, height, rotation_steps, **kwargs)
        FreeCAD.Console.PrintMessage("--- MinkowskiNester initialized ---\n")
        self._decomposition_cache = {} # Cache for polygon decomposition results
        self._nfp_cache = {} # Cache for No-Fit Polygons between master shapes

    def _try_place_part_on_sheet(self, part_to_place, sheet):
        """
        Tries to place a single shape on a given sheet using Minkowski sums.
        Returns the placed shape on success, None on failure.
        """
        FreeCAD.Console.PrintMessage(f"MINKOWSKI: Attempting to place '{part_to_place.id}' on sheet {sheet.id}\n")
        best_placement_info = {'x': None, 'y': None, 'angle': None, 'metric': float('inf')}
        
        # Iterate through all possible rotations for the part.
        # A value of 0 or 1 means 1 step (no rotation).
        for i in range(part_to_place.rotation_steps):
            angle = i * (360.0 / part_to_place.rotation_steps) if part_to_place.rotation_steps > 1 else 0.0
            # We don't move the part itself, we just get its polygon at the target rotation
            rotated_part_poly = rotate(part_to_place.original_polygon, angle, origin='centroid')

            FreeCAD.Console.PrintMessage(f"MINKOWSKI:  - Trying rotation {angle:.1f} degrees.\n")

            # 1. Compute the individual No-Fit Polygons (NFPs).
            individual_nfps = []
            for placed_part in sheet.parts:
                placed_part_master_label = placed_part.shape.source_freecad_object.Label
                part_to_place_master_label = part_to_place.source_freecad_object.Label
                placed_part_angle = placed_part.angle
                
                cache_key = (placed_part_master_label, placed_part_angle, part_to_place_master_label, angle)
                master_nfp = self._nfp_cache.get(cache_key)

                if master_nfp is None:
                    # Cache miss. Calculate the NFP now and store it.
                    FreeCAD.Console.PrintMessage(f"MINKOWSKI: Calculating NFP: {placed_part_master_label} @ {placed_part_angle:.1f} vs {part_to_place_master_label} @ {angle:.1f}\n")
                    placed_part_master_poly = placed_part.shape.original_polygon
                    part_to_place_master_poly = part_to_place.original_polygon
                    
                    master_nfp = self._calculate_and_cache_nfp(
                        placed_part_master_poly, placed_part_angle, 
                        part_to_place_master_poly, angle, cache_key
                    )

                if master_nfp:
                    # The NFP's reference point is the sum of the reference points.
                    # We translate it by the placed part's centroid to position it correctly on the sheet.
                    placed_part_centroid = placed_part.shape.centroid
                    translated_nfp = translate(master_nfp, xoff=placed_part_centroid.x, yoff=placed_part_centroid.y)
                    individual_nfps.append(translated_nfp)

            # 2. Generate candidate points from the NFPs.
            candidate_points = self._get_candidate_positions(individual_nfps)
            FreeCAD.Console.PrintMessage(f"MINKOWSKI: Generated {len(candidate_points)} candidate placement points.\n")

            # We need the polygon at the current rotation to test placements.
            if not rotated_part_poly:
                FreeCAD.Console.PrintError("MINKOWSKI: Part to place has no polygon. Cannot proceed.\n")
                continue

            # 3. Find the best valid placement for this rotation.
            candidate_points.sort(key=lambda p: (p.y, p.x))

            for point in candidate_points:
                # The candidate point is for the part's reference point (centroid).
                # We create a temporary polygon at this position to check for validity.
                temp_poly = translate(rotated_part_poly, xoff=point.x, yoff=point.y)

                # Use the sheet's robust validation method.
                if sheet.is_placement_valid_polygon(temp_poly):
                    # This is a valid placement. We store it if it's better than the current best.
                    # The "best" is the one with the lowest y, then lowest x.
                    metric = point.y * self._bin_width + point.x
                    if metric < best_placement_info['metric']:
                        best_placement_info = {'x': point.x, 'y': point.y, 'angle': angle, 'metric': metric}
                        FreeCAD.Console.PrintMessage(f"MINKOWSKI:  - Found new best candidate placement at ({point.x:.2f}, {point.y:.2f}) with angle {angle:.1f}\n")

        # After checking all rotations and their candidates, apply the best one found.
        if best_placement_info['x'] is not None:
            best_x = best_placement_info['x']
            best_y = best_placement_info['y']
            best_angle = best_placement_info['angle']
            
            FreeCAD.Console.PrintMessage(f"MINKOWSKI: Finalizing placement for '{part_to_place.id}' at ({best_x:.2f}, {best_y:.2f}), angle {best_angle:.1f}\n")
            
            # Apply the best found transformation to the actual part object.
            part_to_place.set_rotation(best_angle)
            
            # Move the part so its centroid is at the best found (x, y) point.
            current_centroid = part_to_place.centroid
            dx = best_x - current_centroid.x
            dy = best_y - current_centroid.y
            part_to_place.move(dx, dy)

            return part_to_place

        FreeCAD.Console.PrintWarning(f"MINKOWSKI: No valid placement found for '{part_to_place.id}' on this sheet after all checks.\n")
        return None

    def _get_candidate_positions(self, nfps):
        """
        Generates candidate placement points from the vertices of the No-Fit Polygon.
        These are the most likely points for an optimal, touching placement.
        """
        from shapely.geometry import Point, MultiPoint
        points = []

        # Add the sheet origin as a primary candidate.
        points.append(Point(0, 0))

        # The vertices of each individual NFP are the primary candidates for placement of the part's reference point.
        # These represent the locations where the part can touch an existing part.
        for nfp in nfps:
            if nfp.geom_type == 'Polygon':
                points.extend([Point(c) for c in nfp.exterior.coords])
                # Also add points from the holes in the NFP, which correspond to
                # valid placements inside the holes of the original parts.
                for interior in nfp.interiors:
                    points.extend([Point(c) for c in interior.coords])
            elif nfp.geom_type == 'MultiPolygon':
                for poly in nfp.geoms:
                    points.extend([Point(c) for c in poly.exterior.coords])
                    for interior in poly.interiors:
                        points.extend([Point(c) for c in interior.coords])

        # The old implementation also tried to match every vertex of the new part
        # with every vertex of the NFP. This created a massive number of candidates
        # (e.g., 26,000+) and was a major performance bottleneck.
        # By removing it, we drastically speed up the nesting process. The trade-off
        # might be slightly less dense packing in some very specific cases, but the
        # performance gain is significant.

        return list(MultiPoint(points).geoms) # Return unique points

    def _calculate_and_cache_nfp(self, poly_A_master, angle_A, poly_B_master, angle_B, cache_key):
        """Calculates the NFP between two polygons and stores it in the cache."""
        if cache_key in self._nfp_cache:
            return self._nfp_cache[cache_key]

        # --- NFP Calculation with Hole Support ---
        # The NFP exterior is the minkowski sum of A's exterior and the reflected B.
        # We pass the master polygons and their transformations directly.
        nfp_exterior = self._minkowski_sum(poly_A_master, angle_A, False, poly_B_master, angle_B, True)
        
        nfp_interiors = []
        # If A has holes, they become valid placement areas for B.
        if poly_A_master.interiors:
            poly_B_rotated = rotate(poly_B_master, angle_B, origin='centroid')
            for hole in poly_A_master.interiors:
                hole_poly = Polygon(hole)
                hole_poly_rotated = rotate(hole_poly, angle_A, origin='centroid')
                # Basic check: if B's area is larger than the hole's, it can't fit.
                if poly_B_rotated.area < hole_poly_rotated.area:
                    # The Minkowski sum of the hole and reflected B gives an "In-Fit Polygon" (IFP).
                    ifp = self._minkowski_sum(hole_poly, angle_A, False, poly_B_master, angle_B, True)
                    if ifp and ifp.area > 0:
                        nfp_interiors.append(ifp.exterior)
        
        master_nfp = Polygon(nfp_exterior.exterior, nfp_interiors) if nfp_exterior and nfp_exterior.area > 0 else None
        self._nfp_cache[cache_key] = master_nfp
        return master_nfp

    def _reflect_polygon(self, polygon):
        """Reflects a shapely polygon about the origin."""
        if not polygon: return None
        # Using scale is a robust way to reflect
        return scale(polygon, xfact=-1.0, yfact=-1.0, origin=(0, 0))

    def _minkowski_sum(self, master_poly1, angle1, reflect1, master_poly2, angle2, reflect2):
        """
        Computes the Minkowski sum of two polygons.
        It uses the pre-cached decomposition of the master polygons and rotates
        the individual convex parts before summing them.
        """
        if master_poly1.is_empty or master_poly2.is_empty:
            return master_poly1.buffer(0) if master_poly2.is_empty else master_poly2.buffer(0)

        # Get the pre-decomposed convex parts from the cache.
        poly1_convex_parts = self._decompose_if_needed(master_poly1)
        poly2_convex_parts = self._decompose_if_needed(master_poly2)

        # --- Transform Convex Parts ---
        poly1_convex_transformed = []
        for p in poly1_convex_parts:
            p_new = rotate(p, angle1, origin='centroid')
            if reflect1:
                p_new = self._reflect_polygon(p_new)
                p_new = scale(p_new, xfact=-1.0, yfact=-1.0, origin=(0, 0))
            poly1_convex_transformed.append(p_new)

        poly2_convex_transformed = []
        for p in poly2_convex_parts:
            p_new = rotate(p, angle2, origin='centroid')
            if reflect2:
                p_new = self._reflect_polygon(p_new)
                p_new = scale(p_new, xfact=-1.0, yfact=-1.0, origin=(0, 0))
            poly2_convex_transformed.append(p_new)

        minkowski_parts = []
        for p1 in poly1_convex_transformed:
            for p2 in poly2_convex_transformed:
                minkowski_parts.append(self._minkowski_sum_convex(p1, p2))

        return unary_union(minkowski_parts)

    def _decompose_if_needed(self, polygon):
        """Decomposes a non-convex polygon into convex parts."""
        if not polygon or polygon.is_empty:
            return []
        
        cache_key = polygon.wkt
        if cache_key in self._decomposition_cache:
            return self._decomposition_cache[cache_key]

        if polygon.geom_type == 'MultiPolygon':
            all_decomposed_parts = []
            for p in polygon.geoms:
                all_decomposed_parts.extend(self._decompose_if_needed(p))
            return all_decomposed_parts

        if math.isclose(polygon.area, polygon.convex_hull.area):
            return [polygon]
        
        try:
            from shapely.ops import triangulate
            triangles = triangulate(polygon)
            decomposed = [tri for tri in triangles if polygon.contains(tri.representative_point())]
            self._decomposition_cache[cache_key] = decomposed
            return decomposed
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"MINKOWSKI:      - Shapely triangulation not available or failed: {e}. Falling back to convex hull.\n")

        result = [polygon.convex_hull]
        self._decomposition_cache[cache_key] = result
        return result

    def _minkowski_sum_convex(self, poly1, poly2):
        """Computes the Minkowski sum of two convex polygons."""
        # The Minkowski sum of two convex polygons is the convex hull of the sum of their vertices.
        # This is a standard and robust method.
        from shapely.geometry import MultiPoint
        
        v1 = poly1.exterior.coords
        v2 = poly2.exterior.coords
        
        sum_vertices = []
        for p1 in v1:
            for p2 in v2:
                sum_vertices.append((p1[0] + p2[0], p1[1] + p2[1]))
        
        # The convex hull of these summed points is the Minkowski sum.
        return MultiPoint(sum_vertices).convex_hull
