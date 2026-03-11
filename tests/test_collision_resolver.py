import pytest
from nestingworkbench.Tools.ManualNester.collision_resolver import CollisionResolver

class MockVector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z
    
    def copy(self):
        return MockVector(self.x, self.y, self.z)

    def __eq__(self, other):
        return abs(self.x - other.x) < 1e-6 and abs(self.y - other.y) < 1e-6 and abs(self.z - other.z) < 1e-6

    def __repr__(self):
        return f"MockVector({self.x}, {self.y}, {self.z})"

class MockBoundBox:
    def __init__(self, xmin, xmax, ymin, ymax):
        self.XMin = xmin
        self.XMax = xmax
        self.YMin = ymin
        self.YMax = ymax
        self.XLength = xmax - xmin
        self.YLength = ymax - ymin

class MockPlacement:
    def __init__(self, base):
        self.Base = base

class MockShape:
    def __init__(self, bbox):
        self.BoundBox = bbox

class MockObj:
    def __init__(self, name, placement, shape):
        self.Name = name
        self.Placement = placement
        self.Shape = shape

@pytest.fixture
def resolver():
    return CollisionResolver()

def test_clamp_to_sheet_inside(resolver):
    sheet_bbox = MockBoundBox(0, 1000, 0, 1000)
    obj_bbox = MockBoundBox(0, 100, 0, 100)
    placement = MockPlacement(MockVector(500, 500, 0))
    obj = MockObj("part", placement, MockShape(obj_bbox))
    
    clamped = resolver.clamp_to_sheet(obj, sheet_bbox)
    assert not clamped
    assert obj.Placement.Base == MockVector(500, 500, 0)

def test_clamp_to_sheet_outside_xmin(resolver):
    sheet_bbox = MockBoundBox(0, 1000, 0, 1000)
    obj_bbox = MockBoundBox(0, 100, 0, 100)
    # obj_min_x = -10 + 0 = -10 (outside)
    placement = MockPlacement(MockVector(-10, 500, 0))
    obj = MockObj("part", placement, MockShape(obj_bbox))
    
    clamped = resolver.clamp_to_sheet(obj, sheet_bbox)
    assert clamped
    # Expected: new_x = -10 + (0 - (-10)) = 0
    assert obj.Placement.Base == MockVector(0, 500, 0)

def test_clamp_to_sheet_outside_xmax(resolver):
    sheet_bbox = MockBoundBox(0, 1000, 0, 1000)
    obj_bbox = MockBoundBox(0, 100, 0, 100)
    # obj_max_x = 950 + 100 = 1050 (outside)
    placement = MockPlacement(MockVector(950, 500, 0))
    obj = MockObj("part", placement, MockShape(obj_bbox))
    
    clamped = resolver.clamp_to_sheet(obj, sheet_bbox)
    assert clamped
    # Expected: new_x = 950 - (1050 - 1000) = 900
    assert obj.Placement.Base == MockVector(900, 500, 0)

def test_separate_overlapping_simple(resolver):
    # part1 at (0,0), size 100x100
    p1 = MockObj("p1", MockPlacement(MockVector(0, 0, 0)), MockShape(MockBoundBox(0, 100, 0, 100)))
    # part2 at (80, 50), size 100x100 -> overlap (80,50) to (100, 150)
    # overlap_x = 100 - 80 = 20
    # overlap_y = 100 - 50 = 50 -> push along X
    p2 = MockObj("p2", MockPlacement(MockVector(80, 50, 0)), MockShape(MockBoundBox(0, 100, 0, 100)))
    
    # We move p2
    resolved = resolver.separate_overlapping(p2, [p1])
    assert resolved
    # p2 was at (80, 50), center (130, 100)
    # p1 center (50, 50)
    # p2.center_x > p1.center_x -> push in +X direction
    # expected p2.x = 80 + 20 = 100
    assert p2.Placement.Base == MockVector(100, 50, 0)

def test_separate_overlapping_multiple(resolver):
    # p1 at (0,0)
    p1 = MockObj("p1", MockPlacement(MockVector(0, 0, 0)), MockShape(MockBoundBox(0, 100, 0, 100)))
    # p2 at (150, 0)
    p2 = MockObj("p2", MockPlacement(MockVector(150, 0, 0)), MockShape(MockBoundBox(0, 100, 0, 100)))
    
    # p_moved overlapping both? No, let's overlap p1 and be pushed into p2
    # p_moved at (80, 0) -> overlaps p1 by 20. Pushed to (100, 0).
    # Then it will overlap p2 at (100, 0) since p2 is at (150, 0) but size is 100, 
    # so p2 starts at 150. Wait, p_moved at 100, ends at 200. p2 starts at 150. Overlap!
    p_moved = MockObj("pm", MockPlacement(MockVector(80, 0, 0)), MockShape(MockBoundBox(0, 100, 0, 100)))
    
    resolved = resolver.separate_overlapping(p_moved, [p1, p2])
    assert resolved
    # p1 is at 0-100. p2 is at 150-250.
    # Initial pm at 80-180.
    # Iteration 1:
    #   Overlaps p1: push X+ by 20 -> pm at 100-200.
    #   Overlaps p2: push X- by 50 -> pm at 50-150. (Center pm (125) < p2 center (200))
    # This might oscillate or converge. Let's see.
    # Actually, pm at 100-200 overlaps p2 (150-250) by 50. 
    # Center of pm is 150. Center of p2 is 200. pm < p2 -> push X- by 50.
    assert p_moved.Placement.Base.x <= 50 or p_moved.Placement.Base.x >= 250
