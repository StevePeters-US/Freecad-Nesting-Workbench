# Nesting/nesting/layout_controller.py

"""
This module contains the LayoutController class, which is responsible for
managing and drawing a complete nesting layout.
"""

import FreeCAD
import Part
import copy
import math

try:
    from shapely.affinity import translate
except ImportError:
    translate = None

class LayoutController:
    """
    Manages the state and representation of a nested layout, including all
    sheets and the parts placed on them.
    """
    def __init__(self, doc, sheets, ui_params, preview_group_name=None, unplaced_parts=None):
        self.doc = doc
        self.sheets = sheets
        self.ui_params = ui_params
        self.preview_group_name = preview_group_name
        self.unplaced_parts = unplaced_parts if unplaced_parts is not None else []

    def calculate_sheet_fills(self):
        """Calculates the fill percentage for each sheet in the layout."""
        return [sheet.calculate_fill_percentage() for sheet in self.sheets]

    def draw(self):
        """
        Creates the final layout group and draws all sheets and their contents.
        """
        if not self.doc:
            return

        # --- Create Final Layout Group and Spreadsheet ---
        group_name = "Layout_000" # Default name
        if self.ui_params.get('edit_mode') and self.ui_params.get('original_layout_name'):
            group_name = self.ui_params['original_layout_name']
        else:
            # Find the next available unique name for the final layout's LABEL.
            base_name, i = "Layout", 0
            existing_labels = [o.Label for o in self.doc.Objects]
            while f"{base_name}_{i:03d}" in existing_labels:
                i += 1
            group_name = f"{base_name}_{i:03d}"

        parent_group = self.doc.addObject("App::DocumentObjectGroup", group_name)
        parent_group.addProperty("App::PropertyBool", "IsStacked", "Nesting").IsStacked = False
        parent_group.addProperty("App::PropertyMap", "OriginalPlacements", "Nesting")

        # Store parameters directly on the layout group object
        parent_group.addProperty("App::PropertyFloat", "SheetWidth", "Nesting").SheetWidth = self.ui_params.get('sheet_w', 0)
        parent_group.addProperty("App::PropertyFloat", "SheetHeight", "Nesting").SheetHeight = self.ui_params.get('sheet_h', 0)
        parent_group.addProperty("App::PropertyFloat", "PartSpacing", "Nesting").PartSpacing = self.ui_params.get('spacing', 0)
        parent_group.addProperty("App::PropertyFile", "FontFile", "Nesting").FontFile = self.ui_params.get('font_path', '')

        # Store efficiencies in a PropertyMap for cleanliness
        sheet_fills = self.calculate_sheet_fills()
        if sheet_fills:
            parent_group.addProperty("App::PropertyMap", "SheetEfficiencies", "Nesting")
            efficiencies_map = {f"Sheet_{i+1}": f"{eff:.2f}%" for i, eff in enumerate(sheet_fills)}
            parent_group.SheetEfficiencies = efficiencies_map

        spacing = self.ui_params.get('spacing', 0)

        # Iterate through the sheets and delegate drawing to the Sheet object itself
        for sheet in self.sheets:
            sheet_origin = sheet.get_origin(spacing)
            sheet.draw(
                self.doc,
                sheet_origin,
                self.ui_params,
                parent_group=parent_group,
                draw_shape=True, # Always draw the shape in the final layout
                draw_shape_bounds=self.ui_params.get('show_bounds', False)
            )

        self.doc.recompute()

    def _create_compound_wire_from_polygon(self, polygon):
        """
        Creates a Part.Compound containing wires for the exterior and all interiors of a polygon.
        Returns None if the polygon is invalid.
        """
        if not polygon or polygon.is_empty:
            return None

        wires = []
        # Create exterior wire
        exterior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in polygon.exterior.coords]
        if len(exterior_verts) > 2: wires.append(Part.makePolygon(exterior_verts))
        # Create interior wires (holes)
        for interior in polygon.interiors:
            interior_verts = [FreeCAD.Vector(v[0], v[1], 0) for v in interior.coords]
            if len(interior_verts) > 2: wires.append(Part.makePolygon(interior_verts))
        return Part.makeCompound(wires) if wires else None

    def draw_preview(self, sheet_layouts, ui_params, moving_part=None, current_sheet_id=None, grid_info=None):
        """Draws a temporary preview of the nesting process, optimized for animation."""
        if not self.doc or not self.preview_group_name:
            return

        self.ui_params = ui_params # Always use the latest UI params passed in
        # --- Full Redraw Cleanup ---
        # For a full redraw (like in the Genetic algorithm), we must delete the
        # entire old preview group to ensure no artifacts (like old sheets) remain.
        if not moving_part:
            group = self.doc.getObject(self.preview_group_name)
            if group:
                self.doc.removeObject(group.Name)
                self.doc.recompute()

        sheet_w = self.ui_params.get('sheet_w', 0)
        sheet_h = self.ui_params.get('sheet_h', 0)
        spacing = self.ui_params.get('spacing', 0)

        # Find or create the main preview group
        group = self.doc.getObject(self.preview_group_name)
        if not group:
            group = self.doc.addObject("App::DocumentObjectGroup", self.preview_group_name)

        # --- Draw or Update Grid ---
        if grid_info:
            grid_group_name = "preview_grid_lines"
            grid_group = self.doc.getObject(grid_group_name)
            if not grid_group:
                grid_group = self.doc.addObject("App::DocumentObjectGroup", grid_group_name)
                group.addObject(grid_group)
                
                # Draw grid lines for all sheets that might be used
                max_sheets = 10 # Assume a max of 10 sheets for grid preview
                for sheet_id in range(max_sheets):
                    sheet_offset_x = sheet_id * (sheet_w + spacing)
                    for r in range(grid_info['rows'] + 1):
                        y = r * grid_info['cell_h']
                        line = Part.makeLine((sheet_offset_x, y, 0), (sheet_offset_x + sheet_w, y, 0))
                        line_obj = self.doc.addObject("Part::Feature", f"grid_h_{sheet_id}_{r}")
                        line_obj.Shape = line
                        grid_group.addObject(line_obj)
                    for c in range(grid_info['cols'] + 1):
                        x = c * grid_info['cell_w']
                        line = Part.makeLine((sheet_offset_x + x, 0, 0), (sheet_offset_x + x, sheet_h, 0))
                        line_obj = self.doc.addObject("Part::Feature", f"grid_v_{sheet_id}_{c}")
                        line_obj.Shape = line
                        grid_group.addObject(line_obj)

        # --- Incremental Update for Moving Part ---
        if moving_part:
            # In animation, the dict has one entry: {sheet_id: [bounds...]}
            # Use the explicitly passed current_sheet_id for the moving part.
            # This ensures the part is drawn on the correct sheet.
            sheet_id = current_sheet_id if current_sheet_id is not None else next(iter(sheet_layouts.keys()))
            # This block now handles both CREATION and UPDATE of the moving part's preview
            sheet_preview_group_name = f"preview_sheet_group_{sheet_id}"
            sheet_group = self.doc.getObject(sheet_preview_group_name)

            # --- Create Sheet Group and Boundary if it doesn't exist ---
            if not sheet_group:
                sheet_group = self.doc.addObject("App::DocumentObjectGroup", sheet_preview_group_name)
                group.addObject(sheet_group)
                sheet_offset_x = sheet_id * (sheet_w + spacing)
                sheet_obj = self.doc.addObject("Part::Feature", f"preview_sheet_{sheet_id}")
                sheet_obj.Shape = Part.makePlane(sheet_w, sheet_h)
                sheet_obj.Placement = FreeCAD.Placement(FreeCAD.Vector(sheet_offset_x, 0, 0), FreeCAD.Rotation())
                sheet_group.addObject(sheet_obj)
                if FreeCAD.GuiUp: sheet_obj.ViewObject.Transparency = 75

            moving_obj_name = f"preview_part_{moving_part.id}"
            moving_obj = self.doc.getObject(moving_obj_name)
            
            sheet_offset_x = sheet_id * (sheet_w + spacing)
            
            if moving_part.shape_bounds.polygon and translate:
                # Use shapely's translate function directly on the original polygon
                translated_poly = translate(moving_part.shape_bounds.polygon, xoff=sheet_offset_x, yoff=0)
                new_compound_wire = self._create_compound_wire_from_polygon(translated_poly)

                if new_compound_wire:
                    if not moving_obj:
                        # Create the object for the first time
                        moving_obj = self.doc.addObject("Part::Feature", moving_obj_name)
                    
                    # --- Ensure correct parenting ---
                    if sheet_group not in moving_obj.InList:
                        for parent_group in moving_obj.InList:
                            parent_group.removeObject(moving_obj)
                        sheet_group.addObject(moving_obj) # Add to the new, correct group

                    moving_obj.Shape = new_compound_wire

                self.doc.recompute()
                if FreeCAD.GuiUp: FreeCAD.Gui.updateGui()
                return # Exit early, no full redraw needed

        # --- Full Redraw (for initial placement or non-animated updates) ---
        for sheet_id, placed_parts_bounds in sorted(sheet_layouts.items()):
            sheet_preview_group_name = f"preview_sheet_group_{sheet_id}"
            sheet_group = self.doc.getObject(sheet_preview_group_name)
            if not sheet_group:
                sheet_group = self.doc.addObject("App::DocumentObjectGroup", sheet_preview_group_name)
                group.addObject(sheet_group)

                # Draw sheet boundary only when the group is first created
                sheet_offset_x = sheet_id * (sheet_w + spacing)
                sheet_obj = self.doc.addObject("Part::Feature", f"preview_sheet_{sheet_id}")
                sheet_obj.Shape = Part.makePlane(sheet_w, sheet_h)
                sheet_obj.Placement = FreeCAD.Placement(FreeCAD.Vector(sheet_offset_x, 0, 0), FreeCAD.Rotation())
                sheet_group.addObject(sheet_obj)
                if FreeCAD.GuiUp:
                    sheet_obj.ViewObject.Transparency = 75

            # Draw the newly placed part (which is the last one in the list)
            if placed_parts_bounds:
                part_bounds = placed_parts_bounds[-1]
                # During a full redraw, there is no moving part, so we create a static ID.
                # This was the source of the bug when animation logic fell through.
                part_id = f"static_{sheet_id}_{len(placed_parts_bounds)-1}"
                part_obj_name = f"preview_part_{part_id}"

                if not self.doc.getObject(part_obj_name):
                    sheet_offset_x = sheet_id * (sheet_w + spacing)
                    if part_bounds.polygon and translate:
                        # Use shapely's translate function directly on the original polygon
                        translated_poly = translate(part_bounds.polygon, xoff=sheet_offset_x, yoff=0)
                        
                        bound_compound = self._create_compound_wire_from_polygon(translated_poly)
                        bound_obj = self.doc.addObject("Part::Feature", part_obj_name)
                        if bound_compound: bound_obj.Shape = bound_compound
                        sheet_group.addObject(bound_obj)

        self.doc.recompute()
        if FreeCAD.GuiUp:
            FreeCAD.Gui.updateGui()