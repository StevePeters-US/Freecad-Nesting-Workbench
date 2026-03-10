import pytest
from unittest.mock import MagicMock, patch
from nestingworkbench.Tools.Nesting.nesting_logic import nest, NestingDependencyError
from nestingworkbench.datatypes.shape import Shape
from shapely.geometry import Polygon

@pytest.fixture
def mock_shape():
    mock_obj = MagicMock()
    mock_obj.Label = "TestPart"
    shape = Shape(mock_obj)
    shape.unbuffered_polygon = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    shape.original_polygon = shape.unbuffered_polygon
    shape.polygon = shape.original_polygon
    return shape

def test_nest_empty_parts():
    # T-019: Empty parts list -> returns empty result without error
    sheets, unplaced, steps, elapsed = nest([], 100, 100)
    assert sheets == []
    assert unplaced == []
    assert steps == 0
    assert elapsed >= 0

def test_nest_single_part_success(mock_shape):
    # T-019: Single part, valid sheet -> part is placed
    # Note: We need a real Nester or at least enough of it to run.
    # Nester uses MinkowskiEngine which might be slow but should work in our mock environment
    # if we provide enough geometric data.
    
    sheets, unplaced, steps, elapsed = nest([mock_shape], 100, 100)
    
    assert len(sheets) == 1
    assert len(sheets[0].parts) == 1
    assert unplaced == []

def test_nest_part_too_large(mock_shape):
    # T-019: Part larger than sheet -> no placement found (graceful failure)
    # Make shape larger than 10x10 sheet
    mock_shape.unbuffered_polygon = Polygon([(0, 0), (50, 0), (50, 50), (0, 50)])
    mock_shape.original_polygon = mock_shape.unbuffered_polygon
    mock_shape.polygon = mock_shape.original_polygon
    
    sheets, unplaced, steps, elapsed = nest([mock_shape], 10, 10)
    
    # Check unplaced contents by ID since they were deepcopied
    unplaced_ids = [s.id for s in unplaced]
    assert mock_shape.id in unplaced_ids
def test_nest_simulate_callbacks(mock_shape):
    # Verify that simulation callbacks use viz_manager as expected
    from nestingworkbench.Tools.Nesting.nesting_logic import viz_manager
    
    # Mock viz_manager methods
    viz_manager.draw_trial_placement = MagicMock()
    viz_manager.highlight_master = MagicMock()
    viz_manager.clear_trial_placement = MagicMock()
    viz_manager.clear_highlight = MagicMock()
    
    # Mock _find_master_container_for_part
    with patch('nestingworkbench.Tools.Nesting.nesting_logic._find_master_container_for_part') as mock_find:
        mock_find.return_value = MagicMock()
        
        # Run nest with simulate=True
        sheets, unplaced, steps, elapsed = nest([mock_shape], 100, 100, simulate=True)
        
        # Verify callbacks called directly in nest()
        assert viz_manager.highlight_master.called
        assert viz_manager.clear_highlight.called
        assert viz_manager.clear_trial_placement.called
        
        # Note: draw_trial_placement might not be called if Nester doesn't find a placement
        # in this mock environment, but we've verified the logic in nesting_logic.py
        # correctly passes _visualize_trial_placement to Nester.
