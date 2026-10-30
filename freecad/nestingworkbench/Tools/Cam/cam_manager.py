# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/Cam/cam_manager.py

"""
This module contains the CAMManager class, which is responsible for creating
and managing CAM jobs from the nested layouts.
"""

import FreeCAD
from freecad.nestingworkbench import nw_logger
from ...constants import FLIPPED_PREFIX, PROP_SHEET_THICKNESS, THICKNESS_MATCH_TOL_MM
from ...freecad_helpers import (
    find_back_boundary, find_back_group, find_sheet_boundary, get_nested_containers,
)

FALLBACK_SHEET_THICKNESS_MM = 3.0

class CAMManager:
    """Manages the creation of FreeCAD CAM jobs from nested layouts."""
    def __init__(self, layout_group):
        self.doc = FreeCAD.ActiveDocument
        self.layout_group = layout_group

    def create_cam_job(self, include_parts=True, include_labels=True, include_outlines=False, template_path=None, post_processor="grbl"):
        """Main method to create the CAM job.
        
        Args:
            include_parts: Include part_* objects (full cuts)
            include_labels: Include label_* objects (engraving)
            include_outlines: Include outline_* objects (silhouettes)
            template_path: Optional path to a CAM template JSON file
            post_processor: Optional post processor to use (default: "grbl")
        """
        if not self.layout_group:
             nw_logger.error("No layout group provided.")
             return

        # Iterate over the layout group to find sheet groups directly
        for obj in self.layout_group.Group:
            # We assume groups starting with "Sheet_" are the sheet containers
            if obj.isDerivedFrom("App::DocumentObjectGroup") and obj.Label.startswith("Sheet_"):
                self._create_job_for_sheet(obj, include_parts, include_labels, include_outlines, template_path, post_processor)
                self._create_back_job(obj, include_parts, include_labels, include_outlines, template_path, post_processor)

    def _create_job_for_sheet(self, sheet_group, include_parts=True, include_labels=True, include_outlines=False, template_path=None, post_processor="grbl"):
        """Creates a CAM job for a sheet with proper stock dimensions.
        
        Args:
            sheet_group: The Sheet_X group to process
            include_parts: Include part_* objects (full cuts)
            include_labels: Include label_* objects (engraving)
            include_outlines: Include outline_* objects (silhouettes)
            template_path: Optional path to a CAM template JSON file
            post_processor: Optional post processor to use (default: "grbl")
        """
        boundary = find_sheet_boundary(sheet_group)
        if not boundary:
            nw_logger.error(f"Cannot create CAM job for '{sheet_group.Label}': no sheet boundary found.")
            return

        sheet_width = boundary.Shape.BoundBox.XLength
        sheet_height = boundary.Shape.BoundBox.YLength
        sheet_origin = boundary.Placement.Base

        sheet_thickness = FALLBACK_SHEET_THICKNESS_MM
        if self.layout_group and hasattr(self.layout_group, PROP_SHEET_THICKNESS):
            sheet_thickness = float(self.layout_group.SheetThickness)

        thickness_mismatches = []
        
        # Collect transformed shapes for CAM
        # We need to bake container placements into the geometry since CAM
        # doesn't correctly handle objects nested in App::Part containers
        parts_shapes = []
        labels_shapes = []
        outlines_shapes = []
        
        for nested_part in get_nested_containers(sheet_group):
            container_placement = nested_part.Placement

            # Find the part_*, label_*, and outline_* shapes inside the container
            for child in nested_part.Group:
                if hasattr(child, 'Shape') and child.Shape and not child.Shape.isNull():
                    combined_placement = container_placement.multiply(child.Placement)

                    if include_parts and child.Label.startswith("part_"):
                        baked = self._bake_part(child.Shape, combined_placement, sheet_origin, sheet_thickness)
                        if abs(baked.BoundBox.ZLength - sheet_thickness) > THICKNESS_MATCH_TOL_MM:
                            thickness_mismatches.append(child.Label)
                        parts_shapes.append(baked)

                    elif include_labels and child.Label.startswith("label_"):
                        labels_shapes.append(self._bake_label(child.Shape, combined_placement, sheet_origin))

                    elif include_outlines and child.Label.startswith("outline_"):
                        outlines_shapes.append(self._bake_outline(child.Shape, combined_placement, sheet_origin))

        if thickness_mismatches:
            nw_logger.warn(
                f"{sheet_group.Label}: {len(thickness_mismatches)} part(s) do not match the "
                f"{sheet_thickness}mm sheet thickness (e.g. {thickness_mismatches[0]}); "
                f"the CAM model will not line up with the stock height."
            )

        self._create_job(sheet_group.Label, sheet_group.Label, parts_shapes, labels_shapes, outlines_shapes,
                         sheet_width, sheet_height, sheet_thickness, template_path, post_processor)

    @staticmethod
    def _to_world(shape, world_placement):
        """A copy of *shape* with *world_placement* baked into its geometry."""
        transformed = shape.copy()
        transformed.Placement = FreeCAD.Placement()
        return transformed.transformGeometry(world_placement.toMatrix())

    def _bake_part(self, shape, world_placement, sheet_origin, sheet_thickness):
        """World shape shifted by -sheet_origin in XY, with its bottom at Z = -sheet_thickness."""
        transformed = self._to_world(shape, world_placement)
        z_offset = -sheet_thickness - transformed.BoundBox.ZMin
        z_placement = FreeCAD.Placement(FreeCAD.Vector(-sheet_origin.x, -sheet_origin.y, z_offset), FreeCAD.Rotation())
        return transformed.transformGeometry(z_placement.toMatrix())

    def _bake_label(self, shape, world_placement, sheet_origin):
        """World shape shifted by -sheet_origin in XY, with its bottom at Z = 0."""
        transformed = self._to_world(shape, world_placement)
        z_placement = FreeCAD.Placement(FreeCAD.Vector(-sheet_origin.x, -sheet_origin.y, -transformed.BoundBox.ZMin), FreeCAD.Rotation())
        return transformed.transformGeometry(z_placement.toMatrix())

    def _bake_outline(self, shape, world_placement, sheet_origin):
        """World shape shifted by -sheet_origin in XY only."""
        transformed = self._to_world(shape, world_placement)
        shift = FreeCAD.Placement(FreeCAD.Vector(-sheet_origin.x, -sheet_origin.y, 0), FreeCAD.Rotation())
        return transformed.transformGeometry(shift.toMatrix())

    def _create_back_job(self, sheet_group, include_parts=True, include_labels=True,
                         include_outlines=False, template_path=None, post_processor="grbl"):
        """The CAM job for a flipped sheet's back side (Back_<n> inside *sheet_group*).

        Parts, labels and outlines, each gated by the same option as the front job
        (SVF decision 11). Built from the drawn flip_part_/flip_label_/flip_outline_
        objects, in the back boundary's frame, so it follows stacking. Does nothing
        when the sheet has no back side.
        """
        back_group = find_back_group(sheet_group)
        if back_group is None:
            return
        boundary = find_back_boundary(back_group)
        if not boundary:
            nw_logger.error(f"Cannot create the back-side CAM job for '{sheet_group.Label}': no back boundary found.")
            return
        origin = boundary.Placement.Base
        sheet_thickness = FALLBACK_SHEET_THICKNESS_MM
        if self.layout_group and hasattr(self.layout_group, PROP_SHEET_THICKNESS):
            sheet_thickness = float(self.layout_group.SheetThickness)
        parts_shapes, labels_shapes, outlines_shapes = [], [], []
        for obj in back_group.Group:
            if not (hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull()):
                continue
            if include_parts and obj.Label.startswith(f"{FLIPPED_PREFIX}part_"):
                parts_shapes.append(self._bake_part(obj.Shape, obj.Placement, origin, sheet_thickness))
            elif include_labels and obj.Label.startswith(f"{FLIPPED_PREFIX}label_"):
                labels_shapes.append(self._bake_label(obj.Shape, obj.Placement, origin))
            elif include_outlines and obj.Label.startswith(f"{FLIPPED_PREFIX}outline_"):
                outlines_shapes.append(self._bake_outline(obj.Shape, obj.Placement, origin))
        self._create_job(f"{sheet_group.Label}_Back", sheet_group.Label,
                         parts_shapes, labels_shapes, outlines_shapes,
                         boundary.Shape.BoundBox.XLength, boundary.Shape.BoundBox.YLength,
                         sheet_thickness, template_path, post_processor)

    def _create_job(self, job_key, group_key, parts_shapes, labels_shapes, outlines_shapes,
                    sheet_width, sheet_height, sheet_thickness, template_path, post_processor):
        """Builds the CAM models, the Job, its box stock and its groups from baked shapes.

        *job_key* names the models and the job (CAM_Job_<job_key>); *group_key* names
        the parent group (CAM_Sheet_<group_key>), so a sheet's back job sits beside
        its front job.
        """
        # Import CAM modules (FreeCAD 26.3+)
        try:
            from Path.Main import Stock as PathStock
        except ImportError as e:
            nw_logger.error(f"Failed to import CAM modules. Error: {e}")
            nw_logger.error("Please ensure the CAM workbench is installed and enabled in FreeCAD 26.3+.")
            return

        if not (parts_shapes or labels_shapes or outlines_shapes):
            nw_logger.warn(f"No objects selected for CAM in {job_key}. Skipping.")
            return
        
        # Build status message
        counts = []
        if parts_shapes:
            counts.append(f"{len(parts_shapes)} parts")
        if labels_shapes:
            counts.append(f"{len(labels_shapes)} labels")
        if outlines_shapes:
            counts.append(f"{len(outlines_shapes)} outlines")
        nw_logger.info(f"Creating CAM job with {', '.join(counts)}...")
        
        # Create compound objects for CAM (one per type)
        # This minimizes the number of base objects
        import Part
        all_models = []
        
        if parts_shapes:
            parts_compound = self.doc.addObject("Part::Feature", f"CAM_Parts_{job_key}")
            parts_compound.Shape = Part.Compound(parts_shapes)
            if hasattr(parts_compound, 'ViewObject') and parts_compound.ViewObject:
                parts_compound.ViewObject.Visibility = False
            all_models.append(parts_compound)
        
        if labels_shapes:
            labels_compound = self.doc.addObject("Part::Feature", f"CAM_Labels_{job_key}")
            labels_compound.Shape = Part.Compound(labels_shapes)
            if hasattr(labels_compound, 'ViewObject') and labels_compound.ViewObject:
                labels_compound.ViewObject.Visibility = False
            all_models.append(labels_compound)
        
        if outlines_shapes:
            outlines_compound = self.doc.addObject("Part::Feature", f"CAM_Outlines_{job_key}")
            outlines_compound.Shape = Part.Compound(outlines_shapes)
            if hasattr(outlines_compound, 'ViewObject') and outlines_compound.ViewObject:
                outlines_compound.ViewObject.Visibility = False
            all_models.append(outlines_compound)
        
        # Use GUI Create function which properly sets up all Model-Job linking
        try:
            import FreeCADGui
            from Path.Main.Gui import Job as PathJobGui
            
            # Pass openTaskPanel=False to suppress the interactive task panel dialog.
            job = PathJobGui.Create(all_models, template_path, openTaskPanel=False)
            
            if job:
                # Rename the job to our desired name
                job.Label = f"CAM_Job_{job_key}"
                
                # Note: CAM_Parts/Labels/Outlines compounds are hidden base objects
                # that the CAM job references. They cannot be deleted.
                
                # Replace the stock with a CreateBox stock matching sheet dimensions
                if job.Stock:
                    old_stock = job.Stock
                    self.doc.removeObject(old_stock.Name)
                
                # Create new box stock with sheet dimensions
                # Position stock at sheet origin, Z positioned to match where parts are
                new_stock = PathStock.CreateBox(job)
                new_stock.Length = sheet_width
                new_stock.Width = sheet_height
                new_stock.Height = sheet_thickness
                
                # Stock positioned with bottom at Z = -sheet_thickness, top at Z = 0
                # This matches the parts which have their bottom at Z = -sheet_thickness
                new_stock.Placement = FreeCAD.Placement(
                    FreeCAD.Vector(0, 0, -sheet_thickness),
                    FreeCAD.Rotation()
                )
                job.Stock = new_stock
                
                # Set the post processor chosen in the options dialog
                if post_processor:
                    try:
                        job.PostProcessor = post_processor
                        job.PostProcessorOutputFile = ""  # Will use default naming
                    except Exception as e:
                        nw_logger.warn(f"Could not set post processor '{post_processor}': {e}")
                
                # Organize the base objects into a group to clean up the tree
                try:
                    group_name = f"CAM_Geometry_{job_key}"
                    # Check if group already exists (unlikely given new job each time, but good practice)
                    cam_group = self.doc.getObject(group_name)
                    if not cam_group:
                        cam_group = self.doc.addObject("App::DocumentObjectGroup", group_name)
                        cam_group.Label = f"CAM Geometry ({job_key})"
                    
                    for model in all_models:
                         cam_group.addObject(model)
                         # Ensure individual models are visible
                         if hasattr(model, 'ViewObject') and model.ViewObject:
                             model.ViewObject.Visibility = True
                    
                    # Create a parent group for the sheet's CAM artifacts
                    parent_group_name = f"CAM_Sheet_{group_key}"
                    parent_group = self.doc.getObject(parent_group_name)
                    if not parent_group:
                        parent_group = self.doc.addObject("App::DocumentObjectGroup", parent_group_name)
                        parent_group.Label = f"CAM ({group_key})"
                    
                    # Add job and geometry group to parent
                    parent_group.addObject(job)
                    parent_group.addObject(cam_group)
                    
                    # Ensure the geometry group is visible so user can see what's being cut
                    if hasattr(cam_group, 'ViewObject') and cam_group.ViewObject:
                        cam_group.ViewObject.Visibility = True
                        
                except Exception as e:
                    nw_logger.warn(f"Could not group CAM geometry: {e}")

                # Recompute to finalize the job
                self.doc.recompute()
                
                nw_logger.info(f"Created CAM job '{job.Label}' for {job_key} (stock: {sheet_width}x{sheet_height}x{sheet_thickness}mm)")
            else:
                nw_logger.error("Failed to create CAM job.")
                
        except Exception as e:
            nw_logger.error(f"Error creating CAM job: {e}")
            import traceback
            traceback.print_exc()

