import math
import warnings

try:
    import taichi as ti
    import numpy as np
    TAICHI_AVAILABLE = True
except ImportError:
    TAICHI_AVAILABLE = False

if TAICHI_AVAILABLE:
    # Initialize Taichi with Vulkan backend if available, fallback to others
    # We use cpu fallback to avoid crashing if no GPU is found, but ideally we want gpu
    try:
        ti.init(arch=ti.vulkan)
    except:
        try:
            ti.init(arch=ti.cuda)
        except:
             try:
                ti.init(arch=ti.opengl)
             except:
                ti.init(arch=ti.cpu)

def is_available():
    return TAICHI_AVAILABLE

if TAICHI_AVAILABLE:
    @ti.kernel
    def compute_minkowski_sum_convex_kernel(
        n_poly_a: int, 
        n_poly_b: int,
        n_rotations: int,
        arr_a: ti.types.ndarray(),  # Flattened vertices of A: [n_poly_a, max_verts_a, 2]
        len_a: ti.types.ndarray(),  # Vertex count for each A: [n_poly_a]
        arr_b: ti.types.ndarray(),  # Flattened vertices of B: [n_poly_b, max_verts_b, 2]
        len_b: ti.types.ndarray(),  # Vertex count for each B: [n_poly_b]
        rotations: ti.types.ndarray(), # Rotation angles in radians: [n_rotations]
        out_vertices: ti.types.ndarray(), # Output: [n_rotations, n_poly_a, n_poly_b, max_verts_out, 2]
        out_len: ti.types.ndarray() # Output counts: [n_rotations, n_poly_a, n_poly_b]
    ):
        """
        Computes the Minkowski Sum of convex polygons A and B for multiple rotations.
        This simple version implements the "brute force sum of vertices" approach for convex polygons,
        generating the Convex Hull of {v_a + v_b_rotated}.
        
        LIMITATION: This kernel computes ALL pairwise sums. The Convex Hull step is easier done on CPU 
        or a separate kernel because reduced hull algorithms are complex to parallelize per-thread.
        So, this kernel outputs ALL combinations of vertices v_a + v_b.
        The CPU will then compute the Convex Hull of these points to get the final NFP.
        
        Actually, for two convex polygons P and Q, the Minkowski sum P + Q is the convex hull 
        of {p_i + q_j} for all vertices.
        """
        
        # Parallelize over rotations, poly_a, and poly_b
        for r, i, j in ti.ndrange(n_rotations, n_poly_a, n_poly_b):
            angle = rotations[r]
            c = ti.cos(angle)
            s = ti.sin(angle)
            
            count_a = len_a[i]
            count_b = len_b[j]
            
            # We simply output all pair sums. 
            # The number of output points is count_a * count_b
            # We need to make sure out_vertices is large enough.
            
            out_idx = 0
            for va_idx in range(count_a):
                ax = arr_a[i, va_idx, 0]
                ay = arr_a[i, va_idx, 1]
                
                for vb_idx in range(count_b):
                    # Rotate B vertices
                    bx_raw = arr_b[j, vb_idx, 0]
                    by_raw = arr_b[j, vb_idx, 1]
                    
                    bx = bx_raw * c - by_raw * s
                    by = bx_raw * s + by_raw * c
                    
                    # Sum
                    out_vertices[r, i, j, out_idx, 0] = ax + bx
                    out_vertices[r, i, j, out_idx, 1] = ay + by
                    out_idx += 1
                    
            out_len[r, i, j] = out_idx


    def compute_nfp_batch(poly_a_list, poly_b_list, rotations_deg):
        """
        Computes the NFP for a list of convex polygons A and B across multiple rotations.
        
        Args:
            poly_a_list: List of shapely.Polygon (convex parts of A)
            poly_b_list: List of shapely.Polygon (convex parts of B)
            rotations_deg: List of rotation angles in degrees
            
        Returns:
            A list of results per rotation. Each result is a list of shapely.Polygon (the convex NFPs).
        """
        from shapely.geometry import Polygon, MultiPoint
        
        n_a = len(poly_a_list)
        n_b = len(poly_b_list)
        n_r = len(rotations_deg)
        
        # max vertices to pad arrays
        # Note: For NFP, strictly speaking we computing A + (-B).
        # So we assume the caller has already negated B or we handle it?
        # Standard Minkowski Sum is A + B. NFP(A,B) ~ A + (-B).
        # We will assume standard sum here and let the caller input -B if needed.
        
        max_v_a = max(len(p.exterior.coords) for p in poly_a_list)
        max_v_b = max(len(p.exterior.coords) for p in poly_b_list)
        
        # Pre-allocate numpy arrays
        np_a = np.zeros((n_a, max_v_a, 2), dtype=np.float32)
        len_a = np.zeros(n_a, dtype=np.int32)
        
        np_b = np.zeros((n_b, max_v_b, 2), dtype=np.float32)
        len_b = np.zeros(n_b, dtype=np.int32)
        
        for i, p in enumerate(poly_a_list):
            coords = np.array(p.exterior.coords)[:-1] # Drop duplicate end point
            c_len = len(coords)
            np_a[i, :c_len] = coords
            len_a[i] = c_len
            
        for i, p in enumerate(poly_b_list):
            coords = np.array(p.exterior.coords)[:-1]
            c_len = len(coords)
            np_b[i, :c_len] = coords
            len_b[i] = c_len
            
        np_rot = np.radians(np.array(rotations_deg, dtype=np.float32))
        
        # Output size: In worst case (brute force sum), we have V_a * V_b points.
        # Convex hull will reduce this significantly later.
        max_out_verts = max_v_a * max_v_b
        
        # Allocate fields
        # Creating fields every call might be slow. In production, we should cache fields 
        # or use dynamic SNode if sizes vary wildly. For now, simple ndarray.
        
        # Note: Taichi ndarray interacting with numpy is fast.
        
        out_verts_np = np.zeros((n_r, n_a, n_b, max_out_verts, 2), dtype=np.float32)
        out_len_np = np.zeros((n_r, n_a, n_b), dtype=np.int32)
        
        # Call Kernel
        compute_minkowski_sum_convex_kernel(
            n_a, n_b, n_r, 
            np_a, 
            len_a, 
            np_b, 
            len_b, 
            np_rot, 
            out_verts_np, 
            out_len_np
        )
        
        # Sync happened implicitly or explicitly? taichi ndarray syncs.
        
        # Post-process on CPU: Compute Convex Hulls
        # This is "embarrassingly parallel" on CPU too if we use threads, 
        # but the sheer number of hulls might is high.
        # However, for NFP we usually have few convex parts (e.g. 1-10 per shape).
        
        results_per_rotation = []
        
        for r in range(n_r):
            minkowski_polys = []
            for i in range(n_a):
                for j in range(n_b):
                    count = out_len_np[r, i, j]
                    if count < 3: continue
                    
                    points = out_verts_np[r, i, j, :count]
                    
                    # Create convex hull from these points
                    # Shapely's MultiPoint(points).convex_hull is robust
                    cloud = MultiPoint(points)
                    hull = cloud.convex_hull
                    
                    if not hull.is_empty:
                        minkowski_polys.append(hull)
                        
            results_per_rotation.append(minkowski_polys)
            
        return results_per_rotation

else:
    def compute_nfp_batch(poly_a_list, poly_b_list, rotations_deg):
        raise ImportError("Taichi is not installed. Cannot compute GPU NFP.")
