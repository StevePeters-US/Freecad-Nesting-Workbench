import pytest
from nestingworkbench.Tools.ManualNester.physics_engine import PhysicsEngine

class MockVector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z
    
    def __eq__(self, other):
        return abs(self.x - other.x) < 1e-6 and abs(self.y - other.y) < 1e-6 and abs(self.z - other.z) < 1e-6

    def __repr__(self):
        return f"MockVector({self.x}, {self.y}, {self.z})"

def test_compute_falloff():
    pe = PhysicsEngine(radius=200.0, curve_exponent=2.0, strength=1.0)
    
    # distance = 0 -> 1.0
    assert pe.compute_falloff(0) == 1.0
    
    # distance = radius -> 0.0
    assert pe.compute_falloff(200.0) == 0.0
    
    # distance = radius/2 -> 1 - (100/200)^2 = 1 - 0.25 = 0.75
    assert pe.compute_falloff(100.0) == 0.75
    
    # distance > radius -> 0.0
    assert pe.compute_falloff(250.0) == 0.0

def test_compute_displacements():
    pe = PhysicsEngine(radius=100.0, curve_exponent=1.0, strength=1.0) # Linear falloff
    
    dragged_center = MockVector(0, 0, 0)
    drag_delta = MockVector(10, 0, 0)
    
    parts_with_centers = [
        ("part1", MockVector(50, 0, 0)),  # distance 50 -> falloff 0.5
        ("part2", MockVector(0, 50, 0)),  # distance 50 -> falloff 0.5
        ("part3", MockVector(150, 0, 0)), # distance 150 -> falloff 0.0
    ]
    
    displacements = pe.compute_displacements(dragged_center, drag_delta, parts_with_centers)
    
    assert len(displacements) == 3
    
    # part1: 10 * 0.5 = 5
    assert displacements[0][0] == "part1"
    assert displacements[0][1] == MockVector(5, 0, 0)
    
    # part2: 10 * 0.5 = 5
    assert displacements[1][0] == "part2"
    assert displacements[1][1] == MockVector(5, 0, 0)
    
    # part3: 10 * 0.0 = 0
    assert displacements[2][0] == "part3"
    assert displacements[2][1] == MockVector(0, 0, 0)

def test_strength():
    pe = PhysicsEngine(radius=100.0, curve_exponent=1.0, strength=2.0)
    
    dragged_center = MockVector(0, 0, 0)
    drag_delta = MockVector(10, 0, 0)
    
    parts_with_centers = [
        ("part1", MockVector(50, 0, 0)),  # distance 50 -> falloff 0.5 * 2.0 = 1.0
    ]
    
    displacements = pe.compute_displacements(dragged_center, drag_delta, parts_with_centers)
    assert displacements[0][1] == MockVector(10, 0, 0)
