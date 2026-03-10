import pytest
from unittest.mock import MagicMock, patch
from nestingworkbench.Tools.Nesting.algorithms.shape_processor import create_single_nesting_part, get_2d_profile_from_obj
from nestingworkbench.datatypes.shape import Shape
import FreeCAD

@pytest.fixture
def mock_fc_obj():
    obj = MagicMock()
    obj.Label = "TestPart"
    obj.Placement = FreeCAD.Placement()
    
    # Mock Shape
    shape = MagicMock()
    shape.BoundBox = MagicMock()
    shape.BoundBox.XMin, shape.BoundBox.XMax = -5.0, 5.0
    shape.BoundBox.YMin, shape.BoundBox.YMax = -5.0, 5.0
    shape.BoundBox.ZMin, shape.BoundBox.ZMax = 0.0, 2.0
    
    shape.copy.return_value = shape
    shape.translate = MagicMock()
    shape.transformShape = MagicMock()
    
    # Mock tessellate to return a square (two triangles)
    # mesh[0] is vertices, mesh[1] is facets
    vertices = [
        (-5, -5, 0), (5, -5, 0), (5, 5, 0), (-5, 5, 0)
    ]
    facets = [
        (0, 1, 2), (0, 2, 3)
    ]
    shape.tessellate.return_value = (vertices, facets)
    
    obj.Shape = shape
    obj.isDerivedFrom.return_value = False # Not a Sketch or Part2DObject
    return obj

def test_get_2d_profile_from_obj(mock_fc_obj):
    # T-021: Test primary public entry point with valid geometry
    poly = get_2d_profile_from_obj(mock_fc_obj)
    assert not poly.is_empty
    assert poly.area == 100.0 # 10x10 square

def test_create_single_nesting_part(mock_fc_obj):
    # T-021: Test creation of nesting part
    shape_to_populate = Shape(mock_fc_obj)
    spacing = 2.0
    
    create_single_nesting_part(
        shape_to_populate, 
        mock_fc_obj, 
        spacing=spacing, 
        simplification=0.1
    )
    
    assert shape_to_populate.polygon is not None
    # Buffered area of 10x10 with 2.0 spacing (1.0 each side -> 12x12)
    # 12 * 12 = 144
    assert pytest.approx(shape_to_populate.polygon.area, rel=0.1) == 144.0
    assert shape_to_populate.spacing == spacing
    assert shape_to_populate.unbuffered_polygon.area == 100.0

def test_get_2d_profile_invalid_obj():
    # T-021: Test with invalid geometry (no vertices)
    obj = MagicMock()
    obj.Label = "EmptyPart"
    obj.Shape.tessellate.return_value = ([], [])
    obj.isDerivedFrom.return_value = False
    
    # It should fall back to bounding box if tessellation fails or returns empty
    # But wait, get_2d_profile_from_obj has a final raise ValueError if nothing worked.
    # If vertices are empty, it might fail before hull or box.
    
    with pytest.raises(ValueError, match="no valid 2D geometry found"):
        get_2d_profile_from_obj(obj)
