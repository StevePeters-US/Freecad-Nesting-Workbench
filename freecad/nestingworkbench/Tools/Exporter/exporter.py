# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/Exporter/exporter.py

"""
This module contains the SheetExporter class. Its function is to create 2D
projections of each sheet's layout within the active document.
"""

import FreeCAD
import Part
import os
import importDXF
from freecad.nestingworkbench import nw_logger, nw_settings
from ...constants import (
    PART_BOUND_PREFIXES, PREF_DXF_BOUND_COLOR, PREF_DXF_LABEL_COLOR, PREF_DXF_PART_COLOR,
    PREF_DXF_SHEET_COLOR, SHEET_BOUNDARY_PREFIX,
)
from ...freecad_helpers import find_back_group, find_offcut_group, get_layout_group, get_sheet_groups, get_all_objects_recursive, recursive_delete

# Source-object label prefix -> the setting that colours its DXF layer. Bounds reach
# the DXF only when export_sheets includes them. Anything else (silhouette outlines
# included) keeps the writer's colour 7.
_DXF_COLOR_KEYS = (
    ("part_", PREF_DXF_PART_COLOR),
    ("label_", PREF_DXF_LABEL_COLOR),
    ("boundary_", PREF_DXF_BOUND_COLOR),
    ("bound_", PREF_DXF_BOUND_COLOR),
    (SHEET_BOUNDARY_PREFIX, PREF_DXF_SHEET_COLOR),
)


def dxf_color_for(source_label):
    """The AutoCAD colour index for a source object's layer, or None to leave it as written."""
    for prefix, key in _DXF_COLOR_KEYS:
        if source_label.startswith(prefix):
            return nw_settings.get_dxf_color(key)
    return None


def recolor_dxf_layers(filepath, layer_colors):
    """Set the colour of each LAYER record named in *layer_colors* {layer name: ACI}.

    FreeCAD's DXF writer puts each object on a layer named after the object's
    Name and writes every layer as colour 7, whatever its LineColor. Entities
    carry no colour of their own, so they draw in their layer's. A negative
    colour means the layer is off; the sign is kept. Returns how many layers
    were rewritten; the file is left untouched when that is 0.
    """
    if not layer_colors:
        return 0
    # latin-1 round-trips every byte, so non-ASCII labels survive unchanged.
    with open(filepath, "r", encoding="latin-1", newline="") as f:
        lines = f.read().splitlines(keepends=True)
    changed = 0
    kind = name = color_line = None
    for i in range(0, len(lines) - 1, 2):
        code, value = lines[i].strip(), lines[i + 1].strip()
        if code == "0":
            changed += _recolor_layer(lines, kind, name, color_line, layer_colors)
            kind, name, color_line = value, None, None
        elif kind == "LAYER" and code == "2":
            name = value
        elif kind == "LAYER" and code == "62":
            color_line = i + 1
    changed += _recolor_layer(lines, kind, name, color_line, layer_colors)
    if changed:
        with open(filepath, "w", encoding="latin-1", newline="") as f:
            f.write("".join(lines))
    return changed


def _recolor_layer(lines, kind, name, color_line, layer_colors):
    """Rewrite one LAYER record's colour line in *lines*: 1 if rewritten, else 0."""
    if kind != "LAYER" or name not in layer_colors or color_line is None:
        return 0
    old = lines[color_line]
    sign = -1 if old.strip().startswith("-") else 1
    ending = old[len(old.rstrip("\r\n")):]
    lines[color_line] = f"{sign * layer_colors[name]:>6}{ending}"
    return 1


def _projection_sources(objects_in_sheet, include_bounds=False):
    """(source object, world placement) for every object a sheet's DXF draws.

    A placed part is an App::Part container (nested_*) holding its part_, label_,
    outline_ and boundary_ children. Projecting the container would fuse them
    into one compound on one layer, so each child is projected on its own and
    gets its own layer and colour. A bound is exported only when include_bounds
    is set, and then even if hidden; any other hidden child stays out, as the
    container's compound left it out.
    """
    sources = []
    for obj in objects_in_sheet:
        if obj.isDerivedFrom("App::Part"):
            for child in obj.Group:
                if child.Label.startswith(PART_BOUND_PREFIXES):
                    if not include_bounds:
                        continue
                elif not getattr(child, "Visibility", True):
                    continue
                sources.append((child, obj.Placement.multiply(child.Placement)))
        else:
            sources.append((obj, obj.Placement))
    return sources


