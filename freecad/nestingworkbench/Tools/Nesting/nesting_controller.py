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
from .shape_preparer import ShapePreparer
from .layout_manager import LayoutManager, Layout
from .ga_coordinator import GACoordinator
from ...freecad_helpers import (recursive_delete, set_master_shapes_visible,
                               hide_all_master_shapes)
from ...constants import *
from ...ui_helpers import closest_angle_index
from .nesting_job import NestingJob
from ... import DEFAULT_FONT

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
        Qt event queue. Simulation redraws queued on the relay call updateGui(),
        which would otherwise deliver this request in the middle of the relay's
        backlog: the final draw ran first, then the leftover sim redraws hid every
        part again, leaving only the labels on screen. Sharing the relay's FIFO
        runs every earlier sim callback before the draw. The emit happens on the
        main thread, where the QThread object lives, so the slot runs directly.
        """
        self._draw_event.clear()
        nesting_logic._main_thread_relay.post(lambda: self.draw_requested.emit(payload))
        self._draw_event.wait()
    
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


# Human-readable names for the layout settings the reload path can fail to
# restore. The warning built from these is read by users, not developers, so it
# must not print raw FreeCAD property names.
_LAYOUT_SETTING_LABELS = {
    PROP_SHEET_WIDTH: "sheet width",
    PROP_SHEET_HEIGHT: "sheet height",
    PROP_PART_SPACING: "part spacing",
    PROP_SHEET_THICKNESS: "sheet thickness",
    PROP_SIMPLIFICATION: "simplification",
    PROP_LABEL_SIZE: "label size",
    PROP_GENERATIONS: "generations",
    PROP_POPULATION_SIZE: "population size",
    PROP_NESTING_DIRECTION: "nesting direction",
}


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
        
        self.viz_manager = VisualizationManager()
        
        # Initialize default fonts
        default_font = DEFAULT_FONT
        self.ui.selected_font_path = default_font
        if hasattr(self.ui, 'font_label'):
            self.ui.font_label.setText(os.path.basename(default_font))

    def execute_nesting(self):
        FreeCAD.Console.PrintMessage("\n--- NESTING START ---\n")
        
        if self.current_job:
            self.current_job.cleanup()
            self.current_job = None

        Shape.clear_caches()

        target_layout = self._ensure_target_layout()
        if not target_layout:
             return # Standard error handled in helper
             
        is_simulating = self.ui.simulate_nesting_checkbox.isChecked()
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
        ann_steps = algo_kwargs.get('anneal_steps', 25) if ui_params.get('algorithm') == 'Physics' else 0
        FreeCAD.Console.PrintMessage(f"Algorithm Selected: {ui_params.get('algorithm', 'Unknown')}\n")
        FreeCAD.Console.PrintMessage(f"  -> UI Rotation Steps Slider: {rot_steps}\n")
        FreeCAD.Console.PrintMessage(f"  -> UI Anneal Steps Input: {ann_steps}\n")
        
        algo_kwargs['cancel_callback'] = self._check_cancel
        
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        prefs.SetBool("VerboseLogging", verbose)
        
        
        def progress_cb(current, total, message=None):
            try:
                self.ui.update_progress(current, total, message)
            except RuntimeError: pass  # Widget deleted (panel closed)
            
        self.ui.reset_progress()
        algo_kwargs['progress_callback'] = progress_cb
        
        self.is_running = True
        self.cancel_requested = False
        self.ui.nest_button.setEnabled(False)
        self.ui.cancel_button.setEnabled(True)
        self._execute_ga_nesting(target_layout, ui_params, quantities, master_map, 
                                 rotation_params, algo_kwargs, is_simulating, self.viz_manager)
    
    def load_selection(self):
        FreeCAD.Console.PrintMessage("Loading selection via Controller...\n")
        selection = FreeCADGui.Selection.getSelection()
        self.ui.shape_table.setRowCount(0)

        if not selection:
            FreeCAD.Console.PrintMessage("  -> No selection found.\n")
            self.ui.status_label.setText("Warning: No shapes selected.")
            self.ui.nest_button.setEnabled(False)
            return

        first_selected = selection[0]
        if first_selected.isDerivedFrom("App::DocumentObjectGroup") and first_selected.Label.startswith("Layout_"):
            FreeCAD.Console.PrintMessage(f"  -> Detected layout selection: {first_selected.Label}\n")
            self.load_layout(first_selected)
        else:
            FreeCAD.Console.PrintMessage(f"  -> Detected {len(selection)} shapes.\n")
            self.load_shapes(selection)

    def load_layout(self, layout_group):
        """Loads the parameters and shapes from a layout group."""
        self.ui.current_layout = layout_group
        self.ui.nest_button.setEnabled(True)
        self.ui.selected_shapes_to_process = []
        self.ui.hidden_originals = []

        params_missing = self._load_params_from_layout(layout_group) or []
        shapes_missing, dropped_masters = self._load_shapes_from_layout(layout_group)

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
                f"{len(missing)} setting(s), now at defaults: "
                f"{'; '.join(missing)}. Re-nest and save to store them.")
        if dropped_masters:
            sentences.append(
                f"{len(dropped_masters)} part(s) could not be loaded: "
                f"{', '.join(dropped_masters)}.")
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
        props_map = {
            PROP_SHEET_WIDTH: self.ui.sheet_width_input,
            PROP_SHEET_HEIGHT: self.ui.sheet_height_input,
            PROP_PART_SPACING: self.ui.part_spacing_input,
            PROP_SHEET_THICKNESS: self.ui.sheet_thickness_input,
            PROP_SIMPLIFICATION: self.ui.simplification_input,
            PROP_LABEL_SIZE: self.ui.label_size_input,
            PROP_GENERATIONS: self.ui.minkowski_generations_input,
            PROP_POPULATION_SIZE: self.ui.minkowski_population_size_input,
            PROP_NESTING_DIRECTION: self.ui.minkowski_direction_dial,
        }
        
        verbose = self._verbose_logging()

        for prop, widget in props_map.items():
            val = getattr(layout_group, prop, None)
            if val is not None:
                widget.setValue(val)
            else:
                missing.append(f"{_LAYOUT_SETTING_LABELS.get(prop, prop)} (defaulted)")

        # Deflection has three outcomes and only one of them is a real default.
        deflection_angle = getattr(layout_group, PROP_DEFLECTION_ANGLE, None)
        if deflection_angle is not None:
            self.ui.deflection_input.setValue(deflection_angle)
        elif hasattr(layout_group, 'Deflection'):
            # Legacy documents stored millimetres. The setting is genuinely
            # restored, so this is a migration rather than a fallback and does
            # not belong in the warning — but it is still worth being able to
            # see, so it goes out under verbose.
            self.ui.deflection_input.setValue(layout_group.Deflection * DEFLECTION_ANGLE_PER_MM)
            if verbose:
                FreeCAD.Console.PrintMessage(
                    f"[NestingController] Layout '{layout_group.Label}': converted legacy "
                    f"Deflection ({layout_group.Deflection} mm) to a deflection angle\n")
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
        else:
            algo = "Minkowski"
            missing.append("algorithm (defaulted to Minkowski)")
        self.ui.algorithm_dropdown.setCurrentText(algo)

        steps = _as_int(getattr(layout_group, PROP_GLOBAL_ROTATION_STEPS, 0), 0)
        if steps > 0:
            target_angle = 360.0 / steps
            angles = PHYSICS_ROTATION_PRESETS if algo == "Physics" else self.ui.rotation_angles
            slider = self.ui.physics_rotation_steps_slider if algo == "Physics" else self.ui.minkowski_rotation_steps_slider
            slider.setValue(closest_angle_index(angles, target_angle))
        else:
            # Absent, or stored as a value the slider cannot be derived from.
            # Either way the slider keeps whatever the panel already had.
            missing.append("global rotation steps (kept the panel's current value)")

        return missing

    def _load_shapes_from_layout(self, layout_group):
        """Identifies master shapes and their quantities/overrides."""
        master_shapes_group = next((c for c in layout_group.Group if c.Label.startswith("MasterShapes")), None)
        
        if not master_shapes_group:
            FreeCAD.Console.PrintWarning(f"  WARNING: No MasterShapes group found in '{layout_group.Label}'\n")
            self.ui.status_label.setText("Warning: Could not find 'MasterShapes' group.")
            return [], []

        shapes_to_load = []
        quantities, overrides, steps_map, up_dirs, fill_map = {}, {}, {}, {}, {}
        dropped_masters = []
        
        missing_counts = {
            PROP_PART_ROTATION_OVERRIDE: 0,
            PROP_PART_ROTATION_STEPS: 0,
            PROP_UP_DIRECTION: 0,
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
                        FreeCAD.Console.PrintMessage(f"[NestingController] Part '{label}': missing {PROP_QUANTITY}, defaulted to 1\n")

                if not hasattr(master, PROP_PART_ROTATION_OVERRIDE):
                    missing_counts[PROP_PART_ROTATION_OVERRIDE] += 1
                    if verbose:
                        FreeCAD.Console.PrintMessage(f"[NestingController] Part '{label}': missing {PROP_PART_ROTATION_OVERRIDE}, defaulted to False\n")

                if not hasattr(master, PROP_PART_ROTATION_STEPS):
                    missing_counts[PROP_PART_ROTATION_STEPS] += 1
                    if verbose:
                        FreeCAD.Console.PrintMessage(f"[NestingController] Part '{label}': missing {PROP_PART_ROTATION_STEPS}, defaulted to global\n")

                if not hasattr(master, PROP_UP_DIRECTION):
                    missing_counts[PROP_UP_DIRECTION] += 1
                    if verbose:
                        FreeCAD.Console.PrintMessage(f"[NestingController] Part '{label}': missing {PROP_UP_DIRECTION}, defaulted to Z+\n")

                if not hasattr(master, PROP_FILL_SHEET):
                    missing_counts[PROP_FILL_SHEET] += 1
                    if verbose:
                        FreeCAD.Console.PrintMessage(f"[NestingController] Part '{label}': missing {PROP_FILL_SHEET}, defaulted to False\n")

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

                up_dirs[label] = str(getattr(master, PROP_UP_DIRECTION, "Z+"))
                fill_map[label] = _as_bool(getattr(master, PROP_FILL_SHEET, False))
            else:
                dropped_masters.append(master.Label)
        
        self.load_shapes(
            shapes_to_load, is_reloading_layout=True, initial_quantities=quantities,
            initial_overrides=overrides, initial_rotation_steps=steps_map,
            initial_up_directions=up_dirs, initial_fill_sheet=fill_map
        )

        missing_descriptions = []
        if missing_counts[PROP_PART_ROTATION_OVERRIDE] > 0:
            c = missing_counts[PROP_PART_ROTATION_OVERRIDE]
            missing_descriptions.append(f"per-part rotation override ({c} {'part' if c == 1 else 'parts'}, defaulted to off)")
        if missing_counts[PROP_PART_ROTATION_STEPS] > 0:
            c = missing_counts[PROP_PART_ROTATION_STEPS]
            missing_descriptions.append(f"per-part rotation steps ({c} {'part' if c == 1 else 'parts'}, defaulted to global)")
        if missing_counts[PROP_UP_DIRECTION] > 0:
            c = missing_counts[PROP_UP_DIRECTION]
            missing_descriptions.append(f"up direction ({c} {'part' if c == 1 else 'parts'}, defaulted to Z+)")
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
                     initial_up_directions=None, initial_fill_sheet=None):
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
                FreeCAD.Console.PrintMessage(f"  -> Extracted {len(selection)} parts from selection.\n")
        
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
            
            up_dir = "Z+"
            if initial_up_directions and obj.Label in initial_up_directions:
                up_dir = initial_up_directions[obj.Label]
                
            fill = False
            if initial_fill_sheet and obj.Label in initial_fill_sheet:
                fill = initial_fill_sheet[obj.Label]

            add_row_fn = getattr(self.ui, 'add_part_row', getattr(self.ui, '_add_part_row', None))
            if add_row_fn:
                 add_row_fn(i, display_label, quantity=qty, rotation_steps=steps, 
                            override_rotation=override, up_direction=up_dir, fill_sheet=fill)
            
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
        """Returns the orientation count based on algorithm-specific angle mapping."""
        algo = self.ui.algorithm_dropdown.currentText()
        
        if algo == "Physics":
            idx = self.ui.physics_rotation_steps_slider.value()
            # Mapping: 360(1), 90(4), 45(8), 30(12), 15(24), 10(36), 5(72), 2(180), 1(360)
            angles = PHYSICS_ROTATION_PRESETS
            if idx < len(angles):
                angle = angles[idx]
                return int(360 / angle)
        else:
            idx = self.ui.minkowski_rotation_steps_slider.value()
            angles = self.ui.rotation_angles
            if idx < len(angles):
                angle = angles[idx]
                return int(360 / angle)
        return 1

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
                'set_status': lambda msg: self.ui.status_label.setText(msg),
                'update_progress': lambda c, t, m: self.ui.update_progress(c, t, m),
                'reset_progress': lambda: self.ui.reset_progress(),
                'play_sound': lambda: QtWidgets.QApplication.beep() if self.ui.sound_checkbox.isChecked() else None,
            },
            draw_callback=self._worker.request_draw_on_main_thread,
            worker=self._worker
        )
        self._worker.coordinator = coordinator
        
        self._worker.status_changed.connect(lambda msg: self.ui.status_label.setText(msg))
        self._worker.progress_updated.connect(lambda c, t, m: self.ui.update_progress(c, t, m))
        self._worker.draw_requested.connect(self._handle_draw_request)
        self._worker.finished_signal.connect(self._on_nesting_finished)
        self._worker.error_signal.connect(self._on_nesting_error)
        
        self._worker.start()

    def _handle_draw_request(self, payload):
        """Main-thread handler for draw requests from worker."""
        try:
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
                    payload['gen'], payload['layouts'], payload['elites'], 
                    payload['master_map'], payload['quantities'], payload['ui_params'], 
                    payload['rotation_steps'], payload['mutation_rate'], 
                    payload['immigrant_ratio'], payload.get('verbose', False)
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
            elif payload.get('doc_recompute_only'):
                self._worker.coordinator.doc.recompute()
        finally:
            self._worker.notify_draw_complete()

    def _on_nesting_finished(self, job):
        """Main-thread handler for nesting completion."""
        if self.cancel_requested:
            if job:
                job.cleanup()
            self.cancel_job()
        else:
            self.current_job = job
            
        self.is_running = False
        self.cancel_requested = False
        self.ui.nest_button.setEnabled(True)
        self.ui.cancel_button.setEnabled(False)
        self.ui.reset_progress()
        self._retire_worker()

    def _on_nesting_error(self, error_msg):
        """Main-thread handler for nesting errors."""
        FreeCAD.Console.PrintError(f"Nesting Error: {error_msg}\n")
        self.ui.status_label.setText(f"Error: {error_msg.split(chr(10))[0]}")
        self.is_running = False
        self.cancel_requested = False
        self.ui.nest_button.setEnabled(True)
        self.ui.cancel_button.setEnabled(False)
        self.ui.reset_progress()
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
        worker, self._worker = self._worker, None
        if worker is None:
            return
        worker.wait()
        coordinator = worker.coordinator
        worker.coordinator = None
        if coordinator is not None:
            coordinator.worker = None
            coordinator.draw_callback = None

    def finalize_job(self):
        """Called when User clicks OK."""
        if self.current_job:
            final_layout = self.current_job.commit()
            
            self.ui.current_layout = final_layout
            
            if final_layout and hasattr(final_layout, "ViewObject"):
                final_layout.ViewObject.Visibility = True
                
            set_master_shapes_visible(final_layout, False)

            if final_layout and hasattr(final_layout, "Group"):
                for child in final_layout.Group:
                    if child.Label.startswith("Sheet_") and hasattr(child, "ViewObject"):
                        child.ViewObject.Visibility = True
            
            self.current_job = None
            FreeCAD.Console.PrintMessage("Job Finalized & Committed.\n")
            self.doc.recompute()

    def request_cancel(self):
        """Called when the custom Cancel Nesting button is clicked."""
        if self.is_running:
            self.cancel_requested = True
            try:
                self.ui.status_label.setText("Cancelling... Please wait.")
                self.ui.cancel_button.setEnabled(False) # Prevent double-click
            except Exception:
                pass  # UI widget may be destroyed if user closed panel while cancelling
            
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
                        FreeCAD.Console.PrintMessage("Removed empty target layout.\n")
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
                    FreeCAD.Console.PrintWarning(f"[NestingController] Cancel cleanup failed for child: {e}\n")
            
            self.current_job = None
            FreeCAD.Console.PrintMessage("Job Cancelled.\n")
            self.doc.recompute()
    
    def on_panel_closed(self):
        """Called when the task panel closes, whatever route it took.

        Master shapes and their outlines exist for the panel's benefit, so they
        go away with it — including rows belonging to layouts this session never
        touched, which older runs could leave switched on.
        """
        try:
            self.viz_manager.clear_highlight()
            hide_all_master_shapes(self.doc)
            if self.doc:
                self.doc.recompute()
        except Exception as e:
            # The document may already be gone when the panel is torn down
            FreeCAD.Console.PrintWarning(f"[NestingController] Panel teardown failed: {e}\n")

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
            
            if obj.Label.startswith("boundary_"):
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

    def _ensure_target_layout(self):
        """Determines the target layout, creating a default one if none exists."""
        target = getattr(self.ui, 'current_layout', None)
        
        if target:
            try:
                if target not in self.doc.Objects: target = None
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"[NestingController] Target validation failed: {e}\n")
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
            'sheet_width': self.ui.sheet_width_input.value(),
            'sheet_height': self.ui.sheet_height_input.value(),
            'spacing': self.ui.part_spacing_input.value(),
            'sheet_thickness': self.ui.sheet_thickness_input.value(),
            'deflection': deflection_mm,
            'deflection_angle': deflection_angle,
            'simplification': self.ui.simplification_input.value(),
            'rotation_steps': self._get_rotation_steps(),
            'add_labels': self.ui.add_labels_checkbox.isChecked(),
            'font_path': getattr(self.ui, 'selected_font_path', None),
            'show_bounds': self.ui.show_bounds_checkbox.isChecked(),
            'label_height': self.ui.label_height_input.value(),
            'label_size': self.ui.label_size_input.value(),
            'generations': self.ui.minkowski_generations_input.value(),
            'population_size': self.ui.minkowski_population_size_input.value(),
            'compactness_weight': self.ui.minkowski_compactness_input.value(),
            'verbose': self.ui.verbose_logging_checkbox.isChecked(),
            'nesting_direction': self.ui.minkowski_direction_dial.value(),
            'algorithm': self.ui.algorithm_dropdown.currentText(),
            'use_random_direction': (self.ui.physics_random_checkbox.isChecked() if self.ui.algorithm_dropdown.currentText() == 'Physics' else self.ui.minkowski_random_checkbox.isChecked()),
            'stability_tolerance': self.ui.physics_improvement_threshold_input.value(),
            'anneal_curve': self.ui.physics_anneal_curve_type.currentText(),
            'anneal_min_amp': self.ui.physics_anneal_min_amp.value(),
            'anneal_max_amp': self.ui.physics_anneal_max_amp.value(),
            'anneal_rot_steps': self.ui.physics_anneal_rot_steps.value(),
            'anneal_rot_curve': self.ui.physics_anneal_rot_curve_type.currentText(),
            'anneal_rot_min': self.ui.physics_anneal_rot_min.value(),
            'anneal_rot_max': self.ui.physics_anneal_rot_max.value()
        }
        
        self.save_settings(settings_dict)
        
        return settings_dict

    def save_settings(self, settings):
        """Saves current UI settings to FreeCAD preferences."""
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        prefs.SetFloat(PROP_SHEET_WIDTH, float(settings['sheet_width']))
        prefs.SetFloat(PROP_SHEET_HEIGHT, float(settings['sheet_height']))
        prefs.SetFloat(PROP_PART_SPACING, float(settings['spacing']))
        prefs.SetFloat(PROP_SHEET_THICKNESS, float(settings['sheet_thickness']))
        prefs.SetFloat(PROP_DEFLECTION_ANGLE, float(settings.get('deflection_angle', 10)))  # Save angle, not mm
        prefs.SetFloat(PROP_SIMPLIFICATION, float(settings['simplification']))
        prefs.SetFloat("GACompactnessWeight", float(settings['compactness_weight']))
        
        mink_steps = int(360 / self.ui.rotation_angles[self.ui.minkowski_rotation_steps_slider.value()])
        prefs.SetInt("MinkowskiRotationSteps", mink_steps)
        
        phys_angles = PHYSICS_ROTATION_PRESETS
        phys_steps = int(360 / phys_angles[self.ui.physics_rotation_steps_slider.value()])
        prefs.SetInt("PhysicsRotationSteps", phys_steps)

        prefs.SetBool(PROP_ADD_LABELS, bool(settings['add_labels']))
        prefs.SetBool(PROP_SHOW_BOUNDS, bool(settings['show_bounds']))
        prefs.SetFloat(PROP_LABEL_HEIGHT, float(settings['label_height']))
        prefs.SetFloat(PROP_LABEL_SIZE, float(settings['label_size']))
        prefs.SetFloat("PhysicsStabilityTolerance", float(settings.get('stability_tolerance', 0.01)))
        prefs.SetString("PhysicsAnnealCurveType", str(settings.get('anneal_curve', "Logarithmic")))
        prefs.SetFloat("PhysicsAnnealMinAmp", float(settings.get('anneal_min_amp', 0.1)))
        prefs.SetFloat("PhysicsAnnealMaxAmp", float(settings.get('anneal_max_amp', 100.0)))
        
        prefs.SetInt("PhysicsAnnealRotSteps", int(settings.get('anneal_rot_steps', 10)))
        prefs.SetString("PhysicsAnnealRotCurveType", str(settings.get('anneal_rot_curve', "Logarithmic")))
        prefs.SetFloat("PhysicsAnnealRotMin", float(settings.get('anneal_rot_min', 1.0)))
        prefs.SetFloat("PhysicsAnnealRotMax", float(settings.get('anneal_rot_max', 90.0)))
        if settings['font_path']:
             prefs.SetString("FontPath", str(settings['font_path']))

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
                
                up_dir_combo = self.ui.shape_table.cellWidget(row, 3)
                up_direction = up_dir_combo.currentText() if up_dir_combo else "Z+"
                
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
                    'up_direction': up_direction,
                    'fill_sheet': fill_sheet
                }
                
                rotation_params[label] = (rot_val, override)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"[NestingController] Skipping row {row} in shape table: {e}\n")
                continue
            
        for obj in self.ui.selected_shapes_to_process:
             try:
                 lbl = obj.Label.replace("master_shape_", "")
                 if lbl in quantities:
                     master_map[obj.Label] = obj
             except Exception as e:
                 FreeCAD.Console.PrintWarning(f"[NestingController] Failed to map object {obj.Label if hasattr(obj, 'Label') else 'unknown'}: {e}\n")
             
        return ui_settings, quantities, master_map, rotation_params

    def _prepare_algo_kwargs(self, ui_params):
        algo_kwargs = {}
        algorithm = ui_params.get('algorithm', 'Minkowski')
        
        if algorithm == 'Physics':
            if self.ui.physics_random_checkbox.isChecked():
                algo_kwargs['physics_direction'] = None 
            else:
                # Dial value is CCW from 6 o'clock. 
                # 0=Down(0,-1), 90=Left(-1,0), 180=Up(0,1), 270=Right(1,0)
                angle_deg = (270 - self.ui.physics_direction_dial.value()) % 360
                angle_rad = math.radians(angle_deg)
                algo_kwargs['physics_direction'] = (math.cos(angle_rad), math.sin(angle_rad))
            
            algo_kwargs['step_size'] = self.ui.physics_step_size_input.value()
            algo_kwargs['max_spawn_count'] = self.ui.physics_max_spawn_input.value()
            algo_kwargs['max_nesting_steps'] = self.ui.physics_max_nesting_steps_input.value()
            algo_kwargs['anneal_steps'] = self.ui.physics_anneal_steps_input.value()
            algo_kwargs['anneal_rotate_enabled'] = self.ui.anneal_rotate_checkbox.isChecked()
            algo_kwargs['anneal_translate_enabled'] = self.ui.anneal_translate_checkbox.isChecked()
            algo_kwargs['anneal_random_shake_direction'] = self.ui.anneal_random_shake_checkbox.isChecked()
            algo_kwargs['stability_tolerance'] = ui_params.get('stability_tolerance', 0.01)
            algo_kwargs['anneal_curve'] = ui_params.get('anneal_curve', "Logarithmic")
            algo_kwargs['anneal_min_amp'] = ui_params.get('anneal_min_amp', 0.1)
            algo_kwargs['anneal_max_amp'] = ui_params.get('anneal_max_amp', 100.0)
            algo_kwargs['anneal_rot_steps'] = ui_params.get('anneal_rot_steps', 10)
            algo_kwargs['anneal_rot_curve'] = ui_params.get('anneal_rot_curve', "Logarithmic")
            algo_kwargs['anneal_rot_min'] = ui_params.get('anneal_rot_min', 1.0)
            algo_kwargs['anneal_rot_max'] = ui_params.get('anneal_rot_max', 90.0)
        else:
            if self.ui.minkowski_random_checkbox.isChecked():
                algo_kwargs['search_direction'] = None
            else:
                angle_deg = (270 - self.ui.minkowski_direction_dial.value()) % 360
                angle_rad = math.radians(angle_deg)
                algo_kwargs['search_direction'] = (math.cos(angle_rad), math.sin(angle_rad))
            
            algo_kwargs['population_size'] = self.ui.minkowski_population_size_input.value()
            algo_kwargs['generations'] = self.ui.minkowski_generations_input.value()

        algo_kwargs['clear_nfp_cache'] = self.ui.clear_cache_checkbox.isChecked()
        algo_kwargs['spacing'] = ui_params['spacing']
        algo_kwargs['random_seed'] = ui_params.get('random_seed')
        
        if hasattr(self.ui, 'log_message'):
            algo_kwargs['log_callback'] = self.ui.log_message
            
        return algo_kwargs



