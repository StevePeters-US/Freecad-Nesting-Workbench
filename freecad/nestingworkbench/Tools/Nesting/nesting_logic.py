# SPDX-License-Identifier: LGPL-2.1-or-later
from PySide import QtCore, QtWidgets
import FreeCAD
import FreeCADGui
import Part
import copy

from .algorithms import nesting_strategy
from .algorithms import beam_nester
from .algorithms import sheet_sequence
from .visualization_manager import VisualizationManager
from freecad.nestingworkbench import nw_logger
from freecad.nestingworkbench.ui_helpers import show_warning_dialog
from .algorithms.placers import make_placer
from .algorithms.fitness import FitnessWeights, MIN_OFFCUT_DEFAULT
from freecad.nestingworkbench.constants import ALGO_GREEDY

class _MainThreadRelay(QtCore.QObject):
    """QObject that lives on the main thread.
    Callables posted from worker threads run on the main thread, in order.

    The relay keeps its own FIFO and never runs one callable inside another.
    Before NLA-059 the simulation callbacks called FreeCADGui.updateGui(), which processes
    pending events. With a plain queued signal per callable, that ran the next posted
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
        """Run queued callables on the main thread until the queue is empty.

        Sim callbacks no longer call updateGui(). The re-entry guard stays for the
        other main-thread code that still calls updateGui() (ga_coordinator, the
        controller's draw handler), which can otherwise re-enter this drain.
        """
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
                    nw_logger.warn(f"[MainThreadRelay] Callback error: {e}")
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

def _blocking_main_thread_call(fn, cancel_check=None):
    """Run *fn* on the main thread and wait until it has run (NLA-B02).

    From the main thread, or headless, it runs inline. From a worker thread it is posted
    through the relay, so it keeps FIFO order with every earlier sim callback, and the
    worker waits. A cancel stops the wait; the posted call still runs.
    """
    app = QtWidgets.QApplication.instance()
    from unittest.mock import Mock
    if not app or isinstance(app, Mock) or QtCore.QThread.currentThread() == app.thread():
        fn()
        return
    import threading
    done = threading.Event()

    def run():
        try:
            fn()
        finally:
            done.set()

    _main_thread_relay.post(run)
    while not done.wait(0.1):
        if cancel_check is not None and cancel_check():
            return

def _latest_only_main_thread_wrapper(fn):
    """Like _main_thread_wrapper, but at most one call is ever queued.

    For previews where only the newest state matters: the worker tries
    candidate positions far faster than the main thread can draw them, and
    queueing every one leaves the preview replaying stale positions long
    after the nester has moved on. Newer arguments replace the pending ones.
    """
    import threading
    lock = threading.Lock()
    # posted: off-thread calls. dropped: calls that found a drain already queued (NLA-060).
    state = {'args': None, 'queued': False, 'posted': 0, 'dropped': 0}

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
            state['posted'] += 1
            state['args'] = (args, kwargs)
            if state['queued']:
                state['dropped'] += 1
                return
            state['queued'] = True
        _main_thread_relay.post(drain)
    wrapper.stats = state
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
    except Exception as e:
        nw_logger.debug(f"[nesting_logic] sheet.get_origin failed: {e}")
        return 0.0, 0.0
    if origin is None:
        return 0.0, 0.0
    if hasattr(origin, 'x'):
        return origin.x, origin.y
    return origin[0], origin[1]

class _SimFailures:
    """Counts simulate-draw failures in one run (NLA-B02); recorded from the worker or the
    main thread, and reported once at teardown, after the last queued draw."""
    def __init__(self):
        self.count = 0
        self.first = None

    def record(self, error):
        self.count += 1
        if self.first is None:
            self.first = f"{type(error).__name__}: {error}"

    def report(self):
        if self.count:
            nw_logger.warn(f"Simulation: {self.count} preview draw(s) failed; "
                           f"first: {self.first}")

def _snapshot_trial(part, angle, x, y, sheet):
    """World XY coordinates of *part*'s outline at a trial pose, computed on the calling
    (worker) thread, so the main-thread draw never reads the live Shape (NLA-B02). None when
    the part has no polygon."""
    poly = getattr(part, 'polygon', None)
    if poly is None or poly.is_empty:
        return None
    rotated = rotate(poly, angle, origin='centroid')
    # x/y are sheet-local: offset by the sheet origin so the preview sits over that sheet.
    ox, oy = _sheet_origin_xy(sheet)
    moved = translate(rotated, xoff=x - rotated.centroid.x + ox, yoff=y - rotated.centroid.y + oy)
    return tuple(moved.exterior.coords)


def _draw_trial_coords(coords, viz_manager, failures):
    """Main thread: draw a snapshot from _snapshot_trial."""
    doc = FreeCAD.ActiveDocument
    if not viz_manager or not doc or not FreeCAD.GuiUp:
        return
    try:
        wire = Part.makePolygon([FreeCAD.Vector(cx, cy, 0) for cx, cy, *_ in coords])
        viz_manager.draw_trial_placement(doc, wire)
    except Exception as e:
        failures.record(e)


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

def _bind_sim_callbacks(kwargs, viz_manager, failures):
    """Attach trial/part-start/part-end callbacks; returns (viz_manager, trial_stats).

    The trial callback snapshots the outline on the worker thread and posts only its
    coordinates, so the main-thread draw never reads the live Shape (NLA-B02).
    trial_stats is the coalescing wrapper's counters ({'posted', 'dropped'}), read at teardown.
    """
    if viz_manager is None:
        viz_manager = VisualizationManager()
    draw = _latest_only_main_thread_wrapper(
        lambda coords: _draw_trial_coords(coords, viz_manager, failures)
    )

    def trial_callback(p, a, x, y, sheet):
        try:
            coords = _snapshot_trial(p, a, x, y, sheet)
        except Exception as e:
            failures.record(e)
            return
        if coords is not None:
            draw(coords)

    kwargs['trial_callback'] = trial_callback
    kwargs['part_start_callback'] = _main_thread_wrapper(
        lambda p: _on_part_start(p, viz_manager)
    )
    kwargs['part_end_callback'] = lambda p, pl: _on_part_end(p, pl, viz_manager)
    return viz_manager, draw.stats


def _bind_sim_update(nester, failures):
    """Attach the per-placement redraw callback.

    The worker waits until the sheet is drawn, so the sheet can't change under the draw
    (NLA-058). A failed draw is recorded, not raised into the placer.
    """
    def update_sim_view(part, sheet):
        doc = FreeCAD.ActiveDocument
        if not doc:
            return
        try:
            sheet.draw(doc, {}, transient_part=part)
            doc.recompute()
        except Exception as e:
            failures.record(e)

    nester.update_callback = lambda part, sheet: _blocking_main_thread_call(
        lambda: update_sim_view(part, sheet), getattr(nester, 'cancel_callback', None))

def _report_sim(failures, trial_stats, verbose):
    """Main thread: the run's one aggregated draw-failure warning, and the verbose frame count."""
    failures.report()
    if verbose:
        dropped = trial_stats['dropped']
        nw_logger.info(f"Simulation: {trial_stats['posted'] - dropped} trial frames drawn, "
                       f"{dropped} skipped")

def _teardown_sim(viz_manager, parts, failures, trial_stats, verbose):
    """Main-thread cleanup of trial viz, highlighting and sim outlines, then the run's report.

    These access FreeCAD doc objects and ViewObject — must run on main thread. The report is
    posted last, so on the relay FIFO it runs after every queued draw (NLA-060).
    """
    _main_thread_wrapper(lambda: _cleanup_trial_viz(viz_manager))()
    _main_thread_wrapper(lambda: _cleanup_highlighting(viz_manager))()
    _main_thread_wrapper(lambda: _hide_sim_outlines(parts))()
    _main_thread_wrapper(lambda: _report_sim(failures, trial_stats, verbose))()

def nest(parts, sheet_sizes, rotation_steps=1, simulate=False, algorithm=ALGO_GREEDY, viz_manager=None, **kwargs):
    """
    Convenience function to run the nesting algorithm.

    *algorithm* picks the placer (ALGO_GREEDY or ALGO_BEAM); an unknown value
    raises ValueError. The caller passes only the chosen placer's kwargs.
    """

    parts_to_process = parts if simulate else copy.deepcopy(parts)

    steps = 0
    sheets = []
    unplaced = []

    if not SHAPELY_AVAILABLE:
        show_shapely_installation_instructions()
        raise NestingDependencyError("The selected algorithm requires the 'Shapely' library.")

    # If simulation is enabled, ensure we have a viz_manager and bind callbacks
    failures = trial_stats = None
    if simulate:
        failures = _SimFailures()
        viz_manager, trial_stats = _bind_sim_callbacks(kwargs, viz_manager, failures)

    nester = make_placer(algorithm, sheet_sizes, rotation_steps, **kwargs)

    if simulate:
        _bind_sim_update(nester, failures)

    import time
    start_time = time.monotonic()
    try:
        result = nester.nest(parts_to_process)
        elapsed = time.monotonic() - start_time
    finally:
        # A raising nester must not strand TrialBounds, the master
        # highlight or the sim outlines in the document.
        if simulate:
            _teardown_sim(viz_manager, parts_to_process, failures, trial_stats,
                          kwargs.get('verbose', False))

    if len(result) == 3:
        sheets, unplaced, steps = result
    else:
        sheets, unplaced = result

    _calculate_efficiency(sheets, verbose=kwargs.get('verbose', False),
                          min_offcut=(kwargs.get('fitness_weights') or FitnessWeights()).min_offcut)

    return sheets, unplaced, steps, elapsed

def fill_existing_sheets(sheets, fill_parts, sheet_sizes, rotation_steps=1,
                         simulate=False, viz_manager=None, **kwargs):
    """Runs only the round-robin fill phase against already-populated sheets.

    Used by the GA coordinator to fill the winning layout once, after the
    generation loop deferred all fill placement. Mutates `sheets` in place
    (appends one sheet only when the run is fill-only and no sheet exists;
    compacts empty sheets at the end).
    Parts are NOT deep-copied — placements land on the caller's instances.
    Returns (unplaced_fill, elapsed_seconds).
    """
    if not fill_parts:
        return [], 0.0
    if not SHAPELY_AVAILABLE:
        show_shapely_installation_instructions()
        raise NestingDependencyError("The selected algorithm requires the 'Shapely' library.")

    kwargs.pop('sort', None)
    failures = trial_stats = None
    if simulate:
        failures = _SimFailures()
        viz_manager, trial_stats = _bind_sim_callbacks(kwargs, viz_manager, failures)

    nester = nesting_strategy.Nester(sheet_sizes, rotation_steps, **kwargs)

    if simulate:
        _bind_sim_update(nester, failures)

    import time
    start_time = time.monotonic()
    ordered_fill = sorted(fill_parts, key=lambda p: p.area, reverse=True)
    unplaced = []
    try:
        nester._nest_fill_parts(sheets, ordered_fill, unplaced,
                                quiet=kwargs.get('quiet', False))
        sheets[:] = sheet_sequence.compact_sheets(sheets)
        for sheet in sheets:
            for placed_part in sheet.parts:
                placed_part.shape.placement = placed_part.shape.get_final_placement(sheet.get_origin())
        elapsed = time.monotonic() - start_time
    finally:
        # A raising fill must not strand TrialBounds, the master highlight
        # or the sim outlines in the document.
        if simulate:
            _teardown_sim(viz_manager, ordered_fill, failures, trial_stats,
                          kwargs.get('verbose', False))

    return unplaced, elapsed

def _calculate_efficiency(sheets, verbose=False, min_offcut=MIN_OFFCUT_DEFAULT):
    """Calculates and displays sheet packing efficiency. The verbose per-sheet line lists the
    sheet's offcuts: its free pieces of at least min_offcut mm²."""
    if not sheets:
        return

    from .algorithms.offcuts import free_rectangles

    total_parts_area = 0
    total_sheet_area = 0
    
    if verbose:
        nw_logger.info("\n--- PACKING EFFICIENCY ---")
    
    for i, sheet in enumerate(sheets):
        sheet_area = sheet.width * sheet.height
        parts_area = sum(part.shape.area for part in sheet.parts if hasattr(part, 'shape') and part.shape)
        
        total_sheet_area += sheet_area
        total_parts_area += parts_area
        
        if verbose and sheet_area > 0:
            efficiency = (parts_area / sheet_area) * 100
            pieces = free_rectangles(sheet.parts, sheet.width, sheet.height, min_offcut)
            largest = max(((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in pieces), default=0.0)
            nw_logger.info(f"  Sheet {i+1}: {efficiency:.1f}% ({parts_area:.0f} / {sheet_area:.0f} mm²), "
                           f"Offcuts: {len(pieces)} ≥ {min_offcut:.0f} mm², largest {largest:.0f} mm²")
    
    if total_sheet_area > 0:
        overall_efficiency = (total_parts_area / total_sheet_area) * 100
        if verbose:
            nw_logger.info(f"  Overall: {overall_efficiency:.1f}% ({total_parts_area:.0f} / {total_sheet_area:.0f} mm²)")
            nw_logger.info("--------------------------")
        else:
            nw_logger.info(f"Packing Efficiency: {overall_efficiency:.1f}%")


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
