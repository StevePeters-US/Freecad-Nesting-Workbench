
import FreeCAD
import Part
import copy
import Draft
from .algorithms import shape_processor
from ...datatypes.shape_object import create_shape_object
from ...datatypes.shape import Shape


def _get_up_direction_rotation(up_direction):
    """
    Returns a FreeCAD.Rotation that transforms the given up_direction to Z+.
    """
    if up_direction == "Z+" or up_direction is None:
        return None  # No rotation needed
    elif up_direction == "Z-":
        return FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
    elif up_direction == "Y+":
        return FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -90)
    elif up_direction == "Y-":
        return FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90)
    elif up_direction == "X+":
        return FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 90)
    elif up_direction == "X-":
        return FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), -90)
    else:
        return None

class ShapePreparer:
    """
    Handles the preparation of shapes for nesting.
    - Creates 'Master' FreeCAD objects.
    - Manages the 'MasterShapes' group.
    - Creates the temporary Shape instances used by the algorithm.
    """
    def __init__(self, doc, processed_shape_cache):
        self.doc = doc
        self.processed_shape_cache = processed_shape_cache

    def prepare_parts(self, ui_global_settings, quantities, master_shapes_map, layout_obj, parts_group):
        """
        Main entry point to prepare parts.
        
        Args:
            ui_global_settings (dict): { 'spacing': float, 'boundary_resolution': float, 'rotation_steps': int, 'add_labels': bool, 'font_path': str }
            quantities (dict): { label: (quantity, rotation_steps) }
            master_shapes_map (dict): { label: FreeCADObject }
            layout_obj (App::DocumentObjectGroup): The layout group.
            parts_group (App::DocumentObjectGroup): The PartsToPlace group to add temp instances to.
        
        Returns:
            list[Shape]: List of prepared Shape objects for the nester.
        """
        spacing = ui_global_settings['spacing']
        boundary_resolution = ui_global_settings['boundary_resolution']
        
        # --- Create or Retrieve the hidden MasterShapes group ---
        master_shapes_group = self._get_or_create_master_group(layout_obj)

        master_shape_obj_map = {} # Maps original FreeCAD object ID to the new master ShapeObject
        master_geometry_cache = {} # Maps original FreeCAD object ID to the processed Shape wrapper
        masters_to_place = []

        # --- Step 1: Create the FreeCAD "master" objects for each unique part. ---
        for label, master_obj in master_shapes_map.items():
            try:
                # Get up_direction for cache key
                part_params = quantities.get(label, {'up_direction': 'Z+'})
                if isinstance(part_params, tuple):
                    up_direction = 'Z+'
                else:
                    up_direction = part_params.get('up_direction', 'Z+')
                
                # Cache Key: (Object Name, Spacing, Resolution, UpDirection)
                cache_key = (master_obj.Name, spacing, boundary_resolution, up_direction)
                is_reloading = master_obj.Label.startswith("master_shape_")
                
                temp_shape_wrapper = None
                
                # Check Cache
                if cache_key in self.processed_shape_cache:
                    temp_shape_wrapper = copy.deepcopy(self.processed_shape_cache[cache_key])
                    temp_shape_wrapper.source_freecad_object = master_obj
                
                if is_reloading:
                    master_shape_obj, temp_shape_wrapper = self._create_temp_from_reloading(
                        master_obj, label, quantities, temp_shape_wrapper, spacing, boundary_resolution, cache_key, layout_obj, master_shapes_group
                    )
                else:
                    master_shape_obj, temp_shape_wrapper = self._handle_new_master(
                        master_obj, label, quantities, temp_shape_wrapper, spacing, boundary_resolution, cache_key, master_shapes_group, is_reloading
                    )

                if master_shape_obj and temp_shape_wrapper:
                    master_shape_obj_map[id(master_obj)] = master_shape_obj
                    master_geometry_cache[id(master_obj)] = temp_shape_wrapper
                    
                    # Need container for sorting/placing
                    # If reloading, master_obj IS the inner ShapeObject. Parent is container.
                    # If new, we created a container.
                    # _handle_new_master returns the shape object. We need to find the container.
                    if master_shape_obj.InList:
                        masters_to_place.append((master_shape_obj.InList[0], temp_shape_wrapper))

            except Exception as e:
                FreeCAD.Console.PrintError(f"Could not create boundary for '{master_obj.Label}', it will be skipped. Error: {e}\n")
                continue
        
        # --- Step 1.5: Sort masters and position them ---
        self._arrange_masters(masters_to_place, spacing)

        # --- Step 2: Create in-memory Shape instances ---
        parts_to_nest = self._create_nesting_instances(
            master_shapes_map, 
            quantities, 
            master_shape_obj_map, 
            master_geometry_cache, 
            ui_global_settings,
            parts_group
        )
        
        return parts_to_nest

    def _get_or_create_master_group(self, layout_obj):
        master_shapes_group = None
        for child in layout_obj.Group:
             if child.Label == "MasterShapes":
                 master_shapes_group = child
                 break
        
        if not master_shapes_group:
            master_shapes_group = self.doc.addObject("App::DocumentObjectGroup", "MasterShapes")
            master_shapes_group.Label = "MasterShapes"
            layout_obj.addObject(master_shapes_group)
        
        # Make MasterShapes visible during nesting (will be hidden after commit)
        if hasattr(master_shapes_group, "ViewObject"):
            master_shapes_group.ViewObject.Visibility = True
        return master_shapes_group

    def _create_temp_from_reloading(self, master_obj, label, quantities, temp_shape_wrapper, spacing, boundary_resolution, cache_key, layout_obj, master_shapes_group):
        """
        Creates a temporary copy of an existing master shape for use in the sandbox.
        
        CLEAN OFFSET DESIGN: Reads SourceCentroid from the original container instead of
        deriving it. This ensures consistency between initial nesting and re-nesting.
        """
        original_label = label.replace("master_shape_", "")
        
        # Find the original container (parent of master_obj)
        original_container = None
        if master_obj.InList:
            for parent in master_obj.InList:
                if hasattr(parent, "SourceCentroid"):
                    original_container = parent
                    break
        
        # 1. Create new temp container
        temp_container = self.doc.addObject("App::Part", f"temp_master_{original_label}")
        master_shapes_group.addObject(temp_container)
        
        # *** CLEAN OFFSET DESIGN ***
        # Copy SourceCentroid from the original container - this is THE source of truth
        temp_container.addProperty("App::PropertyVector", "SourceCentroid", "Nesting", "Original geometry center")
        if original_container and hasattr(original_container, "SourceCentroid"):
            temp_container.SourceCentroid = original_container.SourceCentroid
        else:
            # Fallback: calculate from geometry (less ideal but works)
            temp_container.SourceCentroid = master_obj.Shape.CenterOfMass
        
        # 2. Create the shape object - copy geometry, center at origin with -source_centroid
        temp_master_obj = self.doc.addObject("Part::Feature", f"temp_shape_{original_label}")
        temp_master_obj.Label = f"master_shape_{original_label}"
        temp_master_obj.Shape = master_obj.Shape.copy()
        # Center the shape at the container's origin
        source_centroid = temp_container.SourceCentroid
        temp_master_obj.Placement = FreeCAD.Placement(source_centroid.negative(), FreeCAD.Rotation())
        temp_container.addObject(temp_master_obj)
        
        # 3. Clone Boundary Object
        if hasattr(master_obj, "BoundaryObject") and master_obj.BoundaryObject:
            temp_bound = self.doc.addObject("Part::Feature", f"temp_boundary_{original_label}")
            temp_bound.Shape = master_obj.BoundaryObject.Shape.copy()
            temp_container.addObject(temp_bound)
            
            if not hasattr(temp_master_obj, "BoundaryObject"):
                temp_master_obj.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "Boundary object")
            temp_master_obj.BoundaryObject = temp_bound
            
            if hasattr(temp_bound, "ViewObject"): 
                temp_bound.ViewObject.Visibility = False
        
        # Shape visible, container visible during nesting
        if hasattr(temp_master_obj, "ViewObject"): 
            temp_master_obj.ViewObject.Visibility = True
        if hasattr(temp_container, "ViewObject"): 
            temp_container.ViewObject.Visibility = True

        # 4. Copy Quantity property
        quantity, _ = quantities.get(original_label, (1, 1))
        temp_container.addProperty("App::PropertyInteger", "Quantity", "Nest", "Number of instances").Quantity = quantity

        # 5. Build Shape wrapper using the stored SourceCentroid
        temp_shape_wrapper = None
        if hasattr(temp_master_obj, "BoundaryObject") and temp_master_obj.BoundaryObject:
            try:
                from shapely.geometry import Polygon
                bound_shape = temp_master_obj.BoundaryObject.Shape
                
                wires = bound_shape.Wires
                wires.sort(key=lambda w: w.Length, reverse=True)
                
                if wires:
                    outer_pts = [(v.x, v.y) for v in wires[0].discretize(Deflection=0.01)]
                    if outer_pts:
                        if outer_pts[0] != outer_pts[-1]: 
                            outer_pts.append(outer_pts[0])
                        poly = Polygon(outer_pts)
                        holes = []
                        for w in wires[1:]:
                            h_pts = [(v.x, v.y) for v in w.discretize(Deflection=0.01)]
                            if h_pts[0] != h_pts[-1]: 
                                h_pts.append(h_pts[0])
                            if len(h_pts) > 2: 
                                holes.append(h_pts)
                        
                        final_poly = Polygon(poly.exterior.coords, holes)
                        
                        temp_shape_wrapper = Shape(temp_master_obj)
                        temp_shape_wrapper.polygon = final_poly
                        # Use the stored SourceCentroid - THE source of truth
                        temp_shape_wrapper.source_centroid = temp_container.SourceCentroid
                        
                        self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Shape reload failed for '{label}': {e}. Recalculating.\n")
                temp_shape_wrapper = None
        
        # 6. Recalculate if reuse failed
        if not temp_shape_wrapper:
            temp_shape_wrapper = Shape(temp_master_obj)
            shape_processor.create_single_nesting_part(temp_shape_wrapper, temp_master_obj, spacing, boundary_resolution)
            # Update the container's SourceCentroid with the recalculated value
            if temp_shape_wrapper.source_centroid:
                temp_container.SourceCentroid = temp_shape_wrapper.source_centroid
            self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)

        return temp_master_obj, temp_shape_wrapper

    def _handle_new_master(self, master_obj, label, quantities, temp_shape_wrapper, spacing, boundary_resolution, cache_key, master_shapes_group, is_reloading):
        # Get part parameters from quantities
        part_params = quantities.get(label, {'quantity': 1, 'up_direction': 'Z+'})
        if isinstance(part_params, tuple):
            up_direction = 'Z+'
        else:
            up_direction = part_params.get('up_direction', 'Z+')
        
        if not temp_shape_wrapper:
            temp_shape_wrapper = Shape(master_obj)
            shape_processor.create_single_nesting_part(temp_shape_wrapper, master_obj, spacing, boundary_resolution, up_direction)
            self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)

        master_container = self.doc.addObject("App::Part", f"master_{label}")
        
        # Get part parameters from quantities dict (now a dict of dicts)
        part_params = quantities.get(label, {'quantity': 1, 'rotation_steps': 1, 'up_direction': 'Z+', 'fill_sheet': False})
        if isinstance(part_params, tuple):
            # Legacy format: (quantity, rotation_steps)
            quantity = part_params[0]
            up_direction = 'Z+'
            fill_sheet = False
        else:
            quantity = part_params.get('quantity', 1)
            up_direction = part_params.get('up_direction', 'Z+')
            fill_sheet = part_params.get('fill_sheet', False)
        
        # Store properties
        master_container.addProperty("App::PropertyInteger", "Quantity", "Nest", "Number of instances").Quantity = quantity
        master_container.addProperty("App::PropertyString", "UpDirection", "Nest", "Up direction for 2D projection").UpDirection = up_direction
        master_container.addProperty("App::PropertyBool", "FillSheet", "Nest", "Use to fill remaining space").FillSheet = fill_sheet

        # *** CLEAN OFFSET DESIGN ***
        # Store the source_centroid as a property on the container - this is THE source of truth
        # for the offset between the Shapely polygon (centered at 0,0) and the FreeCAD geometry
        master_container.addProperty("App::PropertyVector", "SourceCentroid", "Nesting", "Original geometry center")
        if temp_shape_wrapper.source_centroid:
            master_container.SourceCentroid = temp_shape_wrapper.source_centroid
        else:
            # Fallback: use the shape's actual center of mass
            master_container.SourceCentroid = master_obj.Shape.CenterOfMass

        # Make container visible during nesting (child boundary visibility is toggled by highlighting)
        if hasattr(master_container, "ViewObject"):
            master_container.ViewObject.Visibility = True
        
        # DEBUG: Try using Part::Feature directly instead of custom object
        master_shape_obj = self.doc.addObject("Part::Feature", f"master_shape_{label}")
        # Add required properties manually since we're not using ShapeObject
        if not hasattr(master_shape_obj, "ShowBounds"):
            master_shape_obj.addProperty("App::PropertyBool", "ShowBounds", "Display", "").ShowBounds = False
        if not hasattr(master_shape_obj, "BoundaryObject"):
            master_shape_obj.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "")
        
        # Get shape in world coordinates (apply source object's placement)
        original_shape = master_obj.Shape.copy()
        if master_obj.Placement and not master_obj.Placement.isIdentity():
            original_shape.transformShape(master_obj.Placement.Matrix)
        FreeCAD.Console.PrintMessage(f"  -> Creating master for '{label}' with up_direction='{up_direction}'\n")
        
        master_shape_obj.Shape = original_shape
        
        # Use source_centroid from shape_processor for consistent centering
        # This is the world-space BB center (after rotation if applicable)
        if temp_shape_wrapper.source_centroid:
            offset = temp_shape_wrapper.source_centroid.negative()
            FreeCAD.Console.PrintMessage(f"     [DEBUG] Using source_centroid: {temp_shape_wrapper.source_centroid}\n")
        else:
            bb = original_shape.BoundBox
            offset = FreeCAD.Vector(
                -(bb.XMin + bb.XMax) / 2,
                -(bb.YMin + bb.YMax) / 2,
                -(bb.ZMin + bb.ZMax) / 2
            )
            FreeCAD.Console.PrintMessage(f"     [DEBUG] Fallback to BoundBox center: {bb}\n")
            
        FreeCAD.Console.PrintMessage(f"     Using offset: ({-offset.x:.2f}, {-offset.y:.2f})\n")
        
        # Get up_direction rotation
        up_rotation = FreeCAD.Rotation()
        if up_direction == "Z-":
            up_rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180)
        elif up_direction == "Y+":
            up_rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), -90)
        elif up_direction == "Y-":
            up_rotation = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 90)
        elif up_direction == "X+":
            up_rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), 90)
        elif up_direction == "X-":
            up_rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 1, 0), -90)
        
        # Compose placement: first translate to origin, then rotate around origin
        translate_to_origin = FreeCAD.Placement(offset, FreeCAD.Rotation())
        rotate_at_origin = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), up_rotation)
        final_placement = rotate_at_origin.multiply(translate_to_origin)
        
        master_shape_obj.Placement = final_placement
            
        if hasattr(master_shape_obj, "ViewObject"):
            master_shape_obj.ViewObject.Visibility = True
        master_container.addObject(master_shape_obj)

        if temp_shape_wrapper.polygon:
            boundary_obj = temp_shape_wrapper.draw_bounds(self.doc, FreeCAD.Vector(0,0,0), None)
            if boundary_obj:
                master_container.addObject(boundary_obj)
                # Bounds are centered at origin - no placement needed
                boundary_obj.Placement = FreeCAD.Placement()
                master_shape_obj.BoundaryObject = boundary_obj
                master_shape_obj.ShowBounds = False
                if hasattr(boundary_obj, "ViewObject"): boundary_obj.ViewObject.Visibility = False
                FreeCAD.Console.PrintMessage(f"     Bounds centroid from polygon: {temp_shape_wrapper.polygon.centroid}\n")
        
        master_shapes_group.addObject(master_container)
        return master_shape_obj, temp_shape_wrapper

    def _arrange_masters(self, masters_to_place, spacing):
        masters_to_place.sort(key=lambda item: item[1].area, reverse=True)
        
        max_master_height = 0
        if masters_to_place:
            max_master_height = max(item[1].bounding_box()[3] for item in masters_to_place if item[1].polygon)

        # Start cursor at 0 (or slight left offset if desired, but 0 is fine)
        cursor_x = 0
        y_offset = -max_master_height - spacing * 4 
        
        for container, shape_wrapper in masters_to_place:
            # bounds is (min_x, min_y, width, height) of the Shapely polygon (centered at 0,0)
            bounds = shape_wrapper.bounding_box()
            width = bounds[2] if bounds else 5
            
            # Calculate center position so the shape starts at cursor_x
            # Shape is centered at container origin, so it extends [-width/2, width/2] relative to container
            # We want Left Edge = cursor_x
            # So Center = cursor_x + width/2
            center_x = cursor_x + (width / 2.0)
            
            container_pos = FreeCAD.Vector(center_x, y_offset, 0)
            container.Placement = FreeCAD.Placement(container_pos, FreeCAD.Rotation())
            
            FreeCAD.Console.PrintMessage(f"DEBUG: Part '{container.Label}' Width: {width:.2f}\n")
            FreeCAD.Console.PrintMessage(f"  -> Placing at Center: {center_x:.2f} (Extents: [{cursor_x:.2f}, {cursor_x + width:.2f}])\n")
            
            # --- Visual Debug: Draw Bounding Box ---
            if bounds:
                min_x, min_y, w, h = bounds
                p1 = FreeCAD.Vector(min_x, min_y, 0)
                p2 = FreeCAD.Vector(min_x + w, min_y, 0)
                p3 = FreeCAD.Vector(min_x + w, min_y + h, 0)
                p4 = FreeCAD.Vector(min_x, min_y + h, 0)
                box_wire = Part.makePolygon([p1, p2, p3, p4, p1])
                
                box_obj = self.doc.addObject("Part::Feature", f"bbox_{container.Label}")
                box_obj.Shape = box_wire
                box_obj.Label = f"bbox_{container.Label}"
                
                # Add to container so it moves with the shape
                container.addObject(box_obj)
                
                if hasattr(box_obj, "ViewObject"):
                    box_obj.ViewObject.ShapeColor = (1.0, 0.0, 0.0) # Red
                    box_obj.ViewObject.LineWidth = 2.0
            # ---------------------------------------
            
            # Move cursor past this shape
            cursor_x += width + spacing

    def _create_nesting_instances(self, master_shapes_map, quantities, master_shape_obj_map, master_geometry_cache, ui_settings, parts_group):
        parts_to_nest = []
        parts_to_place_group = parts_group
        
        add_labels = ui_settings['add_labels']
        font_path = ui_settings['font_path']
        spacing = ui_settings['spacing']
        # Default global rotation
        global_rotation_steps = ui_settings['rotation_steps']

        for label, original_obj in master_shapes_map.items():
            # If reloading, label is master_shape_X, handle mapping
            lookup_label = label
            if label.startswith("master_shape_"):
                 lookup_label = label.replace("master_shape_", "")
            
            # Handle new dict format and legacy tuple format
            part_params = quantities.get(lookup_label, {'quantity': 0, 'rotation_steps': global_rotation_steps})
            if isinstance(part_params, tuple):
                # Legacy format
                quantity = part_params[0]
                part_rotation_steps = part_params[1]
                fill_sheet = False
                up_direction = 'Z+'
            else:
                quantity = part_params.get('quantity', 0)
                part_rotation_steps = part_params.get('rotation_steps', global_rotation_steps)
                fill_sheet = part_params.get('fill_sheet', False)
                up_direction = part_params.get('up_direction', 'Z+')
            
            master_shape_obj = master_shape_obj_map.get(id(original_obj))
            master_wrapper = master_geometry_cache.get(id(original_obj))
            
            if not master_shape_obj or not master_wrapper: continue

            for i in range(quantity):
                shape_instance = Shape(original_obj)
                
                # Copy properties
                shape_instance.polygon = master_wrapper.polygon
                shape_instance.original_polygon = master_wrapper.original_polygon
                shape_instance.unbuffered_polygon = master_wrapper.unbuffered_polygon
                shape_instance.source_centroid = master_wrapper.source_centroid
                shape_instance.spacing = spacing
                
                shape_instance.instance_num = i + 1
                shape_instance.id = f"{lookup_label}_{shape_instance.instance_num}"
                shape_instance.rotation_steps = part_rotation_steps
                shape_instance.fill_sheet = fill_sheet
                shape_instance.up_direction = up_direction

                part_copy = self.doc.addObject("Part::Feature", f"part_{shape_instance.id}")
                
                # Copy shape from master (raw geometry)
                part_copy.Shape = master_shape_obj.Shape.copy()
                
                # Copy the master's placement (which includes translate+rotate for centering and up_direction)
                part_copy.Placement = master_shape_obj.Placement
                
                # Debug: Check what geometry we're getting
                if up_direction != "Z+" and up_direction is not None:
                    FreeCAD.Console.PrintMessage(f"     Part copy {shape_instance.id}: BoundBox={part_copy.Shape.BoundBox}\n")
                
                # Copy boundary if exists
                if hasattr(master_shape_obj, "BoundaryObject") and master_shape_obj.BoundaryObject:
                    boundary_copy = self.doc.addObject("Part::Feature", f"boundary_{shape_instance.id}")
                    boundary_copy.Shape = master_shape_obj.BoundaryObject.Shape.copy()
                    # Hide initially - will be shown by simulation/drawing code
                    if hasattr(boundary_copy, "ViewObject"):
                        boundary_copy.ViewObject.Visibility = False
                    part_copy.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "Boundary object")
                    part_copy.BoundaryObject = boundary_copy
                    parts_to_place_group.addObject(boundary_copy)
                
                # Hide part initially - will be positioned and shown by simulation/drawing code
                if hasattr(part_copy, "ViewObject"):
                    part_copy.ViewObject.Visibility = False
                
                parts_to_place_group.addObject(part_copy)
                shape_instance.fc_object = part_copy
                
                # Do NOT manipulate Placement here. 
                # The Sheet.draw method is the sole authority on where this part ends up.

                if add_labels and Draft and font_path:
                    shape_instance.label_text = shape_instance.id

                parts_to_nest.append(shape_instance)

        return parts_to_nest
