from PySide import QtGui
import FreeCAD
import Part
import copy

from .algorithms import nesting_strategy
from .visualization_manager import VisualizationManager

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

# Global manager for visualization state
viz_manager = VisualizationManager()

def _visualize_trial_placement(part, angle, x, y):
    """Draws the boundary polygon at a trial position during simulation."""
    doc = FreeCAD.ActiveDocument
    if not doc or not FreeCAD.GuiUp:
        return
    
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
            
            # Use the visualization manager to draw
            viz_manager.draw_trial_placement(doc, wire)
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"[nesting_logic] Draw failed: {e}\n")

def _cleanup_trial_viz():
    """Removes the trial visualization object and simulation sheet boundaries."""
    doc = FreeCAD.ActiveDocument
    viz_manager.clear_trial_placement(doc)

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

def _on_part_start(part):
    """Called when starting to place a part - highlight the master shape's boundary if it's a new master."""
    master_container = _find_master_container_for_part(part)
    if master_container:
        viz_manager.highlight_master(master_container)

def _on_part_end(part, placed):
    """Called after part is placed - we don't unhighlight here, we wait for a new master type."""
    # Don't unhighlight here - keep it on until we switch to a different master
    pass

def _cleanup_highlighting():
    """Called after nesting completes to ensure all highlighting is removed."""
    viz_manager.clear_highlight()

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
        **kwargs: Additional arguments for the nester (including progress_callback)
    """
    from ...datatypes.shape import Shape
    
    # Extract progress callback if present (not strictly needed as it goes into kwargs, but good for clarity)
    # progress_callback = kwargs.get('progress_callback')
    
    # Only clear NFP cache if explicitly requested by the user (expensive to recompute)
    if kwargs.pop('clear_nfp_cache', False):
        Shape.clear_nfp_cache()
    
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
        kwargs['trial_callback'] = _visualize_trial_placement
        kwargs['part_start_callback'] = _on_part_start
        kwargs['part_end_callback'] = _on_part_end

    # The controller now passes a fresh list of all parts to be nested.
    nester = nesting_strategy.Nester(width, height, rotation_steps, **kwargs)

    # If simulation is enabled, pass a callback that can draw the sheet state.
    if simulate:
        nester.update_callback = lambda part, sheet: (sheet.draw(FreeCAD.ActiveDocument, {}, transient_part=part), QtGui.QApplication.processEvents())

    import time
    start_time = time.monotonic()
    result = nester.nest(parts_to_process)
    elapsed = time.monotonic() - start_time
    
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
    _calculate_efficiency(sheets, verbose=kwargs.get('verbose', False))

    return sheets, unplaced, steps, elapsed

def _calculate_efficiency(sheets, verbose=False):
    """Calculates and displays sheet packing efficiency."""
    if not sheets:
        return
    
    total_parts_area = 0
    total_sheet_area = 0
    
    if verbose:
        FreeCAD.Console.PrintMessage("\n--- PACKING EFFICIENCY ---\n")
    
    for i, sheet in enumerate(sheets):
        sheet_area = sheet.width * sheet.height
        parts_area = sum(part.shape.area for part in sheet.parts if hasattr(part, 'shape') and part.shape)
        
        total_sheet_area += sheet_area
        total_parts_area += parts_area
        
        if verbose and sheet_area > 0:
            efficiency = (parts_area / sheet_area) * 100
            FreeCAD.Console.PrintMessage(f"  Sheet {i+1}: {efficiency:.1f}% ({parts_area:.0f} / {sheet_area:.0f} mm²)\n")
    
    if total_sheet_area > 0:
        overall_efficiency = (total_parts_area / total_sheet_area) * 100
        if verbose:
            FreeCAD.Console.PrintMessage(f"  Overall: {overall_efficiency:.1f}% ({total_parts_area:.0f} / {total_sheet_area:.0f} mm²)\n")
            FreeCAD.Console.PrintMessage("--------------------------\n")
        else:
            FreeCAD.Console.PrintMessage(f"Packing Efficiency: {overall_efficiency:.1f}%\n")

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
