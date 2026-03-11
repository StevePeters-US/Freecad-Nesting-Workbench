# Nesting/nestingworkbench/Tools/ManualNester/ui_manual_nester.py

"""
This module contains the ManualNesterToolUI class, which defines the user interface
for the manual nester tool task panel.
"""

from PySide import QtGui

class ManualNesterToolUI(QtGui.QWidget):
    """
    Defines the user interface for the manual nester tool task panel.
    """
    def __init__(self, parent=None):
        super(ManualNesterToolUI, self).__init__(parent)
        self.setWindowTitle("Manual Nester")
        self.initUI()

    def initUI(self):
        main_layout = QtGui.QVBoxLayout()

        # Placeholder text. The main "Accept" and "Cancel" are handled by the
        # FreeCAD task panel's default buttons.
        info_label = QtGui.QLabel("Click and drag parts in the 3D view to move them.\n\nUse the 'OK' button to save changes or 'Cancel' to revert.")
        info_label.setWordWrap(True)

        main_layout.addWidget(info_label)
        
        # Physics Settings Group
        physics_group = QtGui.QGroupBox("Physics Settings")
        physics_layout = QtGui.QVBoxLayout()
        
        # Enable Physics
        self.physics_enabled_cb = QtGui.QCheckBox("Enable Physics")
        self.physics_enabled_cb.setChecked(True)
        physics_layout.addWidget(self.physics_enabled_cb)
        
        # Influence Radius
        radius_layout = QtGui.QHBoxLayout()
        radius_layout.addWidget(QtGui.QLabel("Influence Radius (mm):"))
        self.radius_spin = QtGui.QDoubleSpinBox()
        self.radius_spin.setRange(50, 1000)
        self.radius_spin.setValue(200)
        self.radius_spin.setSingleStep(10)
        radius_layout.addWidget(self.radius_spin)
        physics_layout.addLayout(radius_layout)
        
        # Falloff Curve
        curve_layout = QtGui.QHBoxLayout()
        curve_layout.addWidget(QtGui.QLabel("Falloff Curve:"))
        self.curve_dropdown = QtGui.QComboBox()
        self.curve_dropdown.addItems(["Linear", "Smooth", "Sharp"])
        self.curve_dropdown.setCurrentIndex(1)  # Default to Smooth (exp=2)
        curve_layout.addWidget(self.curve_dropdown)
        physics_layout.addLayout(curve_layout)
        
        # Strength
        strength_layout = QtGui.QHBoxLayout()
        strength_layout.addWidget(QtGui.QLabel("Strength:"))
        self.strength_spin = QtGui.QDoubleSpinBox()
        self.strength_spin.setRange(0.1, 2.0)
        self.strength_spin.setValue(1.0)
        self.strength_spin.setSingleStep(0.1)
        strength_layout.addWidget(self.strength_spin)
        physics_layout.addLayout(strength_layout)
        
        physics_group.setLayout(physics_layout)
        main_layout.addWidget(physics_group)

        main_layout.addStretch()
        
        self.setLayout(main_layout)
