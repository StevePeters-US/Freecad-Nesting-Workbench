from PySide import QtGui
import FreeCAD
import Part
import copy

from .algorithms import nesting_strategy

class NestingDependencyError(Exception):
    """Custom exception for missing optional dependencies like Shapely."""
    pass

try:
    # Check for shapely availability without importing specific functions
    import shapely
    from shapely.affinity import rotate, translate
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

# Global reference for trial visualization object
_trial_viz_obj = None

def _draw_trial_bounds(part, angle, x, y):
    """Draws the boundary polygon at a trial position during simulation."""
    global _trial_viz_obj
    
    doc = FreeCAD.ActiveDocument
    if not doc or not FreeCAD.GuiUp:
        return
    
    # Get or create the trial visualization object
    if _trial_viz_obj is None or _trial_viz_obj.Name not in [o.Name for o in doc.Objects]:
        _trial_viz_obj = doc.addObject("Part::Feature", "TrialBounds")
        if hasattr(_trial_viz_obj, "ViewObject"):
            _trial_viz_obj.ViewObject.LineColor = (0.0, 0.5, 1.0)  # Blue
            _trial_viz_obj.ViewObject.LineWidth = 1.5
            _trial_viz_obj.ViewObject.Transparency = 50
    
    try:
        # Get the boundary polygon from the part
        if hasattr(part, 'polygon') and part.polygon:
            # Rotate and translate the polygon to the trial position
            rotated_poly = rotate(part.polygon, angle, origin='centroid')
            translated_poly = translate(rotated_poly, xoff=x, yoff=y)
            
            # Convert shapely polygon to FreeCAD wire
            coords = list(translated_poly.exterior.coords)
            points = [FreeCAD.Vector(c[0], c[1], 0) for c in coords]
            wire = Part.makePolygon(points)
            _trial_viz_obj.Shape = wire
            
            # Force UI update
            QtGui.QApplication.processEvents()
    except Exception as e:
        pass  # Silently ignore drawing errors

def _cleanup_trial_viz():
    """Removes the trial visualization object and simulation sheet boundaries."""
    global _trial_viz_obj
    if _trial_viz_obj:
        try:
            doc = FreeCAD.ActiveDocument
            if doc and _trial_viz_obj.Name in [o.Name for o in doc.Objects]:
                doc.removeObject(_trial_viz_obj.Name)
        except:
            pass
        _trial_viz_obj = None
    
    # Clean up simulation sheet boundaries
    try:
        doc = FreeCAD.ActiveDocument
        if doc:
            to_remove = [o.Name for o in doc.Objects if o.Label.startswith("sim_sheet_boundary_")]
            for name in to_remove:
                try:
                    doc.removeObject(name)
                except:
                    pass
    except:
        pass

# --- Master Shape Highlighting ---
_current_highlighted_master = None  # Track the currently highlighted master container

def _find_master_container_for_part(part):
    """Finds the master container corresponding to a part being placed."""
    doc = FreeCAD.ActiveDocument
    if not doc:
        return None
    
    # Get the base label (e.g., "O" from "O_1")
    base_label = part.id.rsplit('_', 1)[0] if '_' in part.id else part.id
    
    # Try both temp_master_ (during nesting) and master_ prefixes
    master_names = [f"temp_master_{base_label}", f"master_{base_label}"]
    
    # Search in Layout_temp first (active nesting), then other layouts
    for obj in doc.Objects:
        try:
            if hasattr(obj, "Group") and (obj.Label.startswith("Layout_temp") or obj.Label.startswith("Layout")):
                for child in obj.Group:
                    if child.Label == "MasterShapes" and hasattr(child, "Group"):
                        for master in child.Group:
                            if master.Label in master_names:
                                return master
        except RuntimeError:
            # Object might be deleted/invalid, skip it
            continue
    return None

def _highlight_master(master_container, highlight):
    """Sets the highlighting state for a master container's boundary."""
    if master_container and hasattr(master_container, "Group"):
        for child in master_container.Group:
            if hasattr(child, "BoundaryObject") and child.BoundaryObject:
                boundary = child.BoundaryObject
                if hasattr(boundary, "ViewObject"):
                    if highlight:
                        boundary.ViewObject.Visibility = True
                        boundary.ViewObject.LineColor = (0.0, 0.8, 0.0)  # Green
                        boundary.ViewObject.LineWidth = 3.0
                    else:
                        boundary.ViewObject.Visibility = False
                        boundary.ViewObject.LineColor = (1.0, 0.0, 0.0)  # Red
                        boundary.ViewObject.LineWidth = 2.0

def _on_part_start(part):
    """Called when starting to place a part - highlight the master shape's boundary if it's a new master."""
    global _current_highlighted_master
    
    master_container = _find_master_container_for_part(part)
    if master_container:
        # Only switch highlighting if it's a different master
        if _current_highlighted_master != master_container:
            # Unhighlight the previous master
            if _current_highlighted_master:
                _highlight_master(_current_highlighted_master, False)
            # Highlight the new master
            _highlight_master(master_container, True)
            _current_highlighted_master = master_container
            QtGui.QApplication.processEvents()

