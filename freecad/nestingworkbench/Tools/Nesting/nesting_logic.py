# SPDX-License-Identifier: LGPL-2.1-or-later
from PySide import QtCore, QtWidgets
import FreeCAD
import FreeCADGui
import Part
import copy

from .algorithms import nesting_strategy
from .algorithms import physics_nester
from .visualization_manager import VisualizationManager
from freecad.nestingworkbench.ui_helpers import show_warning_dialog

class _MainThreadRelay(QtCore.QObject):
    """QObject that lives on the main thread.
    Callables posted from worker threads run on the main thread, in order.

    The relay keeps its own FIFO and never runs one callable inside another.
    Simulation callbacks call FreeCADGui.updateGui(), which processes pending
    events — with a plain queued signal per callable, that ran the next posted
    callable *inside* the current one, which called updateGui() again, and so
    on. A worker posting faster than the main thread drew (a warm NFP cache on
    a re-nest) nested one level per queued callable until the main thread's
    stack overflowed and FreeCAD died without a Python traceback.
    """
    _kick = QtCore.Signal()

    def __init__(self):
        super().__init__()
        import collections, threading
        self._queue = collections.deque()
        self._lock = threading.Lock()
        self._draining = False
        # QueuedConnection guarantees the slot runs on this object's (main) thread
        self._kick.connect(self._drain, QtCore.Qt.QueuedConnection)

    def _drain(self):
        if self._draining:
            return  # Re-entered from updateGui(); the outer drain continues the queue
        self._draining = True
        try:
            while True:
                with self._lock:
                    if not self._queue:
                        return
                    fn = self._queue.popleft()
                try:
                    fn()
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"[MainThreadRelay] Callback error: {e}\n")
        finally:
            self._draining = False

    def post(self, fn):
        """Post callable to be executed on the main thread."""
        with self._lock:
            self._queue.append(fn)
        self._kick.emit()

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

def _latest_only_main_thread_wrapper(fn):
    """Like _main_thread_wrapper, but at most one call is ever queued.

    For previews where only the newest state matters: the worker tries
    candidate positions far faster than the main thread can draw them, and
    queueing every one leaves the preview replaying stale positions long
    after the nester has moved on. Newer arguments replace the pending ones.
    """
    import threading
    lock = threading.Lock()
    state = {'args': None, 'queued': False}

    def drain():
        with lock:
            args, kwargs = state['args']
            state['args'] = None
            state['queued'] = False
        fn(*args, **kwargs)

    def wrapper(*args, **kwargs):
        app = QtWidgets.QApplication.instance()
        from unittest.mock import Mock
        if not app or isinstance(app, Mock) or QtCore.QThread.currentThread() == app.thread():
            fn(*args, **kwargs)
            return
        with lock:
            state['args'] = (args, kwargs)
            if state['queued']:
                return
            state['queued'] = True
        _main_thread_relay.post(drain)
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

# Global manager removed to improve testability and thread safety

def _sheet_origin_xy(sheet):
    """Returns the (x, y) world origin of `sheet`, or (0, 0) if unavailable.

    `Sheet.get_origin` returns a FreeCAD.Vector when FreeCAD is present and a
    plain tuple otherwise, so accept both.
    """
    if sheet is None:
        return 0.0, 0.0
    try:
        origin = sheet.get_origin()
    except Exception:
        return 0.0, 0.0
    if origin is None:
        return 0.0, 0.0
    if hasattr(origin, 'x'):
        return origin.x, origin.y
    return origin[0], origin[1]

def _visualize_trial_placement(part, angle, x, y, sheet, viz_manager):
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
            # x/y are sheet-local — offset by the sheet origin so the preview is
            # drawn over the sheet being packed, not always over sheet 1.
            ox, oy = _sheet_origin_xy(sheet)
            translated_poly = translate(rotated_poly, xoff=x - cx + ox, yoff=y - cy + oy)
            
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
    """Returns the bound master container for *part*.

    Uses the instance binding because FreeCAD adds 001-style suffixes to duplicate
    labels, making exact-label document searches resolve to the wrong layout.
    """
    return getattr(part, 'master_container', None)

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

