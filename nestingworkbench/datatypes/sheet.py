# Nesting/nesting/datatypes/sheet.py

"""
This module contains the Sheet class, which represents a single bin or sheet
in the nesting layout.
"""

import FreeCAD
import Part

try:
    from shapely.geometry import Polygon
    # from shapely.ops import unary_union
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

try:
    import Draft
except ImportError:
    Draft = None

from .shape_object import create_shape_object
from .label_object import create_label_object

class Sheet:
    """
    Represents a single sheet (or bin) in the nesting layout. It contains
    the parts that have been placed on it.
    """
    def __init__(self, sheet_id, width, height, spacing=0):
        self.id = sheet_id
        self.width = width
        self.height = height
        self.used_area = 0.0 # Track area usage for fast filtering
        self.parts = [] # List of PlacedPart objects
        self.spacing = spacing
        self.parent_group_name = None # Will store the name of the top-level layout group
        self.nfp_cache = {} # Cache for partial NFPs of this sheet: (label, resolution, angle) -> {'polygon': Poly, 'placed_count': int}

    def __repr__(self):
        return f"<Sheet id={self.id}, parts={len(self.parts)}>"

    def __iter__(self):
        """Allows iterating directly over the parts on the sheet."""
        return iter(self.parts)

    def __len__(self):
        """Returns the number of parts on the sheet."""
        return len(self.parts)

    def add_part(self, placed_part):
        """Adds a part to the sheet."""
        self.parts.append(placed_part)
        self.used_area += placed_part.shape.area

    def get_origin(self):
        """
        Calculates the origin (bottom-left corner) of this sheet in a layout.

        Returns:
            FreeCAD.Vector: The calculated origin vector.
        """
        return FreeCAD.Vector(self.id * (self.width + self.spacing), 0, 0)

    def calculate_fill_percentage(self, use_unbuffered_area=True):
        """
        Calculates the fill percentage of the sheet.

        Args:
            use_unbuffered_area (bool): If True, uses the original part area without spacing.
                                        If False, uses the buffered area (including spacing).

        Returns:
            float: The fill percentage (0-100), or 0 if sheet area is zero.
        """
        sheet_area = self.width * self.height
        if sheet_area == 0:
            return 0.0

        total_part_area = 0
        for part in self.parts:
            if part.shape:
                if use_unbuffered_area and part.shape.unbuffered_polygon:
                    total_part_area += part.shape.unbuffered_polygon.area
                elif part.shape.polygon: # Fallback to buffered
                    total_part_area += part.shape.polygon.area
        
        return (total_part_area / sheet_area) * 100.0



    def is_placement_valid(self, shape_to_check, part_to_ignore=None):
        """
        Checks if a shape's placement is valid on this sheet, considering both
        containment and collision with existing parts.

        Args:
            shape_to_check (Shape): The shape instance with its bounds polygon at the desired location.
            part_to_ignore (Shape, optional): A specific shape to exclude from the collision check.

        Returns:
            bool: True if the placement is valid, False otherwise.
        """
        if not SHAPELY_AVAILABLE: return False
        if not shape_to_check.polygon: return False

        # 1. Check containment within sheet boundaries
        bin_polygon = Polygon([(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)])
        if not bin_polygon.contains(shape_to_check.polygon):
            return False

        # 2. Check for collision with other parts
        for placed_part in self.parts:
            if placed_part.shape != part_to_ignore and placed_part.shape and placed_part.shape.polygon:
                if shape_to_check.polygon.intersects(placed_part.shape.polygon):
                    return False
        
        return True

    def is_placement_valid_polygon(self, polygon_to_check, part_to_ignore=None):
        """
        Checks if a shapely polygon's placement is valid on this sheet.
        This version is for checking raw polygons without a full Shape object.

        Args:
            polygon_to_check (shapely.geometry.Polygon): The polygon at the desired location.
            part_to_ignore (Shape, optional): A specific shape to exclude from the collision check.

        Returns:
            bool: True if the placement is valid, False otherwise.
        """
        if not SHAPELY_AVAILABLE or not polygon_to_check: return False

        bin_polygon = Polygon([(0, 0), (self.width, 0), (self.width, self.height), (0, self.height)])
        if not bin_polygon.contains(polygon_to_check):
            return False

        for placed_part in self.parts:
            if placed_part.shape != part_to_ignore and placed_part.shape and placed_part.shape.polygon:
                if polygon_to_check.intersects(placed_part.shape.polygon):
                    return False
        
        return True

    def draw(self, doc, ui_params, parent_group=None, transient_part=None, parts_to_place_group=None, x_offset=0):
        """
        Draws the sheet and its contents into the FreeCAD document.

        Args:
            doc (FreeCAD.Document): The active document.
            sheet_origin (FreeCAD.Vector): The origin vector for this sheet.
            ui_params (dict): A dictionary of parameters from the UI.
            parent_group (App.DocumentObjectGroup): The main layout group to add this sheet to.
            draw_shape (bool): If True, draws the final FreeCAD part.
            draw_shape_bounds (bool): If True, draws the shapely boundary polygon.
            parts_to_place_group (App.DocumentObjectGroup): Optional. The temporary group where parts were created.
                                                            Required to safely remove parts from it to prevent deletion.
            x_offset (float): Optional X offset for placing layouts side by side in GA mode.
        """
        sheet_origin = self.get_origin()
        # Apply X offset for GA layout visualization
        if x_offset != 0:
            sheet_origin = FreeCAD.Vector(sheet_origin.x + x_offset, sheet_origin.y, sheet_origin.z)

        # --- Final Drawing Mode (with parent_group) ---
        if parent_group:
            self.parent_group_name = parent_group.Name
            # Create or Retrieve the group structure for this sheet
            sheet_group_name = f"Sheet_{self.id+1}"
            
            # Find existing sheet group in parent's children (getObject doesn't work on groups)
            sheet_group = None
            if hasattr(parent_group, 'Group'):
                for child in parent_group.Group:
                    if child.Label == sheet_group_name:
                        sheet_group = child
                        break
            
            if not sheet_group:
                 sheet_group = doc.addObject("App::DocumentObjectGroup", sheet_group_name)
                 parent_group.addObject(sheet_group)
            else:
                # Clear existing children
                for child in list(sheet_group.Group):
                    try:
                        doc.removeObject(child.Name)
                    except:
                        pass

            shapes_group_name = f"Shapes_{self.id+1}"
            
            shapes_group = doc.addObject("App::DocumentObjectGroup", shapes_group_name)
            
            sheet_group.addObject(shapes_group)

            # Draw sheet boundary - always create a new one (FreeCAD will auto-rename if collision)
            sheet_boundary_name = f"Sheet_Boundary_{self.id+1}"
            sheet_obj = doc.addObject("Part::Feature", sheet_boundary_name)
            sheet_obj.Shape = Part.makePlane(self.width, self.height)
            sheet_obj.Placement = FreeCAD.Placement(sheet_origin, FreeCAD.Rotation())
            sheet_group.addObject(sheet_obj)
            if FreeCAD.GuiUp:
                sheet_obj.ViewObject.Transparency = 75

            # Draw the parts placed on this sheet
            for placed_part in self.parts:
                self._draw_single_part(doc, placed_part.shape, sheet_origin, ui_params, shapes_group, parts_to_place_group)

        # --- Simulation Drawing Mode (with transient_part) ---
        elif transient_part:
            # Draw/update sheet boundary during simulation
            sim_boundary_name = f"sim_sheet_boundary_{self.id}"
            sim_boundary = doc.getObject(sim_boundary_name)
            if not sim_boundary:
                sim_boundary = doc.addObject("Part::Feature", sim_boundary_name)
                sim_boundary.Shape = Part.makePlane(self.width, self.height)
                if FreeCAD.GuiUp:
                    sim_boundary.ViewObject.Transparency = 75
                    sim_boundary.ViewObject.DisplayMode = "Flat Lines"
            sim_boundary.Placement = FreeCAD.Placement(sheet_origin, FreeCAD.Rotation())
            
            self._draw_single_part(doc, transient_part, sheet_origin, ui_params)

    def _draw_single_part(self, doc, shape, sheet_origin, ui_params, shapes_group=None, parts_to_place_group=None):
        """Helper to draw a single part, either for final placement or simulation."""
        if shape:
            # For final drawing, placement is pre-calculated. For simulation, we calculate it now.
            final_placement = shape.placement if shape.placement else shape.get_final_placement(sheet_origin)

            shape_obj = shape.fc_object
            if shape_obj:
                # FreeCAD.Console.PrintMessage(f"DEBUG:     fc_object '{shape_obj.Label}' found. Proceeding with drawing.\n")

                # During simulation, we just move the existing part.
                # For final drawing, we create a container.
                if shapes_group:
                    # Create a NEW container to hold the part. 
                    # Do NOT try to reuse an existing container by name, as it might belong to 
                    # an old layout that is about to be deleted.
                    # FreeCAD will automatically handle name collisions (e.g. nested_O_1001).
                    container = doc.addObject("App::Part", f"nested_{shape.id}")
                    container.Label = f"nested_{shape.id}" # Ensure label matches intended ID
                    
                    # Add ShowBounds property
                    container.addProperty("App::PropertyBool", "ShowBounds", "Nesting", "Show the boundary check logic used")
                    container.ShowBounds = ui_params.get('show_bounds', False)

                    shapes_group.addObject(container)

                    # Place the boundary object at the container's origin. It is the reference.
                    boundary_obj = shape_obj.BoundaryObject
                    if boundary_obj:
                        boundary_obj.Placement = FreeCAD.Placement()
                        container.addObject(boundary_obj)
                        
                        if hasattr(boundary_obj, "ViewObject"):
                            boundary_obj.ViewObject.Visibility = container.ShowBounds
                            # Set red color for bounds
                            boundary_obj.ViewObject.LineColor = (1.0, 0.0, 0.0)  # Red
                            boundary_obj.ViewObject.LineWidth = 2.0

                    # The shape_obj already has the correct placement (centered + rotated)
                    # from shape_preparer, so we don't touch it. Just add to container.

                    if hasattr(shape_obj, "ViewObject"):
                        shape_obj.ViewObject.Visibility = True
                    container.addObject(shape_obj)
                    
                    if hasattr(container, "ViewObject"):
                        container.ViewObject.Visibility = True

                    # Unlink from the temporary "PartsToPlace" bin so cleanup doesn't delete them
                    if parts_to_place_group:
                        try:
                            if boundary_obj:
                                parts_to_place_group.removeObject(boundary_obj)
                            parts_to_place_group.removeObject(shape_obj)
                        except Exception:
                            pass

                    # Apply the final nesting placement to the CONTAINER.
                    container.Placement = final_placement

                    # --- Handle the label object AFTER the container is placed ---
                    if ui_params.get('add_labels', False) and Draft and ui_params.get('font_path') and hasattr(shape, 'label_text') and shape.label_text:
                        label_name = f"label_{shape.id}"
                        # Allow FreeCAD to auto-rename if collision exists (e.g. label_Part001)
                        # Do NOT delete existing objects by name as they might belong to other layouts.
                        label_obj = create_label_object(label_name)
                        
                        shapestring_geom = Draft.make_shapestring(String=shape.label_text, FontFile=ui_params['font_path'], Size=ui_params.get('label_size', 10.0))
                        label_obj.Shape = shapestring_geom.Shape
                        doc.removeObject(shapestring_geom.Name)
                        
                        # Add label to the CONTAINER (same scope as part)
                        container.addObject(label_obj)
                        
                        # Calculate local placement relative to the part inside the container
                        shapestring_center = label_obj.Shape.BoundBox.Center
                        
                        # The part is centered at (0,0,0) within the container because we applied 
                        # shape_obj.Placement = FreeCAD.Placement(shape.source_centroid.negative(), ...)
                        # So the target visual center for the label is simply (0,0, Z_height)
                        
                        # Apply inverse rotation to keep text horizontal
                        part_rotation = final_placement.Rotation
                        inverse_rotation = part_rotation.inverted()

                        # To center the text correctly, we must rotate the local center offset
                        target_label_center = FreeCAD.Vector(0, 0, ui_params.get('label_height', 0.1))
                        shapestring_center_rotated = inverse_rotation.multVec(shapestring_center)
                        label_placement_base = target_label_center - shapestring_center_rotated
                        
                        label_obj.Placement = FreeCAD.Placement(label_placement_base, inverse_rotation)
                        
                        # Link label to shape (add property if needed for plain Part::Feature)
                        if not hasattr(shape_obj, "LabelObject"):
                            shape_obj.addProperty("App::PropertyLink", "LabelObject", "Nesting", "Link to label")
                        shape_obj.LabelObject = label_obj

                    # Set visibility on the main shape object (add properties if needed)
                    if not hasattr(shape_obj, "ShowShape"):
                        shape_obj.addProperty("App::PropertyBool", "ShowShape", "Nesting", "Show shape geometry")
                    if not hasattr(shape_obj, "ShowBounds"):
                        shape_obj.addProperty("App::PropertyBool", "ShowBounds", "Nesting", "Show bounds")
                    if not hasattr(shape_obj, "ShowLabel"):
                        shape_obj.addProperty("App::PropertyBool", "ShowLabel", "Nesting", "Show label")
                    
                    shape_obj.ShowShape = True
                    shape_obj.ShowBounds = ui_params.get('show_bounds', False)
                    shape_obj.ShowLabel = ui_params.get('add_labels', False)
                else:
                    # Simulation mode: only show and move the boundary object, hide the shape
                    if hasattr(shape_obj, 'ViewObject'):
                        shape_obj.ViewObject.Visibility = False
                    
                    if hasattr(shape_obj, 'BoundaryObject') and shape_obj.BoundaryObject:
                        boundary = shape_obj.BoundaryObject
                        boundary.Placement = final_placement
                        if hasattr(boundary, 'ViewObject'):
                            boundary.ViewObject.Visibility = True
                            boundary.ViewObject.LineColor = (0.0, 0.7, 0.0)  # Green for simulation
                            boundary.ViewObject.LineWidth = 2.0
            else:
                pass # FreeCAD.Console.PrintWarning(f"DEBUG:     fc_object for part '{shape.id}' was None. Skipping drawing.\n")