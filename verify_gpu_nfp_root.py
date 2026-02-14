import sys
import os
import time
import math
import unittest.mock

# --- Setup Paths and Mocks ---
# Add the current directory (repo root) to sys.path
sys.path.append(os.getcwd())

# Mock FreeCAD BEFORE importing any workbench modules
sys.modules["FreeCAD"] = unittest.mock.MagicMock()
sys.modules["FreeCAD"].Console = unittest.mock.MagicMock()
sys.modules["Part"] = unittest.mock.MagicMock()
sys.modules["Mesh"] = unittest.mock.MagicMock()

# Now import modules as part of the nestingworkbench package
# This requires that 'nestingworkbench' directory is in the current directory
try:
    from nestingworkbench.Tools.Nesting.algorithms.minkowski_engine import MinkowskiEngine
    from nestingworkbench.datatypes.shape import Shape
except ImportError as e:
    print(f"Import Error: {e}")
    # Fallback/Debug: print sys.path
    print(f"sys.path: {sys.path}")
    sys.exit(1)

from shapely.geometry import Polygon, box

class MockFreeCADObject:
    def __init__(self, label):
        self.Label = label

def create_test_shapes():
    # A simple L-shape
    poly_A = Polygon([(0,0), (100,0), (100,20), (20,20), (20,100), (0,100)])
    
    # A simple Box
    poly_B = box(0, 0, 10, 10)
    
    # Wrap in Shape object
    shape_A = Shape(MockFreeCADObject("PartA"))
    shape_A.original_polygon = poly_A
    shape_A.polygon = poly_A
    
    shape_B = Shape(MockFreeCADObject("PartB"))
    shape_B.original_polygon = poly_B
    shape_B.polygon = poly_B
    
    return shape_A, shape_B

def run_test():
    shape_A, shape_B = create_test_shapes()
    
    print("--- GPU NFP Verification ---")
    
    # 1. CPU Run
    print("Running CPU NFP...")
    engine_cpu = MinkowskiEngine(1000, 1000, 5, use_gpu=False)
    t0 = time.time()
    nfp_cpu = engine_cpu._calculate_and_cache_nfp(shape_A, 0, shape_B, 0, "cpu_test")
    t_cpu = time.time() - t0
    
    if not nfp_cpu or not nfp_cpu.get('polygon'):
        print("CPU NFP failed or empty.")
        return
        
    poly_cpu = nfp_cpu['polygon']
    print(f"CPU Time: {t_cpu:.4f}s")
    print(f"CPU Area: {poly_cpu.area:.2f}")
    print(f"CPU Centroid: {poly_cpu.centroid}")

    # 2. GPU Run
    print("\nRunning GPU NFP...")
    try:
        engine_gpu = MinkowskiEngine(1000, 1000, 5, use_gpu=True)
        if not engine_gpu.use_gpu:
            print("GPU not available in Engine (Taichi import failed?). Aborting GPU test.")
            return

        t0 = time.time()
        # Force a new cache key
        nfp_gpu = engine_gpu._calculate_and_cache_nfp_gpu(shape_A, 0, shape_B, 0, "gpu_test")
        t_gpu = time.time() - t0
        
        if not nfp_gpu or not nfp_gpu.get('polygon'):
            print("GPU NFP result is None or empty.")
        else:
            poly_gpu = nfp_gpu['polygon']
            print(f"GPU Time: {t_gpu:.4f}s")
            print(f"GPU Area: {poly_gpu.area:.2f}")
            print(f"GPU Centroid: {poly_gpu.centroid}")
            
            # Comparison
            area_diff = abs(poly_cpu.area - poly_gpu.area)
            centroid_dist = poly_cpu.centroid.distance(poly_gpu.centroid)
            
            print(f"\nArea Difference: {area_diff:.4f}")
            print(f"Centroid Distance: {centroid_dist:.4f}")
            
            # Validating correctness
            # Floating point tolerance
            assert area_diff < 1.0, f"Area difference too large! {area_diff}"
            assert centroid_dist < 1.0, f"Centroid distance too large! {centroid_dist}"
            print("SUCCESS: GPU NFP matches CPU NFP within tolerance.")
            
    except Exception as e:
        print(f"GPU Test Failed with Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_test()
