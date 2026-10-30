# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/Nesting/ui_nesting.py

"""
This module contains the NestingPanel class, which defines the user interface
for the main nesting task panel.
"""

from PySide import QtCore, QtWidgets
import FreeCAD
import FreeCADGui
import os
from ...constants import (
    MINKOWSKI_ROTATION_PRESETS, PREFS_PATH, PREF_SHEET_SEQUENCE, PROP_DEFLECTION_ANGLE, PROP_LABEL_SIZE,
    PROP_PART_SPACING, PROP_PART_DIRECTION, PROP_SHOW_OFFCUTS, PROP_SIMPLIFICATION, WEIGHT_PROPS,
    SIM_SINGLE,
    ALGO_BEAM, ALGO_GREEDY,
)
from ... import FONTS_DIR, DEFAULT_FONT
from freecad.nestingworkbench import nw_logger
from freecad.nestingworkbench.ui_helpers import (
    MARGINS_NONE,
    QT_TRANSLATE_NOOP,
    rich_tooltip,
    make_double_spinbox,
    make_quantity_spinbox,
    make_area_spinbox,
    format_length,
    make_int_spinbox,
    make_checkbox,
    make_slider,
    LinkedSliderSpinBox,
    LinkedSliderDoubleSpinBox,
    DirectionDial,
    WeightSliders,
    DIRECTION_DETENT_DEFAULT,
    CollapsibleSection,
    closest_angle_index,
)
from .algorithms.minkowski_engine import DEFAULT_CANDIDATE_SPACING
from .algorithms import sheet_sequence
from .worker_sizing import auto_core_count, balanced_worker_count, get_worker_override


_DEFAULTS = {
    "default_sheet_width": 800.0,
    "default_sheet_height": 800.0,
    "part_spacing": 6.35,
    "sheet_thickness": 19.0,
    "deflection_angle": 30.0,
    "rotation_angles": MINKOWSKI_ROTATION_PRESETS,
    "candidate_spacing": DEFAULT_CANDIDATE_SPACING,
    # Defaults from the 2026-09-03 GA sizing benchmark:
    # the cheapest configuration that is no worse than seeded greedy on every
    # benchmarked corpus. Heavier settings are opt-in presets, not the default.
    "ga_population": 1,
    "ga_generations": 1,
    "ga_winners": 2,
    "ga_mutation_rate": 0.10,
    "weight_gravity": 50,
    "weight_contact": 0,
    "weight_direction": 0,
    "weight_offcut": 50,
    "part_direction": 0.0,
    "fitness_min_offcut": 100000.0,  # mm², 0.1 m²
    # Beam Search defaults:
    "beam_width": 1,
    "beam_width_decay": 1.0,
    "max_part_branches": 1,
    "max_rot_branches": 1,
    "beam_iterative": False,
}

# (label, beam_width, max_part_branches, max_rot_branches, iterative, width_decay)
_BEAM_PRESETS = [
    (QT_TRANSLATE_NOOP("NestingPanel", "Default (W=1, fast greedy)"), 1, 1, 1, False, 1.0),
    (QT_TRANSLATE_NOOP("NestingPanel", "Balanced (W=4, iterative)"), 4, 2, 3, True, 1.0),
    (QT_TRANSLATE_NOOP("NestingPanel", "Thorough (W=8, iterative)"), 8, 2, 3, True, 1.0),
]
_BEAM_CUSTOM_LABEL = QT_TRANSLATE_NOOP("NestingPanel", "Custom")

_BEAM_WIDTH_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets the number of top partial sheet layouts kept after each part is placed.\n"
    "<b>Lower:</b> faster, greedy-like placement. <b>Higher:</b> explores more branches for tighter packing.\n"
    "With Population Size above 1, this is the widest beam the GA will try.\n"
    "<b>Default:</b> 1. <b>Typical:</b> 1–8."
)

_BEAM_DECAY_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Scales the beam width after each placed part (width × decay^parts for the first member).\n"
    "<b>Lower:</b> narrows the search quickly on later parts. <b>Higher:</b> keeps the full beam width longer.\n"
    "<b>Default:</b> 1.00. <b>Typical:</b> 0.85–1.00."
)

_BEAM_PART_BRANCHES_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets the maximum number of distinct remaining part types branched on at each placement step.\n"
    "<b>Lower:</b> faster search. <b>Higher:</b> tries more part-order alternatives at each step.\n"
    "<b>Default:</b> 1. <b>Typical:</b> 1–4."
)

_BEAM_ROT_BRANCHES_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets the maximum number of alternative rotations branched on per part type at each step.\n"
    "<b>Lower:</b> faster search. <b>Higher:</b> keeps more rotation choices open.\n"
    "<b>Default:</b> 1. <b>Typical:</b> 1–4."
)

_BEAM_ITERATIVE_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Runs successive doubling passes (W = 1, 2, 4, … up to Beam Width) and keeps the best layout found so far."
)

_BEAM_PRESET_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Selects a predefined Beam Search configuration.\n"
    "<b>Default (W=1, fast greedy):</b> single-branch greedy pass.\n"
    "<b>Balanced (W=4, iterative):</b> anytime passes up to width 4.\n"
    "<b>Thorough (W=8, iterative):</b> anytime passes up to width 8.\n"
    "<b>Default:</b> Default (W=1, fast greedy)."
)

_BEAM_WARNING_TEXT = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Note: Beam widths > 16 substantially increase runtime with diminishing returns."
)

# (label, population, generations) — from the 2026-09-03 GA sizing benchmark.
# Re-run the sizing sweep before changing any number here.
_GA_PRESETS = [
    (QT_TRANSLATE_NOOP("NestingPanel", "Single run (1 pop, 1 gen)"), 1, 1),
    (QT_TRANSLATE_NOOP("NestingPanel", "Balanced (10 pop, 10 gen)"), 10, 10),
    (QT_TRANSLATE_NOOP("NestingPanel", "Thorough (10 pop, 20 gen)"), 10, 20),
]
_GA_CUSTOM_LABEL = QT_TRANSLATE_NOOP("NestingPanel", "Custom")
_SIM_LABEL = QT_TRANSLATE_NOOP("NestingPanel", "Simulation:")
_SIM_TIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Selects how much of the nesting run is drawn live in the 3D view.\n"
    "<b>Off:</b> draws only the finished layout (fastest).\n"
    "<b>Single:</b> draws one layout at a time.\n"
    "<b>All:</b> draws every GA population member at once in a grid while worker processes run.\n"
    "<b>Default:</b> Single.")
_WORKERS_LABEL = QT_TRANSLATE_NOOP("NestingPanel", "GA worker processes:")
# {0} is the number of workers Auto will start for the current population, e.g. "Auto - (5)".
_WORKERS_AUTO_FMT = QT_TRANSLATE_NOOP("NestingPanel", "Auto - ({0})")
_WORKERS_TIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets how many processes evaluate the GA population in parallel.\n"
    "<b>Lower:</b> uses fewer CPU cores. <b>Higher:</b> evaluates more layouts at once up to physical core count.\n"
    "0 (Auto) picks up to one worker per physical core and balances batch sizes; 1 runs in-process.\n"
    "<b>Default:</b> Auto.")

_GA_WINNERS_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets how many of the best layouts breed the next generation (the top one is also kept unchanged).\n"
    "<b>Lower:</b> converges faster on the leading layout. <b>Higher:</b> keeps the search broader.\n"
    "Needs Population Size above 1.\n"
    "<b>Default:</b> 3. <b>Typical:</b> 2–5."
)

_GA_MUTATION_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets the probability of replacing each child setting with a random value after breeding.\n"
    "<b>Lower:</b> stays close to the winning parents. <b>Higher:</b> explores more random variations.\n"
    "0 turns mutation off; 1 makes every child random.\n"
    "<b>Default:</b> 0.10. <b>Typical:</b> 0.05–0.25."
)

_WEIGHT_GRAVITY_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets how much packing toward the Nesting Direction counts; finished layouts with a smaller bounding box on the last sheet rank higher.\n"
    "<b>Higher:</b> pulls parts harder toward the Nesting Direction.\n"
    "The four weights always add up to 100%; moving one adjusts the unlocked others.\n"
    "<b>Default:</b> 50%.")

_WEIGHT_CONTACT_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets how much a part touching other parts and sheet edges counts (the fraction of its spaced outline running along a neighbour or edge).\n"
    "<b>Higher:</b> parts nest into each other and against sheet edges; slower.\n"
    "0% turns it off.\n"
    "<b>Default:</b> 0%.")

_WEIGHT_DIRECTION_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets how much a part facing the Part Direction counts (180° also counts as aligned, as with wood grain; 90° off counts least).\n"
    "<b>Higher:</b> penalizes rotations away from Part Direction.\n"
    "0% turns it off.\n"
    "<b>Default:</b> 0%.")

