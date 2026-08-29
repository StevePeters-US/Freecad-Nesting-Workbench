# SPDX-License-Identifier: LGPL-2.1-or-later
from PySide import QtCore, QtWidgets
import FreeCAD
import FreeCADGui
import Part
import copy

from .algorithms import nesting_strategy
from .algorithms import physics_nester
from .visualization_manager import VisualizationManager

class _MainThreadRelay(QtCore.QObject):
    """QObject that lives on the main thread.
    Signals emitted from worker threads are queued and execute on the main thread.
    """
    _fn_signal = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        # QueuedConnection guarantees the slot runs on this object's (main) thread
        self._fn_signal.connect(self._run, QtCore.Qt.QueuedConnection)

    def _run(self, fn):
        try:
            fn()
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"[MainThreadRelay] Callback error: {e}\n")

    def post(self, fn):
        """Post callable to be executed on the main thread."""
        self._fn_signal.emit(fn)

# Created once at module load time (always the main thread)
_main_thread_relay = _MainThreadRelay()

def _main_thread_wrapper(fn):
    """Wraps a callback so it always executes on the main thread.

    Uses a relay QObject signal (QueuedConnection) so the callable is
    correctly posted to the main thread's event loop regardless of which
    thread calls the wrapper.
    """
    def wrapper(*args, **kwargs):
        app = QtWidgets.QApplication.instance()
        # Handle mocked test environment where PySide/QtGui is a MagicMock
        from unittest.mock import Mock
        if not app or isinstance(app, Mock):
            fn(*args, **kwargs)
            return

        if QtCore.QThread.currentThread() == app.thread():
            fn(*args, **kwargs)
        else:
            _main_thread_relay.post(lambda: fn(*args, **kwargs))
    return wrapper

class NestingDependencyError(Exception):
# ... (rest of class)
    pass

try:
    # Check for shapely availability without importing specific functions
    import shapely
    from shapely.affinity import rotate, translate
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

# Global manager removed to improve testability and thread safety (CR-118)

def _visualize_trial_placement(part, angle, x, y, viz_manager):
# ... (rest of function)
    if not viz_manager: return
    doc = FreeCAD.ActiveDocument
    if not doc or not FreeCAD.GuiUp:
        return
    
    try:
        # Get the boundary polygon from the part
        if hasattr(part, 'polygon') and part.polygon:
            # Rotate and translate the polygon to the trial position.
            # x,y is the target centroid position; translate by the delta from current centroid.
            rotated_poly = rotate(part.polygon, angle, origin='centroid')
            cx, cy = rotated_poly.centroid.x, rotated_poly.centroid.y
            translated_poly = translate(rotated_poly, xoff=x - cx, yoff=y - cy)
            
            # Convert shapely polygon to FreeCAD wire
            coords = list(translated_poly.exterior.coords)
            points = [FreeCAD.Vector(c[0], c[1], 0) for c in coords]
            wire = Part.makePolygon(points)
            
            # Use the visualization manager to draw
            viz_manager.draw_trial_placement(doc, wire)
    except Exception as e:
        FreeCAD.Console.PrintWarning(f"[nesting_logic] Draw failed: {e}\n")

def _cleanup_trial_viz(viz_manager):
# ... (rest of function)
    if not viz_manager: return
    doc = FreeCAD.ActiveDocument
    viz_manager.clear_trial_placement(doc)

def _find_master_container_for_part(part):
# ... (rest of function)
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

def _on_part_start(part, viz_manager):
# ... (rest of function)
    if not viz_manager: return
    master_container = _find_master_container_for_part(part)
    if master_container:
        viz_manager.highlight_master(master_container)

def _on_part_end(part, placed, viz_manager):
# ... (rest of function)
    pass

def _cleanup_highlighting(viz_manager):
# ... (rest of function)
    if viz_manager:
        viz_manager.clear_highlight()

def nest(parts, width, height, rotation_steps=1, simulate=False, algorithm='Minkowski', viz_manager=None, **kwargs):
    """
    Convenience function to run the nesting algorithm.
    """

    sort = kwargs.pop('sort', True)
    parts_to_process = parts if simulate else copy.deepcopy(parts)

    steps = 0
    sheets = []
    unplaced = []

    if not SHAPELY_AVAILABLE:
        show_shapely_installation_instructions()
        raise NestingDependencyError("The selected algorithm requires the 'Shapely' library.")

    # If simulation is enabled, ensure we have a viz_manager and bind callbacks
    if simulate:
        if viz_manager is None:
            viz_manager = VisualizationManager()
            
        kwargs['trial_callback'] = _main_thread_wrapper(
            lambda p, a, x, y: _visualize_trial_placement(p, a, x, y, viz_manager)
        )
        kwargs['part_start_callback'] = _main_thread_wrapper(
            lambda p: _on_part_start(p, viz_manager)
        )
        kwargs['part_end_callback'] = lambda p, pl: _on_part_end(p, pl, viz_manager)

    if algorithm == 'Physics':
        nester = physics_nester.PhysicsNester(width, height, rotation_steps, **kwargs)
    else:
        nester = nesting_strategy.Nester(width, height, rotation_steps, **kwargs)

    if simulate:
        def update_sim_view(part, sheet):
            doc = FreeCAD.ActiveDocument
            if doc:
                sheet.draw(doc, {}, transient_part=part)
                doc.recompute()
            FreeCADGui.updateGui()
            
        nester.update_callback = _main_thread_wrapper(update_sim_view)

    import time
    start_time = time.monotonic()
    result = nester.nest(parts_to_process, sort=sort)
    elapsed = time.monotonic() - start_time
    
    if simulate:
        # These access FreeCAD doc objects and ViewObject — must run on main thread
        _main_thread_wrapper(lambda: _cleanup_trial_viz(viz_manager))()
        _main_thread_wrapper(lambda: _cleanup_highlighting(viz_manager))()
    
    if len(result) == 3:
        sheets, unplaced, steps = result
    else:
        sheets, unplaced = result

    _calculate_efficiency(sheets, verbose=kwargs.get('verbose', False))

    return sheets, unplaced, steps, elapsed

