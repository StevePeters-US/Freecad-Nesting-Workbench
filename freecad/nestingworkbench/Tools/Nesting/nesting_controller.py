# SPDX-License-Identifier: LGPL-2.1-or-later

import FreeCAD
import FreeCADGui
import Part
import os
import time
import math
import threading
from PySide import QtWidgets, QtCore
from PySide.QtCore import QThread, Signal
from ...datatypes.shape import Shape
from ... import nw_logger
from .shape_preparer import ShapePreparer
from .layout_manager import LayoutManager, Layout
from .ga_coordinator import GACoordinator, DocumentClosedError
from .worker_sizing import set_worker_override
from ...freecad_helpers import (recursive_delete, set_master_shapes_visible,
                               hide_all_master_shapes, get_sheet_groups, read_sheet_instance,
                               find_offcut_group)
from ...constants import (
    FLIPPED_PREFIX, PREFS_PATH, PREF_SHEET_SEQUENCE, PROP_ADD_LABELS, PROP_ALGORITHM,
    PROP_BEAM_ITERATIVE, PROP_BEAM_WIDTH, PROP_BEAM_WIDTH_DECAY, PROP_CANDIDATE_SPACING, PROP_DEFLECTION_ANGLE,
    PROP_FILL_SHEET, PROP_FONT_FILE, PROP_GENERATIONS, PROP_GLOBAL_ROTATION_STEPS, PROP_LABEL_HEIGHT, PROP_LABEL_SIZE,
    PROP_MAX_PART_BRANCHES, PROP_MAX_ROT_BRANCHES, PROP_NESTING_DIRECTION, PROP_PART_ROTATION_OVERRIDE, PROP_PART_ROTATION_STEPS, PROP_PART_SPACING,
    PROP_POPULATION_SIZE, PROP_GA_WINNERS, PROP_MUTATION_RATE, PROP_QUANTITY, PROP_SHEET_SEQUENCE,
    PROP_FITNESS_MIN_OFFCUT, WEIGHT_PROPS, PROP_PART_DIRECTION,
    PROP_WEIGHT_GRAVITY, PROP_WEIGHT_CONTACT, PROP_WEIGHT_DIRECTION, PROP_WEIGHT_OFFCUT,
    PROP_SHOW_BOUNDS, PROP_SHOW_OFFCUTS, PROP_SIMPLIFICATION, PROP_UP_VECTOR,
    SIM_ALL, SIM_OFF, THICKNESS_MATCH_TOL_MM,
    ALGO_BEAM, ALGO_GREEDY,
)
from ...ui_helpers import closest_angle_index
from .nesting_job import NestingJob
from . import nfp_store
from ... import DEFAULT_FONT
from .algorithms import sheet_sequence

DEFLECTION_ANGLE_PER_MM = 200.0  # legacy UI scale: deflection_angle = deflection_mm * 200

from . import nesting_logic
from .nesting_logic import nest
from .visualization_manager import VisualizationManager

class NestingWorker(QThread):
    """Runs nesting computation on a background thread.
    
    Communicates with main thread via Qt signals for:
    - Status/progress updates (non-blocking)
    - Document operations like sheet.draw() (blocking synchronization)
    - Completion/error reporting
    """
    status_changed = Signal(str)
    progress_updated = Signal(int, int, str)   # current, total, message
    draw_requested = Signal(object)             # payload dict for main-thread drawing
    finished_signal = Signal(object)            # NestingJob result (or None)
    error_signal = Signal(str)                  # error message + traceback
    
    def __init__(self, coordinator, run_args, cancel_check_fn, parent=None):
        """
        Args:
            coordinator: GACoordinator instance
            run_args: tuple of (target_layout, ui_params, quantities, master_map,
                      rotation_params, algo_kwargs, is_simulating, viz_manager)
            cancel_check_fn: callable that returns True if cancel requested
        """
        super().__init__(parent)
        self.coordinator = coordinator
        self.run_args = run_args
        self.cancel_check_fn = cancel_check_fn
        self._draw_event = threading.Event()
        self._draw_error = None
    
    def run(self):
        """Execute nesting on worker thread."""
        try:
            (target_layout, ui_params, quantities, master_map,
             rotation_params, algo_kwargs, is_simulating, viz_manager) = self.run_args
            
            job = self.coordinator.run(
                target_layout, ui_params, quantities, master_map,
                rotation_params, algo_kwargs, is_simulating, viz_manager=viz_manager
            )
            self.finished_signal.emit(job)
        except Exception as e:
            import traceback
            self.error_signal.emit(f"{e}\n{traceback.format_exc()}")
    
    def request_draw_on_main_thread(self, payload):
        """Called from worker thread. Emits signal and blocks until main thread draws.

        The emit goes through the simulation relay rather than straight onto the
        Qt event queue. Sim redraws used to call updateGui() (removed in NLA-059),
        which delivered this request in the middle of the relay's backlog: the
        final draw ran first, then the leftover sim redraws hid every part again,
        leaving only the labels on screen. Sharing the relay's FIFO
        runs every earlier sim callback before the draw. The emit happens on the
        main thread, where the QThread object lives, so the slot runs directly.
        """
        self._draw_error = None
        self._draw_event.clear()
        nesting_logic._main_thread_relay.post(lambda: self.draw_requested.emit(payload))
        self._draw_event.wait()
        if self._draw_error is not None:
            raise self._draw_error
    
    def notify_draw_complete(self):
        """Called from main thread after draw finishes."""
        self._draw_event.set()

def _as_bool(value, default=False):
    """Coerces a FreeCAD property read to a real bool.

    Layouts saved before PartRotationOverride existed do not carry it at
    all, and a user can set any type on a scripted object from the
    property view. Anything that is not already bool-like becomes
    `default` rather than reaching QCheckBox.setChecked, which raises
    TypeError on a list (public issue #19).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return default


def _as_int(value, default=0):
    """Coerces a FreeCAD property read to an int, `default` if impossible."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _layout_number(value, as_int=False):
    """A layout property read as a plain number for a Qt spinbox (RLF-002).

    App::PropertyLength reads back as a Base.Quantity, not a float. Converting here
    removes the dependence on PySide's implicit numeric conversion. QSpinBox takes an int.
    """
    number = float(getattr(value, "Value", value))
    return int(round(number)) if as_int else number


# Human-readable names for the layout settings the reload path can fail to
# restore. The warning built from these is read by users, not developers, so it
# must not print raw FreeCAD property names.
_LAYOUT_SETTING_LABELS = {
    PROP_PART_SPACING: "part spacing",
    PROP_SIMPLIFICATION: "simplification",
    PROP_LABEL_SIZE: "label size",
    PROP_GENERATIONS: "generations",
    PROP_POPULATION_SIZE: "population size",
    PROP_GA_WINNERS: "GA winners",
    PROP_MUTATION_RATE: "mutation rate",
    PROP_WEIGHT_GRAVITY: "gravity weight",
    PROP_WEIGHT_CONTACT: "contact weight",
    PROP_WEIGHT_DIRECTION: "direction weight",
    PROP_WEIGHT_OFFCUT: "offcut weight",
    PROP_PART_DIRECTION: "part direction",
    PROP_FITNESS_MIN_OFFCUT: "minimum offcut",
    PROP_NESTING_DIRECTION: "nesting direction",
    PROP_CANDIDATE_SPACING: "candidate spacing",
}


_running_controller = None


