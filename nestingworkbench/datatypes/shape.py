# Nesting/nesting/datatypes/shape.py

"""
This module contains the Shape class, a simple data wrapper for managing
FreeCAD objects during the nesting process.
"""
import Part
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
    def __init__(self, source_freecad_object):
        self.source_freecad_object = source_freecad_object
        self.instance_num = 1 # Default, will be overridden on copies
        self.id = f"{source_freecad_object.Label}_{self.instance_num}"
        
        # This will hold the ShapeBounds object associated with this shape.
        # It is initialized externally after the Shape is created. Using __ for name mangling.
        self.__shape_bounds = None
        self.label_text = None # Will hold the text for the Draft.ShapeString object
        self.show_bounds = False # Default to false, controller will set initial state
        self.rotation_steps = 1 # The definitive number of rotation steps for this part.
        
        self.fc_object = None # Link to the physical FreeCAD object in the 'PartsToPlace' group
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
            if k not in ['source_freecad_object', 'fc_object']: # Don't deepcopy the fc_object link
                setattr(result, k, copy.deepcopy(v, memo))
        return result

    def generate_bounds(self, shape_processor, spacing, resolution):
        """
        Uses a shape processor to generate the ShapeBounds object for this shape
        and establishes a back-reference.

        :param shape_processor: The processor object capable of converting a FreeCAD object.
        :param spacing: The spacing to apply for the buffered boundary.
        :param resolution: The resolution for discretizing curves.
        :return: The generated ShapeBounds object, or None on failure.
        """
        self.__shape_bounds = shape_processor.create_single_nesting_part(self.source_freecad_object, spacing, resolution)
        return self.__shape_bounds

    @property
    def shape_bounds(self):
        """Read-only access to the ShapeBounds object."""
        return self.__shape_bounds

    def draw_bounds(self, doc, sheet_origin, group):
        """
        Draws the exterior and interior boundaries of the shape's final polygon in FreeCAD.

        Args:
            doc (FreeCAD.Document): The active document.
            sheet_origin (FreeCAD.Vector): The origin of the sheet this part is on.
            group (App.DocumentObjectGroup): The group to add the new objects to.
        Returns:
            App.DocumentObject: The created or updated boundary object, or None.
        """
        if not self.__shape_bounds or not self.__shape_bounds.polygon or not SHAPELY_AVAILABLE:
            return None
        
        # The polygon in shape_bounds is already rotated. We just need to translate it.
        final_polygon = translate(self.__shape_bounds.polygon, xoff=sheet_origin.x, yoff=sheet_origin.y)

        bound_obj_name = f"bound_{self.id}"
        bound_obj = doc.getObject(bound_obj_name)

        wires = []
        # Create exterior wire
        exterior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in final_polygon.exterior.coords]
        if len(exterior_verts) > 2: wires.append(Part.makePolygon(exterior_verts))
        # Create interior wires (holes)
        for i, interior in enumerate(final_polygon.interiors):
            interior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in interior.coords]
            if len(interior_verts) > 2: wires.append(Part.makePolygon(interior_verts))
        if not wires: return None

        new_shape = Part.makeCompound(wires)

        if bound_obj:
            bound_obj.Shape = new_shape
        else:
            bound_obj = doc.addObject("Part::Feature", bound_obj_name)
            bound_obj.Shape = new_shape
            group.addObject(bound_obj)
            if FreeCAD.GuiUp: bound_obj.ViewObject.LineColor = (1.0, 0.0, 0.0)
        return bound_obj

    def get_final_placement(self, sheet_origin=None):
        """
        Calculates the final FreeCAD.Placement for the object.
        This method uses the current state of the underlying ShapeBounds object.

        :param sheet_origin: FreeCAD.Vector for the sheet's bottom-left corner.
        :return: A final FreeCAD.Placement object.
        """
        if not self.__shape_bounds or not self.__shape_bounds.polygon:
            return FreeCAD.Placement()

        if sheet_origin is None:
            sheet_origin = FreeCAD.Vector(0,0,0)

        nested_centroid_shapely = self.__shape_bounds.polygon.centroid
        nested_centroid = FreeCAD.Vector(nested_centroid_shapely.x, nested_centroid_shapely.y, 0)
        angle_deg = self.__shape_bounds.angle

        # Define the rotation.
        rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), angle_deg)

        # The final target position for the shape's source_centroid.
        target_centroid_pos = sheet_origin + nested_centroid

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
            self._update_fc_object_placement()

    def move(self, dx, dy):
        """
        Moves the shape's bounds by a given delta.
        This delegates the move to the underlying ShapeBounds object.
        """
        if self.__shape_bounds:
            self.__shape_bounds.move(dx, dy)
            self._update_fc_object_placement()

    def move_to(self, x, y):
        """
        Moves the shape's bounds to an absolute position (bottom-left corner).
        This delegates the move to the underlying ShapeBounds object.
        """
        if self.__shape_bounds:
            self.__shape_bounds.move_to(x, y)
            self._update_fc_object_placement()

    def _update_fc_object_placement(self):
        """Updates the associated FreeCAD object's placement if it exists."""
        if hasattr(self, 'fc_object') and self.fc_object:
            # We calculate the placement on-the-fly from the current state.
            # The sheet origin is (0,0,0) during the nesting phase.
            new_placement = self.get_final_placement()
            self.fc_object.Placement = new_placement
            if hasattr(self.fc_object, 'BoundaryObject') and self.fc_object.BoundaryObject:
                self.fc_object.BoundaryObject.Placement = new_placement
    
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