def _on_part_end(part, placed):
    """Called after part is placed - we don't unhighlight here, we wait for a new master type."""
    # Don't unhighlight here - keep it on until we switch to a different master
    pass

def _cleanup_highlighting():
    """Called after nesting completes to ensure all highlighting is removed."""
    global _current_highlighted_master
    if _current_highlighted_master:
        _highlight_master(_current_highlighted_master, False)
        _current_highlighted_master = None

# --- Public Function ---
def nest(parts, width, height, rotation_steps=1, simulate=False, **kwargs):
    """
    Convenience function to run the nesting algorithm.
    
    Args:
        parts: List of Shape objects to nest
        width: Sheet width
        height: Sheet height
        rotation_steps: Number of rotation steps
        simulate: If True, shows simulation with callbacks
        **kwargs: Additional arguments for the nester
    """
    global _trial_viz_obj
    from ...datatypes.shape import Shape
    
    # Clear NFP cache to ensure fresh calculations
    with Shape.nfp_cache_lock:
        Shape.nfp_cache.clear()
    
    # If simulation is enabled, the nester needs the original list of parts
    # that are linked to the visible FreeCAD objects (fc_object).
    # If simulation is disabled, we MUST use a deepcopy to prevent the nester
    # from modifying the original part objects that the controller will use for
    # the final drawing step.
    parts_to_process = parts if simulate else copy.deepcopy(parts)

    steps = 0
    sheets = []
    unplaced = []

    if not SHAPELY_AVAILABLE:
        show_shapely_installation_instructions()
        raise NestingDependencyError("The selected algorithm requires the 'Shapely' library, which is not installed.")

    # If simulation is enabled, add callbacks to kwargs
    if simulate:
        kwargs['trial_callback'] = _draw_trial_bounds
        kwargs['part_start_callback'] = _on_part_start
        kwargs['part_end_callback'] = _on_part_end

    # The controller now passes a fresh list of all parts to be nested.
    nester = nesting_strategy.Nester(width, height, rotation_steps, **kwargs)

    # If simulation is enabled, pass a callback that can draw the sheet state.
    if simulate:
        nester.update_callback = lambda part, sheet: (sheet.draw(FreeCAD.ActiveDocument, {}, transient_part=part), QtGui.QApplication.processEvents())

    result = nester.nest(parts_to_process)
    
    # Cleanup trial visualization and highlighting
    if simulate:
        _cleanup_trial_viz()
        _cleanup_highlighting()
    
    # Some nesters may return a 3-tuple (sheets, unplaced, steps), while others
    # may return a 2-tuple (sheets, unplaced). We handle both cases here.
    if len(result) == 3:
        sheets, unplaced, steps = result
    else:
        sheets, unplaced = result

    # Calculate and display packing efficiency
    _calculate_efficiency(sheets)

    return sheets, unplaced, steps

def _calculate_efficiency(sheets):
    """Calculates and displays sheet packing efficiency."""
    if not sheets:
        return
    
    total_parts_area = 0
    total_sheet_area = 0
    
    FreeCAD.Console.PrintMessage("\n--- PACKING EFFICIENCY ---\n")
    
    for i, sheet in enumerate(sheets):
        sheet_area = sheet.width * sheet.height
        parts_area = sum(part.shape.area for part in sheet.parts if hasattr(part, 'shape') and part.shape)
        
        total_sheet_area += sheet_area
        total_parts_area += parts_area
        
        if sheet_area > 0:
            efficiency = (parts_area / sheet_area) * 100
            FreeCAD.Console.PrintMessage(f"  Sheet {i+1}: {efficiency:.1f}% ({parts_area:.0f} / {sheet_area:.0f} mm²)\n")
    
    if total_sheet_area > 0:
        overall_efficiency = (total_parts_area / total_sheet_area) * 100
        FreeCAD.Console.PrintMessage(f"  Overall: {overall_efficiency:.1f}% ({total_parts_area:.0f} / {total_sheet_area:.0f} mm²)\n")
    
    FreeCAD.Console.PrintMessage("--------------------------\n")

def show_shapely_installation_instructions():
    msg_box = QtGui.QMessageBox()
    msg_box.setIcon(QtGui.QMessageBox.Warning)
    msg_box.setWindowTitle("Shapely Library Not Found")
    msg_box.setText("The selected nesting algorithm requires the 'Shapely' library, but it is not installed.")
    msg_box.setInformativeText(
        "To use this algorithm, you need to install the 'shapely' library into FreeCAD's Python environment.\n\n"
        "1. **Find FreeCAD's Python Executable:**\n"
        "   Open the Python console in FreeCAD and run:\n"
        "   `import sys; print(sys.executable)`\n"
        "   Copy the path that is printed.\n\n"
        "2. **Open a Command Prompt:**\n"
        "   Open a Windows Command Prompt (cmd.exe).\n\n"
        "3. **Install Shapely:**\n"
        "   In the command prompt, use the path you copied to run the following command (don't forget the quotes):\n"
        "   `\"<path_to_python_exe>\" -m pip install shapely`\n\n"
        "After installation, please restart FreeCAD."
    )
    msg_box.setStandardButtons(QtGui.QMessageBox.Ok)
    msg_box.exec_()
