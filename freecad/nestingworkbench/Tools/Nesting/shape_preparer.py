# SPDX-License-Identifier: LGPL-2.1-or-later

import FreeCAD
import Part
import copy
import hashlib
import Draft
import shapely
import traceback
from freecad.nestingworkbench import nw_logger
from . import shape_processor
from ...datatypes.shape_object import create_shape_object
from ...datatypes.shape import Shape
from ...freecad_helpers import get_up_vector_rotation, create_part_feature, world_shape
from ...constants import (
    PROP_BOOL, PROP_INTEGER, PROP_STRING, PROP_VECTOR,
    PROP_QUANTITY, PROP_UP_VECTOR, PROP_FILL_SHEET,
    PROP_PART_ROTATION_OVERRIDE, PROP_PART_ROTATION_STEPS,
)


def write_master_metadata(container, part_params):
    """Writes the per-part metadata NestingController._load_shapes_from_layout
    reads back when a saved layout is reopened.

    Every property here must be written on BOTH master-container paths:
    _create_master_container (fresh nest) and _create_temp_from_reloading
    (re-nest of an existing layout). NestingJob.commit() deletes the old
    MasterShapes group and promotes the reload path's containers in its
    place (nesting_job.py:47-61), so a property written on only one path
    is silently lost the first time a layout is re-nested. The outline stamp
    (write_outline_stamp) follows the same both-paths rule.

    part_params is one value from the `quantities` dict built by
    NestingController._collect_job_parameters. The legacy tuple form is
    tolerated for the same reason the existing callers tolerate it.
    """
    if isinstance(part_params, tuple):
        part_params = {'quantity': part_params[0]}

    specs = (
        (PROP_INTEGER, PROP_QUANTITY, "Number of instances",
         int(part_params.get('quantity', 1))),
        (PROP_VECTOR, PROP_UP_VECTOR, "Up vector for 2D projection",
         FreeCAD.Vector(part_params.get('up_vector', FreeCAD.Vector(0, 0, 1)))),
        (PROP_BOOL, PROP_FILL_SHEET, "Use to fill remaining space",
         bool(part_params.get('fill_sheet', False))),
        (PROP_BOOL, PROP_PART_ROTATION_OVERRIDE,
         "Per-part rotation steps override the global setting",
         bool(part_params.get('override_rotation', False))),
        (PROP_INTEGER, PROP_PART_ROTATION_STEPS,
         "Per-part rotation steps, used when the override is set",
         int(part_params.get('part_rotation_steps', 0))),
    )
    for type_str, name, doc, value in specs:
        if not hasattr(container, name):
            container.addProperty(type_str, name, "Nest", doc)
        setattr(container, name, value)


OUTLINE_STAMP_PROPS = ("OutlineVersion", "OutlineSpacing", "OutlineDeflection", "OutlineSimplification")


def write_outline_stamp(container, spacing, deflection, simplification):
    """Records what built a master's outline: the outline code version and the
    Spacing, Curve Angle (deflection) and Simplification in mm, exactly as they were
    passed to create_single_nesting_part. The properties are read-only in the editor.
    """
    specs = (
        ("App::PropertyInteger", "OutlineVersion",
         "Outline code version that built this outline", int(shape_processor.OUTLINE_VERSION)),
        ("App::PropertyFloat", "OutlineSpacing",
         "Spacing the outline was built at (mm)", float(spacing)),
        ("App::PropertyFloat", "OutlineDeflection",
         "Curve deflection the outline was built at (mm)", float(deflection)),
        ("App::PropertyFloat", "OutlineSimplification",
         "Simplification the outline was built at (mm)", float(simplification)),
    )
    for type_str, name, doc, value in specs:
        if not hasattr(container, name):
            container.addProperty(type_str, name, "Nesting", doc)
        container.setEditorMode(name, 1)
        setattr(container, name, value)


def outline_stamp_mismatch(container, spacing, deflection, simplification):
    """Why a saved outline cannot be reused, or None when it can.

    Returns "no stamp" when a stamp property is missing (or container is None),
    "outline version N" when the stored version N is not OUTLINE_VERSION, and
    "settings changed" when a stored setting differs by more than 1e-9 mm.
    """
    if container is None or not all(hasattr(container, n) for n in OUTLINE_STAMP_PROPS):
        return "no stamp"
    stored = int(container.OutlineVersion)
    if stored != shape_processor.OUTLINE_VERSION:
        return f"outline version {stored}"
    current = (("OutlineSpacing", spacing), ("OutlineDeflection", deflection),
               ("OutlineSimplification", simplification))
    for name, value in current:
        if abs(float(getattr(container, name)) - float(value)) > 1e-9:
            return "settings changed"
    return None


