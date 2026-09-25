# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/ManualNester/ui_manual_nester.py

"""
This module contains the ManualNesterToolUI class, which defines the user interface
for the manual nester tool task panel.
"""

from PySide import QtCore, QtWidgets
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, make_double_spinbox
from .physics_engine import RADIUS_DEFAULT_MM, RADIUS_MIN_MM, RADIUS_MAX_MM

class ManualNesterToolUI(QtWidgets.QWidget):
    """
    Defines the user interface for the manual nester tool task panel.
    """
    def __init__(self, parent=None):
        super(ManualNesterToolUI, self).__init__(parent)
        self.setWindowTitle(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Manual Nester"))
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout()

        info_label = QtWidgets.QLabel(QT_TRANSLATE_NOOP(
            "ManualNesterToolUI",
            "Click and drag parts in the 3D view to move them.\n\nUse the 'OK' button to save changes or 'Cancel' to revert."
        ))
        info_label.setWordWrap(True)
        main_layout.addWidget(info_label)

        # Mode Group
        mode_group = QtWidgets.QGroupBox(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Mode"))
        mode_layout = QtWidgets.QVBoxLayout()

        self.radio_physics = QtWidgets.QRadioButton(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Push parts (Physics)"))
        self.radio_physics.setToolTip(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Dragging a part pushes nearby parts out of the way."))
        self.radio_physics.setChecked(True)

        self.radio_valid = QtWidgets.QRadioButton(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Valid placement only"))
        self.radio_valid.setToolTip(QT_TRANSLATE_NOOP("ManualNesterToolUI", "The dragged part only moves to positions that don't overlap other parts."))

        self.radio_autorotate = QtWidgets.QRadioButton(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Auto-rotate to fit"))
        self.radio_autorotate.setToolTip(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Pushes nearby parts and also rotates the dragged part for tighter fitment."))

        mode_layout.addWidget(self.radio_physics)
        mode_layout.addWidget(self.radio_valid)
        mode_layout.addWidget(self.radio_autorotate)
        mode_group.setLayout(mode_layout)
        main_layout.addWidget(mode_group)

        # Physics Settings Group (advanced controls)
        self.physics_group = QtWidgets.QGroupBox(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Physics Settings"))
        physics_layout = QtWidgets.QFormLayout()

        self.radius_spin = make_double_spinbox(
            RADIUS_DEFAULT_MM, RADIUS_MIN_MM, RADIUS_MAX_MM, step=10,
            tooltip=QT_TRANSLATE_NOOP("ManualNesterToolUI", "Distance (mm) within which dragged parts push surrounding parts.")
        )
        physics_layout.addRow(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Influence Radius (mm):"), self.radius_spin)

        self.curve_dropdown = QtWidgets.QComboBox()
        self.curve_dropdown.addItems([
            QT_TRANSLATE_NOOP("ManualNesterToolUI", "Linear"),
            QT_TRANSLATE_NOOP("ManualNesterToolUI", "Smooth"),
            QT_TRANSLATE_NOOP("ManualNesterToolUI", "Sharp"),
        ])
        self.curve_dropdown.setCurrentIndex(1)
        self.curve_dropdown.setToolTip(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Force falloff profile over distance (Linear, Smooth cubic, or Sharp exponential)."))
        physics_layout.addRow(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Falloff Curve:"), self.curve_dropdown)

        self.strength_spin = make_double_spinbox(
            1.0, 0.1, 2.0, step=0.1,
            tooltip=QT_TRANSLATE_NOOP("ManualNesterToolUI", "Multiplier scaling the repulsion displacement force applied to neighboring parts.")
        )
        physics_layout.addRow(QT_TRANSLATE_NOOP("ManualNesterToolUI", "Strength:"), self.strength_spin)

        self.physics_group.setLayout(physics_layout)
        main_layout.addWidget(self.physics_group)

        main_layout.addStretch()
        self.setLayout(main_layout)

        # Grey out physics settings when valid-placement mode is active
        self.radio_valid.toggled.connect(
            lambda checked: self.physics_group.setEnabled(not checked)
        )