def fill_existing_sheets(sheets, fill_parts, width, height, rotation_steps=1,
                         simulate=False, viz_manager=None, **kwargs):
    """Runs only the round-robin fill phase against already-populated sheets.

    Used by the GA coordinator to fill the winning layout once, after the
    generation loop deferred all fill placement. Mutates `sheets` in place
    (appends one sheet only when the run is fill-only and no sheet exists).
    Parts are NOT deep-copied — placements land on the caller's instances.
    Returns (unplaced_fill, elapsed_seconds).
    """
    if not fill_parts:
        return [], 0.0
    if not SHAPELY_AVAILABLE:
        show_shapely_installation_instructions()
        raise NestingDependencyError("The selected algorithm requires the 'Shapely' library.")

    kwargs.pop('sort', None)
    if simulate:
        if viz_manager is None:
            viz_manager = VisualizationManager()
        kwargs['trial_callback'] = _main_thread_wrapper(
            lambda p, a, x, y: _visualize_trial_placement(p, a, x, y, viz_manager)
        )
        kwargs['part_start_callback'] = _main_thread_wrapper(
            lambda p: _on_part_start(p, viz_manager)
        )
        kwargs['part_end_callback'] = lambda p, pl: _on_part_end(p, pl, viz_manager)

    nester = nesting_strategy.Nester(width, height, rotation_steps, **kwargs)

    if simulate:
        def update_sim_view(part, sheet):
            doc = FreeCAD.ActiveDocument
            if doc:
                sheet.draw(doc, {}, transient_part=part)
                doc.recompute()
            FreeCADGui.updateGui()
        nester.update_callback = _main_thread_wrapper(update_sim_view)

    import time
    start_time = time.monotonic()
    ordered_fill = sorted(fill_parts, key=lambda p: p.area, reverse=True)
    unplaced = []
    nester._nest_fill_parts(sheets, ordered_fill, unplaced,
                            quiet=kwargs.get('quiet', False))
    elapsed = time.monotonic() - start_time

    if simulate:
        _main_thread_wrapper(lambda: _cleanup_trial_viz(viz_manager))()
        _main_thread_wrapper(lambda: _cleanup_highlighting(viz_manager))()

    return unplaced, elapsed

def _calculate_efficiency(sheets, verbose=False):
    """Calculates and displays sheet packing efficiency."""
    if not sheets:
        return
    
    from .layout_manager import largest_open_area
    
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
            largest_open = largest_open_area(sheet.parts, sheet.width, sheet.height)
            FreeCAD.Console.PrintMessage(f"  Sheet {i+1}: {efficiency:.1f}% ({parts_area:.0f} / {sheet_area:.0f} mm²), Compactness: {largest_open:.0f} mm² largest open\n")
    
    if total_sheet_area > 0:
        overall_efficiency = (total_parts_area / total_sheet_area) * 100
        if verbose:
            FreeCAD.Console.PrintMessage(f"  Overall: {overall_efficiency:.1f}% ({total_parts_area:.0f} / {total_sheet_area:.0f} mm²)\n")
            FreeCAD.Console.PrintMessage("--------------------------\n")
        else:
            FreeCAD.Console.PrintMessage(f"Packing Efficiency: {overall_efficiency:.1f}%\n")

def show_shapely_installation_instructions():
    import sys
    is_windows = sys.platform.startswith("win")
    
    msg_box = QtWidgets.QMessageBox()
    msg_box.setIcon(QtWidgets.QMessageBox.Warning)
    msg_box.setWindowTitle("Shapely Library Not Found")
    msg_box.setText("The selected nesting algorithm requires the 'Shapely' library, but it is not installed.")
    
    if is_windows:
        informative_text = (
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
    else:
        informative_text = (
            "To use this algorithm, you need to install the 'shapely' library into FreeCAD's Python environment.\n\n"
            "1. **Find FreeCAD's Python Executable:**\n"
            "   Open the Python console in FreeCAD and run:\n"
            "   `import sys; print(sys.executable)`\n"
            "   Copy the path that is printed.\n\n"
            "2. **Open a Terminal:**\n"
            "   Open a terminal window (bash/zsh).\n\n"
            "3. **Install Shapely:**\n"
            "   In the terminal, use the path you copied to run the following command (don't forget the quotes):\n"
            "   `\"<path_to_python_exe>\" -m pip install shapely`\n"
            "   (Alternatively, you can install 'shapely' using your system package manager).\n\n"
            "After installation, please restart FreeCAD."
        )
        
    msg_box.setInformativeText(informative_text)
    msg_box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    msg_box.exec_()
