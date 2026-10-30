# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/ManualNester/ui_manual_nester.py

"""
This module contains the ManualNesterToolUI class, which defines the user interface
for the manual nester tool task panel.
"""

from PySide import QtCore, QtWidgets
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, make_double_spinbox, make_quantity_spinbox, rich_tooltip
from .physics_engine import RADIUS_DEFAULT_MM, RADIUS_MIN_MM, RADIUS_MAX_MM

class ManualNesterToolUI(QtWidgets.QWidget):
    """
    Defines the user interface for the manual nester tool task panel.
    """
    def __init__(self, parent=None):
        super(ManualNesterToolUI, self).__init__(parent)
        tr = QtWidgets.QApplication.translate
        self.setWindowTitle(tr("ManualNesterToolUI", "Manual Nester"))
        self._setup_ui()

    def _setup_ui(self):
        tr = QtWidgets.QApplication.translate
        main_layout = QtWidgets.QVBoxLayout()

        info_label = QtWidgets.QLabel(tr(
            "ManualNesterToolUI",
            "Click and drag parts in the 3D view to move them.\n\nUse the 'OK' button to save changes or 'Cancel' to revert."
        ))
        info_label.setWordWrap(True)
        main_layout.addWidget(info_label)

        # Mode Group
        mode_group = QtWidgets.QGroupBox(tr("ManualNesterToolUI", "Mode"))
        mode_layout = QtWidgets.QVBoxLayout()

        self.radio_physics = QtWidgets.QRadioButton(tr("ManualNesterToolUI", "Push parts (Physics)"))
        self.radio_physics.setToolTip(rich_tooltip(
            "ManualNesterToolUI",
            QT_TRANSLATE_NOOP("ManualNesterToolUI", "Pushes nearby parts out of the way while dragging a part."),
        ))
        self.radio_physics.setChecked(True)

        self.radio_valid = QtWidgets.QRadioButton(tr("ManualNesterToolUI", "Valid placement only"))
        self.radio_valid.setToolTip(rich_tooltip(
            "ManualNesterToolUI",
            QT_TRANSLATE_NOOP("ManualNesterToolUI", "Moves the dragged part only to positions that do not overlap other parts."),
        ))

        self.radio_autorotate = QtWidgets.QRadioButton(tr("ManualNesterToolUI", "Auto-rotate to fit"))
        self.radio_autorotate.setToolTip(rich_tooltip(
            "ManualNesterToolUI",
            QT_TRANSLATE_NOOP("ManualNesterToolUI", "Pushes nearby parts and rotates the dragged part for a tighter fit."),
        ))

        mode_layout.addWidget(self.radio_physics)
        mode_layout.addWidget(self.radio_valid)
        mode_layout.addWidget(self.radio_autorotate)
        mode_group.setLayout(mode_layout)
        main_layout.addWidget(mode_group)

        # Physics Settings Group (advanced controls)
        self.physics_group = QtWidgets.QGroupBox(tr("ManualNesterToolUI", "Physics Settings"))
        physics_layout = QtWidgets.QFormLayout()

        self.radius_spin = make_quantity_spinbox(
            RADIUS_DEFAULT_MM, RADIUS_MIN_MM, RADIUS_MAX_MM, step=10,
            tooltip=rich_tooltip("ManualNesterToolUI", QT_TRANSLATE_NOOP("ManualNesterToolUI", "Sets the distance within which a dragged part pushes surrounding parts.\n<b>Lower:</b> affects only immediate neighbours. <b>Higher:</b> pushes parts across a wider area.\n<b>Default:</b> 50 mm."))
        )
        physics_layout.addRow(tr("ManualNesterToolUI", "Influence Radius:"), self.radius_spin)

        self.curve_dropdown = QtWidgets.QComboBox()
        self.curve_dropdown.addItems([
            tr("ManualNesterToolUI", "Linear"),
            tr("ManualNesterToolUI", "Smooth"),
            tr("ManualNesterToolUI", "Sharp"),
        ])
        self.curve_dropdown.setCurrentIndex(1)
        self.curve_dropdown.setToolTip(rich_tooltip(
            "ManualNesterToolUI",
            QT_TRANSLATE_NOOP("ManualNesterToolUI", "Selects the repulsion falloff profile over distance (Linear, Smooth cubic or Sharp exponential).\n<b>Default:</b> Smooth."),
        ))
        physics_layout.addRow(tr("ManualNesterToolUI", "Falloff Curve:"), self.curve_dropdown)

        self.strength_spin = make_double_spinbox(
            1.0, 0.1, 2.0, step=0.1,
            tooltip=rich_tooltip("ManualNesterToolUI", QT_TRANSLATE_NOOP("ManualNesterToolUI", "Scales the repulsion force applied to neighbouring parts.\n<b>Lower:</b> gentler push. <b>Higher:</b> stronger displacement.\n<b>Default:</b> 1.0. <b>Typical:</b> 0.5–1.5."))
        )
        physics_layout.addRow(tr("ManualNesterToolUI", "Strength:"), self.strength_spin)

        self.physics_group.setLayout(physics_layout)
        main_layout.addWidget(self.physics_group)

        main_layout.addStretch()
        self.setLayout(main_layout)

        # Grey out physics settings when valid-placement mode is active
        self.radio_valid.toggled.connect(
            lambda checked: self.physics_group.setEnabled(not checked)
        )
