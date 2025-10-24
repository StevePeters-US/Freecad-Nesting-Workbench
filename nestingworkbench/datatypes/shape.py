# Nesting/nesting/datatypes/shape.py

"""
This module contains the Shape class, a simple data wrapper for managing
FreeCAD objects during the nesting process.
"""

import copy
import FreeCAD
try:
    from shapely.affinity import translate
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

class Shape:
    """
    A simple wrapper class for a FreeCAD object. It holds the source object,
    its instance number, a reference to its geometric bounds, and its final
    calculated placement. This class acts as the central data carrier.
    """
    def __init__(self, source_freecad_object, instance_num=1):
        self.source_freecad_object = source_freecad_object
        self.instance_num = instance_num
        self.id = f"{source_freecad_object.Label}_{instance_num}"
        
        # This will hold the ShapeBounds object associated with this shape.
        # It is initialized externally after the Shape is created. Using __ for name mangling.
        self.__shape_bounds = None
        self.label_text = None # Will hold the text for the Draft.ShapeString object
        self.show_bounds = False # Default to false, controller will set initial state
        self.rotation_steps = 1 # The definitive number of rotation steps for this part.
        
        # This will be populated with the final placement after nesting.
        self.placement = None

    def __repr__(self):
        return f"<Shape: {self.id}, bounds={'set' if self.__shape_bounds else 'unset'}>"

    def __deepcopy__(self, memo):
        """
        Custom deepcopy to handle the non-pickleable FreeCAD object reference.
        """
        # Create a new instance without calling __init__ to avoid re-processing
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result

        # Copy the reference to the FreeCAD object, do NOT deepcopy it.
        result.source_freecad_object = self.source_freecad_object

        # Deepcopy the other attributes that are safe to copy (including __shape_bounds).
        for k, v in self.__dict__.items():
            if k not in ['source_freecad_object']:
                setattr(result, k, copy.deepcopy(v, memo))
        return result

    def generate_bounds(self, shape_processor, tolerance=0.0):
        """
        Uses a shape processor to generate the ShapeBounds object for this shape
        and establishes a back-reference.

        :param shape_processor: The processor object capable of converting a FreeCAD object.
        :param tolerance: The tolerance for the shape conversion.
        :return: The generated ShapeBounds object, or None on failure.
        """
        self.__shape_bounds = shape_processor.get_shape_bounds(self.source_freecad_object, tolerance)
        return self.__shape_bounds

    @property
    def shape_bounds(self):
        """Read-only access to the ShapeBounds object."""
        return self.__shape_bounds

    def get_final_bounds_polygon(self, sheet_origin_vector):
        """
        Returns the shape_bounds polygon translated to its final position on the sheet.

        :param sheet_origin_vector: A FreeCAD.Vector representing the origin of the sheet.
        :return: A translated shapely.geometry.Polygon, or None.
        """
        if not self.__shape_bounds or not self.__shape_bounds.polygon or not SHAPELY_AVAILABLE:
            return None
        
        return translate(self.__shape_bounds.polygon, xoff=sheet_origin_vector.x, yoff=sheet_origin_vector.y)

    def get_final_placement(self, sheet_origin, nested_centroid, angle_deg):
        """
        Calculates the final FreeCAD.Placement for the object.

        This method assumes that the shape's bounds have been normalized by the
        shape_processor, meaning the offset_vector is zero.

        :param sheet_origin: FreeCAD.Vector for the sheet's bottom-left corner.
        :param nested_centroid: FreeCAD.Vector for the part's centroid on the virtual sheet.
        :param angle_deg: The rotation angle in degrees.
        :return: A final FreeCAD.Placement object.
        """
        # Define the rotation.
        rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), angle_deg)

        # The final target position for the shape's source_centroid.
        target_centroid_pos = sheet_origin + nested_centroid

        # The FreeCAD Placement constructor with a center argument follows the formula:
        # final_point = Base + Center + Rotation * (point - Center) (where Center is the rotation center)
        # We want the final position of the source_centroid to be target_centroid_pos.
        # we must set Base = target_centroid_pos - rotation_center.
        
        # It is critical to check for shape_bounds and source_centroid, as this method might be
        # called on temporary objects during preview that don't have these fully initialized.
        rotation_center = self.__shape_bounds.source_centroid if self.__shape_bounds and self.__shape_bounds.source_centroid else FreeCAD.Vector(0, 0, 0)

        final_position = target_centroid_pos - rotation_center

        # Create the final placement.
        return FreeCAD.Placement(final_position, rotation, rotation_center)

    def set_shape_bounds(self, shape_bounds):
        """
        Sets the ShapeBounds object for this shape.
        """
        self.__shape_bounds = shape_bounds

    def set_bounds_visibility(self, is_visible):
        """
        Sets the flag that determines if this shape's bounds should be drawn.
        """
        self.show_bounds = is_visible

    def set_rotation(self, angle, **kwargs):
        """
        Sets the rotation of the shape's bounds to an absolute angle (in degrees).
        This delegates the rotation to the underlying ShapeBounds object.
        """
        if self.__shape_bounds:
            self.__shape_bounds.set_rotation(angle, **kwargs)

    def move(self, dx, dy):
        """
        Moves the shape's bounds by a given delta.
        This delegates the move to the underlying ShapeBounds object.
        """
        if self.__shape_bounds:
            self.__shape_bounds.move(dx, dy)

    def move_to(self, x, y):
        """
        Moves the shape's bounds to an absolute position (bottom-left corner).
        This delegates the move to the underlying ShapeBounds object.
        """
        if self.__shape_bounds:
            self.__shape_bounds.move_to(x, y)

    def bounding_box(self):
        """
        Returns the bounding box of the shape's bounds.
        """
        return self.__shape_bounds.bounding_box() if self.__shape_bounds else (0, 0, 0, 0)

    def area(self):
        """
        Returns the area of the shape's bounds.
        """
        return self.__shape_bounds.area() if self.__shape_bounds else 0.0

    def get_polygon(self):
        """
        Returns the shapely polygon of the shape's bounds.
        """
        return self.__shape_bounds.polygon if self.__shape_bounds else None

    def get_angle(self):
        """
        Returns the current rotation angle of the shape's bounds.
        """
        return self.__shape_bounds.angle if self.__shape_bounds else 0.0

    def get_centroid(self):
        """
        Returns the centroid of the shape's bounds polygon.
        """
        return self.__shape_bounds.polygon.centroid if self.__shape_bounds and self.__shape_bounds.polygon else None