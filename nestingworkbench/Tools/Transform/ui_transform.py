# Nesting/nestingworkbench/Tools/Transform/ui_transform.py

"""
This module contains the TransformToolUI class, which defines the user interface
for the transform tool task panel.
"""

from PySide import QtGui

class TransformToolUI(QtGui.QWidget):
    """
    Defines the user interface for the transform tool task panel.
    """
    def __init__(self, parent=None):
        super(TransformToolUI, self).__init__(parent)
        self.setWindowTitle("Transform Tool")
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
