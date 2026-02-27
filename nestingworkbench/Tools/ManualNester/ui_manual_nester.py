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
        main_layout.addStretch()
        
        self.setLayout(main_layout)
