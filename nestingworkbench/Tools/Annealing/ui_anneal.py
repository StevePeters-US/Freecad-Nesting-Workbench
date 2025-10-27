# Nesting/nesting/ui_anneal.py

"""
This module contains the AnnealUI class, which defines the user interface
for the layout annealing task panel.
"""

from PySide import QtGui, QtCore
from .anneal_controller import AnnealController


class AnnealUI(QtGui.QWidget):
    """
    Defines the user interface for the annealing task panel.
    """
    def __init__(self, layout_group, parent=None):
        super(AnnealUI, self).__init__(parent)
        self.setWindowTitle("Anneal Layout")
        self.layout_group = layout_group
        self.initUI()

    def initUI(self):
        main_layout = QtGui.QVBoxLayout()
        form_layout = QtGui.QFormLayout()
        action_button_layout = QtGui.QHBoxLayout()

        # --- Simulated Annealing Settings (reused from main panel) ---
        self.sa_settings_group = QtGui.QGroupBox("Simulated Annealing Settings")
        sa_form_layout = QtGui.QFormLayout()
        self.sa_temp_initial_input = QtGui.QDoubleSpinBox()
        self.sa_temp_initial_input.setRange(1, 100000)
        self.sa_temp_initial_input.setValue(1000)
        self.sa_temp_final_input = QtGui.QDoubleSpinBox()
        self.sa_temp_final_input.setRange(0.01, 1000)
        self.sa_temp_final_input.setValue(1)
        self.sa_cooling_rate_input = QtGui.QDoubleSpinBox()
        self.sa_cooling_rate_input.setRange(0.8, 0.999)
        self.sa_cooling_rate_input.setSingleStep(0.01)
        self.sa_cooling_rate_input.setValue(0.95)
        self.sa_substeps_input = QtGui.QSpinBox()
        self.sa_substeps_input.setRange(1, 10000)
        self.sa_substeps_input.setValue(10)
        self.sa_total_max_iter_input = QtGui.QSpinBox()
        self.sa_total_max_iter_input.setRange(100, 100000)
        self.sa_total_max_iter_input.setValue(1000)
        self.sa_minima_threshold_input = QtGui.QSpinBox()
        self.sa_minima_threshold_input.setRange(1, 100)
        self.sa_minima_threshold_input.setValue(5)
        sa_form_layout.addRow("Initial Temperature:", self.sa_temp_initial_input)
        sa_form_layout.addRow("Final Temperature:", self.sa_temp_final_input)
        sa_form_layout.addRow("Cooling Rate:", self.sa_cooling_rate_input)
        sa_form_layout.addRow("Substeps/Temp:", self.sa_substeps_input)
        sa_form_layout.addRow("Total Max Iterations:", self.sa_total_max_iter_input)
        sa_form_layout.addRow("Minima Threshold:", self.sa_minima_threshold_input)
        self.sa_settings_group.setLayout(sa_form_layout)

        # --- Consolidation Settings ---
        self.consolidate_checkbox = QtGui.QCheckBox("Consolidate parts from last sheet")
        self.consolidate_checkbox.setChecked(True)
        self.consolidate_checkbox.setToolTip("Tries to move parts from the last sheet into free space on earlier sheets.")

        self.rotation_steps_input = QtGui.QSpinBox()
        self.rotation_steps_input.setRange(1, 360)
        self.rotation_steps_input.setValue(4)

        self.run_button = QtGui.QPushButton("Run Annealing")
        self.status_label = QtGui.QLabel(f"Ready to anneal layout: {self.layout_group.Label}")
        self.status_label.setWordWrap(True)

        form_layout.addRow(self.sa_settings_group)
        form_layout.addRow("Rotation Steps:", self.rotation_steps_input)
        form_layout.addRow(self.consolidate_checkbox)

        action_button_layout.addWidget(self.run_button)

        main_layout.addLayout(form_layout)
        main_layout.addLayout(action_button_layout)
        main_layout.addWidget(self.status_label)
        main_layout.addStretch()
        
        self.setLayout(main_layout)

        # Connect signals
        self.controller = AnnealController(self, self.layout_group)
        self.run_button.clicked.connect(self.controller.execute_annealing)

    def accept(self): return True
    def reject(self): return True