_WEIGHT_OFFCUT_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets how much usable free rectangles on all sheets count when layouts use the same number of sheets.\n"
    "<b>Higher:</b> favours a few large offcuts over many small ones.\n"
    "Measured on finished layouts only. Fewer sheets, and every part placed, always come first.\n"
    "<b>Default:</b> 50%.")

_PART_DIRECTION_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets the target angle in ° that parts should face, measured from how each part is drawn.\n"
    "Used by the Direction weight; 0% turns it off.\n"
    "<b>Default:</b> 0°.")

_FITNESS_MIN_OFFCUT_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets the smallest free rectangle that counts as an offcut; smaller pieces are scrap and count for nothing.\n"
    "<b>Lower:</b> counts smaller leftover rectangles. <b>Higher:</b> rewards only large reusable sheets.\n"
    "<b>Default:</b> 0.1 m².")

_GA_POP_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets the number of candidate layouts (Population Size) evaluated in each generation.\n"
    "<b>Lower:</b> faster runs. <b>Higher:</b> broader search across part orders, directions and rotations.\n"
    "1 runs a single seeded pass.\n"
    "<b>Default:</b> 1. <b>Typical:</b> 1–10."
)

_GA_GEN_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Sets the number of evolutionary cycles (Generations) to run.\n"
    "<b>Lower:</b> finishes sooner. <b>Higher:</b> gives crossover and mutation more cycles to improve the layout.\n"
    "Needs Population Size above 1 to evolve.\n"
    "<b>Default:</b> 1. <b>Typical:</b> 1–20."
)

_GA_PRESET_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Selects a predefined population and generation pair.\n"
    "<b>Single run (1 pop, 1 gen):</b> one seeded pass with no evolution.\n"
    "<b>Balanced (10 pop, 10 gen):</b> good packing improvement at moderate runtime.\n"
    "<b>Thorough (10 pop, 20 gen):</b> deepest search for the tightest layout.\n"
    "<b>Default:</b> Single run (1 pop, 1 gen)."
)

_GA_WARNING_TEXT = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "Note: Populations > 40 or generations > 20 offer diminishing returns and substantially increase runtime."
)

