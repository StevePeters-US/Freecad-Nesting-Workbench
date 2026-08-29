# SPDX-License-Identifier: LGPL-2.1-or-later

import FreeCAD
import Part
import copy
import Draft
import traceback
from .algorithms import shape_processor
from ...datatypes.shape_object import create_shape_object
from ...datatypes.shape import Shape
from ...freecad_helpers import get_up_direction_rotation, create_part_feature

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
            ui_global_settings (dict): { 'spacing': float, 'deflection': float, 'simplification': float, 'rotation_steps': int, 'add_labels': bool, 'font_path': str, 'verbose': bool }
            quantities (dict): { label: {'quantity': int, 'rotation_steps': int, 'up_direction': str, 'fill_sheet': bool} }
            master_shapes_map (dict): { label: FreeCADObject }
            layout_obj (App::DocumentObjectGroup): The layout group.
            parts_group (App::DocumentObjectGroup): The PartsToPlace group to add temp instances to.
        
        Returns:
            list[Shape]: List of prepared Shape objects for the nester.
        """
        spacing = ui_global_settings['spacing']
        deflection = ui_global_settings.get('deflection', 0.05)
        simplification = ui_global_settings.get('simplification', 0.1)
        verbose = ui_global_settings.get('verbose', False)
        
        master_shapes_group = self._get_or_create_master_group(layout_obj)

        master_shape_obj_map = {} # Maps original FreeCAD object ID to the new master ShapeObject
        master_geometry_cache = {} # Maps original FreeCAD object ID to the processed Shape wrapper
        masters_to_place = []

        for label, master_obj in master_shapes_map.items():
            try:
                # Get up_direction for cache key
                part_params = quantities.get(label, {'up_direction': 'Z+'})
                if isinstance(part_params, tuple):
                    up_direction = 'Z+'
                else:
                    up_direction = part_params.get('up_direction', 'Z+')
                
                # Cache Key: (Object Name, Spacing, Deflection, Simplification, UpDirection)
                # Updated cache key to respect new parameters
                cache_key = (master_obj.Name, spacing, deflection, simplification, up_direction)
                is_reloading = master_obj.Label.startswith("master_shape_")
                
                temp_shape_wrapper = None
                
                # Check Cache
                if cache_key in self.processed_shape_cache:
                    temp_shape_wrapper = copy.deepcopy(self.processed_shape_cache[cache_key])
                    temp_shape_wrapper.source_freecad_object = master_obj
                
                if is_reloading:
                    master_shape_obj, temp_shape_wrapper = self._create_temp_from_reloading(
                        master_obj, label, quantities, temp_shape_wrapper, spacing, deflection, simplification, cache_key, layout_obj, master_shapes_group, verbose=verbose
                    )
                else:
                    master_shape_obj, temp_shape_wrapper = self._handle_new_master(
                        master_obj, label, quantities, temp_shape_wrapper, spacing, deflection, simplification, cache_key, master_shapes_group, is_reloading, verbose=verbose
                    )

                if master_shape_obj and temp_shape_wrapper:
                    master_shape_obj_map[id(master_obj)] = master_shape_obj
                    master_geometry_cache[id(master_obj)] = temp_shape_wrapper
                    
                    # Need container for sorting/placing
                    if master_shape_obj.InList:
                        masters_to_place.append((master_shape_obj.InList[0], temp_shape_wrapper))

            except Exception as e:
                FreeCAD.Console.PrintError(f"Could not create boundary for '{master_obj.Label}', it will be skipped. Error: {e}\n{traceback.format_exc()}\n")
                continue
        
        self._arrange_masters(masters_to_place, spacing)

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

    def _create_temp_from_reloading(self, master_obj, label, quantities, temp_shape_wrapper, spacing, deflection, simplification, cache_key, layout_obj, master_shapes_group, verbose=False):
        """
        Creates a temporary copy of an existing master shape for use in the sandbox.
        """
        original_label = label.replace("master_shape_", "")
        
        # Find the original container (parent of master_obj)
        original_container = None
        if master_obj.InList:
            for parent in master_obj.InList:
                if hasattr(parent, "SourceCentroid"):
                    original_container = parent
                    break
        
        temp_container = self.doc.addObject("App::Part", f"temp_master_{original_label}")
        master_shapes_group.addObject(temp_container)
        
        # *** CLEAN OFFSET DESIGN ***
        temp_container.addProperty("App::PropertyVector", "SourceCentroid", "Nesting", "Original geometry center")
        if original_container and hasattr(original_container, "SourceCentroid"):
            temp_container.SourceCentroid = original_container.SourceCentroid
        else:
            bb = master_obj.Shape.BoundBox
            temp_container.SourceCentroid = FreeCAD.Vector(
                 (bb.XMin + bb.XMax) / 2,
                 (bb.YMin + bb.YMax) / 2,
                 (bb.ZMin + bb.ZMax) / 2
            )
        
        temp_master_obj = create_part_feature(
            self.doc, f"temp_shape_{original_label}", master_obj.Shape.copy(), group=temp_container, visible=True
        )
        temp_master_obj.Label = f"master_shape_{original_label}"
        # Center the shape at the container's origin
        source_centroid = temp_container.SourceCentroid
        temp_master_obj.Placement = FreeCAD.Placement(source_centroid.negative(), FreeCAD.Rotation())
        
        if hasattr(master_obj, "BoundaryObject") and master_obj.BoundaryObject:
            temp_bound = create_part_feature(
                self.doc, f"temp_boundary_{original_label}", master_obj.BoundaryObject.Shape.copy(), group=temp_container, visible=False
            )
            
            if not hasattr(temp_master_obj, "BoundaryObject"):
                temp_master_obj.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "Boundary object")
            temp_master_obj.BoundaryObject = temp_bound
        
        # Shape visible, container visible during nesting
        if hasattr(temp_master_obj, "ViewObject"): 
            temp_master_obj.ViewObject.Visibility = True
        if hasattr(temp_container, "ViewObject"): 
            temp_container.ViewObject.Visibility = True

        part_params = quantities.get(original_label, {'quantity': 1})
        if isinstance(part_params, tuple):
            quantity = part_params[0]
        else:
            quantity = part_params.get('quantity', 1)
        temp_container.addProperty("App::PropertyInteger", "Quantity", "Nest", "Number of instances").Quantity = quantity

        temp_shape_wrapper = None
        if hasattr(temp_master_obj, "BoundaryObject") and temp_master_obj.BoundaryObject:
            try:
                bound_shape = temp_master_obj.BoundaryObject.Shape
                wires = bound_shape.Wires
                wires.sort(key=lambda w: w.Length, reverse=True)
                
                final_poly = shape_processor.discretize_wires_to_polygon(wires, deflection)
                if final_poly:
                    temp_shape_wrapper = Shape(temp_master_obj)
                    temp_shape_wrapper.polygon = final_poly
                    temp_shape_wrapper.source_centroid = temp_container.SourceCentroid
                    self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Shape reload failed for '{label}': {e}\n{traceback.format_exc()}. Recalculating.\n")
                temp_shape_wrapper = None
        
        if not temp_shape_wrapper:
            temp_shape_wrapper = Shape(temp_master_obj)
            shape_processor.create_single_nesting_part(temp_shape_wrapper, temp_master_obj, spacing, deflection, simplification, verbose=verbose)
            # Update the container's SourceCentroid with the recalculated value
            if temp_shape_wrapper.source_centroid:
                temp_container.SourceCentroid = temp_shape_wrapper.source_centroid
            self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)

        return temp_master_obj, temp_shape_wrapper

    def _handle_new_master(self, master_obj, label, quantities, temp_shape_wrapper, spacing, deflection, simplification, cache_key, master_shapes_group, is_reloading, verbose=False):
        if not temp_shape_wrapper:
            # Get up_direction for initial processing
            part_params = quantities.get(label, {'up_direction': 'Z+'})
            up_direction = part_params[0] if isinstance(part_params, tuple) else part_params.get('up_direction', 'Z+')
            
            temp_shape_wrapper = Shape(master_obj)
            shape_processor.create_single_nesting_part(temp_shape_wrapper, master_obj, spacing, deflection, simplification, up_direction, verbose=verbose)
            self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)

        if temp_shape_wrapper.source_centroid is not None:
            source_centroid = temp_shape_wrapper.source_centroid
        else:
            # Fallback: calculate from shape bounding box
            temp_shape = master_obj.Shape.copy()
            if master_obj.Placement and not master_obj.Placement.isIdentity():
                temp_shape.transformShape(master_obj.Placement.Matrix)
            bb = temp_shape.BoundBox
            source_centroid = FreeCAD.Vector((bb.XMin + bb.XMax) / 2, (bb.YMin + bb.YMax) / 2, (bb.ZMin + bb.ZMax) / 2)

        master_container, up_direction = self._create_master_container(label, quantities, source_centroid)
        
        original_shape = master_obj.Shape.copy()
        if verbose:
            FreeCAD.Console.PrintMessage(f"  -> Creating master for '{label}' (type: {master_obj.TypeId}) with up_direction='{up_direction}'\n")
        
        if master_obj.isDerivedFrom("Part::Part2DObject"):
            plc = master_obj.Placement
            offset = FreeCAD.Vector(source_centroid.x, source_centroid.y, source_centroid.z)
            original_shape = self._rebuild_2d_shape(master_obj, original_shape, source_centroid, plc, offset, verbose, label)
        else:
            original_shape = self._center_3d_shape(master_obj, original_shape, source_centroid)
            
        master_shape_obj = create_part_feature(
            self.doc, f"master_shape_{label}", original_shape, group=master_container, visible=True
        )
        if not hasattr(master_shape_obj, "ShowBounds"):
            master_shape_obj.addProperty("App::PropertyBool", "ShowBounds", "Display", "").ShowBounds = False
        if not hasattr(master_shape_obj, "BoundaryObject"):
            master_shape_obj.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "")
        
        master_shape_obj.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), get_up_direction_rotation(up_direction))

        self._create_boundary_object(master_container, master_shape_obj, temp_shape_wrapper, verbose)
        master_shapes_group.addObject(master_container)
        
        return master_shape_obj, temp_shape_wrapper

    def _create_master_container(self, label, quantities, source_centroid):
        """Creates the App::Part container and populates it with metadata properties."""
        master_container = self.doc.addObject("App::Part", f"master_{label}")
        
        part_params = quantities.get(label, {'quantity': 1, 'up_direction': 'Z+', 'fill_sheet': False})
        if isinstance(part_params, tuple):
            quantity, up_direction, fill_sheet = part_params[0], 'Z+', False
        else:
            quantity = part_params.get('quantity', 1)
            up_direction = part_params.get('up_direction', 'Z+')
            fill_sheet = part_params.get('fill_sheet', False)
        
        master_container.addProperty("App::PropertyInteger", "Quantity", "Nest", "Number of instances").Quantity = quantity
        master_container.addProperty("App::PropertyString", "UpDirection", "Nest", "Up direction for 2D projection").UpDirection = up_direction
        master_container.addProperty("App::PropertyBool", "FillSheet", "Nest", "Use to fill remaining space").FillSheet = fill_sheet
        master_container.addProperty("App::PropertyVector", "SourceCentroid", "Nesting", "Original geometry center").SourceCentroid = source_centroid

        if hasattr(master_container, "ViewObject"):
            master_container.ViewObject.Visibility = True
            
        return master_container, up_direction

    def _rebuild_2d_shape(self, master_obj, original_shape, center_point, plc, offset, verbose, label):
        """Rebuilds 2D shape by transforming each edge's curve parameters to preserve smooth curves."""
        try:
            new_edges = []
            for edge in original_shape.Edges:
                curve = edge.Curve
                if hasattr(curve, 'Radius') and hasattr(curve, 'Center'):
                    # Circle or Arc: transform center, preserve radius and smoothness
                    world_center = plc.multVec(curve.Center)
                    new_center = world_center - offset
                    new_axis = plc.Rotation.multVec(curve.Axis)
                    if edge.isClosed():
                        new_edges.append(Part.makeCircle(curve.Radius, new_center, new_axis))
                    else:
                        c = Part.Circle(new_center, new_axis, curve.Radius)
                        new_edges.append(c.toShape(edge.FirstParameter, edge.LastParameter))
                elif len(edge.Vertexes) >= 2:
                    # Line: transform endpoints
                    p1 = plc.multVec(edge.Vertexes[0].Point) - offset
                    p2 = plc.multVec(edge.Vertexes[1].Point) - offset
                    new_edges.append(Part.makeLine(p1, p2))
                else:
                    # Fallback: discretize unknown curve types
                    pts = edge.discretize(Number=72)
                    transformed = [plc.multVec(p) - offset for p in pts]
                    for i in range(len(transformed) - 1):
                        new_edges.append(Part.makeLine(transformed[i], transformed[i + 1]))
            
            if new_edges:
                wire = Part.Wire(new_edges)
                try:
                    rebuilt_shape = Part.Face(wire)
                except Exception:
                    rebuilt_shape = Part.Compound([wire])
                if verbose:
                    FreeCAD.Console.PrintMessage(f"     Rebuilt 2D shape with smooth curves\n")
                return rebuilt_shape
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"     Curve preservation unsuccessful for '{label}': {e}. Using polygon approximation.\n")
            # Fallback: discretize to polygon
            new_wires = []
            for wire in master_obj.Shape.Wires:
                pts = wire.discretize(Number=72)
                if len(pts) > 2:
                    transformed = [plc.multVec(p) - offset for p in pts]
                    if transformed[0] != transformed[-1]:
                        transformed.append(transformed[0])
                    new_wires.append(Part.makePolygon(transformed))
            if new_wires:
                try:
                    return Part.Face(new_wires[0])
                except Exception:
                    return Part.Compound(new_wires)
        return original_shape

    def _center_3d_shape(self, master_obj, original_shape, center_point):
        """Centers regular Part objects using combined transformGeometry (Placement + centering)."""
        combined_mat = FreeCAD.Matrix()
        combined_mat.move(center_point.negative())
        if master_obj.Placement and not master_obj.Placement.isIdentity():
            combined_mat = combined_mat.multiply(master_obj.Placement.Matrix)
        return original_shape.transformGeometry(combined_mat)

    def _create_boundary_object(self, master_container, master_shape_obj, temp_shape_wrapper, verbose):
        """Creates the boundary shape object from the temp_shape_wrapper and adds it to the master_container."""
        if temp_shape_wrapper.polygon:
            boundary_obj = temp_shape_wrapper.draw_bounds(self.doc, FreeCAD.Vector(0,0,0), None)
            if boundary_obj:
                master_container.addObject(boundary_obj)
                # Bounds are centered at origin - no placement needed
                boundary_obj.Placement = FreeCAD.Placement()
                master_shape_obj.BoundaryObject = boundary_obj
                master_shape_obj.ShowBounds = False
                if hasattr(boundary_obj, "ViewObject"): 
                    boundary_obj.ViewObject.Visibility = False
                if verbose:
                    FreeCAD.Console.PrintMessage(f"     Bounds centroid from polygon: {temp_shape_wrapper.polygon.centroid}\n")

    def _arrange_masters(self, masters_to_place, spacing):
        masters_to_place.sort(key=lambda item: item[1].area, reverse=True)
        
        max_master_height = 0
        if masters_to_place:
            max_master_height = max(item[1].bounding_box()[3] for item in masters_to_place if item[1].polygon)

        # Start cursor at 0 (or slight left offset if desired, but 0 is fine)
        cursor_x = 0
        y_offset = -max_master_height - spacing * 4 
        
        for container, shape_wrapper in masters_to_place:
            # bounds is (min_x, min_y, width, height) of the Shapely polygon (centered at 0,0) as returned by bounding_box()
            # Note: bounding_box() returns (minx, miny, width, height)
            bounds = shape_wrapper.bounding_box()
            width = bounds[2] if bounds else 5
            
            # Fix for Asymmetric Shapes:
            # The shape geometry is inside the container. 
            # The container is placed at `container_pos`.
            # We want the Left Edge of the shape's bounding box to be at `cursor_x`.
            # The local Left Edge is `bounds[0]` (min_x).
            # So: container_pos.x + min_x = cursor_x
            # => container_pos.x = cursor_x - min_x
            
            min_x_val = bounds[0] if bounds else (-width/2.0)
            center_x = cursor_x - min_x_val
            
            container_pos = FreeCAD.Vector(center_x, y_offset, 0)
            container.Placement = FreeCAD.Placement(container_pos, FreeCAD.Rotation())
            
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
        verbose = ui_settings.get('verbose', False)

        def _spawn_factory(original_obj, master_wrapper, lookup_label,
                           part_rotation_steps, fill_sheet, up_direction,
                           master_shape_obj):
            """Binds one part type's parameters into a dedicated closure scope.

            make_instance is handed out as spawn_next and called long after the
            master-shapes loop has moved on — defining it directly in the loop
            body would late-bind every spawner to the LAST type's variables.
            Each factory call also gets its own instance counter.
            """
            next_instance_num = [0]

            def make_instance():
                next_instance_num[0] += 1
                i = next_instance_num[0]

                shape_instance = Shape(original_obj)

                # Copy properties
                shape_instance.polygon = master_wrapper.polygon
                shape_instance.original_polygon = master_wrapper.original_polygon
                shape_instance.unbuffered_polygon = master_wrapper.unbuffered_polygon
                shape_instance.source_centroid = master_wrapper.source_centroid
                shape_instance.spacing = spacing

                shape_instance.instance_num = i
                shape_instance.id = f"{lookup_label}_{i}"
                shape_instance.master_label = lookup_label  # type identity — never parse .id
                shape_instance.rotation_steps = part_rotation_steps
                shape_instance.fill_sheet = fill_sheet
                shape_instance.up_direction = up_direction

                part_copy = create_part_feature(
                    self.doc, f"part_{shape_instance.id}", master_shape_obj.Shape.copy(), group=parts_to_place_group, visible=False
                )
                part_copy.Placement = master_shape_obj.Placement

                # Debug: Check what geometry we're getting
                if verbose and up_direction != "Z+" and up_direction is not None:
                    FreeCAD.Console.PrintMessage(f"     Part copy {shape_instance.id}: BoundBox={part_copy.Shape.BoundBox}\n")

                # Copy boundary if exists
                if hasattr(master_shape_obj, "BoundaryObject") and master_shape_obj.BoundaryObject:
                    boundary_copy = create_part_feature(
                        self.doc, f"boundary_{shape_instance.id}", master_shape_obj.BoundaryObject.Shape.copy(), group=parts_to_place_group, visible=False
                    )
                    part_copy.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "Boundary object")
                    part_copy.BoundaryObject = boundary_copy

                shape_instance.fc_object = part_copy

                # Do NOT manipulate Placement here.
                # The Sheet.draw method is the sole authority on where this part ends up.

                if add_labels and Draft and font_path:
                    shape_instance.label_text = shape_instance.id

                if fill_sheet:
                    shape_instance.spawn_next = make_instance

                return shape_instance

            return make_instance

        for label, original_obj in master_shapes_map.items():
            # If reloading, label is master_shape_X, handle mapping
            lookup_label = label
            if label.startswith("master_shape_"):
                 lookup_label = label.replace("master_shape_", "")
            
            part_params = quantities.get(lookup_label, {'quantity': 0, 'rotation_steps': global_rotation_steps})
            quantity = part_params.get('quantity', 0)
            part_rotation_steps = part_params.get('rotation_steps', global_rotation_steps)
            fill_sheet = part_params.get('fill_sheet', False)
            up_direction = part_params.get('up_direction', 'Z+')
            
            master_shape_obj = master_shape_obj_map.get(id(original_obj))
            master_wrapper = master_geometry_cache.get(id(original_obj))
            
            if not master_shape_obj or not master_wrapper: continue

            # Fill-sheet parts don't know in advance how many copies will fit,
            # so instance creation lives in a closure; fill parts carry a
            # spawn_next handle that mints one more instance on demand.
            if fill_sheet:
                quantity = max(quantity, 1)

            make_instance = _spawn_factory(
                original_obj, master_wrapper, lookup_label, part_rotation_steps,
                fill_sheet, up_direction, master_shape_obj
            )
            for _ in range(quantity):
                parts_to_nest.append(make_instance())

        return parts_to_nest
