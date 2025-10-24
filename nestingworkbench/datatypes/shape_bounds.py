# Nesting/nesting/datatypes/nesting_part.py

"""
This module contains the ShapeBounds class, which represents a single part
to be nested, backed by a shapely Polygon.
"""

from shapely.geometry import Polygon
from shapely.affinity import translate, rotate
import FreeCAD
import copy
import Part

class ShapeBounds(object):
    """Represents the geometric boundary of a part for nesting algorithms."""
    def __init__(self):
        self.angle = 0
        self.offset_vector = None
        self.polygon = None
        self.original_polygon = None # The un-rotated buffered polygon
        self.unbuffered_polygon = None # The un-rotated, un-buffered polygon
        self.source_centroid = None

    def __deepcopy__(self, memo):
        """Custom deepcopy to handle non-pickleable FreeCAD objects."""
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            if isinstance(v, FreeCAD.Vector):
                setattr(result, k, FreeCAD.Vector(v))
            elif isinstance(v, FreeCAD.Placement):
                setattr(result, k, FreeCAD.Placement(v))
            elif k in ['polygon', 'original_polygon', 'unbuffered_polygon']:
                # Shapely polygons are immutable and can be shared,
                # but deepcopying them is safe and avoids side effects if they are modified.
                setattr(result, k, copy.deepcopy(v, memo))
            else:
                # For other attributes, use the standard deepcopy.
                setattr(result, k, copy.deepcopy(v, memo))
        return result

    def area(self):
        """Returns the area of the part's polygon."""
        return self.polygon.area

    def move(self, dx, dy):
        """Moves the part by a given delta."""
        self.polygon = translate(self.polygon, xoff=dx, yoff=dy)

    def set_rotation(self, angle, reposition=True):
        """
        Sets the rotation of the part to an absolute angle (in degrees).

        Args:
            angle (float): The rotation angle in degrees.
            reposition (bool): If True, the part is moved so its bounding box's
                               bottom-left corner remains in the same place.
                               This is the default for most nesters but should be
                               False for algorithms like Minkowski that handle
                               placement separately from rotation.
        """
        if reposition:
            current_bl_x, current_bl_y, _, _ = self.bounding_box() # Preserve position

        self.angle = angle
        center = self.original_polygon.centroid
        self.polygon = rotate(self.original_polygon, angle, origin=center) # Always rotate from the true original
        
        if reposition:
            self.move_to(current_bl_x, current_bl_y)

    def move_to(self, x, y):
        """Moves the part to an absolute position (bottom-left corner of bounding box)."""
        min_x, min_y, _, _ = self.bounding_box()
        dx = x - min_x
        dy = y - min_y
        self.move(dx, dy)

    def bounding_box(self):
        """Returns the bounding box of the part's polygon."""
        min_x, min_y, max_x, max_y = self.polygon.bounds
        return min_x, min_y, max_x - min_x, max_y - min_y