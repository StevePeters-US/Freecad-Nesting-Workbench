
import math
import FreeCAD
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from shapely.geometry import Polygon, Point, MultiPoint
from shapely.affinity import translate, rotate
from shapely.ops import unary_union
from . import minkowski_utils
try:
    from . import nfp_gpu_taichi
except ImportError:
    nfp_gpu_taichi = None
from ....datatypes.shape import Shape

class MinkowskiEngine:
    """
    Handles geometric operations for Minkowski nesting, such as NFP generation,
    candidate point finding, and placement validation.
    """
    def __init__(self, bin_width, bin_height, step_size, discretize_edges=True, log_callback=None, use_gpu=False):
        self.bin_width = bin_width
        self.bin_height = bin_height
        self.step_size = step_size
        self.discretize_edges = discretize_edges
        self.log_callback = log_callback
        self.use_gpu = use_gpu and nfp_gpu_taichi and nfp_gpu_taichi.is_available() # Check availability
        self._log_lock = Lock()
        
        if use_gpu and not self.use_gpu:
             self.log("GPU acceleration requested but Taichi is not available. Falling back to CPU.")

        self.bin_polygon = Polygon([(0, 0), (self.bin_width, 0), (self.bin_width, self.bin_height), (0, self.bin_height)])

    def log(self, message):
        if self.log_callback:
            with self._log_lock:
                self.log_callback("MINKOWSKI_ENGINE: " + message)
        else:
             # Fallback to FreeCAD console if no callback is wired
             import FreeCAD
             FreeCAD.Console.PrintMessage(f"MINKOWSKI_ENGINE: {message}\n")







    def get_global_nfp_for(self, part_to_place, angle, sheet):
        """
        Calculates (incrementally) the total forbidden area (Union of NFPs) 
        for a specific part rotation on the sheet.
        Returns dict with 'polygon', 'prepared', and candidate 'points'.
        Returns None if NFP calculation fails.
        """
        cache_key = (part_to_place.source_freecad_object.Label, round(angle, 4))
        
        # Initialize or Retrieve cache entry (protected by sheet lock)
        with sheet.nfp_cache_lock:
            if cache_key not in sheet.nfp_cache:
                sheet.nfp_cache[cache_key] = {
                    'polygon': Polygon(), # Start empty
                    'last_part_idx': 0,
                    'points': [],
                    'prepared': None
                }
                
            entry = sheet.nfp_cache[cache_key]
            
            # If we are up to date, return immediately
            if entry['last_part_idx'] >= len(sheet.parts):
                return entry

        # We have new parts to process
        new_polys = []
        part_to_place_master_label = part_to_place.source_freecad_object.Label
        
        # Identify new parts
        parts_to_process = sheet.parts[entry['last_part_idx']:]
        
        for p in parts_to_process:
            placed_label = p.shape.source_freecad_object.Label
            placed_angle = p.angle
            
            # Normalize angle
            relative_angle = (angle - placed_angle) % 360.0
            if abs(relative_angle - 360.0) < 1e-5: relative_angle = 0.0
            relative_angle = round(relative_angle, 4)
            
            nfp_cache_key = (
                placed_label, 
                part_to_place_master_label, 
                relative_angle, 
                part_to_place.spacing,
                part_to_place.deflection,
                part_to_place.simplification
            )
            
            # Get Master NFP
            with Shape.nfp_cache_lock:
                nfp_data = Shape.nfp_cache.get(nfp_cache_key)
            if not nfp_data:
                if self.use_gpu:
                    # Calculate if missing (synchronous, batched via GPU later or single via GPU wrapper now?)
                    # For now we do single pairwise on GPU, but ideally we should batch.
                    # Adapting existing flow:
                    nfp_data = self._calculate_and_cache_nfp_gpu(
                        p.shape, 0.0, part_to_place, relative_angle, nfp_cache_key
                    )
                else:
                    nfp_data = self._calculate_and_cache_nfp(
                        p.shape, 0.0, part_to_place, relative_angle, nfp_cache_key
                    )
            
            # Check for calculation error
            if nfp_data and nfp_data.get('error'):
                self.log(f"Skipping rotation due to NFP error: {nfp_data['error']}")
                return None

            if nfp_data and nfp_data.get('polygon'):
                # Transform to sheet absolute position
                master = nfp_data['polygon']
                # Rotate
                rotated = rotate(master, placed_angle, origin=(0, 0))
                # Translate
                cent = p.shape.centroid
                translated = translate(rotated, xoff=cent.x, yoff=cent.y)
                new_polys.append(translated)
        
        # Update entry (protected by sheet lock)
        with sheet.nfp_cache_lock:
            if new_polys:
                # Union all new usage areas
                batch_union = unary_union(new_polys)
                
                # Union with existing total
                if entry['polygon'].is_empty:
                    entry['polygon'] = batch_union
                else:
                    entry['polygon'] = entry['polygon'].union(batch_union)
                    
                # Update derived data
                # Discretize the *Resulting Union* for clean candidate generation
                points = []
                if not entry['polygon'].is_empty:
                    polys = [entry['polygon']] if entry['polygon'].geom_type == 'Polygon' else entry['polygon'].geoms
                    for poly in polys:
                         # Exterior
                         points.extend(self._discretize_edge(poly.exterior))
                         # Holes
                         for interior in poly.interiors:
                             points.extend(self._discretize_edge(interior))
                
                entry['points'] = points
                entry['prepared'] = None # Invalidate prepared cache as polygon changed
            
            entry['last_part_idx'] = len(sheet.parts)
        return entry



    def _calculate_and_cache_nfp(self, shape_A, angle_A, part_to_place, angle_B, cache_key):
        with Shape.nfp_cache_lock:
            cached_nfp_data = Shape.nfp_cache.get(cache_key)
            if cached_nfp_data:
                return cached_nfp_data

        try:
            poly_A_master = shape_A.original_polygon
            poly_B_master = part_to_place.original_polygon
            
            # Center the master polygons to (0,0) for pure relative NFP calculation
            # This removes any inherent offset in the FreeCAD shape data
            cA = poly_A_master.centroid
            cB = poly_B_master.centroid
            
            poly_A_centered = translate(poly_A_master, -cA.x, -cA.y)
            poly_B_centered = translate(poly_B_master, -cB.x, -cB.y)
            
            # Calculate NFP using centered polygons
            # Target angle_A is usually 0.0 in this context (relative frame)
            nfp_exterior = minkowski_utils.minkowski_sum(
                poly_A_centered, angle_A, False, 
                poly_B_centered, angle_B, True, 
                self.log
            )
            
            nfp_interiors = []
            if poly_A_centered and poly_A_centered.interiors:
                # For holes, B is rotated around its (now 0,0) centroid
                poly_B_rotated = rotate(poly_B_centered, angle_B, origin=(0,0))
                
                for hole in poly_A_centered.interiors:
                    # Holes are also centered relative to A's centroid
                    hole_poly = Polygon(hole.coords)
                    # No need to unrotate/rotate around centroid if angle_A is 0, but effectively:
                    hole_poly_rotated = rotate(hole_poly, angle_A, origin=(0,0))
                    
                    # Check bounds optimization
                    if (poly_B_rotated.bounds[2] - poly_B_rotated.bounds[0] < hole_poly_rotated.bounds[2] - hole_poly_rotated.bounds[0] and
                        poly_B_rotated.bounds[3] - poly_B_rotated.bounds[1] < hole_poly_rotated.bounds[3] - hole_poly_rotated.bounds[1] and
                            poly_B_rotated.area < hole_poly_rotated.area):
                        
                        ifp_raw = minkowski_utils.minkowski_difference(hole_poly_rotated, 0, poly_B_centered, angle_B, self.log)
                        
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
                    
            # Cache failure or empty dict as well to avoid re-calc?
            # If master_nfp is None, nfp_data is None.
            # Returning None implies valid "no restriction" or just not computed?
            # Actually, standard behavior was returning None -> no nfp restriction.
            # We preserve that behavior for successful but empty result.
            if nfp_data is None:
                 nfp_data = {} # Cache empty dict to signify specialized "no nfp" (e.g. invalid inputs but no error?)
                 # Actually, if master_nfp was None, we probably shouldn't cache a failure unless we know it.
                 # Let's stick to original logic: if master_nfp is None, nfp_data is None.
                 # But wait, we want to cache it.
                 pass

        except Exception as e:
            self.log(f"Error calculating NFP for {cache_key}: {e}")
            nfp_data = {'error': str(e)}

        with Shape.nfp_cache_lock:
            Shape.nfp_cache[cache_key] = nfp_data
        
        return nfp_data

    def _calculate_and_cache_nfp_gpu(self, shape_A, angle_A, part_to_place, angle_B, cache_key):
        """
        Calculates NFP using the GPU-accelerated Taichi module.
        """
        with Shape.nfp_cache_lock:
             cached_nfp_data = Shape.nfp_cache.get(cache_key)
             if cached_nfp_data:
                 return cached_nfp_data
        
        try:
            poly_A_master = shape_A.original_polygon
            poly_B_master = part_to_place.original_polygon
            
            # Center polygons (same relativeframe logic)
            cA = poly_A_master.centroid
            cB = poly_B_master.centroid
            
            poly_A_centered = translate(poly_A_master, -cA.x, -cA.y)
            poly_B_centered = translate(poly_B_master, -cB.x, -cB.y)
            
            # Decompose into convex parts
            poly_A_parts = minkowski_utils.decompose_if_needed(poly_A_centered, self.log)
            poly_B_parts = minkowski_utils.decompose_if_needed(poly_B_centered, self.log)
            
            # For NFP(A, B), we technically compute A + (-B).
            # The Taichi kernel computes sums.
            # So we need to negate B (reflect around origin) AND rotate B by angle_B.
            # Wait, standard NFP logic:
            #   If we sweep B around A. 
            #   minkowski_utils.minkowski_sum actually rotates B then reflects it (-B) if reflect2=True.
            
            # Let's prepare inputs for Taichi:
            # A is at angle_A (usually 0).
            # B is at angle_B.
            # We need to reflect B for NFP.
            
            # To match minkowski_utils behavior:
            # It rotates B by angle_B, THEN scales by -1 (reflects).
            # Our Kernel computes A + B'. So B' must be the rotated+reflected B.
            
            # Let's pre-transform B's parts on CPU because rotation/reflection is cheap
            # compared to the M*N pairwise sums.
            # Actually, the Kernel takes rotations.
            # If we pre-transform B, we lose the ability to batch rotations?
            # BUT here we are calculating for a SINGLE relative_angle (angle_B).
            # So batching rotations isn't happening in this function call 
            # (which is called deep inside the loop).
            
            # Optimization TODO: Move this call up to the loop to batch all rotations!
            # For now, 1-to-1 replacement means we call GPU for single rotation. 
            # It's still faster than CPU Shapely for complex parts.
            
            from shapely.affinity import scale
            
            # Transform A parts (angle_A is usually 0, but let's be safe)
            # A is not reflected.
            parts_A_ready = [rotate(p, angle_A, origin=(0,0)) for p in poly_A_parts]
            
            # Transform B parts
            # Reflect B first? No, rotate then reflect.
            # The kernel adds A + B. We want A + (-B_rotated).
            # -B_rotated = rotate(scale(B, -1, -1), angle).
            # Or scale(rotate(B, angle), -1, -1). Order matters for position?
            # Reflection about (0,0) is commutative with rotation about (0,0).
            # Let's use scale(-1, -1) on the parts, then pass angle_B to kernel.
            
            # Reflected parts of B
            parts_B_reflected = [scale(p, xfact=-1.0, yfact=-1.0, origin=(0,0)) for p in poly_B_parts]
            
            # Call Taichi
            # We pass [angle_B] as the rotation list
            results = nfp_gpu_taichi.compute_nfp_batch(
                parts_A_ready, 
                parts_B_reflected, 
                [angle_B] # Single angle
            )
            
            # Results is [ [hull1, hull2...] ] for the first (and only) rotation
            convex_nfps = results[0]
            
            if not convex_nfps:
                master_nfp = None
            else:
                # Union the convex NFPs
                # This is the "holes" logic? 
                # Wait, what about holes? 
                # The existing logic handles holes separately as IFP (Inner Fit Polygon).
                # Current GPU plan only does the Exterior NFP (A + -B).
                # We need to handle holes too.
                # If we rely on _calculate_and_cache_nfp logic for holes (CPU),
                # we can just mix them? 
                
                # GPU for Exterior NFP:
                nfp_exterior_poly = unary_union(convex_nfps)
                
                # For Holes (IFP):
                # The existing code calculates IFP using difference.
                # IFP = Hole - Part.
                # If we want to use GPU for this, it's (Hole + (-Part))?
                # Actually, A - B = A + (-B)? No. 
                # Minkowski Difference A - B = Erosion.
                # Erosion(A, B) = Complement(Complement(A) + (-B))? 
                # This is getting complicated for the first pass.
                # Let's stick to: GPU for Exterior (Usually the most expensive part),
                # and keep using CPU logic for holes (IFP), OR just implement full logic.
                
                # To be safe and identical to CPU version, let's copy the hole logic 
                # from _calculate_and_cache_nfp but use the result of GPU for exterior.
                
                nfp_interiors = []
                # ... (Hole logic copy-paste from CPU version, or refactor to share?)
                # For now let's reuse the CPU logic for holes as it relies on specific bounding box checks
                # and might be fast enough if holes are few.
                
                # Re-implementing just the exterior part on GPU for now.
                # If we want to be 100% robust we should copy the hole logic.
                
                if poly_A_centered.interiors:
                    poly_B_rotated = rotate(poly_B_centered, angle_B, origin=(0,0))
                    for hole in poly_A_centered.interiors:
                        hole_poly = Polygon(hole.coords)
                        hole_poly_rotated = rotate(hole_poly, angle_A, origin=(0,0))
                        
                        # Check bounds optimization (same as CPU)
                        if (poly_B_rotated.bounds[2] - poly_B_rotated.bounds[0] < hole_poly_rotated.bounds[2] - hole_poly_rotated.bounds[0] and
                            poly_B_rotated.bounds[3] - poly_B_rotated.bounds[1] < hole_poly_rotated.bounds[3] - hole_poly_rotated.bounds[1] and
                                poly_B_rotated.area < hole_poly_rotated.area):
                             
                             # Use CPU utils for difference (erosion)
                             ifp_raw = minkowski_utils.minkowski_difference(hole_poly_rotated, 0, poly_B_centered, angle_B, self.log)
                             if ifp_raw and ifp_raw.area > 0:
                                 if ifp_raw.geom_type == 'Polygon':
                                     nfp_interiors.append(ifp_raw.exterior)
                                 elif ifp_raw.geom_type == 'MultiPolygon':
                                     for p in ifp_raw.geoms:
                                         nfp_interiors.append(p.exterior)

                master_nfp = Polygon(nfp_exterior_poly.exterior, nfp_interiors) if nfp_exterior_poly and nfp_exterior_poly.area > 0 else None

            nfp_data = None
            if master_nfp:
                nfp_data = {"polygon": master_nfp}
                if self.discretize_edges:
                    nfp_data["exterior_points"] = self._discretize_edge(master_nfp.exterior)
                    nfp_data["interior_points"] = [self._discretize_edge(interior) for interior in master_nfp.interiors]
                else:
                    nfp_data["exterior_points"] = [Point(x, y) for x, y in master_nfp.exterior.coords]
                    nfp_data["interior_points"] = [[Point(x, y) for x, y in interior.coords] for interior in master_nfp.interiors]
            
            if nfp_data is None:
                 nfp_data = {} 

        except Exception as e:
            self.log(f"GPU NFP Error for {cache_key}: {e}. Falling back to CPU.")
            # Fallback
            return self._calculate_and_cache_nfp(shape_A, angle_A, part_to_place, angle_B, cache_key)

        with Shape.nfp_cache_lock:
            Shape.nfp_cache[cache_key] = nfp_data
        
        return nfp_data

    def _discretize_edge(self, line):
        points = [Point(line.coords[0])]
        length = line.length
        if length > self.step_size:
            num_segments = int(length / self.step_size)
            for i in range(1, num_segments):
                points.append(line.interpolate(float(i) / num_segments, normalized=True))
        points.append(Point(line.coords[-1]))
        return points
