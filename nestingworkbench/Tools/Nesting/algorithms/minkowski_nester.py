import math
import random
from shapely.geometry import Polygon, MultiPolygon, Point
from shapely.affinity import translate, rotate, scale
from shapely.ops import unary_union
from PySide import QtGui

import FreeCAD
from ....datatypes.sheet import Sheet
from .base_nester import BaseNester
from ....datatypes.placed_part import PlacedPart
import Part


class NfpCache:
    """Caches No-Fit Polygons and polygon decompositions."""

    def __init__(self):
        self._nfp_cache = {}
        self._decomposition_cache = {}

    def get_nfp(self, key):
        """Gets a No-Fit Polygon from the cache."""
        return self._nfp_cache.get(key)

    def set_nfp(self, key, nfp):
        """Sets a No-Fit Polygon in the cache."""
        self._nfp_cache[key] = nfp

    def get_decomposition(self, key):
        """Gets a polygon decomposition from the cache."""
        return self._decomposition_cache.get(key)

    def set_decomposition(self, key, decomposition):
        """Sets a polygon decomposition in the cache."""
        self._decomposition_cache[key] = decomposition


class MinkowskiNester(BaseNester):
    """Nesting algorithm using Minkowski sums to create No-Fit Polygons."""
    def __init__(self, width, height, rotation_steps=1, **kwargs):
        super().__init__(width, height, rotation_steps, **kwargs)
        self._debug_group = None
        self._cache = NfpCache()

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
        placed_part_shape = self._try_place_part_on_sheet(part, sheet)

        # Final validation using the Minkowski-aware checker.
        if placed_part_shape and self._is_placement_valid_with_holes(
            placed_part_shape.polygon, sheet, part_to_ignore=placed_part_shape
        ):
            sheet_origin = sheet.get_origin()
            placed_part_shape.placement = placed_part_shape.get_final_placement(sheet_origin)
            sheet.add_part(PlacedPart(placed_part_shape))
            return True
        elif placed_part_shape:
            FreeCAD.Console.PrintWarning(f"Minkowski nester returned an invalid placement for {part.id}. Discarding.\n")
        return False

    def _draw_debug_poly(self, poly, name):
        """Helper to draw a shapely polygon for debugging."""
        if not self._debug_group:
            # The debug group is now created and cleaned up by this nester's nest() method.
            # This prevents interference with the nesting logic.
            doc = FreeCAD.ActiveDocument
            if doc.getObject("MinkowskiDebug") is None:
                self._debug_group = doc.addObject("App::DocumentObjectGroup", "MinkowskiDebug")

        if poly and not poly.is_empty:
            wires = [Part.makePolygon([(v[0], v[1], 0) for v in poly.exterior.coords])]
            for interior in poly.interiors:
                wires.append(Part.makePolygon([(v[0], v[1], 0) for v in interior.coords]))
            debug_obj = self._debug_group.newObject("Part::Feature", name)
            debug_obj.Shape = Part.makeCompound(wires)
            # If this is the boundary, move it up slightly in Z for visibility
            if "IFP_boundary" in name:
                debug_obj.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0.1), FreeCAD.Rotation())

            if FreeCAD.GuiUp:
                if "NFP" in name:
                    debug_obj.ViewObject.ShapeColor = (1.0, 0.0, 0.0) # Red
                    debug_obj.ViewObject.Transparency = 50
                elif "IFP_boundary" in name:
                    debug_obj.ViewObject.LineColor = (0.0, 0.2, 1.0) # Blue
                    debug_obj.ViewObject.LineWidth = 3.0
                    debug_obj.ViewObject.DisplayMode = "Wireframe"
                elif "IFP" in name:
                    debug_obj.ViewObject.ShapeColor = (0.0, 1.0, 0.0) # Green
                    debug_obj.ViewObject.Transparency = 50

    def _draw_debug_point(self, x, y, name, color=None):
        """Helper to draw a point for debugging."""
        if not self._debug_group:
            doc = FreeCAD.ActiveDocument
            if doc.getObject("MinkowskiDebug") is None:
                self._debug_group = doc.addObject("App::DocumentObjectGroup", "MinkowskiDebug")
        
        circle_obj = self._debug_group.newObject("Part::Feature", name) # type: ignore
        circle_edge = Part.makeCircle(1, FreeCAD.Vector(x, y, 0))
        wire = Part.Wire(circle_edge)
        circle_obj.Shape = Part.Face(wire)
        if FreeCAD.GuiUp and color:
            circle_obj.ViewObject.ShapeColor = color

    def _handle_first_part(self, part_to_place, sheet, angle):
        """Handles the placement of the first part on an empty sheet."""
        part_to_place.set_rotation(angle)
        # For the first part, we try to place its bottom-left corner at the origin.
        part_to_place.move_to(0, 0)
        if sheet.is_placement_valid(part_to_place):
            return part_to_place
        return None

    def _generate_nfps(self, part_to_place, sheet, angle):
        """Generates the No-Fit Polygons for a given part and sheet."""
        individual_nfps = []
        for placed_part in sheet.parts:
            placed_part_master_label = placed_part.shape.source_freecad_object.Label
            part_to_place_master_label = part_to_place.source_freecad_object.Label
            placed_part_angle = placed_part.angle

            cache_key = (
                placed_part_master_label,
                placed_part_angle,
                part_to_place_master_label,
                angle,
            )
            master_nfp = self._cache.get_nfp(cache_key)

            if master_nfp is None:
                # Cache miss. Calculate the NFP now and store it.
                master_nfp = self._calculate_and_cache_nfp(
                    placed_part.shape, # Pass the full Shape object
                    placed_part_angle,
                    part_to_place,
                    angle,
                    cache_key,
                )

            if master_nfp:
                # The NFP's reference point is the sum of the reference points.
                # We translate the EXTERIOR of the NFP by the placed part's centroid to position it correctly on the sheet.
                placed_part_centroid = placed_part.shape.centroid

                # The exterior represents the "no-go" zone around the placed part.
                translated_exterior = translate(
                    Polygon(master_nfp.exterior),
                    xoff=placed_part_centroid.x,
                    yoff=placed_part_centroid.y,
                )
                self._draw_debug_poly(translated_exterior, f"NFP_{placed_part.shape.id}_{part_to_place.id}_{angle:.0f}")
                
                # The interiors represent the "go-zones" (holes). These are already in the correct
                # coordinate space relative to the placed part's centroid, so they do NOT need to be translated again.
                # We must, however, translate them to the final position of the placed part on the sheet.
                translated_interiors = []
                for i, interior in enumerate(master_nfp.interiors):
                    ifp = Polygon(interior)
                    translated_ifp = translate(ifp, xoff=placed_part_centroid.x, yoff=placed_part_centroid.y)
                    translated_interiors.append(translated_ifp)
                    
                    # --- New Debugging: Draw the IFP boundary ---
                    if not translated_ifp.is_empty:
                        self._draw_debug_poly(translated_ifp, f"IFP_boundary_{placed_part.shape.id}_{i}")

                    # --- New Debugging: Scatter points inside the IFP ---
                    min_x, min_y, max_x, max_y = translated_ifp.bounds
                    points_drawn = 0
                    attempts = 0
                    # Increased number of circles for better visualization
                    while points_drawn < 500 and attempts < 10000:
                        rand_x = random.uniform(min_x, max_x)
                        rand_y = random.uniform(min_y, max_y)
                        if translated_ifp.contains(Point(rand_x, rand_y)):
                            self._draw_debug_point(
                                rand_x, rand_y, 
                                f"ifp_dot_{placed_part.shape.id}_{i}_{points_drawn}",
                                color=(0.0, 1.0, 0.0) # Bright Green
                            )
                            points_drawn += 1
                        attempts += 1
                individual_nfps.append(
                    Polygon(
                        translated_exterior.exterior, [p.exterior for p in translated_interiors]
                    )
                )
        return individual_nfps

    def _find_best_placement(
        self, part_to_place, sheet, angle, rotated_part_poly, individual_nfps
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

            # Temporarily move the part to the candidate position for validation.
            original_part_polygon = part_to_place.polygon

            # The 'point' is where the centroid should be.
            dx = point.x - rotated_poly_centroid.x
            dy = point.y - rotated_poly_centroid.y
            test_polygon = translate(rotated_part_poly, xoff=dx, yoff=dy)
            part_to_place.polygon = test_polygon

            self._draw_debug_poly(
                test_polygon, f"hole_test_{part_to_place.id}_{angle:.0f}_{i}"
            )

            is_valid = self._is_placement_valid_with_holes(
                part_to_place.polygon, sheet, part_to_place
            )

            part_to_place.polygon = original_part_polygon  # Restore the polygon immediately.
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
            original_part_polygon = part_to_place.polygon

            # The 'point' is where the centroid should be.
            dx = point.x - rotated_poly_centroid.x
            dy = point.y - rotated_poly_centroid.y
            test_polygon = translate(rotated_part_poly, xoff=dx, yoff=dy)
            part_to_place.polygon = test_polygon

            self._draw_debug_poly(
                test_polygon, f"ext_test_{part_to_place.id}_{angle:.0f}_{i}"
            )

            is_valid = self._is_placement_valid_with_holes(
                part_to_place.polygon, sheet, part_to_place
            )

            part_to_place.polygon = original_part_polygon  # Restore the polygon immediately.
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

    def _try_place_part_on_sheet(self, part_to_place, sheet):
        """
        Tries to place a single shape on a given sheet using Minkowski sums.
        Returns the placed shape on success, None on failure.
        """
        
        # Ensure the part has an original_polygon to rotate from. This is critical.
        # If it's missing, the part cannot be rotated, leading to silent failure.
        if part_to_place.original_polygon is None and part_to_place.polygon is not None:
            part_to_place.original_polygon = part_to_place.polygon

        best_placement_info = {'x': None, 'y': None, 'angle': None, 'metric': float('inf')}
        

        for i in range(part_to_place.rotation_steps):
            angle = i * (360.0 / part_to_place.rotation_steps) if part_to_place.rotation_steps > 1 else 0.0

            # --- Handle the first part on an empty sheet ---
            if not sheet.parts:
                placed_part = self._handle_first_part(part_to_place, sheet, angle)
                if placed_part:
                    return placed_part
                continue  # Try next rotation if origin placement is invalid

            # We don't move the part itself, we just get its polygon at the target rotation
            rotated_part_poly = rotate(part_to_place.original_polygon, angle, origin='centroid')

            FreeCAD.Console.PrintMessage(f"MINKOWSKI:  - Trying rotation {angle:.1f} degrees.\n")

            # 1. Compute the individual No-Fit Polygons (NFPs).
            individual_nfps = self._generate_nfps(part_to_place, sheet, angle)

            placement_info = self._find_best_placement(
                part_to_place, sheet, angle, rotated_part_poly, individual_nfps
            )

            if placement_info and placement_info.get('metric') is not None and placement_info.get('metric') < best_placement_info.get('metric', float('inf')):
                best_placement_info = placement_info

        # After checking all rotations and their candidates, apply the best one found.
        if best_placement_info['x'] is not None:
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

    def _is_placement_valid_with_holes(self, polygon_to_check, sheet, part_to_ignore):
        """
        Custom validation function for Minkowski that correctly handles holes.
        This logic is self-contained and does not modify the Sheet class.
        """
        # 1. Check containment within sheet boundaries
        bin_polygon = Polygon([(0, 0), (sheet.width, 0), (sheet.width, sheet.height), (0, sheet.height)])
        if not bin_polygon.contains(polygon_to_check):
            return False

        # 2. Manually build the union of placed parts, respecting part_to_ignore
        other_parts_polygons = [p.shape.polygon for p in sheet.parts if p.shape is not part_to_ignore and p.shape.polygon]
        if not other_parts_polygons:
            return True # No other parts to collide with

        union_of_other_parts = unary_union(other_parts_polygons)

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

    def _get_candidate_positions(self, nfps, part_to_place_poly=None):
        """
        Generates candidate placement points from the vertices of the No-Fit Polygon.
        These are the most likely points for an optimal, touching placement.
        """
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
        for nfp in nfps:
            if nfp.geom_type == 'Polygon':
                external_points.extend([Point(c) for c in nfp.exterior.coords])
                # Also add points from the holes in the NFP, which correspond to
                # valid placements inside the holes of the original parts.
                for interior in nfp.interiors:
                    hole_points.extend([Point(c) for c in interior.coords])
            elif nfp.geom_type == 'MultiPolygon':
                for poly in nfp.geoms:
                    external_points.extend([Point(c) for c in poly.exterior.coords])
                    for interior in poly.interiors:
                        hole_points.extend([Point(c) for c in interior.coords])

        # The old implementation also tried to match every vertex of the new part
        # with every vertex of the NFP. This created a massive number of candidates
        # (e.g., 26,000+) and was a major performance bottleneck.
        # By removing it, we drastically speed up the nesting process. The trade-off
        # might be slightly less dense packing in some very specific cases, but the
        # performance gain is significant.

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
        if self._cache.get_nfp(cache_key):
            return self._cache.get_nfp(cache_key)

        poly_A_master = shape_A.original_polygon
        # --- NFP Calculation with Hole Support ---
        poly_B_master = part_to_place.original_polygon
        # The NFP exterior is the minkowski sum of A's exterior and the reflected B.
        # We pass the master polygons and their transformations directly.
        nfp_exterior = self._minkowski_sum(poly_A_master, angle_A, False, poly_B_master, angle_B, True)
        
        nfp_interiors = []
        # If A has holes, they become valid placement areas for B.
        # We must use the hole from the BUFFERED polygon, as this is the actual boundary for placement.
        if poly_A_master and poly_A_master.interiors:
            poly_B_rotated = rotate(poly_B_master, angle_B, origin='centroid')
            for hole in poly_A_master.interiors:
                hole_poly_unrotated = Polygon(hole.coords)
                hole_poly_rotated = rotate(hole_poly_unrotated, angle_A, origin=poly_A_master.centroid)
                
                # Check if the bounding box of the part to place can even fit inside the hole's bounding box.
                # This is a fast check to avoid expensive calculations.
                if (poly_B_rotated.bounds[2] - poly_B_rotated.bounds[0] < hole_poly_rotated.bounds[2] - hole_poly_rotated.bounds[0] and
                    poly_B_rotated.bounds[3] - poly_B_rotated.bounds[1] < hole_poly_rotated.bounds[3] - hole_poly_rotated.bounds[1]):
                    
                    # For now, just use the interior bounds directly as the go-space.
                    if hole_poly_rotated and hole_poly_rotated.area > 0:
                        nfp_interiors.append(hole_poly_rotated.exterior)
        
        master_nfp = Polygon(nfp_exterior.exterior, nfp_interiors) if nfp_exterior and nfp_exterior.area > 0 else None
        self._cache.set_nfp(cache_key, master_nfp)
        return master_nfp

    def _minkowski_sum(self, master_poly1, angle1, reflect1, master_poly2, angle2, reflect2, rot_origin1=None, rot_origin2=None):
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
            rot_origin = rot_origin1 if rot_origin1 is not None else 'centroid'
            p_new = rotate(p, angle1, origin=rot_origin)
            if reflect1:
                p_new = scale(p_new, xfact=-1.0, yfact=-1.0, origin=(0, 0))
            poly1_convex_transformed.append(p_new)

        poly2_convex_transformed = []
        for p in poly2_convex_parts:
            rot_origin = rot_origin2 if rot_origin2 is not None else 'centroid'
            p_new = rotate(p, angle2, origin=rot_origin)
            if reflect2:
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
        if self._cache.get_decomposition(cache_key):
            return self._cache.get_decomposition(cache_key)

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
            self._cache.set_decomposition(cache_key, decomposed)
            return decomposed
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"MINKOWSKI:      - Shapely triangulation not available or failed: {e}. Falling back to convex hull.\n")

        result = [polygon.convex_hull]
        self._cache.set_decomposition(cache_key, result)
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