class NestingPanel(QtWidgets.QWidget):
    """
    Defines the user interface for the main nesting task panel, including
    all input fields, buttons, and the table of shapes.
    """
    def __init__(self, parent=None):
        super(NestingPanel, self).__init__(parent)
        tr = QtWidgets.QApplication.translate
        nw_logger.info("NestingPanel initialized.")
        self.setWindowTitle(tr("NestingPanel", "Nesting Tool"))
        self.selected_shapes_to_process = []
        self.hidden_originals = []
        self.current_layout = None
        self.selected_font_path = ""
        self.rotation_angles = _DEFAULTS["rotation_angles"]
        self._setup_ui()
        self.set_default_font()
    
    def accept(self):
        """Called when the user clicks Standard Button OK / Apply."""
        if hasattr(self, 'controller'):
            if not self.controller.finalize_job():
                return False
            self.controller.on_panel_closed()
        return True

    def reject(self):
        """Called when the user clicks Standard Button Cancel / Close."""
        if hasattr(self, 'controller'):
            self.controller.cancel_job()
            self.controller.on_panel_closed()
            
        # Also ensure visibility is restored if controller didn't fully run
        for obj in self.hidden_originals:
             if hasattr(obj, "ViewObject"):
                 obj.ViewObject.Visibility = True
                 
        return True

    def _build_algorithm_group(self):
        """Builds the Algorithm group: the algorithm dropdown, each algorithm's
        parameters page (only the selected one is shown), and the shared Advanced section."""
        tr = QtWidgets.QApplication.translate
        group = QtWidgets.QGroupBox(tr("NestingPanel", "Algorithm"))
        form_layout = QtWidgets.QFormLayout()

        # The Advanced section places widgets the Minkowski page creates, so it comes after.
        self.minkowski_settings_group = self._build_minkowski_group()
        self.advanced_section = self._build_advanced_section()

        self.algorithm_dropdown = QtWidgets.QComboBox()
        for label, key in ((QT_TRANSLATE_NOOP("NestingPanel", "Greedy"), ALGO_GREEDY),
                           (QT_TRANSLATE_NOOP("NestingPanel", "Beam Search"), ALGO_BEAM)):
            self.algorithm_dropdown.addItem(tr("NestingPanel", label), key)
        self.algorithm_dropdown.setCurrentIndex(0)
        self.algorithm_dropdown.setToolTip(rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
            "NestingPanel",
            "Selects the placement strategy.\n"
            "<b>Greedy:</b> places parts one at a time at the best spot found. Fast.\n"
            "<b>Beam Search:</b> keeps the top partial layouts at every step. Slower, usually tighter.\n"
            "The Genetic Algorithm section below runs whichever you choose.\n"
            "<b>Default:</b> Greedy.")))
        self.algorithm_dropdown.currentIndexChanged.connect(
            lambda *_: self._on_algorithm_change(self.algorithm()))

        form_layout.addRow(tr("NestingPanel", "Placement:"), self.algorithm_dropdown)
        form_layout.addRow(self.minkowski_settings_group)
        form_layout.addRow(self.advanced_section)
        group.setLayout(form_layout)
        return group

    def _build_sheet_and_boundary_inputs(self):
        """Builds sheet dimension and boundary resolution inputs inside a group box."""
        tr = QtWidgets.QApplication.translate
        group = QtWidgets.QGroupBox(tr("NestingPanel", "Sheet Setup"))
        form_layout = QtWidgets.QFormLayout()

        self.sheet_width_input = make_quantity_spinbox(
            _DEFAULTS["default_sheet_width"], 1, 10000,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the Width of the stock sheet.\n<b>Default:</b> 600 mm."))
        )
        self.sheet_height_input = make_quantity_spinbox(
            _DEFAULTS["default_sheet_height"], 1, 10000,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the Height of the stock sheet.\n<b>Default:</b> 600 mm."))
        )
        self.sheet_thickness_input = make_quantity_spinbox(
            _DEFAULTS["sheet_thickness"], 0.1, 1000,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the Thickness of the stock sheet.\n<b>Default:</b> 3 mm."))
        )
        self.sheet_flip_combo = QtWidgets.QComboBox()
        self.sheet_flip_combo.addItem(tr("NestingPanel", "None (one side)"), "none")
        self.sheet_flip_combo.addItem(tr("NestingPanel", "About X (top edge to bottom edge)"), "x")
        self.sheet_flip_combo.addItem(tr("NestingPanel", "About Y (left edge to right edge)"), "y")
        self.sheet_flip_combo.setToolTip(rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
            "NestingPanel",
            "Turns this sheet over after the first side is cut to machine its back.\n"
            "Library sheets set this in the library; Custom sheets set it here.\n"
            "<b>Default:</b> None (one side).")))
        self.part_spacing_input = make_quantity_spinbox(
            _DEFAULTS["part_spacing"], 0, 1000,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
                "NestingPanel",
                "Sets the minimum gap kept between nested parts and sheet edges.\n"
                "<b>Lower:</b> tighter packing. <b>Higher:</b> leaves more clearance for wider cutters.\n"
                "0 lets parts touch.\n"
                "<b>Default:</b> 12.5 mm. <b>Typical:</b> 0–25 mm."))
        )

        self.deflection_input = make_double_spinbox(
            _DEFAULTS["deflection_angle"], 1, 90,
            step=1, decimals=0, suffix="°",
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
                "NestingPanel",
                "Sets the maximum angular deviation in ° when approximating curved edges as polygons.\n"
                "<b>Lower:</b> smoother curves, more vertices, slower nesting. <b>Higher:</b> coarser curves, fewer vertices, faster.\n"
                "<b>Default:</b> 30°. <b>Typical:</b> 5–30°."
            ))
        )

        self.simplification_input = make_quantity_spinbox(
            1.0, 0.001, 10.0,
            step=0.1,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
                "NestingPanel",
                "Sets the tolerance for removing redundant boundary vertices.\n"
                "<b>Lower:</b> keeps finer boundary detail, slower nesting. <b>Higher:</b> simpler outlines, faster nesting.\n"
                "<b>Default:</b> 1 mm. <b>Typical:</b> 0.1–5 mm."
            ))
        )

        self.sheet_combo = QtWidgets.QComboBox()
        self.sheet_combo.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP(
                "NestingPanel",
                "Selects a sheet from the library to fill in its size, thickness and flip.\n"
                "Library sheets lock those fields here; choose Custom to edit them directly.",
            ),
        ))
        self.sheet_library_button = QtWidgets.QToolButton()
        self.sheet_library_button.setText(tr("NestingPanel", "Library…"))
        self.sheet_library_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP(
                "NestingPanel",
                "Opens the Sheet Library to add or edit saved sheets and materials.",
            ),
        ))
        self.sheet_library_button.clicked.connect(self._open_sheet_library)
        self.sheet_combo.currentIndexChanged.connect(self._on_library_sheet_changed)

        sheet_row = QtWidgets.QHBoxLayout()
        sheet_row.setContentsMargins(*MARGINS_NONE)
        sheet_row.addWidget(self.sheet_combo, 1)
        sheet_row.addWidget(self.sheet_library_button)
        self._sheet_library = None
        self._resolved_sheets = {}

        self.sheet_material_label = QtWidgets.QLabel("—")
        self.sheet_material_label.setToolTip(rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
            "NestingPanel",
            "Shows the material of the selected library sheet, or — for a Custom sheet.")))

        self._sheet_rows = [
            sheet_sequence.custom_row(_DEFAULTS["default_sheet_width"], _DEFAULTS["default_sheet_height"], _DEFAULTS["sheet_thickness"])
        ]
        self._updating_editor = False

        self.sheet_list = QtWidgets.QListWidget()
        self.sheet_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.sheet_list.setFixedHeight(self.sheet_list.fontMetrics().height() * 5 + 16)
        self.sheet_list.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP(
                "NestingPanel",
                "Lists the sheets used in order; the last sheet repeats when more are needed.\n"
                "'+ back' means the sheet is flipped to machine its back side.",
            ),
        ))
        self.sheet_list.currentRowChanged.connect(self._on_sheet_list_row_changed)

        self.sheet_add_button = QtWidgets.QToolButton()
        self.sheet_add_button.setText(tr("NestingPanel", "Add"))
        self.sheet_add_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP("NestingPanel", "Adds a copy of the selected sheet to the sequence."),
        ))
        self.sheet_add_button.clicked.connect(self._on_sheet_add)

        self.sheet_remove_button = QtWidgets.QToolButton()
        self.sheet_remove_button.setText(tr("NestingPanel", "Remove"))
        self.sheet_remove_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP("NestingPanel", "Removes the selected sheet from the sequence."),
        ))
        self.sheet_remove_button.clicked.connect(self._on_sheet_remove)

        self.sheet_up_button = QtWidgets.QToolButton()
        self.sheet_up_button.setText(tr("NestingPanel", "Up"))
        self.sheet_up_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP("NestingPanel", "Moves the selected sheet earlier in the sequence."),
        ))
        self.sheet_up_button.clicked.connect(self._on_sheet_up)

        self.sheet_down_button = QtWidgets.QToolButton()
        self.sheet_down_button.setText(tr("NestingPanel", "Down"))
        self.sheet_down_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP("NestingPanel", "Moves the selected sheet later in the sequence."),
        ))
        self.sheet_down_button.clicked.connect(self._on_sheet_down)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(*MARGINS_NONE)
        button_row.addWidget(self.sheet_add_button)
        button_row.addWidget(self.sheet_remove_button)
        button_row.addWidget(self.sheet_up_button)
        button_row.addWidget(self.sheet_down_button)
        button_row.addStretch()

        sheet_list_col = QtWidgets.QVBoxLayout()
        sheet_list_col.setContentsMargins(*MARGINS_NONE)
        sheet_list_col.addWidget(self.sheet_list)
        sheet_list_col.addLayout(button_row)

        form_layout.addRow(tr("NestingPanel", "Sheets:"), sheet_list_col)
        form_layout.addRow(tr("NestingPanel", "Sheet:"), sheet_row)
        form_layout.addRow(tr("NestingPanel", "Material:"), self.sheet_material_label)
        form_layout.addRow(tr("NestingPanel", "Sheet Width:"), self.sheet_width_input)
        form_layout.addRow(tr("NestingPanel", "Sheet Height:"), self.sheet_height_input)
        form_layout.addRow(tr("NestingPanel", "Sheet Thickness:"), self.sheet_thickness_input)
        form_layout.addRow(tr("NestingPanel", "Flip:"), self.sheet_flip_combo)
        form_layout.addRow(tr("NestingPanel", "Part Spacing:"), self.part_spacing_input)

        self.sheet_width_input.valueChanged.connect(self._on_sheet_spinbox_changed)
        self.sheet_height_input.valueChanged.connect(self._on_sheet_spinbox_changed)
        self.sheet_thickness_input.valueChanged.connect(self._on_sheet_spinbox_changed)
        self.sheet_flip_combo.currentIndexChanged.connect(self._on_sheet_spinbox_changed)

        curve_settings_layout = QtWidgets.QHBoxLayout()
        curve_settings_layout.addWidget(QtWidgets.QLabel(tr("NestingPanel", "Curve:")))
        curve_settings_layout.addWidget(self.deflection_input)
        curve_settings_layout.addWidget(QtWidgets.QLabel(tr("NestingPanel", "Simplify:")))
        curve_settings_layout.addWidget(self.simplification_input)
        form_layout.addRow(tr("NestingPanel", "Bounds Resolution:"), curve_settings_layout)

        group.setLayout(form_layout)
        self._refresh_sheet_list()
        self._load_row_into_editor(0)
        self._update_sheet_buttons()
        return group

    def _sheet_row_text(self, index):
        """Display text for row index in the sheet list."""
        tr = QtWidgets.QApplication.translate
        row = self._sheet_rows[index]
        name = row["sheet_name"] or tr("NestingPanel", "Custom")
        w = float(row["width"])
        h = float(row["height"])
        t = float(row["thickness"])
        text = f"{index + 1}. {name} — {format_length(w)} × {format_length(h)} × {format_length(t)}"
        flip = row["flip"]
        if flip in ("x", "y"):
            text += tr("NestingPanel", " + back (flip %1)").replace("%1", flip.upper())
        if index == len(self._sheet_rows) - 1:
            text += tr("NestingPanel", " (repeats)")
        return text

    def _refresh_sheet_list(self):
        """Rewrites every item's text and updates item count to match self._sheet_rows."""
        selected = self.selected_sheet_row()
        self.sheet_list.blockSignals(True)
        self.sheet_list.clear()
        for i in range(len(self._sheet_rows)):
            self.sheet_list.addItem(self._sheet_row_text(i))
        if self._sheet_rows:
            target = max(0, min(selected, len(self._sheet_rows) - 1))
            self.sheet_list.setCurrentRow(target)
        self.sheet_list.blockSignals(False)

    def _update_sheet_buttons(self):
        """Recomputes button enabled states based on current selection and row count."""
        count = len(self._sheet_rows)
        row = self.selected_sheet_row()
        self.sheet_add_button.setEnabled(count > 0 and 0 <= row < count)
        self.sheet_remove_button.setEnabled(count > 1 and 0 <= row < count)
        self.sheet_up_button.setEnabled(count > 1 and row > 0)
        self.sheet_down_button.setEnabled(count > 1 and 0 <= row < count - 1)

    def _selected_flip(self):
        return self.sheet_flip_combo.itemData(self.sheet_flip_combo.currentIndex()) or "none"

    def _load_row_into_editor(self, index):
        """Load sheet row index into combo and spinboxes."""
        if not (0 <= index < len(self._sheet_rows)):
            return
        row = self._sheet_rows[index]
        self._updating_editor = True
        try:
            self.sheet_combo.blockSignals(True)
            sheet_id = row.get("sheet_id", "")
            if sheet_id and sheet_id in self._resolved_sheets:
                combo_idx = self.sheet_combo.findData(sheet_id)
                self.sheet_combo.setCurrentIndex(combo_idx if combo_idx >= 0 else 0)
                self.apply_library_sheet(self._resolved_sheets[sheet_id])
            else:
                self.sheet_combo.setCurrentIndex(0)
                self.sheet_width_input.setValue(float(row["width"]))
                self.sheet_height_input.setValue(float(row["height"]))
                self.sheet_thickness_input.setValue(float(row["thickness"]))
                flip_idx = self.sheet_flip_combo.findData(row["flip"])
                self.sheet_flip_combo.setCurrentIndex(flip_idx if flip_idx >= 0 else 0)
                self.release_library_sheet()
            self.sheet_combo.blockSignals(False)
        finally:
            self._updating_editor = False

    def _on_sheet_list_row_changed(self, row):
        if row >= 0:
            self._load_row_into_editor(row)
            self._update_sheet_buttons()

    def _on_sheet_spinbox_changed(self, *args):
        if self._updating_editor:
            return
        # write the value into the selected row only when the combo is on Custom
        combo_id = self.sheet_combo.itemData(self.sheet_combo.currentIndex()) or ""
        if combo_id:
            return
        row_idx = self.selected_sheet_row()
        if not (0 <= row_idx < len(self._sheet_rows)):
            return
        self._sheet_rows[row_idx] = sheet_sequence.custom_row(
            self.sheet_width_input.value(),
            self.sheet_height_input.value(),
            self.sheet_thickness_input.value(),
            self._selected_flip(),
        )
        item = self.sheet_list.item(row_idx)
        if item is not None:
            item.setText(self._sheet_row_text(row_idx))

    def _on_sheet_add(self):
        sel = self.selected_sheet_row()
        if not (0 <= sel < len(self._sheet_rows)):
            sel = len(self._sheet_rows) - 1
        new_row = dict(self._sheet_rows[sel])
        insert_idx = sel + 1
        self._sheet_rows.insert(insert_idx, new_row)
        self.set_sheet_sequence(self._sheet_rows, selected=insert_idx)

    def _on_sheet_remove(self):
        if len(self._sheet_rows) <= 1:
            return
        sel = self.selected_sheet_row()
        if not (0 <= sel < len(self._sheet_rows)):
            return
        del self._sheet_rows[sel]
        new_sel = min(sel, len(self._sheet_rows) - 1)
        self.set_sheet_sequence(self._sheet_rows, selected=new_sel)

    def _on_sheet_up(self):
        sel = self.selected_sheet_row()
        if sel <= 0:
            return
        self._sheet_rows[sel - 1], self._sheet_rows[sel] = self._sheet_rows[sel], self._sheet_rows[sel - 1]
        self.set_sheet_sequence(self._sheet_rows, selected=sel - 1)

    def _on_sheet_down(self):
        sel = self.selected_sheet_row()
        if sel < 0 or sel >= len(self._sheet_rows) - 1:
            return
        self._sheet_rows[sel], self._sheet_rows[sel + 1] = self._sheet_rows[sel + 1], self._sheet_rows[sel]
        self.set_sheet_sequence(self._sheet_rows, selected=sel + 1)

    def selected_sheet_row(self):
        """Current selected row index in the sheet list (0-based), or 0."""
        row = self.sheet_list.currentRow()
        return max(0, row) if self._sheet_rows else 0

    def sheet_sequence(self):
        """Copies of the rows, in order."""
        return [dict(r) for r in self._sheet_rows]

    def set_sheet_sequence(self, rows, selected=0):
        """Replace every row (non-empty list), select *selected* (clamped), load it
        into the editor, refresh the list and the buttons."""
        if not rows:
            raise ValueError("sheet sequence must be a non-empty list")
        self._sheet_rows = [dict(r) for r in rows]
        target = max(0, min(selected, len(self._sheet_rows) - 1))
        self.sheet_list.blockSignals(True)
        self.sheet_list.clear()
        for i in range(len(self._sheet_rows)):
            self.sheet_list.addItem(self._sheet_row_text(i))
        self.sheet_list.setCurrentRow(target)
        self.sheet_list.blockSignals(False)
        self._load_row_into_editor(target)
        self._update_sheet_buttons()

    def _open_sheet_library(self):
        """Open the Library window on the Sheets tab, then refresh the Sheet combo."""
        from ...Tools.Library.ui_library import LibraryDialog
        LibraryDialog(self, tab="sheets").exec_()
        converted = self.populate_library_sheets()
        if converted:
            rows_str = ", ".join(str(r) for r in converted)
            self.log_message(
                f"Sheet row(s) {rows_str} are no longer in the sheet library; "
                f"they are now Custom with their last size.",
                level="warning")

    def _reconcile_rows_with_library(self):
        """Reconcile all sheet rows with current library state. Returns 1-based converted row numbers."""
        converted = []
        for i, row in enumerate(self._sheet_rows):
            sheet_id = row.get("sheet_id", "")
            if not sheet_id:
                continue
            record = self._resolved_sheets.get(sheet_id)
            is_selectable = False
            if record is not None:
                combo_idx = self.sheet_combo.findData(sheet_id)
                if combo_idx > 0:
                    is_selectable = True
            if is_selectable:
                self._sheet_rows[i] = sheet_sequence.library_row(record)
            else:
                self._sheet_rows[i] = sheet_sequence.custom_row(
                    row["width"], row["height"], row["thickness"], row["flip"]
                )
                converted.append(i + 1)
        return converted

    def populate_library_sheets(self, select_id=None):
        """Refill the Sheet combo from disk, reconcile sequence rows, and reload editor.

        Returns the list of 1-based row numbers that were converted to Custom.
        """
        from ...sheet_library import model, store
        tr = QtWidgets.QApplication.translate
        self._sheet_library = store.load_library()
        combo = self.sheet_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr("NestingPanel", "Custom"), "")
        hidden = []
        rows = model.sheet_rows(self._sheet_library)
        self._resolved_sheets = {sid: rec for _d, sid, _n, rec, err in rows if err is None}
        for depth, sheet_id, name, _sheet, error in rows:
            if error:
                hidden.append(f"'{name}' ({error})")
            else:
                combo.addItem(name, sheet_id)
        combo.blockSignals(False)

        if hidden:
            nw_logger.warn(f"Sheet library: {len(hidden)} sheet(s) left out of the picker: {'; '.join(hidden)}")

        converted = self._reconcile_rows_with_library()
        self._refresh_sheet_list()
        self._load_row_into_editor(self.selected_sheet_row())
        self._update_sheet_buttons()
        return converted

    def apply_library_sheet(self, sheet):
        """Show a library sheet and lock what it decides."""
        self.sheet_width_input.setValue(sheet["width"])
        self.sheet_height_input.setValue(sheet["height"])
        self.sheet_thickness_input.setValue(sheet["thickness"])
        flip_idx = self.sheet_flip_combo.findData(sheet["flip"])
        self.sheet_flip_combo.setCurrentIndex(flip_idx if flip_idx >= 0 else 0)
        self.sheet_material_label.setText(sheet["material"] or "—")
        for widget in (self.sheet_width_input, self.sheet_height_input, self.sheet_thickness_input, self.sheet_flip_combo):
            widget.setEnabled(False)

    def release_library_sheet(self):
        """Custom: give width, height, thickness and flip back to the user; values are kept."""
        for widget in (self.sheet_width_input, self.sheet_height_input, self.sheet_thickness_input, self.sheet_flip_combo):
            widget.setEnabled(True)
        self.sheet_material_label.setText("—")

    def _on_library_sheet_changed(self, index):
        if self._updating_editor:
            return
        sheet_id = self.sheet_combo.itemData(index) or ""
        row_idx = self.selected_sheet_row()
        if not (0 <= row_idx < len(self._sheet_rows)):
            return
        if not sheet_id:
            self._sheet_rows[row_idx] = sheet_sequence.custom_row(
                self.sheet_width_input.value(),
                self.sheet_height_input.value(),
                self.sheet_thickness_input.value(),
                self._selected_flip(),
            )
            self.release_library_sheet()
        else:
            record = self._resolved_sheets[sheet_id]
            self._sheet_rows[row_idx] = sheet_sequence.library_row(record)
            self.apply_library_sheet(record)
        item = self.sheet_list.item(row_idx)
        if item is not None:
            item.setText(self._sheet_row_text(row_idx))

    def _build_minkowski_group(self):
        """Builds the Minkowski parameters page shown inside the Algorithm group."""
        tr = QtWidgets.QApplication.translate
        page = QtWidgets.QWidget()
        form_layout = QtWidgets.QFormLayout()
        form_layout.setContentsMargins(*MARGINS_NONE)
        form_layout.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)

        self.minkowski_direction_dial = DirectionDial()

        self.evolve_direction_checkbox = make_checkbox(
            tr("NestingPanel", "Let the GA Vary Direction"),
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
                "NestingPanel",
                "Lets the GA vary the nesting direction across population members (Greedy only; the first member uses the dial)."))
        )

        self.direction_dial_label = QtWidgets.QLabel(tr("NestingPanel", "Nesting Direction:"))

        self.beam_width_input = make_int_spinbox(
            _DEFAULTS["beam_width"], 1, 64,
            tooltip=rich_tooltip("NestingPanel", _BEAM_WIDTH_TOOLTIP)
        )
        self.beam_max_part_branches_input = make_int_spinbox(
            _DEFAULTS["max_part_branches"], 1, 16,
            tooltip=rich_tooltip("NestingPanel", _BEAM_PART_BRANCHES_TOOLTIP)
        )
        self.beam_max_rot_branches_input = make_int_spinbox(
            _DEFAULTS["max_rot_branches"], 1, 32,
            tooltip=rich_tooltip("NestingPanel", _BEAM_ROT_BRANCHES_TOOLTIP)
        )
        self.beam_width_decay_input = make_double_spinbox(
            _DEFAULTS["beam_width_decay"], 0.5, 1.0, step=0.05, decimals=2,
            tooltip=rich_tooltip("NestingPanel", _BEAM_DECAY_TOOLTIP)
        )
        self.beam_iterative_checkbox = make_checkbox(
            tr("NestingPanel", "Iterative Anytime"),
            checked=_DEFAULTS["beam_iterative"],
            tooltip=rich_tooltip("NestingPanel", _BEAM_ITERATIVE_TOOLTIP)
        )

        self.beam_preset_dropdown = QtWidgets.QComboBox()
        for label, *_rest in _BEAM_PRESETS:
            self.beam_preset_dropdown.addItem(
                QtWidgets.QApplication.translate("NestingPanel", label)
            )
        self.beam_preset_dropdown.addItem(
            QtWidgets.QApplication.translate("NestingPanel", _BEAM_CUSTOM_LABEL)
        )
        self.beam_preset_dropdown.setCurrentIndex(0)
        self.beam_preset_dropdown.setToolTip(
            rich_tooltip("NestingPanel", _BEAM_PRESET_TOOLTIP)
        )

        self.beam_warning_label = QtWidgets.QLabel()
        self.beam_warning_label.setStyleSheet("color: #d97706; font-size: 11px;")
        self.beam_warning_label.setWordWrap(True)
        self.beam_warning_label.setVisible(False)

        self._updating_beam_preset = False

        def _on_beam_preset_changed(idx):
            if self._updating_beam_preset:
                return
            self._updating_beam_preset = True
            try:
                if 0 <= idx < len(_BEAM_PRESETS):
                    _, w, pb, rb, it, dc = _BEAM_PRESETS[idx]
                    self.beam_width_input.setValue(w)
                    self.beam_width_decay_input.setValue(dc)
                    self.beam_max_part_branches_input.setValue(pb)
                    self.beam_max_rot_branches_input.setValue(rb)
                    self.beam_iterative_checkbox.setChecked(it)
            finally:
                self._updating_beam_preset = False
            _sync_beam_warning_and_preset()

        def _sync_beam_warning_and_preset():
            w = self.beam_width_input.value()
            pb = self.beam_max_part_branches_input.value()
            rb = self.beam_max_rot_branches_input.value()
            it = self.beam_iterative_checkbox.isChecked()
            dc = self.beam_width_decay_input.value()

            if w > 16:
                self.beam_warning_label.setText(
                    QtWidgets.QApplication.translate("NestingPanel", _BEAM_WARNING_TEXT)
                )
                self.beam_warning_label.setVisible(True)
            else:
                self.beam_warning_label.setVisible(False)

            if not self._updating_beam_preset:
                self._updating_beam_preset = True
                try:
                    matched_idx = -1
                    for i, (_, pw, ppb, prb, pit, pdc) in enumerate(_BEAM_PRESETS):
                        if w == pw and pb == ppb and rb == prb and it == pit and abs(dc - pdc) < 1e-9:
                            matched_idx = i
                            break
                    if matched_idx != -1:
                        self.beam_preset_dropdown.setCurrentIndex(matched_idx)
                    else:
                        self.beam_preset_dropdown.setCurrentIndex(len(_BEAM_PRESETS))
                finally:
                    self._updating_beam_preset = False

        self.beam_preset_dropdown.currentIndexChanged.connect(_on_beam_preset_changed)
        self.beam_width_input.valueChanged.connect(lambda *a: _sync_beam_warning_and_preset())
        self.beam_width_decay_input.valueChanged.connect(lambda *a: _sync_beam_warning_and_preset())
        self.beam_max_part_branches_input.valueChanged.connect(lambda *a: _sync_beam_warning_and_preset())
        self.beam_max_rot_branches_input.valueChanged.connect(lambda *a: _sync_beam_warning_and_preset())
        self.beam_iterative_checkbox.toggled.connect(lambda *a: _sync_beam_warning_and_preset())

        self.minkowski_population_size_input = make_int_spinbox(
            _DEFAULTS["ga_population"], 1, 500,
            tooltip=rich_tooltip("NestingPanel", _GA_POP_TOOLTIP)
        )
        self.ga_winners_input = make_int_spinbox(
            _DEFAULTS["ga_winners"], 1, 500,
            tooltip=rich_tooltip("NestingPanel", _GA_WINNERS_TOOLTIP)
        )
        self.ga_mutation_rate_input = make_double_spinbox(
            _DEFAULTS["ga_mutation_rate"], 0.0, 1.0, step=0.05, decimals=2,
            tooltip=rich_tooltip("NestingPanel", _GA_MUTATION_TOOLTIP)
        )
        # Winners can't exceed the population. The box shows min(chosen, population)
        # and remembers the chosen value, so population 1 shows 1 and a later,
        # larger population gets the chosen value back. Prefs and layouts store the
        # chosen value (ga_winners()); the coordinator clamps it as well.
        self._ga_winners_chosen = _DEFAULTS["ga_winners"]
        self._clamping_ga_winners = False

        def _on_ga_winners_changed(value):
            if not self._clamping_ga_winners:
                self._ga_winners_chosen = value

        self.ga_winners_input.valueChanged.connect(_on_ga_winners_changed)
        self.minkowski_population_size_input.valueChanged.connect(
            lambda *_: self._clamp_ga_winners())
        self._clamp_ga_winners()
        self.minkowski_generations_input = make_int_spinbox(
            _DEFAULTS["ga_generations"], 1, 1000,
            tooltip=rich_tooltip("NestingPanel", _GA_GEN_TOOLTIP)
        )
        self.ga_preset_dropdown = QtWidgets.QComboBox()
        for label, _, _ in _GA_PRESETS:
            self.ga_preset_dropdown.addItem(
                QtWidgets.QApplication.translate("NestingPanel", label)
            )
        self.ga_preset_dropdown.addItem(
            QtWidgets.QApplication.translate("NestingPanel", _GA_CUSTOM_LABEL)
        )
        self.ga_preset_dropdown.setCurrentIndex(0)
        self.ga_preset_dropdown.setToolTip(
            rich_tooltip("NestingPanel", _GA_PRESET_TOOLTIP)
        )
        self.ga_warning_label = QtWidgets.QLabel()
        self.ga_warning_label.setStyleSheet("color: #d97706; font-size: 11px;")
        self.ga_warning_label.setWordWrap(True)
        self.ga_warning_label.setVisible(False)
        self._updating_ga_preset = False

        def _on_ga_preset_changed(idx):
            if self._updating_ga_preset:
                return
            self._updating_ga_preset = True
            try:
                if 0 <= idx < len(_GA_PRESETS):
                    _, pop, gen = _GA_PRESETS[idx]
                    self.minkowski_population_size_input.setValue(pop)
                    self.minkowski_generations_input.setValue(gen)
            finally:
                self._updating_ga_preset = False
            _sync_ga_warning_and_preset()

        def _sync_ga_warning_and_preset():
            pop = self.minkowski_population_size_input.value()
            gen = self.minkowski_generations_input.value()

            # Soft warning on extreme settings
            # Thresholds from the GA sizing benchmark: fitness was identical at 20
            # and 40 generations on both corpora, and populations above 10 never
            # improved on it, so 20 generations is where extra work stops buying
            # anything. Keep this above the Thorough preset (10/20) or the panel
            # warns about its own recommendation.
            if pop > 40 or gen > 20:
                self.ga_warning_label.setText(
                    QtWidgets.QApplication.translate("NestingPanel", _GA_WARNING_TEXT)
                )
                self.ga_warning_label.setVisible(True)
            else:
                self.ga_warning_label.setVisible(False)

            if not self._updating_ga_preset:
                self._updating_ga_preset = True
                try:
                    matched_idx = -1
                    for i, (_, p, g) in enumerate(_GA_PRESETS):
                        if pop == p and gen == g:
                            matched_idx = i
                            break
                    if matched_idx != -1:
                        self.ga_preset_dropdown.setCurrentIndex(matched_idx)
                    else:
                        self.ga_preset_dropdown.setCurrentIndex(len(_GA_PRESETS))
                finally:
                    self._updating_ga_preset = False

        self.ga_preset_dropdown.currentIndexChanged.connect(_on_ga_preset_changed)
        self.minkowski_population_size_input.valueChanged.connect(lambda *a: _sync_ga_warning_and_preset())
        self.minkowski_generations_input.valueChanged.connect(lambda *a: _sync_ga_warning_and_preset())

        self.part_direction_dial = DirectionDial()
        self.part_direction_dial.setDegrees(_DEFAULTS["part_direction"])
        self.part_direction_dial.setToolTip(rich_tooltip("NestingPanel", _PART_DIRECTION_TOOLTIP))
        self.weight_sliders = WeightSliders([
            ("gravity", tr("NestingPanel", "Gravity:"), _DEFAULTS["weight_gravity"],
             rich_tooltip("NestingPanel", _WEIGHT_GRAVITY_TOOLTIP)),
            ("contact", tr("NestingPanel", "Contact:"), _DEFAULTS["weight_contact"],
             rich_tooltip("NestingPanel", _WEIGHT_CONTACT_TOOLTIP)),
            ("direction", tr("NestingPanel", "Direction:"), _DEFAULTS["weight_direction"],
             rich_tooltip("NestingPanel", _WEIGHT_DIRECTION_TOOLTIP)),
            ("offcut", tr("NestingPanel", "Offcuts:"), _DEFAULTS["weight_offcut"],
             rich_tooltip("NestingPanel", _WEIGHT_OFFCUT_TOOLTIP)),
        ])
        self.fitness_min_offcut_input = make_area_spinbox(
            _DEFAULTS["fitness_min_offcut"], 1.0, 1.0e8,
            tooltip=rich_tooltip("NestingPanel", _FITNESS_MIN_OFFCUT_TOOLTIP))

        self.minkowski_rotation_steps_slider = make_slider(
            0, len(self.rotation_angles) - 1, 3,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
                "NestingPanel", "Sets the angle step parts may be rotated by while nesting.\n"
                "<b>Smaller steps:</b> tighter fits, slower nesting. <b>Larger steps:</b> fewer orientations, faster.\n"
                "<b>Default:</b> 90° (4 steps).")))
        self.minkowski_rotation_display_label = QtWidgets.QLabel("")
        self.minkowski_rotation_display_label.setFixedWidth(100)
        self.minkowski_rotation_steps_slider.valueChanged.connect(lambda *_: self._update_rotation_label())

        mink_rot_layout = QtWidgets.QHBoxLayout()
        mink_rot_layout.addWidget(self.minkowski_rotation_steps_slider)
        mink_rot_layout.addWidget(self.minkowski_rotation_display_label)
        mink_rot_layout.setContentsMargins(*MARGINS_NONE)

        form_layout.addRow(tr("NestingPanel", "Rotation Angle:"), mink_rot_layout)

        self.minkowski_candidate_spacing_input = make_quantity_spinbox(
            _DEFAULTS["candidate_spacing"], 1.0, 100.0, step=0.5,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
                "NestingPanel",
                "Sets distance between candidate points when checking part placement. A smaller value "
                "means more candidate points which makes it more likely to pack tightly. This can make "
                "nesting slower, especially for large parts.\n"
                "Left: 5 mm spacing. Right: 20 mm, which misses the tight corner."),
                image="NestingWorkbench_CandidateSpacing.svg"))

        translate = QtWidgets.QApplication.translate
        # Detected once: physical_core_count() shells out to sysctl on macOS.
        # When it falls back to logical processors, the coordinator warns at
        # run time (WPS-004).
        auto_cores, _ = auto_core_count()
        self.worker_processes_input = make_int_spinbox(
            0, 0, os.cpu_count() or 1,
            tooltip=rich_tooltip("NestingPanel", _WORKERS_TIP))

        def update_auto_label(population):
            self.worker_processes_input.setSpecialValueText(
                translate("NestingPanel", _WORKERS_AUTO_FMT).format(
                    balanced_worker_count(population, auto_cores)))

        self.minkowski_population_size_input.valueChanged.connect(update_auto_label)
        update_auto_label(self.minkowski_population_size_input.value())

        self.beam_section = CollapsibleSection(tr("NestingPanel", "Beam Search"), expanded=True)
        self.beam_section.addRow(tr("NestingPanel", "Preset:"), self.beam_preset_dropdown)
        self.beam_section.addRow(tr("NestingPanel", "Beam Width:"), self.beam_width_input)
        self.beam_section.addRow(tr("NestingPanel", "Width Decay:"), self.beam_width_decay_input)
        self.beam_section.addRow(tr("NestingPanel", "Max Part Branches:"), self.beam_max_part_branches_input)
        self.beam_section.addRow(tr("NestingPanel", "Max Rotation Branches:"), self.beam_max_rot_branches_input)
        self.beam_section.addRow(self.beam_iterative_checkbox)
        self.beam_section.addRow(self.beam_warning_label)
        form_layout.addRow(self.beam_section)

        self.ga_section = CollapsibleSection(tr("NestingPanel", "Genetic Algorithm"), expanded=True)
        self.ga_section.addRow(tr("NestingPanel", "Preset:"), self.ga_preset_dropdown)
        self.ga_section.addRow(tr("NestingPanel", "Generations:"), self.minkowski_generations_input)
        self.ga_section.addRow(tr("NestingPanel", "Population Size:"), self.minkowski_population_size_input)
        self.ga_section.addRow(tr("NestingPanel", "Winners:"), self.ga_winners_input)
        self.ga_section.addRow(tr("NestingPanel", "Mutation Rate:"), self.ga_mutation_rate_input)
        self.ga_section.addRow(self.ga_warning_label)
        form_layout.addRow(self.ga_section)

        # One Scoring section (NLA ruling 29): the dials and the four weights score both single
        # placements and finished layouts, for every placer.
        self.scoring_section = CollapsibleSection(tr("NestingPanel", "Scoring"), expanded=True)
        self.scoring_section.addRow(self.direction_dial_label, self.minkowski_direction_dial)
        self.scoring_section.addRow(self.evolve_direction_checkbox)
        self.scoring_section.addRow(tr("NestingPanel", "Part Direction:"), self.part_direction_dial)
        self.scoring_section.addRow(self.weight_sliders)
        self.scoring_section.addRow(tr("NestingPanel", "Minimum Offcut:"), self.fitness_min_offcut_input)
        form_layout.addRow(self.scoring_section)

        page.setLayout(form_layout)
        return page

    def _build_advanced_section(self):
        """Builds the one Advanced section, shared by every algorithm."""
        tr = QtWidgets.QApplication.translate
        self.simulate_combo = QtWidgets.QComboBox()
        # Order must match SIM_OFF, SIM_SINGLE, SIM_ALL.
        self.simulate_combo.addItem(tr("NestingPanel", "Off"))
        self.simulate_combo.addItem(tr("NestingPanel", "Single"))
        self.simulate_combo.addItem(tr("NestingPanel", "All"))
        self.simulate_combo.setCurrentIndex(SIM_SINGLE)
        self.simulate_combo.setToolTip(rich_tooltip("NestingPanel", _SIM_TIP))
        self.clear_cache_checkbox = make_checkbox(
            tr("NestingPanel", "Clear NFP Cache"),
            checked=False,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Forces recalculation of No-Fit Polygons. Slower, but resolves potential caching issues."))
        )

        spacing_label = QtWidgets.QLabel(tr("NestingPanel", "Candidate Spacing:"))
        workers_label = QtWidgets.QLabel(tr("NestingPanel", _WORKERS_LABEL))

        section = CollapsibleSection(tr("NestingPanel", "Advanced"), expanded=False)
        section.addRow(tr("NestingPanel", _SIM_LABEL), self.simulate_combo)
        section.addRow(spacing_label, self.minkowski_candidate_spacing_input)
        section.addRow(workers_label, self.worker_processes_input)
        section.addRow(self.clear_cache_checkbox)
        return section

    def _build_font_layout(self):
        """Builds the font selection button and label layout."""
        tr = QtWidgets.QApplication.translate
        font_layout = QtWidgets.QHBoxLayout()
        self.font_select_button = QtWidgets.QPushButton(tr("NestingPanel", "Select Font"))
        self.font_select_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP("NestingPanel", "Chooses a TrueType (.ttf) or OpenType (.otf) font file for part identifier labels."),
        ))
        self.font_label = QtWidgets.QLabel(tr("NestingPanel", "No Font Selected"))
        self.font_label.setWordWrap(True)
        font_layout.addWidget(self.font_select_button)
        font_layout.addWidget(self.font_label)
        return font_layout

    def _build_label_options_row(self):
        """Builds the label options row with size and height inputs."""
        tr = QtWidgets.QApplication.translate
        self.add_labels_checkbox = make_checkbox(
            tr("NestingPanel", "Add Identifier Labels"),
            checked=True,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Adds an identifier label to each nested part."))
        )

        self.label_size_input = make_quantity_spinbox(
            10.0, 1, 100,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the text height of part identifier labels.\n<b>Default:</b> 10 mm. <b>Typical:</b> 5–25 mm."))
        )

        self.label_height_input = make_quantity_spinbox(
            25.0, 0, 1000,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the Z-offset height for part identifier labels.\n<b>Default:</b> 25 mm."))
        )

        label_options_layout = QtWidgets.QHBoxLayout()
        label_options_layout.addWidget(self.add_labels_checkbox)
        label_options_layout.addWidget(QtWidgets.QLabel(tr("NestingPanel", "Size:")))
        label_options_layout.addWidget(self.label_size_input)
        label_options_layout.addWidget(QtWidgets.QLabel(tr("NestingPanel", "Height (Z):")))
        label_options_layout.addWidget(self.label_height_input)
        label_options_layout.addStretch()
        return label_options_layout

    def _build_display_options_row(self):
        """Builds the Show Bounds and Show Offcuts row, shown for every algorithm."""
        tr = QtWidgets.QApplication.translate
        self.show_bounds_checkbox = make_checkbox(
            tr("NestingPanel", "Show Bounds"),
            checked=True,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Draws the sheet and part bounds in the 3D view while nesting."))
        )
        self.show_offcuts_checkbox = make_checkbox(
            tr("NestingPanel", "Show Offcuts"),
            checked=True,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Tints the offcuts on each sheet: the free rectangles of at least Minimum Offcut that the Offcuts score counts. Display only; they are never exported or cut."))
        )
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.show_bounds_checkbox)
        row.addWidget(self.show_offcuts_checkbox)
        row.addStretch()
        return row

    def _build_parts_table_and_buttons(self, main_layout):
        """Builds the parts table and Add/Remove buttons."""
        tr = QtWidgets.QApplication.translate
        self.shape_table = QtWidgets.QTableWidget()
        self.shape_table.setColumnCount(5)
        self.shape_table.setHorizontalHeaderLabels([
            tr("NestingPanel", "Shape"),
            tr("NestingPanel", "Quantity"),
            tr("NestingPanel", "Rotations"),
            tr("NestingPanel", "Up Dir"),
            tr("NestingPanel", "Fill"),
        ])
        self.shape_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)

        self.add_parts_button = QtWidgets.QPushButton(tr("NestingPanel", "Add Selected"))
        self.add_parts_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP("NestingPanel", "Adds the shapes currently selected in the FreeCAD document to the parts table. On a reopened layout, the parts already in it keep their settings."),
        ))
        self.remove_parts_button = QtWidgets.QPushButton(tr("NestingPanel", "Remove Selected"))
        self.remove_parts_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP("NestingPanel", "Removes the selected rows from the parts table."),
        ))

        table_button_layout = QtWidgets.QHBoxLayout()
        table_button_layout.addWidget(self.add_parts_button)
        table_button_layout.addWidget(self.remove_parts_button)

        self.all_quantity_spinbox = make_int_spinbox(
            1, 1, 500,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the copy count applied when clicking Set All.\n<b>Default:</b> 1."))
        )
        self.set_all_quantity_button = QtWidgets.QPushButton(tr("NestingPanel", "Set All"))
        self.set_all_quantity_button.setToolTip(rich_tooltip(
            "NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the quantity of every part in the table to this value.")))
        self.set_all_quantity_button.clicked.connect(self.set_all_quantities)

        all_quantity_layout = QtWidgets.QHBoxLayout()
        all_quantity_layout.addWidget(QtWidgets.QLabel(tr("NestingPanel", "Quantity of all parts:")))
        all_quantity_layout.addWidget(self.all_quantity_spinbox)
        all_quantity_layout.addWidget(self.set_all_quantity_button)
        all_quantity_layout.addStretch()

        main_layout.addWidget(self.shape_table)
        main_layout.addLayout(table_button_layout)
        main_layout.addLayout(all_quantity_layout)

    def set_all_quantities(self):
        """Sets every row's quantity to the 'all parts' spinbox value."""
        value = self.all_quantity_spinbox.value()
        for row in range(self.shape_table.rowCount()):
            spinbox = self.shape_table.cellWidget(row, 1)
            if spinbox is not None:
                spinbox.setValue(value)

    def _build_action_buttons(self):
        """Builds the main Run Nesting and Cancel action buttons."""
        tr = QtWidgets.QApplication.translate
        self.nest_button = QtWidgets.QPushButton(tr("NestingPanel", "Run Nesting"))
        self.nest_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP("NestingPanel", "Starts nesting the parts in the table onto the configured sheets."),
        ))
        self.cancel_button = QtWidgets.QPushButton(tr("NestingPanel", "Cancel Nesting"))
        self.cancel_button.setToolTip(rich_tooltip(
            "NestingPanel",
            QT_TRANSLATE_NOOP("NestingPanel", "Stops the running nesting job and keeps the best layout found so far."),
        ))
        self.cancel_button.setEnabled(False)

        action_button_layout = QtWidgets.QHBoxLayout()
        action_button_layout.addWidget(self.nest_button)
        action_button_layout.addWidget(self.cancel_button)
        return action_button_layout

    def _build_progress_and_status(self, main_layout):
        """Builds the progress bar and status message label."""
        tr = QtWidgets.QApplication.translate
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setVisible(False)

        self.status_label = QtWidgets.QLabel(tr("NestingPanel", "Select master shapes to nest."))
        self.status_label.setWordWrap(True)

        main_layout.addWidget(self.progressBar)
        main_layout.addWidget(self.status_label)

    def _setup_ui(self):
        tr = QtWidgets.QApplication.translate
        main_layout = QtWidgets.QVBoxLayout()
        form_layout = QtWidgets.QFormLayout()

        self.sheet_setup_group = self._build_sheet_and_boundary_inputs()
        form_layout.addRow(self.sheet_setup_group)

        self.algorithm_group = self._build_algorithm_group()
        form_layout.addRow(self.algorithm_group)

        font_layout = self._build_font_layout()
        form_layout.addRow(tr("NestingPanel", "Identifier Font:"), font_layout)

        label_options_layout = self._build_label_options_row()
        form_layout.addRow(label_options_layout)

        form_layout.addRow(self._build_display_options_row())
        main_layout.addLayout(form_layout)

        self._build_parts_table_and_buttons(main_layout)

        action_button_layout = self._build_action_buttons()
        main_layout.addLayout(action_button_layout)

        self._build_progress_and_status(main_layout)
        main_layout.addStretch()
        self.setLayout(main_layout)

        # Only after setLayout: until then the groups have no parent widget, and
        # setVisible(True) on a parentless widget pops it up as its own window.
        self._on_algorithm_change(self.algorithm())

        # Connect signals
        def toggle_label_inputs(enabled):
            self.label_size_input.setEnabled(enabled)
            self.label_height_input.setEnabled(enabled)

        self.add_labels_checkbox.toggled.connect(toggle_label_inputs)
        toggle_label_inputs(self.add_labels_checkbox.isChecked())

        # Connect the nesting controller
        from .nesting_controller import NestingController
        self.controller = NestingController(self)
        self.nest_button.clicked.connect(self.controller.execute_nesting)
        self.cancel_button.clicked.connect(self.controller.request_cancel)
        self.font_select_button.clicked.connect(self.select_font_file)
        self.show_bounds_checkbox.toggled.connect(lambda _on: self.controller.toggle_bounds_visibility())
        self.show_offcuts_checkbox.toggled.connect(lambda _on: self.controller.toggle_offcuts_visibility())
        self.add_parts_button.clicked.connect(self.controller.add_selected_shapes)
        self.remove_parts_button.clicked.connect(self.controller.remove_selected_shapes)

        self.load_persisted_settings()
        self._update_rotation_label()
        self.controller.load_selection()

    def add_part_row(self, row_index, label, quantity=1, rotation_steps=4, override_rotation=False, 
                       up_vector=None, fill_sheet=False):
        """Helper function to create and populate a single row in the parts table."""
        tr = QtWidgets.QApplication.translate
        label_item = QtWidgets.QTableWidgetItem(label)
        label_item.setFlags(label_item.flags() & ~QtCore.Qt.ItemIsEditable)

        quantity_spinbox = make_int_spinbox(
            quantity, 1, 500,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the Number of copies of this part to place.\n<b>Default:</b> 1."))
        )

        rotation_widget = QtWidgets.QWidget()
        rotation_layout = QtWidgets.QHBoxLayout(rotation_widget)
        rotation_layout.setContentsMargins(*MARGINS_NONE)

        override_checkbox = make_checkbox(
            "", checked=override_rotation,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Overrides the global rotation step count for this part."))
        )

        rotation_spinbox = make_int_spinbox(
            rotation_steps, 0, 360,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the number of rotation steps around 360° for this part.\n0 or 1 locks the part to its current angle.\n<b>Default:</b> 4 (90° steps)."))
        )
        rotation_spinbox.setEnabled(override_rotation)
        override_checkbox.toggled.connect(rotation_spinbox.setEnabled)

        rotation_layout.addWidget(override_checkbox)
        rotation_layout.addWidget(rotation_spinbox)

        if up_vector is None:
            up_vector = FreeCAD.Vector(0, 0, 1)

        up_vector_widget = QtWidgets.QWidget()
        up_vector_layout = QtWidgets.QHBoxLayout(up_vector_widget)
        up_vector_layout.setContentsMargins(*MARGINS_NONE)

        tooltip = rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the local axis of this 3D part that points up (+Z) before projecting to 2D.\n<b>Default:</b> (0, 0, 1)."))
        up_x_spinbox = make_double_spinbox(up_vector.x, -1.0, 1.0, decimals=2, tooltip=tooltip)
        up_x_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        up_y_spinbox = make_double_spinbox(up_vector.y, -1.0, 1.0, decimals=2, tooltip=tooltip)
        up_y_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        up_z_spinbox = make_double_spinbox(up_vector.z, -1.0, 1.0, decimals=2, tooltip=tooltip)
        up_z_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)

        up_vector_layout.addWidget(up_x_spinbox)
        up_vector_layout.addWidget(up_y_spinbox)
        up_vector_layout.addWidget(up_z_spinbox)

        fill_checkbox = make_checkbox(
            "", checked=fill_sheet,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Fills remaining space on the sheets with extra copies of this part after placing the required quantity."))
        )

        self.shape_table.setItem(row_index, 0, label_item)
        self.shape_table.setCellWidget(row_index, 1, quantity_spinbox)
        self.shape_table.setCellWidget(row_index, 2, rotation_widget)
        self.shape_table.setCellWidget(row_index, 3, up_vector_widget)
        self.shape_table.setCellWidget(row_index, 4, fill_checkbox)

    def select_font_file(self):
        """Opens a file dialog to let the user select a font file."""
        tr = QtWidgets.QApplication.translate
        default_font_dir = FONTS_DIR
        if not os.path.isdir(default_font_dir):
            default_font_dir = "" # Fallback if fonts dir doesn't exist

        file_dialog_result = QtWidgets.QFileDialog.getOpenFileName(
            self, 
            tr("NestingPanel", "Select Font File"), 
            default_font_dir, # Set the default directory
            tr("NestingPanel", "Font Files (*.ttf *.otf)")
        )
        font_path = file_dialog_result[0]

        if font_path:
            self.selected_font_path = font_path
            # Display just the filename for a cleaner UI
            self.font_label.setText(os.path.basename(font_path))

    def set_default_font(self):
        """Checks for and sets a default font on initialization."""
        try:
            default_font_path = DEFAULT_FONT

            if os.path.exists(default_font_path):
                self.selected_font_path = default_font_path
                self.font_label.setText(os.path.basename(default_font_path))
        except Exception as e:
            nw_logger.warn(f"[NestingPanel] Failed to set default font: {e}")

    def log_message(self, message, level="message"):
        """Displays a message in the status label and logs to the console."""
        try:
            self.status_label.setText(message)
        except RuntimeError as e:
            # The widget C++ object has been deleted (panel closed), but Python object persists.
            # We can just log to console and ignore the UI update.
            nw_logger.debug(f"[NestingPanel] status_label.setText skipped (widget deleted): {e}")

        if level == "warning":
            nw_logger.warn(message)
        else:
            nw_logger.info(message)
        
        # Process UI events to make sure the label updates immediately
        # We wrap this too, just in case
        try:
            FreeCADGui.updateGui()
        except RuntimeError as e:
            nw_logger.debug(f"[NestingPanel] updateGui skipped (GUI window closed): {e}")

    def load_persisted_settings(self):
        """Loads settings from FreeCAD preferences."""
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        text = prefs.GetString(PREF_SHEET_SEQUENCE, "")
        if text:
            try:
                rows = sheet_sequence.from_json(text)
            except ValueError as e:
                nw_logger.warn(f"Invalid saved sheet sequence ({e}); using default Custom sheet.")
                rows = None
        else:
            rows = None

        if rows:
            self.set_sheet_sequence(rows)
            converted = self.populate_library_sheets()
            if converted:
                rows_str = ", ".join(str(r) for r in converted)
                self.log_message(
                    f"Sheet row(s) {rows_str} from last time are no longer in the sheet library; "
                    f"they are now Custom with their last size.",
                    level="warning",
                )
        else:
            self.populate_library_sheets()

        self.part_spacing_input.setValue(prefs.GetFloat(PROP_PART_SPACING, 6.35))
        self.label_size_input.setValue(prefs.GetFloat(PROP_LABEL_SIZE, 10.0))
        self.deflection_input.setValue(prefs.GetFloat(PROP_DEFLECTION_ANGLE, 30.0) or 30.0)
        self.simplification_input.setValue(prefs.GetFloat(PROP_SIMPLIFICATION, 1.0))
        self.weight_sliders.setValues({key: prefs.GetFloat(prop, _DEFAULTS[f"weight_{key}"])
                                       for prop, key in WEIGHT_PROPS})
        for prop, key in WEIGHT_PROPS:
            self.weight_sliders.setLocked(key, prefs.GetBool(prop + "Locked", False))
        self.part_direction_dial.setDegrees(prefs.GetFloat(PROP_PART_DIRECTION, _DEFAULTS["part_direction"]))
        self.fitness_min_offcut_input.setValue(prefs.GetFloat("FitnessMinOffcut", _DEFAULTS["fitness_min_offcut"]))
        self.show_offcuts_checkbox.setChecked(prefs.GetBool(PROP_SHOW_OFFCUTS, True))
        self.beam_width_decay_input.setValue(prefs.GetFloat("BeamWidthDecay", _DEFAULTS["beam_width_decay"]))
        self.set_ga_winners(prefs.GetInt("GAWinners", _DEFAULTS["ga_winners"]))
        self.ga_mutation_rate_input.setValue(prefs.GetFloat("GAMutationRate", _DEFAULTS["ga_mutation_rate"]))
        self.evolve_direction_checkbox.setChecked(prefs.GetBool("EvolveDirection", False))
        self.worker_processes_input.setValue(get_worker_override())
        # Load Rotation Steps (Isolated)
        # Minkowski
        mink_rot_steps = prefs.GetInt("MinkowskiRotationSteps", 4) # Default 90 deg (4 steps)
        if mink_rot_steps > 0:
            target_angle = 360.0 / mink_rot_steps
            self.minkowski_rotation_steps_slider.setValue(
                closest_angle_index(self.rotation_angles, target_angle)
            )

        detent = prefs.GetFloat("DirectionDetentStep", DIRECTION_DETENT_DEFAULT)
        self.minkowski_direction_dial.setDetentStep(detent)
        self.part_direction_dial.setDetentStep(detent)
        
    def update_progress(self, current, total, message=None):
        """Updates the progress bar."""
        try:
            if total > 0:
                percentage = int((float(current) / float(total)) * 100)
                self.progressBar.setValue(percentage)
                self.progressBar.setVisible(True)
                
                if message:
                    self.progressBar.setFormat(f"%p% - {message}")
                else:
                    self.progressBar.setFormat("%p%")
                
                # Force UI update
                FreeCADGui.updateGui()
            else:
                self.progressBar.setValue(0)
                self.progressBar.setVisible(False)
        except RuntimeError as e:
            nw_logger.debug(f"[NestingPanel] update_progress widget deleted: {e}")
        except Exception as e:
            nw_logger.warn(f"UI Update Error: {e}")

    def _update_rotation_label(self):
        value = self.minkowski_rotation_steps_slider.value()
        angles = self.rotation_angles
        if value < len(angles):
            self.minkowski_rotation_display_label.setText(f"{angles[value]}°")

    def algorithm(self):
        """The selected placement algorithm key (ALGO_GREEDY or ALGO_BEAM)."""
        return self.algorithm_dropdown.currentData()

    def set_algorithm(self, key):
        """Select *key*. Returns False, leaving the selection alone, when *key* is not offered."""
        idx = self.algorithm_dropdown.findData(key)
        if idx < 0:
            return False
        self.algorithm_dropdown.setCurrentIndex(idx)
        return True

    def ga_winners(self):
        """The chosen Winners value. The box may show less while the population is smaller."""
        return self._ga_winners_chosen

    def set_ga_winners(self, value):
        """Choose *value* winners; the box shows it capped at the population."""
        self._ga_winners_chosen = max(1, int(value))
        self._clamp_ga_winners()

    def _clamp_ga_winners(self):
        """Caps the Winners box at the population and shows min(chosen, population)."""
        pop = max(1, self.minkowski_population_size_input.value())
        self._clamping_ga_winners = True
        try:
            self.ga_winners_input.setMaximum(pop)
            self.ga_winners_input.setValue(min(self._ga_winners_chosen, pop))
        finally:
            self._clamping_ga_winners = False

    def _on_algorithm_change(self, algo_name):
        """Shows the selected placer's settings; the GA section is always shown."""
        is_beam = algo_name == ALGO_BEAM
        self.beam_section.setVisible(is_beam)
        self.evolve_direction_checkbox.setVisible(not is_beam)
        self._update_rotation_label()

    def reset_progress(self):
        """Resets and hides the progress bar."""
        try:
            self.progressBar.setValue(0)
            self.progressBar.setVisible(False)
        except RuntimeError as e:
            nw_logger.debug(f"[NestingPanel] reset_progress widget deleted: {e}")


