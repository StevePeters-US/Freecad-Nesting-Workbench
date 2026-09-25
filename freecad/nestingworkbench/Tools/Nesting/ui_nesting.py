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
from ...constants import *
from ... import FONTS_DIR, DEFAULT_FONT
from freecad.nestingworkbench.ui_helpers import (
    show_info_dialog,
    MARGINS_NONE,
    QT_TRANSLATE_NOOP,
    make_double_spinbox,
    make_int_spinbox,
    make_checkbox,
    LinkedSliderSpinBox,
    CollapsibleSection,
    closest_angle_index,
)

_MINKOWSKI_DIR_MAX = 359

_DEFAULTS = {
    "sheet_width": 600.0,
    "sheet_height": 600.0,
    "part_spacing": 12.5,
    "sheet_thickness": 3.0,
    "deflection_angle": 30.0,
    "verbose_logging": False,
    "rotation_angles": MINKOWSKI_ROTATION_PRESETS,
    # Defaults from the 2026-09-03 GA sizing benchmark:
    # the cheapest configuration that is no worse than seeded greedy on every
    # benchmarked corpus. Heavier settings are opt-in presets, not the default.
    "ga_population": 1,
    "ga_generations": 1,
}

# (label, population, generations) — from the 2026-09-03 GA sizing benchmark.
# Re-run the sizing sweep before changing any number here.
_GA_PRESETS = [
    (QT_TRANSLATE_NOOP("NestingPanel", "Default (1 pop, 1 gen)"), 1, 1),
    (QT_TRANSLATE_NOOP("NestingPanel", "Balanced (10 pop, 10 gen)"), 10, 10),
    (QT_TRANSLATE_NOOP("NestingPanel", "Thorough (10 pop, 20 gen)"), 10, 20),
]
_GA_CUSTOM_LABEL = QT_TRANSLATE_NOOP("NestingPanel", "Custom")

_GA_POP_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "<b>Population Size:</b><br>"
    "Number of candidate layouts evaluated per generation.<br><br>"
    "<b>Default (1):</b> Single seeded placement; matches greedy on both benchmarked "
    "corpora in under 0.11s.<br>"
    "<b>Balanced (10):</b> paired with 10 generations, 1.02% better than greedy on "
    "rectangular parts and 0.79% on irregular parts, at 16.4x the Default's run time "
    "(1.84s).<br>"
    "<b>Larger (20-40):</b> measured no better than 10 on either corpus, and costs "
    "proportionally more time."
)

_GA_GEN_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "<b>Generations:</b><br>"
    "Number of evolutionary optimization cycles.<br><br>"
    "<b>Default (1):</b> Single pass without evolutionary iteration.<br>"
    "<b>Balanced (10):</b> Paired with population 10; the winning layout appeared by "
    "generation 3 on average (generation 6 at the latest) on rectangular parts.<br>"
    "<b>Thorough (20):</b> best measured packing, 1.67% better than greedy on "
    "rectangular parts.<br>"
    "<b>Large (&gt;20):</b> fitness was identical at 20 and 40 generations on both "
    "corpora, and stagnation detection halts early anyway, so extra generations cost "
    "time without changing the result."
)

