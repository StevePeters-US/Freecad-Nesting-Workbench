import pytest
from shapely.geometry import Polygon
from nestingworkbench.Tools.Nesting.algorithms.minkowski_utils import (
    decompose_if_needed,
    minkowski_sum_convex,
    minkowski_difference_convex,
    minkowski_difference,
    minkowski_sum
)

def test_decompose_if_needed_convex(unit_square):
    # Convex polygon should return itself in a list
    logger = lambda x, level=None: None
    result = decompose_if_needed(unit_square, logger)
    assert len(result) == 1
    assert result[0].equals(unit_square)

def test_decompose_if_needed_non_convex(l_shape):
    # Non-convex polygon should be decomposed into triangles
    logger = lambda x, level=None: None
    result = decompose_if_needed(l_shape, logger)
    assert len(result) > 1
    # Check that they are all convex (triangles are convex)
    for part in result:
        assert part.equals(part.convex_hull)
    # Check that union of parts matches original area
    from shapely.ops import unary_union
    assert pytest.approx(unary_union(result).area) == l_shape.area

def test_minkowski_sum_convex_unit_squares(unit_square):
    # A unit square summed with itself should be a 2x2 square
    # (since the sum is centered relative to the origin of the vertices)
    result = minkowski_sum_convex(unit_square, unit_square)
    assert result.area == 4.0
    # Min/max coords: v1 + v2. Wait, 0+0=0, 1+1=2. Correct.
    assert result.bounds == (0, 0, 2, 2)

def test_minkowski_difference_convex_IFP(large_square, unit_square):
    # Inner-Fit Polygon: valid positions for 1x1 centroid inside 10x10.
    # If the 1x1 is at origin [0,1]x[0,1], its centroid is at 0.5, 0.5.
    # The valid centroid range should be 0.5 to 9.5 in both axes.
    # Wait, minkowski_difference_convex uses translate(poly1, -v2[i])
    # Let's check the area.
    result = minkowski_difference_convex(large_square, unit_square)
    assert result is not None
    # 10 - 1 = 9. Area should be 9*9 = 81.
    assert pytest.approx(result.area) == 81.0

def test_minkowski_difference_integration(large_square, unit_square):
    # Integration test for minkowski_difference
    logger = lambda x, level=None: None
    # IFP for 1x1 inside 10x10
    result = minkowski_difference(large_square, 0, unit_square, 0, logger)
    assert result is not None
    assert pytest.approx(result.area) == 81.0
