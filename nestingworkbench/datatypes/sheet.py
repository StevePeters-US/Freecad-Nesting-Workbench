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
        self.parts = [] # List of PlacedPart objects
        self.spacing = spacing
        self.parent_group_name = None # Will store the name of the top-level layout group

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

    def draw(self, doc, ui_params, parent_group=None, transient_part=None):
        """
        Draws the sheet and its contents into the FreeCAD document.

        Args:
            doc (FreeCAD.Document): The active document.
            sheet_origin (FreeCAD.Vector): The origin vector for this sheet.
            ui_params (dict): A dictionary of parameters from the UI.
            parent_group (App.DocumentObjectGroup): The main layout group to add this sheet to.
            draw_shape (bool): If True, draws the final FreeCAD part.
            draw_shape_bounds (bool): If True, draws the shapely boundary polygon.
        """
        sheet_origin = self.get_origin()

        # --- Final Drawing Mode (with parent_group) ---
        if parent_group:
            self.parent_group_name = parent_group.Name
            # Create or Retrieve the group structure for this sheet
            sheet_group_name = f"Sheet_{self.id+1}"
            sheet_group = parent_group.getObject(sheet_group_name)
            
            # Since we cleared the layout children in the controller, we expect to create new ones.
            # However, if the user manually kept something, or if the cleanup failed, we should be robust.
            # But wait, the controller explicitly deletes children starting with 'Sheet_'.
            # The issue described is "Creates 3 new folders - shapes_X, text_x, sheet_boundary_x".
            # This implies `doc.addObject` is creating unique names like 'Shapes_1001', 'Shapes_1002' 
            # while the sheet group itself might be successfully recreated as 'Sheet_1'.
            
            # Actually, `doc.addObject` will auto-increment names if "Sheet_1" exists in the document 
            # (even if deleted but not purged? No, deleted objects are gone).
            # The user says "new folders - shapes_X...".
            # If we just deleted "Sheet_1", we can recreate "Sheet_1".
            
            if not sheet_group:
                 sheet_group = doc.addObject("App::DocumentObjectGroup", sheet_group_name)
                 parent_group.addObject(sheet_group)
            else:
                # If we are reusing the sheet group, we must ensure it is empty of old contents.
                # The controller removes the Sheet group itself, but if we are in a state where it wasn't removed,
                # or if we found a lingering group by name, we need to clean it.
                # Actually, simply clearing the group's children is safer than deleting/recreating.
                # Note: doc.removeObject deletes the object from the document.
                # We need to iterate over a copy of the list.
                for child in list(sheet_group.Group):
                    doc.removeObject(child.Name)

            shapes_group_name = f"Shapes_{self.id+1}"
            text_group_name = f"Text_{self.id+1}"
            
            shapes_group = doc.addObject("App::DocumentObjectGroup", shapes_group_name)
            text_group = doc.addObject("App::DocumentObjectGroup", text_group_name)
            
            sheet_group.addObject(shapes_group)
            sheet_group.addObject(text_group)

            # Draw sheet boundary
            sheet_boundary_name = f"Sheet_Boundary_{self.id+1}"
            sheet_obj = doc.getObject(sheet_boundary_name)
            if not sheet_obj:
                sheet_obj = doc.addObject("Part::Feature", sheet_boundary_name)
            
            sheet_obj.Shape = Part.makePlane(self.width, self.height)
            sheet_obj.Placement = FreeCAD.Placement(sheet_origin, FreeCAD.Rotation())
            sheet_group.addObject(sheet_obj)
            if FreeCAD.GuiUp:
                sheet_obj.ViewObject.Transparency = 75

            # Draw the parts placed on this sheet
            FreeCAD.Console.PrintMessage(f"DEBUG: --- Drawing Sheet {self.id+1} --- \n")
            for placed_part in self.parts:
                self._draw_single_part(doc, placed_part.shape, sheet_origin, ui_params, shapes_group, text_group)

        # --- Simulation Drawing Mode (with transient_part) ---
        elif transient_part:
            self._draw_single_part(doc, transient_part, sheet_origin, ui_params)

    def _draw_single_part(self, doc, shape, sheet_origin, ui_params, shapes_group=None, text_group=None):
        """Helper to draw a single part, either for final placement or simulation."""
        if shape:
            # FreeCAD.Console.PrintMessage(f"DEBUG:   Attempting to draw part '{shape.id}' (id={id(shape)}). Checking for fc_object...\n")
            # For final drawing, placement is pre-calculated. For simulation, we calculate it now.
            final_placement = shape.placement if shape.placement else shape.get_final_placement(sheet_origin)

            shape_obj = shape.fc_object
            if shape_obj:
                # FreeCAD.Console.PrintMessage(f"DEBUG:     fc_object '{shape_obj.Label}' found. Proceeding with drawing.\n")

                # During simulation, we just move the existing part.
                # For final drawing, we create a container.
                if shapes_group:
                    # Create or Retrieve a container to hold the part and its bounds
                    container_name = f"nested_{shape.id}"
                    container = doc.getObject(container_name)
                    if not container:
                         container = doc.addObject("App::Part", container_name)
                    else:
                         # Clear existing contents if reusing
                         for child in list(container.Group):
                             # Be careful not to delete the master shape object itself, 
                             # but wait, the master shape object is LINKED here, not created here?
                             # In nesting_controller loop: `part_copy = self.doc.copyObject(master_shape_obj, True)`
                             # The `shape.fc_object` IS `part_copy`.
                             # And we add `shape_obj` (which is `part_copy`) into `container`.
                             # So the container holds the unique copy for this instance.
                             # If we are reusing the container, we probably need to remove the OLD copy if it exists?
                             # But `shape.fc_object` is a NEW copy created in this run by the controller's `_prepare_parts_from_ui`.
                             # So the container from a previous run contains the OLD copy.
                             # We should empty the container.
                             # Note: doc.removeObject deletes it from document. 
                             doc.removeObject(child.Name)
                             
                    shapes_group.addObject(container)

                    # Place the boundary object at the container's origin. It is the reference.
                    boundary_obj = shape_obj.BoundaryObject
                    if boundary_obj:
                        boundary_obj.Placement = FreeCAD.Placement()
                        container.addObject(boundary_obj)

                    # Place the shape object inside the container, offsetting it by -source_centroid
                    # to align it with the boundary object.
                    if shape.source_centroid:
                        shape_obj.Placement = FreeCAD.Placement(shape.source_centroid.negative(), FreeCAD.Rotation())
                    else:
                        shape_obj.Placement = FreeCAD.Placement()
                    container.addObject(shape_obj)

                    # Apply the final nesting placement to the CONTAINER.
                    container.Placement = final_placement
                    # FreeCAD.Console.PrintMessage(f"DEBUG: PLACEMENT for '{container.Label}': {container.Placement}")

                    # --- Handle the label object AFTER the container is placed ---
                    if ui_params.get('add_labels', False) and Draft and ui_params.get('font_path') and hasattr(shape, 'label_text') and shape.label_text and text_group:
                        label_name = f"label_{shape.id}"
                        label_obj = doc.getObject(label_name)
                        if label_obj:
                            doc.removeObject(label_name)
                            
                        label_obj = create_label_object(label_name)
                        shapestring_geom = Draft.make_shapestring(String=shape.label_text, FontFile=ui_params['font_path'], Size=ui_params.get('spacing', 0) * 0.6)
                        label_obj.Shape = shapestring_geom.Shape
                        doc.removeObject(shapestring_geom.Name)
                        
                        shapestring_center = label_obj.Shape.BoundBox.Center
                        final_part_center = container.Placement.Base
                        target_label_center = final_part_center + FreeCAD.Vector(0, 0, ui_params.get('label_height', 0.1))
                        label_placement_base = target_label_center - shapestring_center
                        label_obj.Placement = FreeCAD.Placement(label_placement_base, FreeCAD.Rotation())
                        
                        text_group.addObject(label_obj)
                        shape_obj.LabelObject = label_obj

                    # Set visibility on the main shape object
                    shape_obj.ShowShape = True
                    shape_obj.ShowBounds = ui_params.get('show_bounds', False)
                    shape_obj.ShowLabel = ui_params.get('add_labels', False)
                else:
                    # Simulation mode: just update the placement of the existing object
                    shape_obj.Placement = final_placement
                    if hasattr(shape_obj, 'BoundaryObject') and shape_obj.BoundaryObject:
                        shape_obj.BoundaryObject.Placement = shape_obj.Placement.copy()
            else:
                pass # FreeCAD.Console.PrintWarning(f"DEBUG:     fc_object for part '{shape.id}' was None. Skipping drawing.\n")