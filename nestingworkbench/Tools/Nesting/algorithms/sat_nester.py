import math
from .base_nester import BaseNester
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

class SatNester(BaseNester):
    """
    A nester using the Separating Axis Theorem for collision detection.
    This algorithm is very fast for convex polygons. It places parts by finding
    the best position along the boundary of the combined shape of already placed parts.
    """

    def _try_place_part_on_sheet(self, part_to_place, sheet, update_callback=None):
        """
        Tries to place a single part on the given sheet using an SAT-based strategy.
        It finds the best position by "sliding" the part along existing boundaries.
        Returns the placed part on success, None on failure.
        """
        best_placement_info = {'x': None, 'y': None, 'angle': None, 'metric': float('inf')}

        # Determine the number of rotations to check for this specific part
        # A value of 0 or 1 means 1 step (no rotation).
        num_rotations = part_to_place.rotation_steps

        for i in range(num_rotations):
            angle = i * (360.0 / num_rotations) if num_rotations > 1 else 0.0
            part_to_place.set_rotation(angle)
            rotated_part_poly = part_to_place.get_polygon()

            if not sheet.parts:
                # For the first part, place it at the origin.
                part_to_place.move_to(0, 0)
                if sheet.is_placement_valid(part_to_place):
                    return part_to_place
                else:
                    continue # Try next rotation if origin placement is invalid

            # Get the union of all parts already on the sheet
            placed_union = sheet.get_union_of_placed_parts()
            if not placed_union: continue

            # Generate candidate positions by sliding the new part along the boundary of the placed union
            candidate_points = self._get_candidate_positions(rotated_part_poly, placed_union)

            for p in candidate_points:
                part_to_place.move_to(p.x, p.y)
                if self._is_placement_valid_sat(part_to_place, sheet):
                    # This position is valid. Evaluate its metric (bottom-left-most).
                    metric = p.y * self._bin_width + p.x
                    if metric < best_placement_info['metric']:
                        best_placement_info = {'x': p.x, 'y': p.y, 'angle': angle, 'metric': metric}

        if best_placement_info['x'] is not None:
            part_to_place.set_rotation(best_placement_info['angle'])
            part_to_place.move_to(best_placement_info['x'], best_placement_info['y'])
            return part_to_place

        return None

    def _get_candidate_positions(self, part_poly, placed_union):
        """
        Generates candidate positions by placing the part's vertices against the
        vertices of the placed parts' union.
        """
        candidates = [Point(0, 0)]
        part_coords = part_poly.exterior.coords
        placed_coords = placed_union.exterior.coords

        for v_part in part_coords:
            for v_placed in placed_coords:
                # Candidate places the part's vertex v_part at the placed vertex v_placed
                # The part's origin (bottom-left) needs to be calculated.
                part_bounds = part_poly.bounds
                part_origin_offset_x = v_part[0] - part_bounds[0]
                part_origin_offset_y = v_part[1] - part_bounds[1]
                
                candidate_x = v_placed[0] - part_origin_offset_x
                candidate_y = v_placed[1] - part_origin_offset_y
                candidates.append(Point(candidate_x, candidate_y))

        return candidates

    def _is_placement_valid_sat(self, part_to_check, sheet):
        """
        A fast collision check using the Separating Axis Theorem.
        This assumes all polygons are convex.
        """
        part_poly = part_to_check.get_polygon()
        if not self._bin_polygon.contains(part_poly):
            return False

        for placed in sheet.parts:
            placed_poly = placed.shape.get_polygon()
            if self._check_collision_sat(part_poly, placed_poly):
                return False
        return True

    def _check_collision_sat(self, poly1, poly2):
        """Performs SAT check between two convex polygons."""
        polygons = [poly1, poly2]
        for poly in polygons:
            coords = list(poly.exterior.coords)
            for i in range(len(coords) - 1):
                p1 = coords[i]
                p2 = coords[i+1]
                
                edge = (p2[0] - p1[0], p2[1] - p1[1])
                # Axis is perpendicular to the edge
                axis = (-edge[1], edge[0])
                
                min1, max1 = self._project_polygon(axis, poly1)
                min2, max2 = self._project_polygon(axis, poly2)

                # If there is a gap between the projections, they don't overlap
                if max1 < min2 or max2 < min1:
                    return False # Found a separating axis

        return True # No separating axis found, polygons are colliding

    def _project_polygon(self, axis, polygon):
        """Projects a polygon onto an axis."""
        min_proj = float('inf')
        max_proj = float('-inf')
        
        ax_len_sq = axis[0]**2 + axis[1]**2
        if ax_len_sq == 0: return 0, 0

        for point in polygon.exterior.coords:
            # Dot product of point vector and axis vector
            dot_product = point[0] * axis[0] + point[1] * axis[1]
            
            # Project onto the axis (scalar projection)
            projection = dot_product / math.sqrt(ax_len_sq)
            
            min_proj = min(min_proj, projection)
            max_proj = max(max_proj, projection)
            
        return min_proj, max_proj