class NestingController:
    """
    Main controller for the Nesting Workbench.
    Handles UI interaction, Job creation, and Layout management.
    """
    def __init__(self, ui_panel):
        self.ui = ui_panel
        self.doc = FreeCAD.ActiveDocument
        self.current_job = None
        self.shape_preparer = ShapePreparer(self.doc, {})
        self.is_running = False
        self.cancel_requested = False
        self.doc_closed = False
        self._run_doc_label = None
        self._has_transaction = False
        self._run_rows = None
        self._worker = None
        self._undone_job = None
        self._undone_names = None
        
        self.viz_manager = VisualizationManager()
        
        # Initialize default fonts
        default_font = DEFAULT_FONT
        self.ui.selected_font_path = default_font
        if hasattr(self.ui, 'font_label'):
            self.ui.font_label.setText(os.path.basename(default_font))

    def execute_nesting(self):
        global _running_controller
        if _running_controller is not None and getattr(_running_controller, "is_running", False):
            other_label = getattr(_running_controller, "_run_doc_label", None)
            if not other_label:
                try:
                    other_doc = getattr(_running_controller, "doc", None)
                    other_label = getattr(other_doc, "Label", getattr(other_doc, "Name", None)) if other_doc else None
                except Exception:
                    other_label = None
            other_label = other_label or "another document"
            msg = f"A nest is already running in {other_label}. Wait for it to finish."
            self.ui.status_label.setText(msg)
            nw_logger.warn(msg)
            return

        nw_logger.info("\n--- NESTING START ---")

        ok, self._run_rows = self._resolve_sheet_sequence()
        if not ok:
            return
        
        if getattr(self, "_has_transaction", False) and self.doc and not self.doc_closed:
            try:
                self.doc.abortTransaction()
            except Exception as e:
                nw_logger.debug(f"[NestingController] abortTransaction failed: {e}")
            self._has_transaction = False

        if self.current_job:
            self.current_job.cleanup()
            self.current_job = None

        Shape.clear_caches()

        if self.doc:
            try:
                self.doc.openTransaction("Nest")
                self._has_transaction = True
            except Exception as e:
                nw_logger.debug(f"[NestingController] openTransaction failed: {e}")
                self._has_transaction = False

        redone = self._take_redone_job()
        if redone is not None:
            redone.cleanup()

        target_layout = self._ensure_target_layout()
        if not target_layout:
            if getattr(self, "_has_transaction", False) and self.doc and not self.doc_closed:
                try:
                    self.doc.abortTransaction()
                except Exception as e:
                    nw_logger.debug(f"[NestingController] abortTransaction failed: {e}")
                self._has_transaction = False
            return # Standard error handled in helper
             
        is_simulating = self.ui.simulate_combo.currentIndex() != SIM_OFF
        if hasattr(target_layout, "ViewObject"):
            target_layout.ViewObject.Visibility = False

        # Every layout arranges its master row with its own spacing, so a row
        # left over from an earlier run sits slightly offset from this one's and
        # reads as a doubled, misaligned outline. Only the row being packed shows.
        hide_all_master_shapes(self.doc)

        ui_params = self._collect_ui_params()
        ui_params, quantities, master_map, rotation_params = self._collect_job_parameters(ui_params)
        
        algo_kwargs = self._prepare_algo_kwargs(ui_params)
        verbose = self.ui.verbose_logging_checkbox.isChecked()
        algo_kwargs['verbose'] = verbose
        
        rot_steps = ui_params.get('rotation_steps', 1)
        nw_logger.info(f"Algorithm Selected: {ui_params.get('algorithm', 'Unknown')}")
        nw_logger.info(f"  -> UI Rotation Steps Slider: {rot_steps}")
        
        algo_kwargs['cancel_callback'] = self._check_cancel
        
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        prefs.SetBool("VerboseLogging", verbose)
        
        
        def progress_cb(current, total, message=None):
            try:
                self.ui.update_progress(current, total, message)
            except RuntimeError as e:
                nw_logger.debug(f"[NestingController] progress update skipped (panel closed): {e}")
            
        self.ui.reset_progress()
        algo_kwargs['progress_callback'] = progress_cb
        
        _running_controller = self
        self._run_doc_label = getattr(self.doc, "Label", getattr(self.doc, "Name", "another document")) if self.doc else "another document"
        self.is_running = True
        self.cancel_requested = False
        self.ui.nest_button.setEnabled(False)
        self.ui.cancel_button.setEnabled(True)
        self._execute_ga_nesting(target_layout, ui_params, quantities, master_map, 
                                 rotation_params, algo_kwargs, is_simulating, self.viz_manager)
    
    def load_selection(self):
        nw_logger.info("Loading selection via Controller...")
        selection = FreeCADGui.Selection.getSelection()
        self.ui.shape_table.setRowCount(0)

        if not selection:
            nw_logger.info("  -> No selection found.")
            self.ui.status_label.setText("Warning: No shapes selected.")
            self.ui.nest_button.setEnabled(False)
            return

        first_selected = selection[0]
        if first_selected.isDerivedFrom("App::DocumentObjectGroup") and first_selected.Label.startswith("Layout_"):
            nw_logger.info(f"  -> Detected layout selection: {first_selected.Label}")
            self.load_layout(first_selected)
        else:
            nw_logger.info(f"  -> Detected {len(selection)} shapes.")
            self.load_shapes(selection)

    def load_layout(self, layout_group):
        """Loads the parameters and shapes from a layout group."""
        self.ui.current_layout = layout_group
        self.ui.nest_button.setEnabled(True)
        self.ui.selected_shapes_to_process = []
        self.ui.hidden_originals = []

        params_missing = self._load_params_from_layout(layout_group) or []
        library_sentence = self._restore_sheet_sequence(layout_group)
        shapes_missing, dropped_masters = self._load_shapes_from_layout(layout_group)
        try:
            nfp_store.load(self.doc, layout_group)
        except Exception as e:
            nw_logger.warn(f"Layout '{layout_group.Label}': stored NFPs could not be loaded: {e}")

        # The panel owns master visibility while it is open: this layout's row is
        # on screen, every other layout's row is off.
        hide_all_master_shapes(self.doc, except_layout=layout_group)
        set_master_shapes_visible(layout_group, True)

        # A dropped part and a defaulted setting are separate sentences, but one
        # log call — a second call would overwrite the first in the status label.
        missing = params_missing + shapes_missing
        sentences = []
        if missing:
            sentences.append(
                f"Layout '{layout_group.Label}' was saved without "
                f"{len(missing)} setting(s): "
                f"{'; '.join(missing)}. Re-nest and save to store them.")
        if dropped_masters:
            sentences.append(
                f"{len(dropped_masters)} part(s) could not be loaded: "
                f"{', '.join(dropped_masters)}.")
        if library_sentence:
            sentences.append(library_sentence)
        if sentences:
            self.ui.log_message(" ".join(sentences), level="warning")

    def _verbose_logging(self):
        """True when the panel's verbose-logging checkbox is on.

        Guarded: some entry points build the controller against a minimal UI
        stub that has no such widget, and a missing checkbox must mean "quiet",
        not AttributeError.
        """
        box = getattr(self.ui, "verbose_logging_checkbox", None)
        return bool(box.isChecked()) if box is not None else False

    def _load_params_from_layout(self, layout_group):
        """Extracts algorithm parameters from layout properties."""
        missing = []
        # The bool marks a QSpinBox, which takes an int.
        props_map = {
            PROP_PART_SPACING: self.ui.part_spacing_input,
            PROP_SIMPLIFICATION: self.ui.simplification_input,
            PROP_LABEL_SIZE: self.ui.label_size_input,
            PROP_CANDIDATE_SPACING: self.ui.minkowski_candidate_spacing_input,
            PROP_GENERATIONS: (self.ui.minkowski_generations_input, True),
            PROP_POPULATION_SIZE: (self.ui.minkowski_population_size_input, True),
            PROP_MUTATION_RATE: self.ui.ga_mutation_rate_input,
            PROP_FITNESS_MIN_OFFCUT: self.ui.fitness_min_offcut_input,
        }
        
        verbose = self._verbose_logging()

        for prop, widget in props_map.items():
            widget, as_int = widget if isinstance(widget, tuple) else (widget, False)
            val = getattr(layout_group, prop, None)
            if val is not None:
                widget.setValue(_layout_number(val, as_int))
            else:
                missing.append(f"{_LAYOUT_SETTING_LABELS.get(prop, prop)} (kept the panel's current value)")
        # The four Scoring weights are read as a set: all four or none (NLA ruling 30).
        weight_vals = {key: getattr(layout_group, prop, None) for prop, key in WEIGHT_PROPS}
        if all(v is not None for v in weight_vals.values()):
            self.ui.weight_sliders.setValues({key: float(v) for key, v in weight_vals.items()})
        else:
            missing.append("scoring weights (kept the panel's current values)")
        part_direction = getattr(layout_group, PROP_PART_DIRECTION, None)
        if part_direction is not None:
            self.ui.part_direction_dial.setDegrees(part_direction)
        else:
            missing.append(f"{_LAYOUT_SETTING_LABELS[PROP_PART_DIRECTION]} (kept the panel's current value)")
        # Winners goes through the panel's setter, after the population: a plain
        # setValue would clamp the chosen value to the box's current maximum.
        winners = getattr(layout_group, PROP_GA_WINNERS, None)
        if winners is not None:
            self.ui.set_ga_winners(winners)
        else:
            missing.append(f"{_LAYOUT_SETTING_LABELS.get(PROP_GA_WINNERS, PROP_GA_WINNERS)} (kept the panel's current value)")

        beam_props = ((PROP_BEAM_WIDTH,
                       lambda v: self.ui.beam_width_input.setValue(_layout_number(v, True))),
                      (PROP_MAX_PART_BRANCHES,
                       lambda v: self.ui.beam_max_part_branches_input.setValue(_layout_number(v, True))),
                      (PROP_MAX_ROT_BRANCHES,
                       lambda v: self.ui.beam_max_rot_branches_input.setValue(_layout_number(v, True))),
                      (PROP_BEAM_ITERATIVE,
                       lambda v: self.ui.beam_iterative_checkbox.setChecked(bool(v))),
                      (PROP_BEAM_WIDTH_DECAY,
                       lambda v: self.ui.beam_width_decay_input.setValue(_layout_number(v))))
        beam_absent = 0
        for prop, apply in beam_props:
            val = getattr(layout_group, prop, None)
            if val is None:
                beam_absent += 1
            else:
                apply(val)
        if beam_absent:
            missing.append("beam search settings (kept the panel's current values)")


        nesting_direction = getattr(layout_group, PROP_NESTING_DIRECTION, None)
        if nesting_direction is not None:
            self.ui.minkowski_direction_dial.setDegrees(nesting_direction)
        else:
            missing.append(f"{_LAYOUT_SETTING_LABELS[PROP_NESTING_DIRECTION]} (kept the panel's current value)")

        # Deflection has three outcomes and only one of them is a real default.
        deflection_angle = getattr(layout_group, PROP_DEFLECTION_ANGLE, None)
        if deflection_angle is not None:
            self.ui.deflection_input.setValue(_layout_number(deflection_angle))
        elif hasattr(layout_group, 'Deflection'):
            # Legacy documents stored millimetres. The setting is genuinely
            # restored, so this is a migration rather than a fallback and does
            # not belong in the warning — but it is still worth being able to
            # see, so it goes out under verbose.
            self.ui.deflection_input.setValue(_layout_number(layout_group.Deflection) * DEFLECTION_ANGLE_PER_MM)
            if verbose:
                nw_logger.info(
                    f"[NestingController] Layout '{layout_group.Label}': converted legacy "
                    f"Deflection ({layout_group.Deflection} mm) to a deflection angle")
        else:
            missing.append("deflection angle (kept the panel's current value)")

        # Font: "never stored one", "stored no font" and "stored one that has
        # since moved" are three different things. The middle one is a real
        # setting being honoured, so only the other two are reported.
        if not hasattr(layout_group, PROP_FONT_FILE):
            missing.append("label font (kept the panel's current font)")
        else:
            font_path = getattr(layout_group, PROP_FONT_FILE, None)
            if font_path:
                if os.path.exists(font_path):
                    self.ui.selected_font_path = font_path
                    self.ui.font_label.setText(os.path.basename(font_path))
                else:
                    missing.append(
                        f"label font '{os.path.basename(font_path)}' (saved path no longer "
                        f"exists, kept the panel's current font)")

        # The algorithm is a setting in its own right. It used to be restored
        # only inside the `steps > 0` branch below, so a layout that carried
        # Algorithm could still reopen on the wrong one — silently, because
        # hasattr() was true and nothing was reported as defaulted.
        if hasattr(layout_group, PROP_ALGORITHM):
            algo = str(getattr(layout_group, PROP_ALGORITHM))
            if not self.ui.set_algorithm(algo):
                missing.append(f"algorithm '{algo}' (no longer an option; "
                               f"kept the panel's current choice)")
        else:
            self.ui.set_algorithm(ALGO_GREEDY)
            missing.append("algorithm (defaulted to Greedy)")

        steps = _as_int(getattr(layout_group, PROP_GLOBAL_ROTATION_STEPS, 0), 0)
        if steps > 0:
            target_angle = 360.0 / steps
            self.ui.minkowski_rotation_steps_slider.setValue(
                closest_angle_index(self.ui.rotation_angles, target_angle))
        else:
            # Absent, or stored as a value the slider cannot be derived from.
            # Either way the slider keeps whatever the panel already had.
            missing.append("global rotation steps (kept the panel's current value)")

        return missing


    def _restore_sheet_sequence(self, layout_group):
        """Restore the sheet sequence from layout properties or sheet groups.

        Returns a warning sentence or None.
        """
        from ...sheet_library import model, store
        label = getattr(layout_group, "Label", "")
        rebuilt = False
        raw_seq = getattr(layout_group, PROP_SHEET_SEQUENCE, None)
        if raw_seq is not None:
            try:
                rows = sheet_sequence.from_json(raw_seq)
            except ValueError:
                return f"Layout '{label}' has an unreadable sheet list; kept the panel's current sheets."
        else:
            sheets = get_sheet_groups(layout_group)
            if not sheets:
                return f"Layout '{label}' was saved without a sheet list; kept the panel's current sheets."
            rows = []
            for g in sheets:
                snap = read_sheet_instance(g)
                if snap is None:
                    return f"Layout '{label}' was saved without a sheet list; kept the panel's current sheets."
                rows.append(snap)
            while len(rows) > 1 and rows[-1] == rows[-2]:
                rows.pop()
            rebuilt = True

        self.ui.set_sheet_sequence(rows)

        # Compare each library row with the disk library before populate_library_sheets reconciles it.
        lib = store.load_library()
        missing_rows = []
        changed_messages = []
        for i, row in enumerate(rows, start=1):
            sheet_id = row.get("sheet_id")
            if not sheet_id:
                continue
            record = lib.sheets.get(sheet_id)
            if record is None or model.validate_sheet(record, lib):
                missing_rows.append(str(i))
            else:
                effective = model.resolve_sheet(record, lib)[0]
                changed = [key for key in ("width", "height", "thickness", "cost")
                           if abs(float(effective[key]) - float(row[key])) > 1e-6]
                if effective["material"] != row["material"]:
                    changed.append("material")
                if effective["flip"] != row["flip"]:
                    changed.append("flip")
                if changed:
                    changed_messages.append(
                        f"Sheet row {i} ('{row['sheet_name']}') has changed in the library since this layout was nested ({', '.join(changed)}). Re-nest to apply it."
                    )

        sentences = []
        if missing_rows:
            rows_str = ", ".join(missing_rows)
            sentences.append(
                f"Sheet row(s) {rows_str} are not in this machine's sheet library or are not valid; using the layout's saved size as Custom."
            )
        sentences.extend(changed_messages)

        self.ui.populate_library_sheets()

        if rebuilt:
            sentences.append("sheet list rebuilt from the layout's sheets")

        return " ".join(sentences) if sentences else None

    def _load_shapes_from_layout(self, layout_group):
        """Identifies master shapes and their quantities/overrides."""
        master_shapes_group = next((c for c in layout_group.Group if c.Label.startswith("MasterShapes")), None)
        
        if not master_shapes_group:
            nw_logger.warn(f"  WARNING: No MasterShapes group found in '{layout_group.Label}'")
            self.ui.status_label.setText("Warning: Could not find 'MasterShapes' group.")
            return [], []

        shapes_to_load = []
        quantities, overrides, steps_map, up_vectors, fill_map = {}, {}, {}, {}, {}
        dropped_masters = []
        
        missing_counts = {
            PROP_PART_ROTATION_OVERRIDE: 0,
            PROP_PART_ROTATION_STEPS: 0,
            PROP_UP_VECTOR: 0,
            PROP_FILL_SHEET: 0,
            PROP_QUANTITY: 0,
        }

        verbose = self._verbose_logging()

        for master in master_shapes_group.Group:
            if not hasattr(master, "Group"): continue
            
            shape_obj = next((child for child in master.Group if child.Label.startswith("master_shape_")), None)
            # A Part shape is always truthy; an emptied one is only detectable via isNull()
            shape = getattr(shape_obj, "Shape", None) if shape_obj else None
            if shape is not None and not shape.isNull():
                shapes_to_load.append(shape_obj)
                label = shape_obj.Label
                
                if not hasattr(master, PROP_QUANTITY):
                    missing_counts[PROP_QUANTITY] += 1
                    if verbose:
                        nw_logger.info(f"[NestingController] Part '{label}': missing {PROP_QUANTITY}, defaulted to 1")

                if not hasattr(master, PROP_PART_ROTATION_OVERRIDE):
                    missing_counts[PROP_PART_ROTATION_OVERRIDE] += 1
                    if verbose:
                        nw_logger.info(f"[NestingController] Part '{label}': missing {PROP_PART_ROTATION_OVERRIDE}, defaulted to False")

                if not hasattr(master, PROP_PART_ROTATION_STEPS):
                    missing_counts[PROP_PART_ROTATION_STEPS] += 1
                    if verbose:
                        nw_logger.info(f"[NestingController] Part '{label}': missing {PROP_PART_ROTATION_STEPS}, defaulted to global")

                if not hasattr(master, PROP_UP_VECTOR):
                    missing_counts[PROP_UP_VECTOR] += 1
                    if verbose:
                        nw_logger.info(f"[NestingController] Part '{label}': missing {PROP_UP_VECTOR}, defaulted to (0, 0, 1)")

                if not hasattr(master, PROP_FILL_SHEET):
                    missing_counts[PROP_FILL_SHEET] += 1
                    if verbose:
                        nw_logger.info(f"[NestingController] Part '{label}': missing {PROP_FILL_SHEET}, defaulted to False")

                quantities[label] = _as_int(getattr(master, PROP_QUANTITY, 1), 1)

                overrides[label] = _as_bool(
                    getattr(master, PROP_PART_ROTATION_OVERRIDE, False))

                # Only override load_shapes' own default (4) when the
                # layout actually carries a per-part value. 0 means the
                # property is absent or never set, not "no rotation".
                raw_steps = _as_int(
                    getattr(master, PROP_PART_ROTATION_STEPS, 0), 0)
                if raw_steps > 0:
                    steps_map[label] = raw_steps

                up_vectors[label] = getattr(master, PROP_UP_VECTOR, FreeCAD.Vector(0, 0, 1))
                fill_map[label] = _as_bool(getattr(master, PROP_FILL_SHEET, False))
            else:
                dropped_masters.append(master.Label)
        
        self.load_shapes(
            shapes_to_load, is_reloading_layout=True, initial_quantities=quantities,
            initial_overrides=overrides, initial_rotation_steps=steps_map,
            initial_up_vectors=up_vectors, initial_fill_sheet=fill_map
        )

        missing_descriptions = []
        if missing_counts[PROP_PART_ROTATION_OVERRIDE] > 0:
            c = missing_counts[PROP_PART_ROTATION_OVERRIDE]
            missing_descriptions.append(f"per-part rotation override ({c} {'part' if c == 1 else 'parts'}, defaulted to off)")
        if missing_counts[PROP_PART_ROTATION_STEPS] > 0:
            c = missing_counts[PROP_PART_ROTATION_STEPS]
            missing_descriptions.append(f"per-part rotation steps ({c} {'part' if c == 1 else 'parts'}, defaulted to global)")
        if missing_counts[PROP_UP_VECTOR] > 0:
            c = missing_counts[PROP_UP_VECTOR]
            missing_descriptions.append(f"up vector ({c} {'part' if c == 1 else 'parts'}, defaulted to (0, 0, 1))")
        if missing_counts[PROP_FILL_SHEET] > 0:
            c = missing_counts[PROP_FILL_SHEET]
            missing_descriptions.append(f"fill sheet ({c} {'part' if c == 1 else 'parts'}, defaulted to off)")
        if missing_counts[PROP_QUANTITY] > 0:
            c = missing_counts[PROP_QUANTITY]
            missing_descriptions.append(f"quantity ({c} {'part' if c == 1 else 'parts'}, defaulted to 1)")

        return missing_descriptions, dropped_masters

    def _extract_parts_from_selection(self, selection):
        """
        Extracts parts from Assembly containers only.
        Regular Part objects are used directly without extracting children.
        """
        parts = []
        
        def is_assembly(obj):
            """Check if object is an Assembly container (not a regular Part/Body)."""
            type_id = obj.TypeId if hasattr(obj, 'TypeId') else ''
            if 'Assembly' in type_id:
                return True
            if type_id == 'App::Part' and hasattr(obj, 'Group'):
                for child in obj.Group:
                    child_type = child.TypeId if hasattr(child, 'TypeId') else ''
                    if 'Link' in child_type or 'Assembly' in child_type:
                        return True
            return False
        
        def extract_from_assembly(obj):
            """Recursively extract nestable parts from an assembly."""
            if hasattr(obj, 'Group'):
                for child in obj.Group:
                    child_type = child.TypeId if hasattr(child, 'TypeId') else ''
                    if 'Constraint' in child_type or 'Origin' in child_type:
                        continue
                    if hasattr(child, 'LinkedObject') and child.LinkedObject:
                        linked = child.LinkedObject
                        if hasattr(linked, 'Shape') and linked.Shape and not linked.Shape.isNull():
                            parts.append(linked)
                    elif hasattr(child, 'Shape') and child.Shape and not child.Shape.isNull():
                        if is_assembly(child):
                            extract_from_assembly(child)
                        else:
                            parts.append(child)
        
        for obj in selection:
            if is_assembly(obj):
                extract_from_assembly(obj)
            else:
                parts.append(obj)
        
        return parts

    def load_shapes(self, selection, is_reloading_layout=False, initial_quantities=None, 
                     initial_overrides=None, initial_rotation_steps=None,
                     initial_up_vectors=None, initial_fill_sheet=None):
        """Loads a selection of shapes into the UI."""
        self.ui.nest_button.setEnabled(True)
        
        selection_counts = {}

        if not is_reloading_layout:
            extracted = self._extract_parts_from_selection(selection)
            if extracted:
                # Count occurrences before deduping
                for obj in extracted:
                    if obj in selection_counts:
                        selection_counts[obj] += 1
                    else:
                        selection_counts[obj] = 1
                
                selection = extracted
                nw_logger.info(f"  -> Extracted {len(selection)} parts from selection.")
        
        self.ui.selected_shapes_to_process = list(dict.fromkeys(selection)) 
        
        if not is_reloading_layout:
            self.ui.current_layout = None
            self.ui.hidden_originals = list(self.ui.selected_shapes_to_process)
        
        seen_labels = set()
        duplicate_labels = set()

        self.ui.shape_table.setRowCount(len(self.ui.selected_shapes_to_process))
        for i, obj in enumerate(self.ui.selected_shapes_to_process):
            display_label = obj.Label
            if display_label.startswith("master_shape_"):
                display_label = display_label.replace("master_shape_", "")
            
            if display_label in seen_labels:
                duplicate_labels.add(display_label)
            seen_labels.add(display_label)

            qty = selection_counts.get(obj, 1)
            
            if initial_quantities and obj.Label in initial_quantities:
                qty = initial_quantities[obj.Label]
                
            steps = 4
            override = False
            if initial_rotation_steps and obj.Label in initial_rotation_steps:
                steps = initial_rotation_steps[obj.Label]
            if initial_overrides and obj.Label in initial_overrides:
                override = initial_overrides[obj.Label]
            
            up_vec = FreeCAD.Vector(0, 0, 1)
            if initial_up_vectors and obj.Label in initial_up_vectors:
                up_vec = initial_up_vectors[obj.Label]
                
            fill = False
            if initial_fill_sheet and obj.Label in initial_fill_sheet:
                fill = initial_fill_sheet[obj.Label]

            add_row_fn = getattr(self.ui, 'add_part_row', getattr(self.ui, '_add_part_row', None))
            if add_row_fn:
                 add_row_fn(i, display_label, quantity=qty, rotation_steps=steps, 
                            override_rotation=override, up_vector=up_vec, fill_sheet=fill)
            
            item = self.ui.shape_table.item(i, 0)
            if item:
                item.setData(QtCore.Qt.UserRole, obj)
        
        self.ui.shape_table.resizeColumnsToContents()
        self.ui.status_label.setText(f"{len(selection)} unique object(s) selected. Specify quantities and nest.")

        if duplicate_labels:
            msg = f"Duplicate part label(s) detected: {', '.join(sorted(duplicate_labels))}. Only one part per duplicate label will be nested."
            self.ui.log_message(msg, level="warning")  # status label + Report view

    def add_selected_shapes(self):
        """Adds the currently selected FreeCAD objects to the shape table."""
        selection = FreeCADGui.Selection.getSelection()
        if not selection:
            self.ui.status_label.setText("Select shapes in the 3D view or tree to add them.")
            return

        extracted = self._extract_parts_from_selection(selection)
        selection_counts = {}
        if extracted:
            for obj in extracted:
                selection_counts[obj] = selection_counts.get(obj, 0) + 1
            selection = extracted

        existing_labels = set(self.ui.shape_table.item(row, 0).text() for row in range(self.ui.shape_table.rowCount()))
        
        added_count = 0
        duplicate_labels = set()
        
        unique_selection = list(dict.fromkeys(selection))
        
        for obj in unique_selection:
            display_label = obj.Label
            if display_label.startswith("master_shape_"):
                display_label = display_label.replace("master_shape_", "")

            if display_label in existing_labels:
                duplicate_labels.add(display_label)
            else:
                existing_labels.add(display_label)
                row_position = self.ui.shape_table.rowCount()
                self.ui.shape_table.insertRow(row_position)
                
                qty = selection_counts.get(obj, 1)
                
                add_row_fn = getattr(self.ui, 'add_part_row', getattr(self.ui, '_add_part_row', None))
                if add_row_fn:
                    add_row_fn(row_position, display_label, quantity=qty)
                
                item = self.ui.shape_table.item(row_position, 0)
                if item:
                    item.setData(QtCore.Qt.UserRole, obj)
                    
                self.ui.selected_shapes_to_process.append(obj)
                added_count += 1
        
        self.ui.shape_table.resizeColumnsToContents()
        self.ui.status_label.setText(f"Added {added_count} new shape(s).")

        if duplicate_labels:
            msg = f"Duplicate part label(s) detected: {', '.join(sorted(duplicate_labels))}. Only one part per duplicate label will be nested."
            self.ui.log_message(msg, level="warning")  # status label + Report view

        if self.ui.shape_table.rowCount() > 0:
            self.ui.nest_button.setEnabled(True)

    def remove_selected_shapes(self):
        """Removes the selected rows from the shape table."""
        selected_items = self.ui.shape_table.selectedItems()
        selected_rows = sorted(list(set(item.row() for item in selected_items)), reverse=True)
        objects_to_remove = []
        for row in selected_rows:
            item = self.ui.shape_table.item(row, 0)
            obj_to_remove = item.data(QtCore.Qt.UserRole) if item else None
            if obj_to_remove is not None:
                objects_to_remove.append(obj_to_remove)
            else:
                label_to_remove = item.text() if item else ""
                obj_match = next((o for o in self.ui.selected_shapes_to_process if o.Label == label_to_remove), None)
                if obj_match:
                    objects_to_remove.append(obj_match)
            self.ui.shape_table.removeRow(row)

        for obj in objects_to_remove:
            self.ui.selected_shapes_to_process = [o for o in self.ui.selected_shapes_to_process if o is not obj]

        self.ui.status_label.setText(f"Removed {len(selected_rows)} shape(s).")

        if self.ui.shape_table.rowCount() == 0:
            self.ui.nest_button.setEnabled(False)

    def _get_rotation_steps(self):
        """Returns the orientation count from the rotation slider's angle preset."""
        idx = self.ui.minkowski_rotation_steps_slider.value()
        angles = self.ui.rotation_angles
        if idx < len(angles):
            return int(360 / angles[idx])
        return 1

    def _ui_safe(self, fn, *args):
        try:
            return fn(*args)
        except RuntimeError as e:
            nw_logger.debug(f"[NestingController] panel gone: {e}")
            return None

    def _execute_ga_nesting(self, target_layout, ui_params, quantities, master_map, 
                            rotation_params, algo_kwargs, is_simulating, viz_manager=None):
        """GA optimization on a background thread."""
        
        self._worker = NestingWorker(
            coordinator=None, # Set below
            run_args=(target_layout, ui_params, quantities, master_map,
                      rotation_params, algo_kwargs, is_simulating, viz_manager),
            cancel_check_fn=self._check_cancel,
            parent=None
        )
        
        coordinator = GACoordinator(
            doc=self.doc,
            shape_preparer=self.shape_preparer,
            ui_callbacks={
                'set_status': lambda msg: self._ui_safe(self.ui.status_label.setText, msg),
                'update_progress': lambda c, t, m: self._ui_safe(self.ui.update_progress, c, t, m),
                'reset_progress': lambda: self._ui_safe(self.ui.reset_progress),
                'play_sound': lambda: self._ui_safe(
                    lambda: QtWidgets.QApplication.beep() if getattr(self.ui, 'sound_checkbox', None) and self.ui.sound_checkbox.isChecked() else None
                ),
            },
            draw_callback=self._worker.request_draw_on_main_thread,
            worker=self._worker
        )
        self._worker.coordinator = coordinator
        
        self._worker.status_changed.connect(lambda msg: self._ui_safe(self.ui.status_label.setText, msg))
        self._worker.progress_updated.connect(lambda c, t, m: self._ui_safe(self.ui.update_progress, c, t, m))
        self._worker.draw_requested.connect(self._handle_draw_request)
        self._worker.finished_signal.connect(self._on_nesting_finished)
        self._worker.error_signal.connect(self._on_nesting_error)
        
        self._worker.start()

    def _handle_draw_request(self, payload):
        """Main-thread handler for draw requests from worker."""
        try:
            if getattr(self, "doc_closed", None) is True:
                raise DocumentClosedError("The document was closed during nesting.")
            if payload.get('updateGui_only'):
                FreeCADGui.updateGui()
            elif payload.get('create_population'):
                layouts = self._worker.coordinator.layout_manager.create_ga_population(
                    payload['master_map'], payload['quantities'], 
                    payload['ui_params'], payload['population_size'],
                    payload['rotation_steps'], verbose=payload.get('verbose', False)
                )
                self._worker.coordinator._pending_layouts = layouts
            elif payload.get('build_next_generation'):
                layouts = self._worker.coordinator._build_next_generation(
                    payload['gen'], payload['layouts'], payload['parents'],
                    payload['master_map'], payload['quantities'], payload['ui_params'],
                    payload['rotation_steps'], payload['mutation_rate'],
                    payload.get('verbose', False)
                )
                self._worker.coordinator._pending_layouts = layouts
            elif payload.get('spawn_fill_part'):
                payload['result_holder'][0] = payload['spawn_fn']()
            elif payload.get('cleanup_layouts'):
                for layout in payload['layouts']:
                    if layout != payload['best_layout']:
                        self._worker.coordinator.layout_manager.delete_layout(layout, verbose=payload.get('verbose', False))
            elif payload.get('sheets'):
                for sheet in payload['sheets']:
                    sheet.draw(payload['doc'], payload['ui_params'], payload['layout_group'],
                              parts_to_place_group=payload['parts_group'],
                              verbose=payload.get('verbose', False))
                if payload.get('hide_layout'):
                    lg = payload['layout_group']
                    if lg and hasattr(lg, "ViewObject"):
                        lg.ViewObject.Visibility = False
                FreeCADGui.updateGui()
            elif payload.get('ga_finalize'):
                # _finalize() and doc.recompute() must run on main thread (ViewObject + recompute)
                coordinator = self._worker.coordinator
                job = coordinator._finalize(
                    payload['best_layout'], payload['best_efficiency'],
                    payload['total_time'], payload['target_layout'], payload['ui_params']
                )
                payload['result_holder'][0] = job
                coordinator.doc.recompute()
            elif payload.get('discard_template'):
                self._worker.coordinator.layout_manager.discard_template()
            elif payload.get('sim_member_rows'):
                self.viz_manager.draw_member_rows(self.doc, payload['rows'])
                FreeCADGui.updateGui()
            elif payload.get('clear_sim_member_rows'):
                self.viz_manager.clear_member_rows(self.doc)
            elif payload.get('doc_recompute_only'):
                self._worker.coordinator.doc.recompute()
        except Exception as e:
            self._worker._draw_error = e
        finally:
            self._worker.notify_draw_complete()

    def _on_nesting_finished(self, job):
        """Main-thread handler for nesting completion."""
        was_cancelled = self.cancel_requested
        self.is_running = False
        self.cancel_requested = False
        global _running_controller
        if _running_controller is self:
            _running_controller = None

        if getattr(self, "doc_closed", None) is True:
            self._retire_worker()
            return

        if was_cancelled:
            if job:
                job.cleanup()
            self.cancel_job()
        else:
            self.current_job = job

        try:
            if was_cancelled:
                # request_cancel showed "Cancelling... Please wait."; the run is over now.
                self.ui.status_label.setText("Nesting cancelled.")
            self.ui.nest_button.setEnabled(True)
            self.ui.cancel_button.setEnabled(False)
            self.ui.reset_progress()
        except RuntimeError as e:
            nw_logger.debug(f"[NestingController] panel gone: {e}")

        self._retire_worker()

    def _on_nesting_error(self, error_msg):
        """Main-thread handler for nesting errors."""
        self.is_running = False
        self.cancel_requested = False
        global _running_controller
        if _running_controller is self:
            _running_controller = None

        if getattr(self, "_has_transaction", False) and self.doc and not self.doc_closed:
            try:
                self.doc.abortTransaction()
            except Exception as e:
                nw_logger.debug(f"[NestingController] abortTransaction failed: {e}")
            self._has_transaction = False

        nw_logger.error(f"Nesting Error: {error_msg}")
        try:
            self.ui.status_label.setText(f"Error: {error_msg.split(chr(10))[0]}")
            self.ui.nest_button.setEnabled(True)
            self.ui.cancel_button.setEnabled(False)
            self.ui.reset_progress()
        except RuntimeError as e:
            nw_logger.debug(f"[NestingController] panel gone: {e}")

        self._retire_worker()

    def _retire_worker(self):
        """Releases the finished worker thread on the main thread.

        finished_signal / error_signal are emitted from inside run(), so the
        thread may still be unwinding when these handlers execute. The worker
        and its GACoordinator also reference each other (worker.coordinator /
        coordinator.worker), so dropping self._worker alone did not free the
        QThread: it lingered until Python's cyclic GC, which can fire on any
        thread — typically the NEXT run's worker thread, mid-allocation.
        Destroying a QThread from a foreign thread (or while still running)
        is a hard crash, which is why a second run in the same panel could
        take FreeCAD down. Wait for the thread, then break the cycle so the
        wrapper is freed here, deterministically, by refcount.
        """
        worker = getattr(self, "_worker", None)
        self._worker = None
        if worker is None:
            return
        worker.wait()
        coordinator = worker.coordinator
        worker.coordinator = None
        if coordinator is not None:
            coordinator.worker = None
            coordinator.draw_callback = None

    def _job_objects_exist(self, job):
        if not job or self.doc is None:
            return False
        try:
            temp_layout = getattr(job, "temp_layout", None)
            if temp_layout is None:
                return False
            name = getattr(temp_layout, "Name", None)
            if not isinstance(name, str) or self.doc.getObject(name) is None:
                return False
            for obj_name in getattr(job, "_owned_object_names", set()):
                if not isinstance(obj_name, str) or self.doc.getObject(obj_name) is None:
                    return False
            return True
        except (ReferenceError, RuntimeError, TypeError):
            return False

    def _take_redone_job(self):
        job = getattr(self, "_undone_job", None)
        names = getattr(self, "_undone_names", None)
        self._undone_job = None
        self._undone_names = None
        if job is None or self.doc is None:
            return None
        if not self._job_objects_exist(job):
            return None
        if names:
            try:
                temp_name, target_name = names
                job.temp_layout = self.doc.getObject(temp_name)
                job.target_layout = self.doc.getObject(target_name)
            except Exception as e:
                nw_logger.debug(f"[NestingController] re-resolving redone job layouts failed: {e}")
        return job

    def finalize_job(self):
        """Called when User clicks OK."""
        redone = False
        if not self.current_job:
            redone_job = self._take_redone_job()
            if redone_job is not None:
                self.current_job = redone_job
                redone = True
                if self.doc:
                    try:
                        self.doc.openTransaction("Nest")
                        self._has_transaction = True
                    except Exception as e:
                        nw_logger.debug(f"[NestingController] openTransaction failed: {e}")
                        self._has_transaction = False
                nw_logger.info("The redone nest was committed.")
            else:
                return True

        if not redone and getattr(self, "_has_transaction", False) and self.doc and not getattr(self.doc, "HasPendingTransaction", False):
            if self._job_objects_exist(self.current_job):
                self._has_transaction = False
                nw_logger.warn("The nest was committed, but other edits were made before OK, so Undo won't remove it in one step.")
            else:
                try:
                    self._undone_job = self.current_job
                    self._undone_names = (self.current_job.temp_layout.Name, self.current_job.target_layout.Name)
                except (AttributeError, ReferenceError, RuntimeError):
                    self._undone_job = None
                    self._undone_names = None
                self.current_job = None
                self._has_transaction = False
                nw_logger.warn("The nest was undone before OK. Run Nest again.")
                try:
                    self.ui.status_label.setText("The nest was undone. Run Nest again.")
                except RuntimeError as e:
                    nw_logger.debug(f"[NestingController] panel gone: {e}")
                return False

        try:
            job_sheets = self.current_job.sheets
            final_layout = self.current_job.commit()
            self._store_nfps(final_layout, job_sheets)

            self.ui.current_layout = final_layout
            
            if final_layout and hasattr(final_layout, "ViewObject"):
                final_layout.ViewObject.Visibility = True
                
            set_master_shapes_visible(final_layout, False)

            if final_layout and hasattr(final_layout, "Group"):
                for child in final_layout.Group:
                    if child.Label.startswith("Sheet_") and hasattr(child, "ViewObject"):
                        child.ViewObject.Visibility = True
            
            self.current_job = None
            nw_logger.info("Job Finalized & Committed.")
            if self.doc:
                self.doc.recompute()
                if getattr(self, "_has_transaction", False):
                    self.doc.commitTransaction()
                    self._has_transaction = False
            return True
        except Exception:
            if self.doc and getattr(self, "_has_transaction", False):
                try:
                    self.doc.abortTransaction()
                except Exception as e:
                    nw_logger.debug(f"[NestingController] abortTransaction failed: {e}")
                self._has_transaction = False
            raise

    def _store_nfps(self, layout, sheets):
        """Saves the accepted layout's NFPs beside the document. A failure here
        must not undo an accepted nest, so it is reported and swallowed."""
        try:
            nfp_store.save(self.doc, layout, sheets)
        except Exception as e:
            nw_logger.warn(f"The accepted layout's NFPs could not be stored: {e}")

    def request_cancel(self):
        """Called when the custom Cancel Nesting button is clicked."""
        if self.is_running:
            self.cancel_requested = True
            try:
                self.ui.status_label.setText("Cancelling... Please wait.")
                self.ui.cancel_button.setEnabled(False) # Prevent double-click
            except Exception as e:
                nw_logger.debug(f"[NestingController] UI update on cancel skipped: {e}")
            
            # If worker is running, also unblock it from any draw wait
            if hasattr(self, '_worker') and self._worker:
                self._worker.notify_draw_complete() # Unblock if waiting for draw
        else:
            self.cancel_job()
            if hasattr(self.ui, 'reject'):
                self.ui.reject()

    def _check_cancel(self):
        return self.cancel_requested

    def cancel_job(self):
        """Called when User clicks Cancel."""
        if not self.current_job:
            redone = self._take_redone_job()
            if redone is not None:
                if self.doc:
                    try:
                        self.doc.openTransaction("Discard nest")
                    except Exception as e:
                        nw_logger.debug(f"[NestingController] openTransaction failed: {e}")
                self.current_job = redone
                target = self.current_job.target_layout
                self.current_job.cleanup()
                if target:
                    try:
                        has_content = any(
                            child.Label.startswith("Sheet_") or child.Label.startswith("MasterShapes")
                            for child in (target.Group if hasattr(target, "Group") else [])
                        )
                        if not has_content:
                            recursive_delete(self.doc, target)
                            if hasattr(self.ui, 'current_layout') and self.ui.current_layout == target:
                                self.ui.current_layout = None
                            nw_logger.info("Removed empty target layout.")
                    except Exception as e:
                        nw_logger.warn(f"[NestingController] Cancel cleanup failed for child: {e}")
                self.current_job = None
                if self.doc:
                    try:
                        self.doc.commitTransaction()
                    except Exception as e:
                        nw_logger.debug(f"[NestingController] commitTransaction failed: {e}")
                nw_logger.warn("A nest brought back by Redo was discarded.")
                return

        if getattr(self, "_has_transaction", False) and self.doc and not self.doc_closed:
            try:
                self.doc.abortTransaction()
            except Exception as e:
                nw_logger.debug(f"[NestingController] abortTransaction failed: {e}")
            self._has_transaction = False
            self.current_job = None
            nw_logger.info("Job Cancelled (transaction aborted).")
            return

        if self.current_job:
            target = self.current_job.target_layout
            
            self.current_job.cleanup()
            
            if target:
                try: 
                    has_content = any(
                        child.Label.startswith("Sheet_") or child.Label.startswith("MasterShapes")
                        for child in (target.Group if hasattr(target, "Group") else [])
                    )
                    
                    if not has_content:
                        recursive_delete(self.doc, target)
                        if hasattr(self.ui, 'current_layout') and self.ui.current_layout == target:
                            self.ui.current_layout = None
                        nw_logger.info("Removed empty target layout.")
                    else:
                        if hasattr(target, "ViewObject"):
                            target.ViewObject.Visibility = True
                        
                        if hasattr(target, "Group"):
                            for child in target.Group:
                                if child.Label.startswith("Sheet_") and hasattr(child, "ViewObject"):
                                    child.ViewObject.Visibility = True

                        # Cancelling a run does not close the panel, and the
                        # master row belongs to the panel: leave it on screen.
                        set_master_shapes_visible(target, True)
                except Exception as e:
                    nw_logger.warn(f"[NestingController] Cancel cleanup failed for child: {e}")
            
            self.current_job = None
            nw_logger.info("Job Cancelled.")
            if self.doc and not self.doc_closed:
                try:
                    self.doc.recompute()
                except Exception as e:
                    nw_logger.debug(f"[NestingController] doc recompute skipped on cancel: {e}")
    
    def on_panel_closed(self):
        """Called when the task panel closes, whatever route it took.

        Master shapes and their outlines exist for the panel's benefit, so they
        go away with it — including rows belonging to layouts this session never
        touched, which older runs could leave switched on.
        """
        self._undone_job = None
        self._undone_names = None
        try:
            self.viz_manager.clear_highlight()
            if getattr(self, "doc_closed", False):
                return
            hide_all_master_shapes(self.doc)
            if self.doc:
                self.doc.recompute()
        except Exception as e:
            # The document may already be gone when the panel is torn down
            nw_logger.warn(f"[NestingController] Panel teardown failed: {e}")

    def toggle_bounds_visibility(self):
        is_visible = self.ui.show_bounds_checkbox.isChecked()
        
        # If a job is active, use its temp_layout (where current results are)
        # Otherwise use the committed current_layout
        if self.current_job and self.current_job.temp_layout:
            target_layout = self.current_job.temp_layout
        else:
            target_layout = getattr(self.ui, 'current_layout', None)
        
        if not target_layout: 
            return
        
        found_count = 0
        
        # Recursively find and toggle bounds visibility
        def set_show_bounds(obj, depth=0):
            nonlocal found_count
            indent = "  " * depth
            
            if obj.Label.startswith(("boundary_", f"{FLIPPED_PREFIX}boundary_")):
                found_count += 1
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.Visibility = is_visible
                    
            if hasattr(obj, "BoundaryObject") and obj.BoundaryObject:
                found_count += 1
                if hasattr(obj.BoundaryObject, "ViewObject"):
                    obj.BoundaryObject.ViewObject.Visibility = is_visible
                
            if hasattr(obj, "Group"):
                for child in obj.Group:
                    set_show_bounds(child, depth + 1)
                    
        set_show_bounds(target_layout)
        self.doc.recompute()

    def toggle_offcuts_visibility(self, _state=None):
        """Show or hide every offcut in the layout on screen (NLA ruling 20).

        `_state` is the checkbox's stateChanged argument; the checkbox is read instead.
        """
        visible = self.ui.show_offcuts_checkbox.isChecked()
        if self.current_job and self.current_job.temp_layout:
            target_layout = self.current_job.temp_layout
        else:
            target_layout = getattr(self.ui, 'current_layout', None)
        if not target_layout:
            return
        for sheet_group in get_sheet_groups(target_layout):
            group = find_offcut_group(sheet_group)
            for obj in (group.Group if group else ()):
                if getattr(obj, "ViewObject", None):
                    obj.ViewObject.Visibility = visible
        self.doc.recompute()

    def _resolve_sheet_sequence(self):
        """Re-read the library for every row in the sequence just before a run.

        Returns (ok, rows). Refuses the run on a missing or invalid library sheet
        or a thickness mismatch. On success, updates the panel sequence to refreshed
        disk values and returns (True, rows).
        """
        from ...sheet_library import model, store
        library = store.load_library()
        rows = self.ui.sheet_sequence()
        reasons = []
        resolved_rows = []

        for n, row in enumerate(rows, start=1):
            sheet_id = row.get("sheet_id", "")
            if not sheet_id:
                resolved_rows.append(dict(row))
                continue
            record = library.sheets.get(sheet_id) if library else None
            if record is None:
                reasons.append(f"row {n}: sheet '{row.get('sheet_name', '')}' is no longer in the sheet library")
            else:
                errors = model.validate_sheet(record, library)
                if errors:
                    name = record.get("name", row.get("sheet_name", ""))
                    reasons.append(f"row {n}: sheet '{name}' is not valid ({'; '.join(errors)})")
                else:
                    effective, _err = model.resolve_sheet(record, library)
                    resolved_rows.append(sheet_sequence.library_row(effective))

        if reasons:
            self.ui.log_message("Cannot nest: " + "; ".join(reasons) + ". Fix the row or pick another sheet.", level="warning")
            return False, None

        mismatch = sheet_sequence.thickness_mismatch(resolved_rows, THICKNESS_MATCH_TOL_MM)
        if mismatch:
            t1 = resolved_rows[0]["thickness"]
            mismatch_details = ", ".join(f"row {n} is {t:g} mm" for n, t in mismatch)
            self.ui.log_message(
                f"Cannot nest: every sheet in the list must be the same thickness (row 1 is {t1:g} mm; {mismatch_details}).",
                level="warning",
            )
            return False, None

        cur_sel = self.ui.selected_sheet_row()
        self.ui.set_sheet_sequence(resolved_rows, selected=cur_sel)
        return True, resolved_rows

    def _ensure_target_layout(self):
        """Determines the target layout, creating a default one if none exists."""
        target = getattr(self.ui, 'current_layout', None)
        
        if target:
            try:
                if target not in self.doc.Objects: target = None
            except Exception as e:
                nw_logger.warn(f"[NestingController] Target validation failed: {e}")
                target = None
            
        if not target and hasattr(self.ui, 'selected_shapes_to_process') and self.ui.selected_shapes_to_process:
             # Logic to find parent layout derived previously...
             # Simplified for brevity/robustness
             pass 

        if not target:
            base_name = "Layout"
            i = 0
            existing_labels = [o.Label for o in self.doc.Objects]
            while f"{base_name}_{i:03d}" in existing_labels: i += 1
            target = self.doc.addObject("App::DocumentObjectGroup", f"{base_name}_{i:03d}")
            target.Label = f"{base_name}_{i:03d}"
            self.ui.current_layout = target
            
        return target

    def _collect_ui_params(self):
        deflection_angle = self.ui.deflection_input.value()
        deflection_mm = deflection_angle / DEFLECTION_ANGLE_PER_MM
        
        settings_dict = {
            'spacing': self.ui.part_spacing_input.value(),
            'deflection': deflection_mm,
            'deflection_angle': deflection_angle,
            'simplification': self.ui.simplification_input.value(),
            'rotation_steps': self._get_rotation_steps(),
            'add_labels': self.ui.add_labels_checkbox.isChecked(),
            'font_path': getattr(self.ui, 'selected_font_path', None),
            'show_bounds': self.ui.show_bounds_checkbox.isChecked(),
            'show_offcuts': self.ui.show_offcuts_checkbox.isChecked(),
            'label_height': self.ui.label_height_input.value(),
            'label_size': self.ui.label_size_input.value(),
            'generations': self.ui.minkowski_generations_input.value(),
            'population_size': self.ui.minkowski_population_size_input.value(),
            'beam_width': self.ui.beam_width_input.value() if hasattr(self.ui, 'beam_width_input') else 1,
            'max_part_branches': self.ui.beam_max_part_branches_input.value() if hasattr(self.ui, 'beam_max_part_branches_input') else 2,
            'max_rot_branches': self.ui.beam_max_rot_branches_input.value() if hasattr(self.ui, 'beam_max_rot_branches_input') else 3,
            'beam_width_decay': self.ui.beam_width_decay_input.value(),
            'ga_winners': self.ui.ga_winners(),
            'mutation_rate': self.ui.ga_mutation_rate_input.value(),
            'beam_iterative': self.ui.beam_iterative_checkbox.isChecked() if hasattr(self.ui, 'beam_iterative_checkbox') else False,
            'candidate_spacing': self.ui.minkowski_candidate_spacing_input.value(),
            **{f"{k}_weight": float(v) for k, v in self.ui.weight_sliders.values().items()},
            'part_direction': self.ui.part_direction_dial.degrees(),
            'min_offcut': self.ui.fitness_min_offcut_input.value(),
            'verbose': self.ui.verbose_logging_checkbox.isChecked(),
            'nesting_direction': self.ui.minkowski_direction_dial.degrees(),
            'algorithm': self.ui.algorithm(),
            'evolve_direction': self.ui.evolve_direction_checkbox.isChecked(),
            'direction_detent': self.ui.minkowski_direction_dial.detentStep(),
            'worker_processes': self.ui.worker_processes_input.value(),
            'sheet_sequence': [dict(r) for r in self._run_rows] if self._run_rows else [],
            'sheet_thickness': self._run_rows[0]['thickness'] if self._run_rows else self.ui.sheet_thickness_input.value(),
        }
        
        self.save_settings(settings_dict)
        
        return settings_dict

    def save_settings(self, settings):
        """Saves current UI settings to FreeCAD preferences."""
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        prefs.SetString(PREF_SHEET_SEQUENCE, sheet_sequence.to_json(settings['sheet_sequence']))
        prefs.SetFloat(PROP_PART_SPACING, float(settings['spacing']))
        prefs.SetFloat(PROP_DEFLECTION_ANGLE, float(settings.get('deflection_angle', 10)))  # Save angle, not mm
        prefs.SetFloat(PROP_SIMPLIFICATION, float(settings['simplification']))
        for prop, key in WEIGHT_PROPS:
            prefs.SetFloat(prop, float(settings[f"{key}_weight"]))
            prefs.SetBool(prop + "Locked", self.ui.weight_sliders.isLocked(key))
        prefs.SetFloat(PROP_PART_DIRECTION, float(settings['part_direction']))
        prefs.SetFloat("FitnessMinOffcut", float(settings['min_offcut']))
        prefs.SetFloat("BeamWidthDecay", float(settings.get('beam_width_decay', 1.0)))
        prefs.SetInt("GAWinners", int(settings.get('ga_winners', 2)))
        prefs.SetFloat("GAMutationRate", float(settings.get('mutation_rate', 0.1)))
        prefs.SetBool("EvolveDirection", bool(settings.get('evolve_direction', False)))
        prefs.SetFloat("DirectionDetentStep", self.ui.minkowski_direction_dial.detentStep())
        
        mink_steps = int(360 / self.ui.rotation_angles[self.ui.minkowski_rotation_steps_slider.value()])
        prefs.SetInt("MinkowskiRotationSteps", mink_steps)
        
        prefs.SetBool(PROP_ADD_LABELS, bool(settings['add_labels']))
        prefs.SetBool(PROP_SHOW_BOUNDS, bool(settings['show_bounds']))
        prefs.SetBool(PROP_SHOW_OFFCUTS, bool(settings['show_offcuts']))
        prefs.SetFloat(PROP_LABEL_HEIGHT, float(settings['label_height']))
        prefs.SetFloat(PROP_LABEL_SIZE, float(settings['label_size']))
        if settings['font_path']:
             prefs.SetString("FontPath", str(settings['font_path']))
        set_worker_override(settings.get('worker_processes', 0))

    def _collect_job_parameters(self, ui_settings):
        quantities = {}
        master_map = {}
        rotation_params = {}
        
        global_rot = ui_settings['rotation_steps']
        
        for row in range(self.ui.shape_table.rowCount()):
            try:
                label = self.ui.shape_table.item(row, 0).text()
                qty = self.ui.shape_table.cellWidget(row, 1).value()
                
                rot_widget = self.ui.shape_table.cellWidget(row, 2)
                rot_val = rot_widget.findChild(QtWidgets.QSpinBox).value()
                override_box = rot_widget.findChild(QtWidgets.QCheckBox)
                override = override_box.isChecked() if override_box else False
                
                up_vector_widget = self.ui.shape_table.cellWidget(row, 3)
                if up_vector_widget:
                    spinboxes = up_vector_widget.findChildren(QtWidgets.QDoubleSpinBox)
                    up_vector = FreeCAD.Vector(spinboxes[0].value(), spinboxes[1].value(), spinboxes[2].value())
                else:
                    up_vector = FreeCAD.Vector(0, 0, 1)
                
                fill_checkbox = self.ui.shape_table.cellWidget(row, 4)
                fill_sheet = fill_checkbox.isChecked() if fill_checkbox else False
                
                quantities[label] = {
                    'quantity': qty,
                    # Resolved value the placer uses. Unchanged — do not
                    # repoint this at the raw spinbox.
                    'rotation_steps': rot_val if override else global_rot,
                    # Raw widget state, persisted onto the master container so
                    # reopening the layout can restore the row.
                    'override_rotation': override,
                    'part_rotation_steps': rot_val,
                    'up_vector': up_vector,
                    'fill_sheet': fill_sheet
                }
                
                rotation_params[label] = (rot_val, override)
            except Exception as e:
                nw_logger.warn(f"[NestingController] Skipping row {row} in shape table: {e}")
                continue
            
        for obj in self.ui.selected_shapes_to_process:
             try:
                 lbl = obj.Label.replace("master_shape_", "")
                 if lbl in quantities:
                     master_map[obj.Label] = obj
             except Exception as e:
                 nw_logger.warn(f"[NestingController] Failed to map object {obj.Label if hasattr(obj, 'Label') else 'unknown'}: {e}")
             
        return ui_settings, quantities, master_map, rotation_params

    def _prepare_algo_kwargs(self, ui_params):
        algo_kwargs = {}
        algorithm = ui_params.get('algorithm', ALGO_GREEDY)

        # degrees() is a maths angle: 0 = Right (1,0), 90 = Up (0,1), 180 = Left, 270 = Down (0,-1)
        # Always the dial's vector: under Greedy the genome decides per member, under
        # Beam it is a fixed setting.
        angle_rad = math.radians(self.ui.minkowski_direction_dial.degrees())
        algo_kwargs['search_direction'] = (math.cos(angle_rad), math.sin(angle_rad))

        algo_kwargs['population_size'] = self.ui.minkowski_population_size_input.value()
        algo_kwargs['generations'] = self.ui.minkowski_generations_input.value()
        if algorithm == ALGO_BEAM:
            # Only the chosen placer's keys: Nester ignores stray kwargs silently.
            algo_kwargs['beam_width'] = self.ui.beam_width_input.value()
            algo_kwargs['width_decay'] = self.ui.beam_width_decay_input.value()
            algo_kwargs['max_part_branches'] = self.ui.beam_max_part_branches_input.value()
            algo_kwargs['max_rot_branches'] = self.ui.beam_max_rot_branches_input.value()
            algo_kwargs['iterative'] = self.ui.beam_iterative_checkbox.isChecked()
        algo_kwargs['candidate_spacing'] = self.ui.minkowski_candidate_spacing_input.value()
        algo_kwargs['worker_processes'] = ui_params.get('worker_processes', 0)
        algo_kwargs['sim_show_all_members'] = self.ui.simulate_combo.currentIndex() == SIM_ALL

        algo_kwargs['clear_nfp_cache'] = self.ui.clear_cache_checkbox.isChecked()
        algo_kwargs['spacing'] = ui_params['spacing']
        algo_kwargs['random_seed'] = ui_params.get('random_seed')
        
        if hasattr(self.ui, 'log_message'):
            algo_kwargs['log_callback'] = self.ui.log_message
            
        return algo_kwargs



