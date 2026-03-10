import pytest
from unittest.mock import MagicMock
from nestingworkbench.datatypes.shape import Shape
from shapely.geometry import Polygon

def test_shape_initialization(unit_square):
    mock_obj = MagicMock()
    mock_obj.Label = "TestPart"
    
    shape = Shape(mock_obj)
    shape.polygon = unit_square
    
    # id is Label + _ + instance_num
    assert shape.id == "TestPart_1"
    assert shape.area == 1.0

def test_shape_set_rotation(unit_square):
    mock_obj = MagicMock()
    mock_obj.Label = "TestPart"
    shape = Shape(mock_obj)
    shape.original_polygon = unit_square
    shape.polygon = unit_square
    
    # Rotate 90 degrees around centroid (0.5, 0.5)
    shape.set_rotation(90)
    assert shape.angle == 90
    
    # Check centroid stability
    assert pytest.approx(shape.centroid.x) == 0.5
    assert pytest.approx(shape.centroid.y) == 0.5

def test_shape_move(unit_square):
    mock_obj = MagicMock()
    mock_obj.Label = "TestPart"
    shape = Shape(mock_obj)
    shape.polygon = unit_square
    
    shape.move(10, 20)
    assert shape.polygon.bounds == (10, 20, 11, 21)

def test_shape_move_to(unit_square):
    mock_obj = MagicMock()
    mock_obj.Label = "TestPart"
    shape = Shape(mock_obj)
    shape.polygon = unit_square
    
    shape.move_to(100, 200)
    assert shape.polygon.bounds == (100, 200, 101, 201)

def test_shape_bounding_box(unit_square):
    mock_obj = MagicMock()
    mock_obj.Label = "TestPart"
    shape = Shape(mock_obj)
    shape.polygon = unit_square
    
    # (minx, miny, width, height)
    bbox = shape.bounding_box()
    assert bbox == (0, 0, 1, 1)

def test_shape_get_final_placement(unit_square):
    import FreeCAD
    mock_obj = MagicMock()
    mock_obj.Label = "TestPart"
    shape = Shape(mock_obj)
    shape.polygon = unit_square # Centroid (0.5, 0.5)
    shape.original_polygon = unit_square
    
    # 0 deg rotation
    shape.set_rotation(0)
    placement_0 = shape.get_final_placement(FreeCAD.Vector(10, 10, 0))
    # container_pos = sheet_origin (10,10,0) + centroid (0.5, 0.5, 0) = (10.5, 10.5, 0)
    assert placement_0.Base.x == 10.5
    assert placement_0.Base.y == 10.5
    assert placement_0.Rotation.Angle == 0.0

    # 90 deg rotation
    shape.set_rotation(90)
    placement_90 = shape.get_final_placement(FreeCAD.Vector(0, 0, 0))
    # Centroid should remain (0.5, 0.5) if it rotates around its own centroid
    assert placement_90.Base.x == 0.5
    assert placement_90.Base.y == 0.5
    assert placement_90.Rotation.Angle == 90.0

    # 180 deg rotation
    shape.set_rotation(180)
    placement_180 = shape.get_final_placement(FreeCAD.Vector(0, 0, 0))
    assert placement_180.Rotation.Angle == 180.0

    # Negative rotation
    shape.set_rotation(-45)
    placement_neg = shape.get_final_placement(FreeCAD.Vector(0, 0, 0))
    assert placement_neg.Rotation.Angle == -45.0
