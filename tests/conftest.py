import sys
import types
from unittest.mock import MagicMock

# --- Mock FreeCAD ---
def setup_mocks():
    # Mock FreeCAD
    mock_freecad = types.ModuleType("FreeCAD")
    mock_freecad.Console = MagicMock()
    mock_freecad.Vector = MagicMock()
    mock_freecad.Rotation = MagicMock()
    mock_freecad.Placement = MagicMock()
    
    # Mock FreeCADGui
    mock_freecad_gui = types.ModuleType("FreeCADGui")
    
    # Mock Part
    mock_part = types.ModuleType("Part")
    mock_part.makePolygon = MagicMock()
    mock_part.Face = MagicMock()
    
    # Inject into sys.modules
    sys.modules["FreeCAD"] = mock_freecad
    sys.modules["FreeCADGui"] = mock_freecad_gui
    sys.modules["Part"] = mock_part

setup_mocks()

import pytest
from shapely.geometry import Polygon

@pytest.fixture
def unit_square():
    return Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])

@pytest.fixture
def l_shape():
    return Polygon([(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)])

@pytest.fixture
def large_square():
    return Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