def outline_is_current(container, spacing, deflection, simplification):
    """True when the container's stamp shows its outline was built by the current
    OUTLINE_VERSION at exactly these settings. See outline_stamp_mismatch."""
    return outline_stamp_mismatch(container, spacing, deflection, simplification) is None


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
        # (master label, reason) for each saved outline rebuilt by the current prepare_parts call.
        self._rebuilt_outlines = []

    def prepare_parts(self, ui_global_settings, quantities, master_shapes_map, layout_obj, parts_group):
        """
        Main entry point to prepare parts.
        
        Args:
            ui_global_settings (dict): { 'spacing': float, 'deflection': float, 'simplification': float, 'rotation_steps': int, 'add_labels': bool, 'font_path': str, 'verbose': bool }
            quantities (dict): { label: {'quantity': int, 'rotation_steps': int, 'up_vector': FreeCAD.Vector, 'fill_sheet': bool} }
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
        self._rebuilt_outlines = []
        
        master_shapes_group = self._get_or_create_master_group(layout_obj)

        master_shape_obj_map = {} # Maps original FreeCAD object ID to the new master ShapeObject
        master_geometry_cache = {} # Maps original FreeCAD object ID to the processed Shape wrapper
        masters_to_place = []

        for label, master_obj in master_shapes_map.items():
            try:
                cache_key = self._processed_cache_key(
                    label, master_obj, quantities, spacing, deflection, simplification)
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
                nw_logger.exception(f"Could not create boundary for '{master_obj.Label}', it will be skipped. Error: {e}")
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

        if self._rebuilt_outlines:
            reasons = {}
            for _, reason in self._rebuilt_outlines:
                reasons[reason] = reasons.get(reason, 0) + 1
            summary = ", ".join(f"{reason}: {count}" for reason, count in reasons.items())
            names = ", ".join(name for name, _ in self._rebuilt_outlines)
            nw_logger.info(
                f"Rebuilt {len(self._rebuilt_outlines)} saved part outlines at the current "
                f"Spacing / Curve Angle / Simplification ({summary}): {names}")

        return parts_to_nest

    @staticmethod
    def _processed_cache_key(label, master_obj, quantities, spacing, deflection, simplification):
        """Key for processed_shape_cache: these settings produced this wrapper.

        `label` is `master_shape_<name>` on the reload path, but `quantities`
        is keyed by the table's display label `<name>`, so strip the prefix
        before the lookup (as _create_temp_from_reloading and
        _create_nesting_instances already do). Without it, every reloaded
        part is keyed at the default up vector.
        """
        part_params = quantities.get(label.removeprefix("master_shape_"), {})
        if isinstance(part_params, tuple):
            up_vector = FreeCAD.Vector(0, 0, 1)
        else:
            up_vector = part_params.get('up_vector', FreeCAD.Vector(0, 0, 1))
        # Vector isn't hashable; (x, y, z) is.
        return (master_obj.Name, spacing, deflection, simplification,
                (up_vector.x, up_vector.y, up_vector.z))

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
        
        # A saved outline is reused only when its stamp shows the current outline version
        # and settings (ROS-003). Otherwise it is rebuilt from the saved master solid.
        mismatch = outline_stamp_mismatch(original_container, spacing, deflection, simplification)
        stale = mismatch is not None

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
        # `master_obj` is an existing master_shape_*, so its geometry is ALREADY
        # centred on the nesting polygon's centroid and its Placement carries
        # only the up-direction rotation. Re-centring it on SourceCentroid here
        # shifted the visible shape off its origin-centred boundary (and off
        # every part instance's boundary, since _create_nesting_instances copies
        # this placement) by the source geometry's world centroid. Mirror the
        # source master's placement instead.
        temp_master_obj.Placement = FreeCAD.Placement(
            FreeCAD.Vector(0, 0, 0), master_obj.Placement.Rotation
        )
        
        if hasattr(master_obj, "BoundaryObject") and master_obj.BoundaryObject:
            temp_bound = create_part_feature(
                self.doc, f"temp_boundary_{original_label}", master_obj.BoundaryObject.Shape.copy(), group=temp_container, visible=True
            )
            
            if not hasattr(temp_master_obj, "BoundaryObject"):
                temp_master_obj.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "Boundary object")
            temp_master_obj.BoundaryObject = temp_bound
        
        # Shape visible, container visible during nesting
        if hasattr(temp_master_obj, "ViewObject"): 
            temp_master_obj.ViewObject.Visibility = True
        if hasattr(temp_container, "ViewObject"): 
            temp_container.ViewObject.Visibility = True

        # Keyed by the stripped label: `label` here is `master_shape_<name>`,
        # while `quantities` is keyed by the table's display label.
        part_params = quantities.get(original_label, {'quantity': 1})
        write_master_metadata(temp_container, part_params)

        temp_shape_wrapper = None
        if not stale and hasattr(temp_master_obj, "BoundaryObject") and temp_master_obj.BoundaryObject:
            try:
                bound_shape = temp_master_obj.BoundaryObject.Shape
                wires = bound_shape.Wires
                # Enclosed extent, not perimeter: a hole is inside the exterior ring, so its
                # bbox is too, but a convoluted hole can be longer than a smooth outline. A
                # perimeter sort would hand discretize_wires_to_polygon the hole as the
                # exterior and rebuild the part inside out (RLF-001).
                wires.sort(key=lambda w: w.BoundBox.XLength * w.BoundBox.YLength, reverse=True)
                
                final_poly = shape_processor.discretize_wires_to_polygon(wires, deflection)
                if final_poly:
                    temp_shape_wrapper = Shape(temp_master_obj)
                    temp_shape_wrapper.polygon = final_poly
                    # The saved boundary is the buffered outline at angle 0,
                    # so it is also the rotation base. Leaving this at None
                    # makes compute_and_cache_nfp raise on `mA.centroid` and
                    # cache an error for every pair key (issue #19).
                    temp_shape_wrapper.original_polygon = final_poly
                    temp_shape_wrapper.spacing = spacing
                    temp_shape_wrapper.deflection = float(deflection)
                    temp_shape_wrapper.simplification = float(simplification)
                    temp_shape_wrapper.source_centroid = temp_container.SourceCentroid
                    self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)
            except Exception as e:
                nw_logger.warn(f"Shape reload failed for '{label}': {e}\n{traceback.format_exc()}. Recalculating.")
                temp_shape_wrapper = None
        
        # A stale outline skips this fallback: the reproject below builds the wrapper itself.
        # Running both would build the outline twice and overwrite temp_container.SourceCentroid
        # before the reproject adds its delta to it.
        if not temp_shape_wrapper and not stale:
            temp_shape_wrapper = Shape(temp_master_obj)
            shape_processor.create_single_nesting_part(temp_shape_wrapper, temp_master_obj, spacing, deflection, simplification, verbose=verbose)
            # Update the container's SourceCentroid with the recalculated value
            if temp_shape_wrapper.source_centroid:
                temp_container.SourceCentroid = temp_shape_wrapper.source_centroid
            self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)

        # The table's Up Dir wins over the one saved with the layout (RUP-001). The saved
        # container is the one whose UpVector was stamped when the layout was committed;
        # write_master_metadata has already overwritten temp_container with the new one.
        new_up = (FreeCAD.Vector(0, 0, 1) if isinstance(part_params, tuple)
                  else FreeCAD.Vector(part_params.get('up_vector', FreeCAD.Vector(0, 0, 1))))
        saved_up = FreeCAD.Vector(getattr(original_container, "UpVector", FreeCAD.Vector(0, 0, 1)))
        if (new_up - saved_up).Length > 1e-9 or stale:
            temp_shape_wrapper = self._reproject_reloaded_master(
                temp_master_obj, temp_container, new_up, spacing, deflection, simplification,
                verbose=verbose)
            self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)

        if stale:
            self._rebuilt_outlines.append((original_label, mismatch))
        write_outline_stamp(temp_container, spacing, deflection, simplification)

        return temp_master_obj, temp_shape_wrapper

    def _reproject_reloaded_master(self, temp_master_obj, temp_container, up_vector,
                                   spacing, deflection, simplification, verbose=False):
        """Re-projects a reloaded master for a changed up vector (RUP-001).

        temp_master_obj is a copy of a saved master: the source geometry centred on its
        old pivot, with the OLD up rotation held in its Placement. Clearing that
        Placement recovers the unrotated geometry, so the new outline, pivot and
        rotation are derived without the source object. The result matches a fresh
        nest of the source at up_vector.

        Returns the new Shape wrapper. temp_container.SourceCentroid is moved by the
        same amount the geometry is re-centred by.
        """
        base = temp_master_obj.Shape.copy()
        base.Placement = FreeCAD.Placement()
        temp_master_obj.Placement = FreeCAD.Placement()
        temp_master_obj.Shape = base

        wrapper = Shape(temp_master_obj)
        shape_processor.create_single_nesting_part(
            wrapper, temp_master_obj, spacing, deflection, simplification, up_vector,
            verbose=verbose)

        # wrapper.source_centroid is the new pivot in the saved geometry's own frame.
        delta = wrapper.source_centroid
        temp_master_obj.Shape = self._center_3d_shape(temp_master_obj, base.copy(), delta)
        temp_master_obj.Placement = FreeCAD.Placement(
            FreeCAD.Vector(0, 0, 0), get_up_vector_rotation(up_vector))

        temp_container.SourceCentroid = temp_container.SourceCentroid + delta
        wrapper.source_centroid = temp_container.SourceCentroid

        # The saved outline is for the old vector: replace it.
        old_boundary = getattr(temp_master_obj, "BoundaryObject", None)
        if old_boundary:
            self.doc.removeObject(old_boundary.Name)
        if not hasattr(temp_master_obj, "ShowBounds"):
            temp_master_obj.addProperty("App::PropertyBool", "ShowBounds", "Display", "").ShowBounds = False
        if not hasattr(temp_master_obj, "BoundaryObject"):
            temp_master_obj.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "")
        self._create_boundary_object(temp_container, temp_master_obj, wrapper, verbose)
        return wrapper

    def _handle_new_master(self, master_obj, label, quantities, temp_shape_wrapper, spacing, deflection, simplification, cache_key, master_shapes_group, is_reloading, verbose=False):
        if not temp_shape_wrapper:
            # Get up_vector for initial processing
            part_params = quantities.get(label, {'up_vector': FreeCAD.Vector(0, 0, 1)})
            up_vector = FreeCAD.Vector(0, 0, 1) if isinstance(part_params, tuple) else part_params.get('up_vector', FreeCAD.Vector(0, 0, 1))
            
            temp_shape_wrapper = Shape(master_obj)
            shape_processor.create_single_nesting_part(temp_shape_wrapper, master_obj, spacing, deflection, simplification, up_vector, verbose=verbose)
            self.processed_shape_cache[cache_key] = copy.deepcopy(temp_shape_wrapper)

        if temp_shape_wrapper.source_centroid is not None:
            source_centroid = temp_shape_wrapper.source_centroid
        else:
            # Fallback: calculate from shape bounding box of world shape
            # (including parents' placement; GLB-001).
            bb = world_shape(master_obj).BoundBox
            source_centroid = FreeCAD.Vector((bb.XMin + bb.XMax) / 2, (bb.YMin + bb.YMax) / 2, (bb.ZMin + bb.ZMax) / 2)

        master_container, up_vector = self._create_master_container(label, quantities, source_centroid)
        # The outline built above (here or from processed_shape_cache, same settings in the key).
        write_outline_stamp(master_container, spacing, deflection, simplification)

        original_shape = world_shape(master_obj)
        if verbose:
            nw_logger.info(f"  -> Creating master for '{label}' (type: {master_obj.TypeId}) with up_vector='{up_vector}'")
        
        if master_obj.isDerivedFrom("Part::Part2DObject"):
            # Edge points and curve centres of original_shape are already in world
            # coordinates (including parents' placement; GLB-001), so the rebuild
            # applies no further placement.
            plc = FreeCAD.Placement()
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
        
        master_shape_obj.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), get_up_vector_rotation(up_vector))

        self._create_boundary_object(master_container, master_shape_obj, temp_shape_wrapper, verbose)
        master_shapes_group.addObject(master_container)
        
        return master_shape_obj, temp_shape_wrapper

    def _create_master_container(self, label, quantities, source_centroid):
        """Creates the App::Part container and populates it with metadata properties."""
        master_container = self.doc.addObject("App::Part", f"master_{label}")
        
        part_params = quantities.get(label, {'quantity': 1, 'up_vector': FreeCAD.Vector(0, 0, 1), 'fill_sheet': False})
        if isinstance(part_params, tuple):
            quantity, up_vector, fill_sheet = part_params[0], FreeCAD.Vector(0, 0, 1), False
        else:
            quantity = part_params.get('quantity', 1)
            up_vector = part_params.get('up_vector', FreeCAD.Vector(0, 0, 1))
            fill_sheet = part_params.get('fill_sheet', False)
        
        write_master_metadata(master_container, part_params)
        master_container.addProperty("App::PropertyVector", "SourceCentroid", "Nesting", "Original geometry center").SourceCentroid = source_centroid

        if hasattr(master_container, "ViewObject"):
            master_container.ViewObject.Visibility = True
            
        return master_container, up_vector

    def _rebuild_2d_shape(self, master_obj, original_shape, center_point, plc, offset, verbose, label):
        """Rebuilds a 2D shape as one face with its holes, every curve kept exact.

        Each edge is copied and moved rather than re-derived from its endpoints, so a
        B-spline, Bezier or ellipse arc stays that curve (a straight chord before
        ISK-002). The edges are chained into loops and the bullseye face maker nests
        them, so holes survive; one Part.Wire of all edges failed for any sketch with
        a hole and fell back to the first wire alone.
        """
        try:
            edges = []
            for edge in original_shape.Edges:
                moved = edge.copy()
                moved.transformShape(plc.Matrix)
                moved.translate(offset.negative())
                edges.append(moved)
            groups = Part.sortEdges(edges, 0.01)
            for group in groups:
                for e in group:
                    e.fixTolerance(0.01)
            wires = [Part.Wire(group) for group in groups]
            closed = [w for w in wires if w.isClosed()]
            if closed:
                rebuilt_shape = Part.makeFace(closed, "Part::FaceMakerBullseye")
                if verbose:
                    nw_logger.info(f"     Rebuilt 2D shape: {len(closed)} loop(s), curves kept")
                return rebuilt_shape
            if wires:
                nw_logger.warn(f"     '{label}' has no closed loop; using its open wires as-is.")
                return Part.Compound(wires)
        except Exception as e:
            nw_logger.warn(f"     Curve preservation unsuccessful for '{label}': {e}. Using polygon approximation.")
            new_wires = []
            for wire in original_shape.Wires:
                pts = wire.discretize(Number=72)
                if len(pts) > 2:
                    transformed = [plc.multVec(p) - offset for p in pts]
                    if transformed[0] != transformed[-1]:
                        transformed.append(transformed[0])
                    new_wires.append(Part.makePolygon(transformed))
            if new_wires:
                try:
                    return Part.makeFace(new_wires, "Part::FaceMakerBullseye")
                except Exception as e:
                    nw_logger.debug(f"[ShapePreparer] Face creation failed, falling back to Compound wires: {e}")
                    return Part.Compound(new_wires)
        return original_shape


    def _center_3d_shape(self, master_obj, original_shape, center_point):
        """Bakes the source's world placement and the centring into the geometry.

        original_shape is master_obj.Shape.copy(), which already carries
        master_obj.Placement as its own Placement. The caller then overwrites the
        feature's Placement with the up-vector rotation, so the Placement is
        moved into the matrix and cleared here (SRC-001).

        transformShape keeps lines and circles exact. transformGeometry turned every
        edge into a B-spline, which reached the DXF as SPLINE entities that some
        importers drop, and approximated every circle (XFM-001).
        """
        world_plc = original_shape.Placement
        original_shape.Placement = FreeCAD.Placement()
        combined_mat = FreeCAD.Matrix()
        combined_mat.move(center_point.negative())
        combined_mat = combined_mat.multiply(world_plc.Matrix)
        original_shape.transformShape(combined_mat, True)
        return original_shape

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
                # The outline stays on screen alongside its master for as long
                # as the nesting panel is open; the panel hides the whole row
                # again when it closes.
                if hasattr(boundary_obj, "ViewObject"): 
                    boundary_obj.ViewObject.Visibility = True
                if verbose:
                    nw_logger.info(f"     Bounds centroid from polygon: {temp_shape_wrapper.polygon.centroid}")

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

    @staticmethod
    def _geometry_type_label(label, polygon):
        """The part type's cache identity: its label plus a hash of its outline.

        Shape.nfp_cache survives from one run to the next, and its key is built from
        type_label. A label alone says nothing about the outline, so a changed Up Dir
        (or an edited source) reused another outline's NFPs and stacked the parts
        (NFC-001). The hash makes the key differ whenever the outline does, and stay the
        same, so the cache stays warm, when it does not.
        """
        if polygon is None:
            return label
        digest = hashlib.sha1(shapely.to_wkb(polygon), usedforsecurity=False).hexdigest()[:10]
        return f"{label}@{digest}"

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
                           part_rotation_steps, fill_sheet, up_vector,
                           master_shape_obj, master_container, type_label):
            """Binds one part type's parameters into a dedicated closure scope.

            make_instance is handed out as spawn_next and called long after the
            master-shapes loop has moved on — defining it directly in the loop
            body would late-bind every spawner to the LAST type's variables.
            Each factory call also gets its own instance counter. type_label is the
            part type's cache identity (label plus outline hash), shared by every
            instance of this master.
            """
            next_instance_num = [0]

            def make_instance(as_fill=fill_sheet):
                next_instance_num[0] += 1
                i = next_instance_num[0]

                shape_instance = Shape(original_obj)

                # Copy properties
                shape_instance.polygon = master_wrapper.polygon
                shape_instance.original_polygon = master_wrapper.original_polygon
                shape_instance.unbuffered_polygon = master_wrapper.unbuffered_polygon
                shape_instance.source_centroid = master_wrapper.source_centroid
                shape_instance.spacing = spacing

                # Part of the NFP cache key (MinkowskiEngine._nfp_cache_key,
                # enumerate_nfp_jobs). Left unset, every instance carries
                # Shape.__init__'s 0.05 / 1.0 and the cache stops telling
                # one Curve Angle or Simplification setting from another.
                shape_instance.deflection = master_wrapper.deflection
                shape_instance.simplification = master_wrapper.simplification

                shape_instance.instance_num = i
                shape_instance.id = f"{lookup_label}_{i}"
                shape_instance.master_label = lookup_label  # type identity — never parse .id
                shape_instance.type_label = type_label
                shape_instance.rotation_steps = part_rotation_steps
                shape_instance.fill_sheet = as_fill
                shape_instance.up_vector = up_vector
                # The simulation highlighter needs the row this instance came
                # from; it must never go looking for it by label (see
                # nesting_logic._find_master_container_for_part).
                shape_instance.master_container = master_container

                part_copy = create_part_feature(
                    self.doc, f"part_{shape_instance.id}", master_shape_obj.Shape.copy(), group=parts_to_place_group, visible=False
                )
                part_copy.Placement = master_shape_obj.Placement

                # Debug: Check what geometry we're getting
                if verbose and up_vector is not None:
                    nw_logger.info(f"     Part copy {shape_instance.id}: BoundBox={part_copy.Shape.BoundBox}")

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

                if as_fill:
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
            up_vector = part_params.get('up_vector', FreeCAD.Vector(0, 0, 1))
            
            master_shape_obj = master_shape_obj_map.get(id(original_obj))
            master_wrapper = master_geometry_cache.get(id(original_obj))
            
            if not master_shape_obj or not master_wrapper: continue


            master_container = master_shape_obj.InList[0] if master_shape_obj.InList else None

            type_label = self._geometry_type_label(
                lookup_label,
                master_wrapper.original_polygon if master_wrapper.original_polygon is not None
                else master_wrapper.polygon)

            make_instance = _spawn_factory(
                original_obj, master_wrapper, lookup_label, part_rotation_steps,
                fill_sheet, up_vector, master_shape_obj, master_container, type_label
            )
            # The quantity is a requirement even when Fill is on: those copies
            # are regular parts the GA arranges with everything else. Fill only
            # adds extras on top — one fill seed whose spawn_next mints another
            # copy on demand until no gap fits. (Marking the required copies as
            # fill parts queued them behind every other part and dropped the
            # ones that no longer fit, so 20 requested circles nested as 12.)
            for _ in range(quantity):
                parts_to_nest.append(make_instance(as_fill=False))
            if fill_sheet:
                parts_to_nest.append(make_instance(as_fill=True))

        return parts_to_nest
