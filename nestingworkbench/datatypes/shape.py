# Nesting/nesting/datatypes/shape.py

"""
This module contains the Shape class, which represents a single part to be
nested. It holds the source FreeCAD object, its shapely-based geometry for
the nesting algorithm, and its final placement information.
"""
import Part
import copy
import FreeCAD
import threading
from ..freecad_helpers import get_up_direction_rotation

try:
    from shapely.affinity import translate, rotate
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

class Shape:
    """
    Represents a single part for nesting. This class holds the source object,
    its geometric boundary (as a shapely Polygon), and its placement state
    during and after the nesting process.
    """
    nfp_cache = {}
    nfp_cache_lock = threading.Lock()
    decomposition_cache = {}
    
    @classmethod
    def clear_caches(cls):
        """Clears decomposition cache between nesting runs. Does NOT clear NFP cache
        since NFP calculations are expensive and benefit from persistence."""
        cls.decomposition_cache.clear()

    @classmethod
    def clear_nfp_cache(cls):
        """Clears the NFP cache. Only call when explicitly requested by the user."""
        with cls.nfp_cache_lock:
            cls.nfp_cache.clear()
    
    def __init__(self, source_freecad_object):
        self.source_freecad_object = source_freecad_object
        self.instance_num = 1 # Default, will be overridden on copies
        self.id = f"{source_freecad_object.Label}_{self.instance_num}"
        
        # --- Geometric properties (merged from ShapeBounds) ---
        self._angle = 0
        self.polygon = None # The current, transformed polygon for collision checks
        self.original_polygon = None # The un-rotated buffered polygon, used as a base for rotation
        self.unbuffered_polygon = None # The un-rotated, un-buffered polygon for area calculation
        self.source_centroid = None # The original pivot point from the FreeCAD geometry

        # --- Metadata ---
        self.label_text = None # Will hold the text for the Draft.ShapeString object
        self.rotation_steps = 1 # The definitive number of rotation steps for this part.
        self.spacing = 0 # The spacing used for the nesting operation.
        self.deflection = 0.05 # The deflection tolerance used.
        self.simplification = 1.0 # The simplification tolerance used.
        self.up_direction = "Z+" # The up direction for 2D projection
        self.fill_sheet = False # If True, use to fill remaining space
        
        # --- State during/after nesting ---
        self.fc_object = None # Link to the physical FreeCAD object in the 'PartsToPlace' group
        self.placement = None # This will be populated with the final FreeCAD.Placement after nesting.

    def __repr__(self):
        return f"<Shape: {self.id}, polygon={'set' if self.polygon else 'unset'}>"

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

        # CRITICAL: The fc_object is a link to a live FreeCAD object and cannot be
        # deep-copied. We explicitly set it to None on the new copy. This is essential
        # for creating copies for the nesting algorithm without causing pickling errors.
        result.fc_object = None 

        # Deepcopy other attributes, explicitly skipping the non-copyable ones.
        for k, v in self.__dict__.items():
            if k in ['source_freecad_object', 'fc_object']:
                continue

            if isinstance(v, FreeCAD.Vector):
                setattr(result, k, FreeCAD.Vector(v))
            elif isinstance(v, FreeCAD.Placement):
                setattr(result, k, FreeCAD.Placement(v))
            elif k in ['polygon', 'original_polygon', 'unbuffered_polygon']:
                # Shapely polygons are immutable, but deepcopying is safer.
                setattr(result, k, copy.deepcopy(v, memo))
            else:
                setattr(result, k, copy.deepcopy(v, memo))

        return result

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
        if not self.polygon or not SHAPELY_AVAILABLE:
            return None
        
        # The polygon in shape_bounds is already rotated. We just need to translate it.
        final_polygon = translate(self.polygon, xoff=sheet_origin.x, yoff=sheet_origin.y)

        bound_obj_name = f"bound_{self.id}"
        # ALWAYS create new boundary object - don't reuse existing ones as they may belong to other layouts
        # FreeCAD will auto-rename if there's a name collision (e.g., bound_Triangle001)
        bound_obj = doc.addObject("Part::Feature", bound_obj_name)

        wires = []
        # Create exterior wire
        exterior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in final_polygon.exterior.coords]
        if len(exterior_verts) > 2: wires.append(Part.makePolygon(exterior_verts))
        # Create interior wires (holes)
        for i, interior in enumerate(final_polygon.interiors):
            interior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in interior.coords]
            if len(interior_verts) > 2: wires.append(Part.makePolygon(interior_verts))
        if not wires:
            # Remove the empty object we created
            doc.removeObject(bound_obj.Name)
            return None

        new_shape = Part.makeCompound(wires)
        bound_obj.Shape = new_shape
        if group: group.addObject(bound_obj)
        if FreeCAD.GuiUp: bound_obj.ViewObject.LineColor = (1.0, 0.0, 0.0)
        return bound_obj

    def get_final_placement(self, sheet_origin=None):
        """
        Calculates the final FreeCAD.Placement for the container.
        
        CLEAN OFFSET DESIGN:
        - The child shape inside the container has its own rotation for up_direction
        - The container placement only handles XY position and in-plane (Z) rotation
        - This keeps bounds flat on the sheet

        :param sheet_origin: FreeCAD.Vector for the sheet's bottom-left corner.
        :return: A final FreeCAD.Placement object.
        """
        if not self.polygon:
            return FreeCAD.Placement()

        if sheet_origin is None:
            sheet_origin = FreeCAD.Vector(0, 0, 0)

        # Where the nested polygon's center ended up
        nested_centroid_shapely = self.polygon.centroid
        nested_centroid = FreeCAD.Vector(nested_centroid_shapely.x, nested_centroid_shapely.y, 0)
        
        # Container position = target world position for the shape's center
        container_pos = sheet_origin + nested_centroid
        
        # Only in-plane Z rotation for the container (keeps bounds flat)
        angle_deg = self._angle
        z_rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), angle_deg)
        
        # Rotation center is at origin
        return FreeCAD.Placement(container_pos, z_rotation, FreeCAD.Vector(0, 0, 0))
    

    def set_rotation(self, angle, reposition=True):
        """
        Sets the rotation of the shape's bounds to an absolute angle (in degrees).
        """
        if self.original_polygon:
            if reposition:
                current_bl_x, current_bl_y, _, _ = self.bounding_box() # Preserve position

            self._angle = angle
            center = self.original_polygon.centroid
            self.polygon = rotate(self.original_polygon, angle, origin=center) # Always rotate from the true original
            
            if reposition:
                self.move_to(current_bl_x, current_bl_y)

    def move(self, dx, dy):
        """
        Moves the shape's bounds by a given delta.
        """
        if not self.polygon:
            return
        self.polygon = translate(self.polygon, xoff=dx, yoff=dy)

    def move_to(self, x, y):
        """
        Moves the shape's bounds to an absolute position (bottom-left corner).
        """
        if self.polygon:
            min_x, min_y, _, _ = self.bounding_box()
            dx = x - min_x
            dy = y - min_y
            self.move(dx, dy)

    def bounding_box(self):
        """
        Returns the bounding box of the shape's bounds.
        """
        if not self.polygon: return (0, 0, 0, 0)
        min_x, min_y, max_x, max_y = self.polygon.bounds
        return min_x, min_y, max_x - min_x, max_y - min_y

    @property
    def area(self):
        """
        Returns the area of the shape's bounds.
        """
        return self.polygon.area if self.polygon else 0.0

    @property
    def angle(self):
        """
        Returns the current rotation angle of the shape's bounds.
        """
        return self._angle

    @property
    def centroid(self):
        """
        Returns the centroid of the shape's bounds polygon.
        """
        return self.polygon.centroid if self.polygon else None