_GA_PRESET_TOOLTIP = QT_TRANSLATE_NOOP(
    "NestingPanel",
    "<b>Optimization Preset:</b><br>"
    "Predefined population and generation configurations, sized from benchmark runs.<br><br>"
    "<b>Default (1 pop, 1 gen):</b> Fast seeded placement; matched greedy exactly on "
    "both corpora, in 0.11s or less.<br>"
    "<b>Balanced (10 pop, 10 gen):</b> 1.02% better than greedy on rectangular parts "
    "and 0.79% on irregular parts, 16.4x the Default's run time (1.84s).<br>"
    "<b>Thorough (10 pop, 20 gen):</b> Best measured packing, 1.67% better than greedy "
    "on rectangular parts, 17.1x the Default's run time (1.92s)."
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
        FreeCAD.Console.PrintMessage("NestingPanel initialized.\n")
        self.setWindowTitle(QT_TRANSLATE_NOOP("NestingPanel", "Nesting Tool"))
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
            self.controller.finalize_job()
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

    def _build_direction_dial(self, default_label="Down"):
        """Creates a direction dial and synchronized label pair."""
        dial = QtWidgets.QDial()
        dial.setRange(0, _MINKOWSKI_DIR_MAX)
        dial.setValue(0)
        dial.setWrapping(True)
        dial.setNotchesVisible(True)

        label = QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", default_label))
        label.setAlignment(QtCore.Qt.AlignCenter)

        def update_dial_label(value):
            direction_map = {
                0: QT_TRANSLATE_NOOP("NestingPanel", "Down"),
                90: QT_TRANSLATE_NOOP("NestingPanel", "Left"),
                180: QT_TRANSLATE_NOOP("NestingPanel", "Up"),
                270: QT_TRANSLATE_NOOP("NestingPanel", "Right"),
            }
            direction_text = direction_map.get(value, "")
            label.setText(direction_text if direction_text else f"{value}°")

        dial.valueChanged.connect(update_dial_label)
        return dial, label

    def _build_algorithm_selector(self, form_layout):
        """Builds and adds the algorithm dropdown to the form layout."""
        self.algorithm_dropdown = QtWidgets.QComboBox()
        self.algorithm_dropdown.addItems([
            QT_TRANSLATE_NOOP("NestingPanel", "Minkowski"),
            QT_TRANSLATE_NOOP("NestingPanel", "Physics"),
        ])
        self.algorithm_dropdown.setCurrentIndex(0)
        self.algorithm_dropdown.currentTextChanged.connect(self._on_algorithm_change)
        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Nesting Algorithm:"), self.algorithm_dropdown)

    def _build_sheet_and_boundary_inputs(self):
        """Builds sheet dimension and boundary resolution inputs inside a group box."""
        group = QtWidgets.QGroupBox(QT_TRANSLATE_NOOP("NestingPanel", "Sheet Setup"))
        form_layout = QtWidgets.QFormLayout()

        self.sheet_width_input = make_double_spinbox(
            _DEFAULTS["sheet_width"], 1, 10000
        )
        self.sheet_height_input = make_double_spinbox(
            _DEFAULTS["sheet_height"], 1, 10000
        )
        self.sheet_thickness_input = make_double_spinbox(
            _DEFAULTS["sheet_thickness"], 0.1, 1000
        )
        self.part_spacing_input = make_double_spinbox(
            _DEFAULTS["part_spacing"], 0, 1000
        )

        self.deflection_input = make_double_spinbox(
            _DEFAULTS["deflection_angle"], 1, 90,
            step=1, decimals=0, suffix="°",
            tooltip=QT_TRANSLATE_NOOP(
                "NestingPanel",
                "<b>Curve Angle (Tessellation Quality):</b><br>"
                "Maximum angular deviation when approximating curves.<br><br>"
                "<b>Smaller (5-10°):</b> Smoother curves, more points, slower.<br>"
                "<b>Larger (20-45°):</b> Coarser curves, fewer points, faster.<br><br>"
                "<i>Tip: 10° is good for most parts. Use 5° for precision, 30°+ for speed.</i>"
            )
        )

        self.simplification_input = make_double_spinbox(
            1.0, 0.001, 10.0,
            step=0.1, decimals=3,
            tooltip=QT_TRANSLATE_NOOP(
                "NestingPanel",
                "<b>Simplification (Point Reduction):</b><br>"
                "Tolerance (mm) for removing redundant boundary points.<br><br>"
                "<b>Smaller (0.1-0.5):</b> More detailed boundaries, slower nesting.<br>"
                "<b>Larger (1.0-5.0):</b> Simpler boundaries, faster nesting.<br><br>"
                "<i>Tip: Set this to your machine's precision tolerance (e.g., 1mm for routers).</i>"
            )
        )

        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Sheet Width:"), self.sheet_width_input)
        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Sheet Height:"), self.sheet_height_input)
        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Sheet Thickness:"), self.sheet_thickness_input)
        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Part Spacing:"), self.part_spacing_input)

        curve_settings_layout = QtWidgets.QHBoxLayout()
        curve_settings_layout.addWidget(QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", "Curve:")))
        curve_settings_layout.addWidget(self.deflection_input)
        curve_settings_layout.addWidget(QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", "Simplify:")))
        curve_settings_layout.addWidget(self.simplification_input)
        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Bounds Resolution:"), curve_settings_layout)

        group.setLayout(form_layout)
        return group

    def _build_minkowski_group(self):
        """Builds the Minkowski nesting configuration group box."""
        group = QtWidgets.QGroupBox(QT_TRANSLATE_NOOP("NestingPanel", "Minkowski Nesting Settings"))
        form_layout = QtWidgets.QFormLayout()

        self.minkowski_direction_dial, self.minkowski_direction_label = self._build_direction_dial("Down")
        dial_layout = QtWidgets.QVBoxLayout()
        dial_layout.addWidget(self.minkowski_direction_dial)
        dial_layout.addWidget(self.minkowski_direction_label)

        self.minkowski_random_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Use Random Direction"),
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "If checked, each part will use a randomized placement weighting.")
        )
        self.minkowski_random_checkbox.stateChanged.connect(lambda state: self.minkowski_direction_dial.setDisabled(state))

        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Nesting Direction:"), dial_layout)
        form_layout.addRow(self.minkowski_random_checkbox)

        self.minkowski_population_size_input = make_int_spinbox(
            _DEFAULTS["ga_population"], 1, 500,
            tooltip=QtWidgets.QApplication.translate("NestingPanel", _GA_POP_TOOLTIP)
        )

        self.minkowski_generations_input = make_int_spinbox(
            _DEFAULTS["ga_generations"], 1, 1000,
            tooltip=QtWidgets.QApplication.translate("NestingPanel", _GA_GEN_TOOLTIP)
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
            QtWidgets.QApplication.translate("NestingPanel", _GA_PRESET_TOOLTIP)
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

        self.minkowski_compactness_input = make_double_spinbox(
            1.0, 0.0, 10.0, step=0.1,
            tooltip=QT_TRANSLATE_NOOP(
                "NestingPanel",
                "Keeps the leftover material on the last sheet together as one "
                "large offcut\ninstead of scattered gaps. 1.0 (default) is a "
                "balanced setting,\nhigher favours a bigger offcut, 0 turns it "
                "off. Needs Generations\nand Population Size above 1."
            )
        )

        self.minkowski_compactness_help = QtWidgets.QPushButton("?")
        self.minkowski_compactness_help.setFixedSize(20, 20)
        self.minkowski_compactness_help.setToolTip(QT_TRANSLATE_NOOP("NestingPanel", "Click to learn more about how the Compactness function works."))
        self.minkowski_compactness_help.clicked.connect(self._show_compactness_info)

        mink_compactness_layout = QtWidgets.QHBoxLayout()
        mink_compactness_layout.addWidget(self.minkowski_compactness_input)
        mink_compactness_layout.addWidget(self.minkowski_compactness_help)
        mink_compactness_layout.addStretch()
        mink_compactness_layout.setContentsMargins(*MARGINS_NONE)

        self.minkowski_rotation_steps_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.minkowski_rotation_steps_slider.setRange(0, len(self.rotation_angles) - 1)
        self.minkowski_rotation_steps_slider.setValue(3)
        self.minkowski_rotation_display_label = QtWidgets.QLabel("")
        self.minkowski_rotation_display_label.setFixedWidth(100)
        self.minkowski_rotation_steps_slider.valueChanged.connect(lambda: self._update_rotation_label())

        mink_rot_layout = QtWidgets.QHBoxLayout()
        mink_rot_layout.addWidget(self.minkowski_rotation_steps_slider)
        mink_rot_layout.addWidget(self.minkowski_rotation_display_label)
        mink_rot_layout.setContentsMargins(*MARGINS_NONE)

        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Rotation Angle:"), mink_rot_layout)

        ga_section = CollapsibleSection(QT_TRANSLATE_NOOP("NestingPanel", "Genetic Algorithm"), expanded=True)
        ga_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Preset:"), self.ga_preset_dropdown)
        ga_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Generations:"), self.minkowski_generations_input)
        ga_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Population Size:"), self.minkowski_population_size_input)
        ga_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Compactness:"), mink_compactness_layout)
        ga_section.addRow(self.ga_warning_label)
        form_layout.addRow(ga_section)

        group.setLayout(form_layout)
        return group

    def _build_physics_group(self):
        """Builds the Physics nesting configuration group box."""
        group = QtWidgets.QGroupBox(QT_TRANSLATE_NOOP("NestingPanel", "Physics Nesting Settings"))
        form_layout = QtWidgets.QFormLayout()

        self.physics_direction_dial, self.physics_direction_label = self._build_direction_dial("Down")
        dial_layout = QtWidgets.QVBoxLayout()
        dial_layout.addWidget(self.physics_direction_dial)
        dial_layout.addWidget(self.physics_direction_label)

        self.physics_random_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Use Random Direction"),
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "If checked, randomized gravity direction will be applied.")
        )
        self.physics_random_checkbox.stateChanged.connect(lambda state: self.physics_direction_dial.setDisabled(state))

        self.physics_step_size_input = make_double_spinbox(
            5.0, 0.1, 100.0,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Simulation step size in mm per iteration. Smaller values increase accuracy but take longer.")
        )

        self.physics_max_spawn_input = make_int_spinbox(
            100, 1, 1000,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Maximum number of attempts to find an initial collision-free position for each part.")
        )

        self.physics_max_nesting_steps_input = make_int_spinbox(
            500, 1, 5000,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Maximum physics simulation steps per cycle before freezing placement.")
        )

        self.physics_anneal_steps_input = make_int_spinbox(
            25, 0, 500,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Number of simulated annealing shake steps to settle parts into compact gaps.")
        )

        self.anneal_rotate_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Anneal Rotate"),
            checked=True,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Allow rotational perturbation during the annealing phase.")
        )

        self.anneal_translate_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Anneal Translate"),
            checked=True,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Allow translational perturbation during the annealing phase.")
        )

        self.anneal_random_shake_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Random Shake Direction"),
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Apply randomized perturbation direction during annealing instead of gravity direction.")
        )

        self.physics_anneal_rot_steps = make_int_spinbox(
            10, 0, 500,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Number of rotational perturbation iterations during annealing.")
        )

        self.physics_anneal_rot_curve_type = QtWidgets.QComboBox()
        self.physics_anneal_rot_curve_type.addItems(["Logarithmic", "Linear", "Power 1.5", "Quadratic", "Exponential"])
        self.physics_anneal_rot_curve_type.setToolTip(QT_TRANSLATE_NOOP("NestingPanel", "Decay curve profile for rotational annealing temperature/amplitude over iterations."))

        self.physics_anneal_rot_min = make_double_spinbox(
            1.0, 0.0, 360.0,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Minimum rotation angle (degrees) applied at the end of annealing.")
        )

        self.physics_anneal_rot_max = make_double_spinbox(
            90.0, 0.0, 360.0,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Maximum rotation angle (degrees) applied at the start of annealing.")
        )

        self.physics_anneal_curve_type = QtWidgets.QComboBox()
        self.physics_anneal_curve_type.addItems(["Logarithmic", "Linear", "Power 1.5", "Quadratic", "Exponential"])
        self.physics_anneal_curve_type.setToolTip(QT_TRANSLATE_NOOP("NestingPanel", "Decay curve profile for translation annealing amplitude over iterations."))

        self.physics_anneal_min_amp = make_double_spinbox(
            0.1, 0.0, 1000.0,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Minimum translation step (mm) applied at the end of annealing.")
        )

        self.physics_anneal_max_amp = make_double_spinbox(
            100.0, 0.0, 5000.0,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Maximum translation step (mm) applied at the start of annealing.")
        )

        self.physics_improvement_threshold_input = make_double_spinbox(
            0.01, 0.000001, 1.0, step=0.01, decimals=6,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Minimum score improvement required to reset simulation cycle. Prevents infinite loops from noise.")
        )

        self.physics_rotation_steps_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.physics_rotation_steps_slider.setRange(0, len(PHYSICS_ROTATION_PRESETS) - 1)
        self.physics_rotation_steps_slider.setValue(1)
        self.physics_rotation_display_label = QtWidgets.QLabel("")
        self.physics_rotation_display_label.setFixedWidth(120)
        self.physics_rotation_steps_slider.valueChanged.connect(lambda: self._update_rotation_label())

        phys_rot_layout = QtWidgets.QHBoxLayout()
        phys_rot_layout.addWidget(self.physics_rotation_steps_slider)
        phys_rot_layout.addWidget(self.physics_rotation_display_label)
        phys_rot_layout.setContentsMargins(*MARGINS_NONE)

        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Gravity Direction:"), dial_layout)
        form_layout.addRow(self.physics_random_checkbox)
        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Step Size:"), self.physics_step_size_input)
        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Max Spawn Attempts:"), self.physics_max_spawn_input)
        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Max Nesting Steps:"), self.physics_max_nesting_steps_input)

        anneal_section = CollapsibleSection(QT_TRANSLATE_NOOP("NestingPanel", "Annealing (Shake)"), expanded=True)
        anneal_section.addRow(self.anneal_rotate_checkbox)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Rotation Steps:"), phys_rot_layout)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Rot Anneal Steps:"), self.physics_anneal_rot_steps)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Rot Curve Type:"), self.physics_anneal_rot_curve_type)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Rot Min Angle:"), self.physics_anneal_rot_min)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Rot Max Angle:"), self.physics_anneal_rot_max)
        anneal_section.addRow(self.anneal_translate_checkbox)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Anneal Steps:"), self.physics_anneal_steps_input)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Improvement Threshold:"), self.physics_improvement_threshold_input)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Curve Type:"), self.physics_anneal_curve_type)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Min Amplitude:"), self.physics_anneal_min_amp)
        anneal_section.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Max Amplitude:"), self.physics_anneal_max_amp)
        anneal_section.addRow(self.anneal_random_shake_checkbox)
        form_layout.addRow(anneal_section)

        group.setLayout(form_layout)
        return group

    def _build_font_layout(self):
        """Builds the font selection button and label layout."""
        font_layout = QtWidgets.QHBoxLayout()
        self.font_select_button = QtWidgets.QPushButton(QT_TRANSLATE_NOOP("NestingPanel", "Select Font"))
        self.font_label = QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", "No Font Selected"))
        self.font_label.setWordWrap(True)
        font_layout.addWidget(self.font_select_button)
        font_layout.addWidget(self.font_label)
        return font_layout

    def _build_label_options_row(self):
        """Builds the label options row with size and height inputs."""
        self.add_labels_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Add Identifier Labels"),
            checked=True
        )

        self.label_size_input = make_double_spinbox(
            10.0, 1, 100,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "The text size for identifier labels in mm.")
        )

        self.label_height_input = make_double_spinbox(
            25.0, 0, 1000,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "The height (Z-offset) for the identifier labels.")
        )

        label_options_layout = QtWidgets.QHBoxLayout()
        label_options_layout.addWidget(self.add_labels_checkbox)
        label_options_layout.addWidget(QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", "Size:")))
        label_options_layout.addWidget(self.label_size_input)
        label_options_layout.addWidget(QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", "Height (Z):")))
        label_options_layout.addWidget(self.label_height_input)
        label_options_layout.addStretch()
        return label_options_layout

    def _build_general_checkboxes(self, form_layout):
        """Builds general runtime options checkboxes inside a collapsible section."""
        adv_section = CollapsibleSection(
            QT_TRANSLATE_NOOP("NestingPanel", "Advanced Options"),
            expanded=False
        )
        self.simulate_nesting_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Simulate Nesting (slower)"),
            checked=True
        )

        self.verbose_logging_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Verbose Logging"),
            checked=_DEFAULTS["verbose_logging"],
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Enables detailed logging of the nesting process in the FreeCAD console.")
        )

        self.clear_cache_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Clear NFP Cache"),
            checked=False,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Forces recalculation of No-Fit Polygons. Slower, but resolves potential caching issues.")
        )

        self.show_bounds_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Show Bounds"),
            checked=True
        )

        self.sound_checkbox = make_checkbox(
            QT_TRANSLATE_NOOP("NestingPanel", "Play sound on completion"),
            checked=True
        )

        adv_section.addRow(self.simulate_nesting_checkbox)
        adv_section.addRow(self.verbose_logging_checkbox)
        adv_section.addRow(self.clear_cache_checkbox)
        adv_section.addRow(self.show_bounds_checkbox)
        adv_section.addRow(self.sound_checkbox)
        form_layout.addRow(adv_section)

    def _build_parts_table_and_buttons(self, main_layout):
        """Builds the parts table and Add/Remove buttons."""
        self.shape_table = QtWidgets.QTableWidget()
        self.shape_table.setColumnCount(5)
        self.shape_table.setHorizontalHeaderLabels([
            QT_TRANSLATE_NOOP("NestingPanel", "Shape"),
            QT_TRANSLATE_NOOP("NestingPanel", "Quantity"),
            QT_TRANSLATE_NOOP("NestingPanel", "Rotations"),
            QT_TRANSLATE_NOOP("NestingPanel", "Up Dir"),
            QT_TRANSLATE_NOOP("NestingPanel", "Fill"),
        ])

        self.add_parts_button = QtWidgets.QPushButton(QT_TRANSLATE_NOOP("NestingPanel", "Add Selected"))
        self.remove_parts_button = QtWidgets.QPushButton(QT_TRANSLATE_NOOP("NestingPanel", "Remove Selected"))

        table_button_layout = QtWidgets.QHBoxLayout()
        table_button_layout.addWidget(self.add_parts_button)
        table_button_layout.addWidget(self.remove_parts_button)

        main_layout.addWidget(self.shape_table)
        main_layout.addLayout(table_button_layout)

    def _build_action_buttons(self):
        """Builds the main Run Nesting and Cancel action buttons."""
        self.nest_button = QtWidgets.QPushButton(QT_TRANSLATE_NOOP("NestingPanel", "Run Nesting"))
        self.cancel_button = QtWidgets.QPushButton(QT_TRANSLATE_NOOP("NestingPanel", "Cancel Nesting"))
        self.cancel_button.setEnabled(False)

        action_button_layout = QtWidgets.QHBoxLayout()
        action_button_layout.addWidget(self.nest_button)
        action_button_layout.addWidget(self.cancel_button)
        return action_button_layout

    def _build_progress_and_status(self, main_layout):
        """Builds the progress bar and status message label."""
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(True)
        self.progressBar.setVisible(False)

        self.status_label = QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", "Select master shapes to nest."))
        self.status_label.setWordWrap(True)

        main_layout.addWidget(self.progressBar)
        main_layout.addWidget(self.status_label)

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout()
        form_layout = QtWidgets.QFormLayout()

        self.sheet_setup_group = self._build_sheet_and_boundary_inputs()
        form_layout.addRow(self.sheet_setup_group)

        self._build_algorithm_selector(form_layout)

        self.minkowski_settings_group = self._build_minkowski_group()
        self.physics_settings_group = self._build_physics_group()
        form_layout.addRow(self.minkowski_settings_group)
        form_layout.addRow(self.physics_settings_group)

        font_layout = self._build_font_layout()
        form_layout.addRow(QT_TRANSLATE_NOOP("NestingPanel", "Identifier Font:"), font_layout)

        label_options_layout = self._build_label_options_row()
        form_layout.addRow(label_options_layout)

        self._build_general_checkboxes(form_layout)
        main_layout.addLayout(form_layout)

        self._build_parts_table_and_buttons(main_layout)

        action_button_layout = self._build_action_buttons()
        main_layout.addLayout(action_button_layout)

        self._build_progress_and_status(main_layout)
        main_layout.addStretch()
        self.setLayout(main_layout)

        # Only after setLayout: until then the groups have no parent widget, and
        # setVisible(True) on a parentless widget pops it up as its own window.
        self.minkowski_settings_group.setVisible(True)
        self.physics_settings_group.setVisible(False)

        # Connect signals
        def toggle_label_inputs(state):
            enabled = state == QtCore.Qt.Checked
            self.label_size_input.setEnabled(enabled)
            self.label_height_input.setEnabled(enabled)

        self.add_labels_checkbox.stateChanged.connect(toggle_label_inputs)
        toggle_label_inputs(QtCore.Qt.Checked if self.add_labels_checkbox.isChecked() else QtCore.Qt.Unchecked)

        # Connect the nesting controller
        from .nesting_controller import NestingController
        self.controller = NestingController(self)
        self.nest_button.clicked.connect(self.controller.execute_nesting)
        self.cancel_button.clicked.connect(self.controller.request_cancel)
        self.font_select_button.clicked.connect(self.select_font_file)
        self.show_bounds_checkbox.stateChanged.connect(self.controller.toggle_bounds_visibility)
        self.add_parts_button.clicked.connect(self.controller.add_selected_shapes)
        self.remove_parts_button.clicked.connect(self.controller.remove_selected_shapes)

        self.load_persisted_settings()
        self._update_rotation_label()
        self.controller.load_selection()

    def add_part_row(self, row_index, label, quantity=1, rotation_steps=4, override_rotation=False, 
                       up_direction="Z+", fill_sheet=False):
        """Helper function to create and populate a single row in the parts table."""
        label_item = QtWidgets.QTableWidgetItem(label)
        label_item.setFlags(label_item.flags() & ~QtCore.Qt.ItemIsEditable)

        quantity_spinbox = make_int_spinbox(quantity, 1, 500)

        rotation_widget = QtWidgets.QWidget()
        rotation_layout = QtWidgets.QHBoxLayout(rotation_widget)
        rotation_layout.setContentsMargins(*MARGINS_NONE)

        override_checkbox = make_checkbox("", checked=override_rotation)
        override_checkbox.setToolTip(QT_TRANSLATE_NOOP("NestingPanel", "Override global rotation steps for this part."))

        rotation_spinbox = make_int_spinbox(
            rotation_steps, 0, 360,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "Override global rotation steps for this part. 0 or 1 means no rotation.")
        )
        rotation_spinbox.setEnabled(override_rotation)
        override_checkbox.stateChanged.connect(lambda state: rotation_spinbox.setEnabled(state == QtCore.Qt.Checked))

        rotation_layout.addWidget(override_checkbox)
        rotation_layout.addWidget(rotation_spinbox)

        up_dir_combo = QtWidgets.QComboBox()
        up_dir_combo.addItems(["Z+", "Z-", "Y+", "Y-", "X+", "X-"])
        up_dir_combo.setCurrentText(up_direction)
        up_dir_combo.setToolTip(QT_TRANSLATE_NOOP("NestingPanel", "Define which direction is 'up' for this part when projecting to 2D."))

        fill_checkbox = make_checkbox(
            "", checked=fill_sheet,
            tooltip=QT_TRANSLATE_NOOP("NestingPanel", "The quantity is always placed. If checked, extra copies are then added to fill the remaining space.")
        )

        self.shape_table.setItem(row_index, 0, label_item)
        self.shape_table.setCellWidget(row_index, 1, quantity_spinbox)
        self.shape_table.setCellWidget(row_index, 2, rotation_widget)
        self.shape_table.setCellWidget(row_index, 3, up_dir_combo)
        self.shape_table.setCellWidget(row_index, 4, fill_checkbox)

    def select_font_file(self):
        """Opens a file dialog to let the user select a font file."""
        default_font_dir = FONTS_DIR
        if not os.path.isdir(default_font_dir):
            default_font_dir = "" # Fallback if fonts dir doesn't exist

        file_dialog_result = QtWidgets.QFileDialog.getOpenFileName(
            self, 
            QT_TRANSLATE_NOOP("NestingPanel", "Select Font File"), 
            default_font_dir, # Set the default directory
            QT_TRANSLATE_NOOP("NestingPanel", "Font Files (*.ttf *.otf)")
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
            FreeCAD.Console.PrintWarning(f"[NestingPanel] Failed to set default font: {e}\n")

    def log_message(self, message, level="message"):
        """Displays a message in the status label and logs to the console."""
        try:
            self.status_label.setText(message)
        except RuntimeError:
            # The widget C++ object has been deleted (panel closed), but Python object persists.
            # We can just log to console and ignore the UI update.
            pass

        if level == "warning":
            FreeCAD.Console.PrintWarning(message + "\n")
        else:
            FreeCAD.Console.PrintMessage(message + "\n")
        
        # Process UI events to make sure the label updates immediately
        # We wrap this too, just in case
        try:
            FreeCADGui.updateGui()
        except RuntimeError:
            pass  # GUI window closed

    def load_persisted_settings(self):
        """Loads settings from FreeCAD preferences."""
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        self.sheet_width_input.setValue(prefs.GetFloat(PROP_SHEET_WIDTH, 600.0))
        self.sheet_height_input.setValue(prefs.GetFloat(PROP_SHEET_HEIGHT, 600.0))
        self.part_spacing_input.setValue(prefs.GetFloat(PROP_PART_SPACING, 12.5))
        self.sheet_thickness_input.setValue(prefs.GetFloat(PROP_SHEET_THICKNESS, 3.0))
        self.label_size_input.setValue(prefs.GetFloat(PROP_LABEL_SIZE, 10.0))
        self.deflection_input.setValue(prefs.GetFloat(PROP_DEFLECTION_ANGLE, 30.0) or 30.0)
        self.simplification_input.setValue(prefs.GetFloat(PROP_SIMPLIFICATION, 1.0))
        self.minkowski_compactness_input.setValue(prefs.GetFloat("GACompactnessWeight", 1.0))
        self.verbose_logging_checkbox.setChecked(prefs.GetBool("VerboseLogging", False))
        self.physics_improvement_threshold_input.setValue(prefs.GetFloat("PhysicsStabilityTolerance", 0.01))
        
        self.physics_anneal_curve_type.setCurrentText(prefs.GetString("PhysicsAnnealCurveType", "Logarithmic"))
        self.physics_anneal_min_amp.setValue(prefs.GetFloat("PhysicsAnnealMinAmp", 0.1))
        self.physics_anneal_max_amp.setValue(prefs.GetFloat("PhysicsAnnealMaxAmp", 100.0))
        
        self.physics_anneal_rot_steps.setValue(prefs.GetInt("PhysicsAnnealRotSteps", 10))
        self.physics_anneal_rot_curve_type.setCurrentText(prefs.GetString("PhysicsAnnealRotCurveType", "Logarithmic"))
        self.physics_anneal_rot_min.setValue(prefs.GetFloat("PhysicsAnnealRotMin", 1.0))
        self.physics_anneal_rot_max.setValue(prefs.GetFloat("PhysicsAnnealRotMax", 90.0))
        
        # Load Rotation Steps (Isolated)
        # Minkowski
        mink_rot_steps = prefs.GetInt("MinkowskiRotationSteps", 4) # Default 90 deg (4 steps)
        if mink_rot_steps > 0:
            target_angle = 360.0 / mink_rot_steps
            self.minkowski_rotation_steps_slider.setValue(
                closest_angle_index(self.rotation_angles, target_angle)
            )
            
        # Physics
        phys_rot_steps = prefs.GetInt("PhysicsRotationSteps", 4) # Default 90 deg (4 steps)
        if phys_rot_steps > 0:
            target_angle = 360.0 / phys_rot_steps
            self.physics_rotation_steps_slider.setValue(
                closest_angle_index(PHYSICS_ROTATION_PRESETS, target_angle)
            )
        
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
        except RuntimeError:
            pass # Widget deleted
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"UI Update Error: {e}\n")

    def _update_rotation_label(self):
        algo = self.algorithm_dropdown.currentText()
        
        if algo == "Physics":
            value = self.physics_rotation_steps_slider.value()
            angles = PHYSICS_ROTATION_PRESETS
            if value < len(angles):
                angle = angles[value]
                steps = int(360 / angle) if angle > 0 else 1
                self.physics_rotation_display_label.setText(f"{angle}° ({steps} steps)")
        else:
            value = self.minkowski_rotation_steps_slider.value()
            angles = self.rotation_angles
            if value < len(angles):
                angle = angles[value]
                self.minkowski_rotation_display_label.setText(f"{angle}°")

    def _on_algorithm_change(self, algo_name):
        """Handles switching between nesting algorithms."""
        self.minkowski_settings_group.setVisible(algo_name == "Minkowski")
        self.physics_settings_group.setVisible(algo_name == "Physics")
        # Ensure the rotation label/steps are immediately clarified for the new algorithm
        self._update_rotation_label()

    def reset_progress(self):
        """Resets and hides the progress bar."""
        try:
            self.progressBar.setValue(0)
            self.progressBar.setVisible(False)
        except RuntimeError: pass  # Widget deleted

    def _show_compactness_info(self):
        """Shows an informative dialog explaining the GA Compactness function."""
        html_content = (
            "<b>Compactness</b><br><br>"
            "Controls the shape of the leftover material on your last sheet.<br>"
            "<ul>"
            "<li><b>1.0 (default):</b> the nester tries to keep the leftover "
            "together as one large, usable offcut.</li>"
            "<li><b>0:</b> off. Parts are simply squeezed into the smallest area, "
            "and the leftover can end up as several scattered gaps.</li>"
            "</ul>"
            "Raise it above 1.0 if you want an even bigger single offcut and "
            "don't mind the parts spreading out a little.<br><br>"
            "It will never add an extra sheet or leave a part unplaced just to "
            "make a bigger offcut, and it only has an effect when Generations "
            "and Population Size are above 1."
        )
        show_info_dialog(self, QT_TRANSLATE_NOOP("NestingPanel", "GA Compactness Optimization"), html_content)
        
