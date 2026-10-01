# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
import Part
from freecad.nestingworkbench import nw_logger

class VisualizationManager:
    """
    Manages global visualization state for nesting operations,
    including trial placement drawing and master shape highlighting.
    """
    def __init__(self):
        self._trial_viz_obj = None
        self._highlighted_master = None
        self._member_rows = {}

    def draw_trial_placement(self, doc, bounds):
        """
        Draws a trial placement boundary in the FreeCAD document.
        
        Args:
            doc: The active FreeCAD document
            bounds: A Part.Shape (typically a wire) or a list of FreeCAD.Vectors 
                   representing the boundary to visualize.
        """
        if not doc or not FreeCAD.GuiUp:
            return

        # Get or create the trial visualization object
        if self._trial_viz_obj is None or self._trial_viz_obj.Name not in [o.Name for o in doc.Objects]:
            self._trial_viz_obj = doc.addObject("Part::Feature", "TrialBounds")
            if hasattr(self._trial_viz_obj, "ViewObject"):
                self._trial_viz_obj.ViewObject.LineColor = (0.0, 0.5, 1.0)  # Blue
                self._trial_viz_obj.ViewObject.LineWidth = 1.5
                self._trial_viz_obj.ViewObject.Transparency = 50
        
        try:
            if isinstance(bounds, Part.Shape):
                self._trial_viz_obj.Shape = bounds
            elif isinstance(bounds, list):
                # Assume list of vectors
                wire = Part.makePolygon(bounds)
                self._trial_viz_obj.Shape = wire
            
            # Force UI update to show the change immediately during simulation
            FreeCADGui.updateGui()
        except Exception as e:
            nw_logger.warn(f"[VisualizationManager] Draw failed: {e}")

    def clear_trial_placement(self, doc):
        """
        Removes the trial visualization object and any simulation-related 
        boundary objects from the document.
        """
        if self._trial_viz_obj:
            try:
                if doc and self._trial_viz_obj.Name in [o.Name for o in doc.Objects]:
                    doc.removeObject(self._trial_viz_obj.Name)
            except Exception as e:
                nw_logger.debug(f"[VisualizationManager] removeObject failed: {e}")
            self._trial_viz_obj = None
        
        # Clean up simulation sheet boundaries (ported from nesting_logic)
        try:
            if doc:
                to_remove = [o.Name for o in doc.Objects if o.Label.startswith("sim_sheet_boundary_")]
                for name in to_remove:
                    try:
                        doc.removeObject(name)
                    except Exception as e:
                        nw_logger.warn(f"[VisualizationManager] Cleanup failed: {e}")
        except Exception as e:
            nw_logger.debug(f"[VisualizationManager] simulation boundary cleanup failed during teardown: {e}")

    def highlight_master(self, obj):
        """
        Highlights a master container object by making its boundary visible.
        Only one master can be highlighted at a time.
        """
        # Only switch highlighting if it's a different master
        if self._highlighted_master != obj:
            # Unhighlight the previous master
            if self._highlighted_master:
                self._set_highlight(self._highlighted_master, False)
            
            # Highlight the new master
            if obj:
                self._set_highlight(obj, True)
                self._highlighted_master = obj
                FreeCADGui.updateGui()

    def clear_highlight(self):
        """Removes the highlight from the currently highlighted master container."""
        if self._highlighted_master:
            self._set_highlight(self._highlighted_master, False)
            self._highlighted_master = None

    def _set_highlight(self, master_container, highlight):
        """
        Internal helper to set the highlight state of a master container's 
        boundary object.
        """
        try:
            children = master_container.Group if master_container else []
        except (AttributeError, ReferenceError, RuntimeError) as e:
            nw_logger.debug(f"[VisualizationManager] Container group access skipped: {e}")
            return  # Container belonged to a layout that has since been deleted
        for child in children:
            if hasattr(child, "BoundaryObject") and child.BoundaryObject:
                boundary = child.BoundaryObject
                if hasattr(boundary, "ViewObject"):
                    boundary.ViewObject.Visibility = True
                    if highlight:
                        boundary.ViewObject.LineColor = (0.0, 0.8, 0.0)  # Green
                        boundary.ViewObject.LineWidth = 3.0
                    else:
                        # Every master outline stays on screen while the panel is
                        # open; un-highlighting only drops the glow. The panel
                        # hides the whole row when it closes.
                        boundary.ViewObject.LineColor = (0.0, 0.7, 0.0)
                        boundary.ViewObject.LineWidth = 2.0

    def draw_member_rows(self, doc, rows):
        """rows: {row: (sheet_rects, part_outlines)} of Shapely polygons.

        Rows not in `rows` keep their last shape. Main thread only.
        """
        if not doc or not FreeCAD.GuiUp:
            return
        for row, (rects, outlines) in rows.items():
            wires = [Part.makePolygon([FreeCAD.Vector(x, y, 0) for x, y in p.exterior.coords])
                     for p in list(rects) + list(outlines)]
            obj = self._member_rows.get(row)
            if obj is None or obj.Name not in [o.Name for o in doc.Objects]:
                obj = doc.addObject("Part::Feature", "SimMember")
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.LineColor = (0.0, 0.6, 0.2)
                    obj.ViewObject.LineWidth = 1.0
                self._member_rows[row] = obj
            obj.Shape = Part.makeCompound(wires) if wires else Part.Shape()

    def clear_member_rows(self, doc):
        """Deletes every preview row object. Safe to call twice."""
        for obj in self._member_rows.values():
            try:
                if doc and doc.getObject(obj.Name):
                    doc.removeObject(obj.Name)
            except Exception as e:
                nw_logger.debug(f"[VisualizationManager] preview row already gone: {e}")
        self._member_rows = {}
