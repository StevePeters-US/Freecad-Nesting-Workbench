import sys
import types
from unittest.mock import MagicMock

# --- Mock FreeCAD ---
class Vector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
    def __add__(self, other):
        return Vector(self.x + other.x, self.y + other.y, self.z + other.z)
    def __sub__(self, other):
        return Vector(self.x - other.x, self.y - other.y, self.z - other.z)
    def negative(self):
        return Vector(-self.x, -self.y, -self.z)
    def __repr__(self):
        return f"Vector({self.x}, {self.y}, {self.z})"

class Rotation:
    def __init__(self, *args):
        if len(args) >= 2:
            self.Axis = args[0]
            self.Angle = float(args[1])
        else:
            self.Axis = Vector(0, 0, 1)
            self.Angle = 0.0
    def inverted(self):
        return Rotation(self.Axis, -self.Angle)
    def multVec(self, vec):
        # Dummy rotation logic for tests
        return Vector(vec.x, vec.y, vec.z)

class Placement:
    def __init__(self, *args):
        # args can be (base, rotation) or (base, rotation, center)
        if len(args) >= 1:
            self.Base = args[0]
        else:
            self.Base = Vector()
        if len(args) >= 2:
            self.Rotation = args[1]
        else:
            self.Rotation = Rotation()
        self.Matrix = MagicMock()
    def isIdentity(self):
        return True # Default to identity for tests

def setup_mocks():
    # Mock FreeCAD
    mock_freecad = types.ModuleType("FreeCAD")
    mock_freecad.Console = MagicMock()
    mock_freecad.Vector = Vector
    mock_freecad.Rotation = Rotation
    mock_freecad.Placement = Placement
    mock_freecad.ActiveDocument = MagicMock()
    
    # Mock FreeCADGui
    mock_freecad_gui = types.ModuleType("FreeCADGui")
    
    # Mock Part
    mock_part = types.ModuleType("Part")
    mock_part.makePolygon = MagicMock()
    mock_part.Face = MagicMock()
    mock_part.makeCompound = MagicMock()
    mock_part.makePlane = MagicMock()
    
    # Mock PySide
    mock_pyside = types.ModuleType("PySide")
    mock_pyside.QtGui = MagicMock()
    
    # Inject into sys.modules
    sys.modules["FreeCAD"] = mock_freecad
    sys.modules["FreeCADGui"] = mock_freecad_gui
    sys.modules["Part"] = mock_part
    sys.modules["PySide"] = mock_pyside
    sys.modules["PySide.QtGui"] = mock_pyside.QtGui

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