class SheetExporter:
    """
    Handles finding the layout group, iterating through sheets, and creating
    a new group containing 2D projections of each sheet's geometry.
    """
    def __init__(self, layout_group=None):
        self.doc = FreeCAD.ActiveDocument
        if layout_group:
            self.layout_group = layout_group
        else:
            self.layout_group = get_layout_group(self.doc)

    def export_sheets(self, export_dir, delete_generated_objects=True, include_bounds=False):
        """Main method to create 2D projections of the layout in a new folder.

        include_bounds: also write each part's bound on its own layer.
        """
        if not self.layout_group:
            nw_logger.info("No valid packed layout found to create views from.")
            return

        sheet_groups = get_sheet_groups(self.layout_group)
        if not sheet_groups:
            nw_logger.info("No sheets found within the layout group.")
            return

        # Create a new top-level folder for the 2D views
        views_folder_name = f"{self.layout_group.Label}_2D_Views"
        
        # If a folder with this name already exists, remove it for a clean slate
        if self.doc.getObject(views_folder_name):
            self.doc.removeObject(views_folder_name)

        views_folder = self.doc.addObject("App::DocumentObjectGroup", views_folder_name)

        # Process each sheet individually
        layer_colors = {}  # sheet view folder Name -> {2D object Name: ACI}
        for sheet_group in sheet_groups:
            # A flipped sheet's back side is not exported (SVF decision 12).
            back = find_back_group(sheet_group)
            back_names = {o.Name for o in get_all_objects_recursive(back)} if back else set()
            # Offcuts are display only (NLA ruling 20).
            offcuts = find_offcut_group(sheet_group)
            offcut_names = {o.Name for o in get_all_objects_recursive(offcuts)} if offcuts else set()
            skip = back_names | offcut_names
            objects_in_sheet = [o for o in get_all_objects_recursive(sheet_group) if o.Name not in skip]
            
            # Filter for only the objects we want to project: no annotations, and no
            # bounds unless the caller asked for them
            objects_to_project = [
                (obj, placement) for obj, placement in _projection_sources(objects_in_sheet, include_bounds)
                if (hasattr(obj, 'Shape') and obj.Shape and
                    not obj.isDerivedFrom("Draft::Text") and
                    (include_bounds or not obj.Label.startswith(PART_BOUND_PREFIXES)))
            ]
            
            if not objects_to_project:
                nw_logger.warn(f"No projectable geometry found in {sheet_group.Label}. Skipping.")
                continue

            # Create a sub-folder for this specific sheet's views
            sheet_view_folder = self.doc.addObject("App::DocumentObjectGroup", f"{sheet_group.Label}_Views")
            views_folder.addObject(sheet_view_folder)
            colors = layer_colors.setdefault(sheet_view_folder.Name, {})

            # Add a 2D projection of each object to the new sub-folder
            failed_objects = []
            for obj, placement in objects_to_project:
                try:
                    # Get the base shape, which is defined at the origin
                    base_shape = obj.Shape.copy()
                    
                    # The DXF writer takes wires, faces and compounds as they are. Anything
                    # else (a 3D part's solid) goes in a compound, as its container's did.
                    if isinstance(base_shape, (Part.Wire, Part.Face, Part.Compound)):
                        shape_2d = base_shape
                    else:
                        # The shape carries its own placement; the 2D object gets the
                        # world placement below, so drop it here or it counts twice.
                        base_shape.Placement = FreeCAD.Placement()
                        shape_2d = Part.makeCompound([base_shape])
                    
                    # Create the new 2D object
                    new_2d_obj = self.doc.addObject("Part::Part2DObject", f"{obj.Name}_2D")
                    new_2d_obj.Shape = shape_2d
                    
                    # Apply the source's world placement
                    new_2d_obj.Placement = placement
                    
                    # Add the new 2D object to this sheet's view folder
                    sheet_view_folder.addObject(new_2d_obj)
                    aci = dxf_color_for(obj.Label)
                    if aci is not None:
                        colors[new_2d_obj.Name] = aci  # the writer names the layer after the Name
                except Exception as e:
                    label = getattr(obj, "Label", obj.Name if hasattr(obj, "Name") else str(obj))
                    failed_objects.append(label)
                    nw_logger.error(f"An error occurred creating 2D view for '{label}' in {sheet_group.Label}: {e}")

            if failed_objects:
                msg = f"Failed to create 2D view for {len(failed_objects)} object(s) in {sheet_group.Label}: {', '.join(failed_objects)}"
                nw_logger.error(f"{msg}")
                if hasattr(FreeCAD, "GuiUp") and FreeCAD.GuiUp:
                    try:
                        from freecad.nestingworkbench.ui_helpers import show_warning_dialog
                        show_warning_dialog(None, "Export Warning", msg)
                    except Exception as e:
                        # Already reported via error above; the dialog is best-effort.
                        nw_logger.debug(f"[SheetExporter] Warning dialog failed: {e}")
            else:
                nw_logger.info(f"Successfully created 2D views for {sheet_group.Label}")

        nw_logger.info(f"Finished creating 2D views in folder: {views_folder.Label}")

        # Export to DXF
        for sheet_view_folder in views_folder.Group:
            if sheet_view_folder.isDerivedFrom("App::DocumentObjectGroup"):
                filename = f"{self.doc.Name}_{sheet_view_folder.Label}.dxf"
                filepath = os.path.join(export_dir, filename)
                importDXF.export(sheet_view_folder.Group, filepath)
                try:
                    recolor_dxf_layers(filepath, layer_colors.get(sheet_view_folder.Name, {}))
                except OSError as e:
                    nw_logger.warn(f"Exported {filepath} but could not set its layer colors: {e}")
                nw_logger.info(f"Exported {sheet_view_folder.Label} to {filepath}")

        # Delete the generated 2D views if requested
        if delete_generated_objects:
            recursive_delete(self.doc, views_folder)
            nw_logger.info("Deleted temporary 2D views folder.")

