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
