
import FreeCAD
import copy
import Draft
from .algorithms import shape_processor
from ...datatypes.shape_object import create_shape_object
from ...datatypes.shape import Shape

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

    def prepare_parts(self, ui_global_settings, quantities, master_shapes_map, layout_obj):
        """
        Main entry point to prepare parts.
        
        Args:
            ui_global_settings (dict): { 'spacing': float, 'boundary_resolution': float, 'rotation_steps': int, 'add_labels': bool, 'font_path': str }
            quantities (dict): { label: (quantity, rotation_steps) }
            master_shapes_map (dict): { label: FreeCADObject }
            layout_obj (App::DocumentObjectGroup): The layout group.
        
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
                # Cache Key: (Object Name, Spacing, Resolution)
                cache_key = (master_obj.Name, spacing, boundary_resolution)
                is_reloading = master_obj.Label.startswith("master_shape_")
                
                temp_shape_wrapper = None
                
                # Check Cache
                if cache_key in self.processed_shape_cache:
                    temp_shape_wrapper = copy.deepcopy(self.processed_shape_cache[cache_key])
                    temp_shape_wrapper.source_freecad_object = master_obj
                
                if is_reloading:
                    master_shape_obj, temp_shape_wrapper = self._handle_reloading(
                        master_obj, label, quantities, temp_shape_wrapper, spacing, boundary_resolution, cache_key
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
            ui_global_settings
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
            layout_obj.addObject(master_shapes_group)
        
        if hasattr(master_shapes_group, "ViewObject"):
            master_shapes_group.ViewObject.Visibility = False
        return master_shapes_group

    def _handle_reloading(self, master_obj, label, quantities, temp_shape_wrapper, spacing, boundary_resolution, cache_key):
        master_shape_obj = master_obj
        master_container = master_shape_obj.InList[0]
        
        # Update quantity on existing container
        # label here is "master_shape_X", so we replace to get original X
        original_label = label.replace("master_shape_", "")
        quantity, _ = quantities.get(original_label, (1, 1))
        
        if hasattr(master_container, "Quantity"):
            master_container.Quantity = quantity
        else:
            master_container.addProperty("App::PropertyInteger", "Quantity", "Nest", "Number of instances").Quantity = quantity

        # If not in cache, process now
        if not temp_shape_wrapper:
            temp_shape_wrapper = Shape(master_shape_obj)
            shape_processor.create_single_nesting_part(temp_shape_wrapper, master_shape_obj, spacing, boundary_resolution)
            self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)
            
        return master_shape_obj, temp_shape_wrapper

    def _handle_new_master(self, master_obj, label, quantities, temp_shape_wrapper, spacing, boundary_resolution, cache_key, master_shapes_group, is_reloading):
        if not temp_shape_wrapper:
            temp_shape_wrapper = Shape(master_obj)
            shape_processor.create_single_nesting_part(temp_shape_wrapper, master_obj, spacing, boundary_resolution)
            self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)

        master_container = self.doc.addObject("App::Part", f"master_{label}")
        
        # Store quantity
        quantity, _ = quantities.get(label, (1, 1))
        master_container.addProperty("App::PropertyInteger", "Quantity", "Nest", "Number of instances").Quantity = quantity

        # Hide container
        if hasattr(master_container, "ViewObject"):
            master_container.ViewObject.Visibility = False
            
        master_shape_obj = create_shape_object(f"master_shape_{label}")
        master_shape_obj.Shape = master_obj.Shape.copy()

        if temp_shape_wrapper.source_centroid:
            master_shape_obj.Placement = FreeCAD.Placement(temp_shape_wrapper.source_centroid.negative(), FreeCAD.Rotation())
        master_container.addObject(master_shape_obj)

        if temp_shape_wrapper.polygon:
            boundary_obj = temp_shape_wrapper.draw_bounds(self.doc, FreeCAD.Vector(0,0,0), None)
            if boundary_obj:
                master_container.addObject(boundary_obj)
                boundary_obj.Placement = FreeCAD.Placement()
                master_shape_obj.BoundaryObject = boundary_obj
                master_shape_obj.ShowBounds = False
                if hasattr(boundary_obj, "ViewObject"): boundary_obj.ViewObject.Visibility = False
        
        master_shapes_group.addObject(master_container)
        return master_shape_obj, temp_shape_wrapper

    def _arrange_masters(self, masters_to_place, spacing):
        masters_to_place.sort(key=lambda item: item[1].area, reverse=True)
        
        max_master_height = 0
        if masters_to_place:
            max_master_height = max(item[1].bounding_box()[3] for item in masters_to_place if item[1].polygon)

        current_x = 0
        y_offset = -max_master_height - spacing * 4 
        
        for container, shape_wrapper in masters_to_place:
            container.Placement = FreeCAD.Placement(FreeCAD.Vector(current_x, y_offset, 0), FreeCAD.Rotation())
            current_x += container.Shape.BoundBox.XLength + spacing * 2

    def _create_nesting_instances(self, master_shapes_map, quantities, master_shape_obj_map, master_geometry_cache, ui_settings):
        parts_to_nest = []
        parts_to_place_group = self.doc.getObject("PartsToPlace")
        
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
            
            quantity, part_rotation_steps = quantities.get(lookup_label, (0, global_rotation_steps))
            
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
                shape_instance.id = f"{shape_instance.source_freecad_object.Label}_{shape_instance.instance_num}"
                shape_instance.rotation_steps = part_rotation_steps

                # Create temp copy
                part_copy = self.doc.copyObject(master_shape_obj, True)
                part_copy.Label = f"part_{shape_instance.id}"
                parts_to_place_group.addObject(part_copy)
                shape_instance.fc_object = part_copy

                if master_shape_obj.InList:
                    master_container = master_shape_obj.InList[0]
                    part_copy.Placement = master_shape_obj.getGlobalPlacement()
                    if hasattr(part_copy, 'BoundaryObject') and part_copy.BoundaryObject:
                        part_copy.BoundaryObject.Placement = master_container.getGlobalPlacement()

                part_copy.ShowBounds = True
                part_copy.ShowShape = False

                if add_labels and Draft and font_path:
                    shape_instance.label_text = shape_instance.id

                parts_to_nest.append(shape_instance)

        return parts_to_nest