def _hide_sim_outlines(parts):
    """Hides each part's green BoundaryObject after simulation.

    Simulation leaves outlines visible; they must be hidden explicitly so outlines
    do not stack across GA members. The winning layout redraws them if requested.
    """
    for part in parts or []:
        fc_object = getattr(part, 'fc_object', None)
        boundary = getattr(fc_object, 'BoundaryObject', None)
        view_object = getattr(boundary, 'ViewObject', None)
        if view_object is not None:
            view_object.Visibility = False

def _bind_sim_callbacks(kwargs, viz_manager):
    """Attach trial/part-start/part-end callbacks; returns the viz_manager to use."""
    if viz_manager is None:
        viz_manager = VisualizationManager()
    kwargs['trial_callback'] = _latest_only_main_thread_wrapper(
        lambda p, a, x, y, sheet: _visualize_trial_placement(p, a, x, y, sheet, viz_manager)
    )
    kwargs['part_start_callback'] = _main_thread_wrapper(
        lambda p: _on_part_start(p, viz_manager)
    )
    kwargs['part_end_callback'] = lambda p, pl: _on_part_end(p, pl, viz_manager)
    return viz_manager

def _bind_sim_update(nester):
    """Attach the per-placement redraw callback."""
    def update_sim_view(part, sheet):
        doc = FreeCAD.ActiveDocument
        if doc:
            sheet.draw(doc, {}, transient_part=part)
            doc.recompute()
        FreeCADGui.updateGui()
    nester.update_callback = _main_thread_wrapper(update_sim_view)

def _teardown_sim(viz_manager, parts):
    """Main-thread cleanup of trial viz, highlighting and sim outlines.

    These access FreeCAD doc objects and ViewObject — must run on main thread.
    """
    _main_thread_wrapper(lambda: _cleanup_trial_viz(viz_manager))()
    _main_thread_wrapper(lambda: _cleanup_highlighting(viz_manager))()
    _main_thread_wrapper(lambda: _hide_sim_outlines(parts))()

def nest(parts, width, height, rotation_steps=1, simulate=False, algorithm='Minkowski', viz_manager=None, **kwargs):
    """
    Convenience function to run the nesting algorithm.
    """

    sort = kwargs.pop('sort', True)
    # Optional out-param: a list the nester's consumption order is
    # appended to, so the GA can record a chromosome that reproduces
    # the layout it describes without re-deriving the nester's sort.
    order_out = kwargs.pop('order_out', None)
    parts_to_process = parts if simulate else copy.deepcopy(parts)

    steps = 0
    sheets = []
    unplaced = []

    if not SHAPELY_AVAILABLE:
        show_shapely_installation_instructions()
        raise NestingDependencyError("The selected algorithm requires the 'Shapely' library.")

    # If simulation is enabled, ensure we have a viz_manager and bind callbacks
    if simulate:
        viz_manager = _bind_sim_callbacks(kwargs, viz_manager)

    if algorithm == 'Physics':
        nester = physics_nester.PhysicsNester(width, height, rotation_steps, **kwargs)
    else:
        nester = nesting_strategy.Nester(width, height, rotation_steps, **kwargs)

    if simulate:
        _bind_sim_update(nester)

    import time
    start_time = time.monotonic()
    result = nester.nest(parts_to_process, sort=sort)
    elapsed = time.monotonic() - start_time
    if order_out is not None:
        order_out.extend(getattr(nester, 'last_consumption_order', []))
    
    if simulate:
        _teardown_sim(viz_manager, parts_to_process)
    
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
        viz_manager = _bind_sim_callbacks(kwargs, viz_manager)

    nester = nesting_strategy.Nester(width, height, rotation_steps, **kwargs)

    if simulate:
        _bind_sim_update(nester)

    import time
    start_time = time.monotonic()
    ordered_fill = sorted(fill_parts, key=lambda p: p.area, reverse=True)
    unplaced = []
    nester._nest_fill_parts(sheets, ordered_fill, unplaced,
                            quiet=kwargs.get('quiet', False))
    elapsed = time.monotonic() - start_time

    if simulate:
        _teardown_sim(viz_manager, ordered_fill)

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
            "   `\"<path_to_python_exe>\" -m pip install shapely`\n\n"
            "   (Alternatively, you can install 'shapely' using your system package manager).\n\n"
            "After installation, please restart FreeCAD."
        )

    show_warning_dialog(
        None,
        "Shapely Library Not Found",
        "The selected nesting algorithm requires the 'Shapely' library, but it is not installed.",
        informative_text=informative_text,
    )
