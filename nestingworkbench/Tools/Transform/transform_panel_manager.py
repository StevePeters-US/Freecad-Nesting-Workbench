# Nesting/nestingworkbench/Tools/Transform/transform_panel_manager.py

"""
This module contains the TransformTaskPanel class, which is responsible for
creating, showing, and managing the lifecycle of the FreeCAD Task Panel
for the manual transform tool.
"""

import FreeCADGui
from .ui_transform import TransformToolUI
from .transform_tool import TransformToolObserver

class TransformTaskPanel:
    """Manages the FreeCAD Task Panel dialog for the transform tool."""
    def __init__(self, view):
        self.form = TransformToolUI()
        self.observer = TransformToolObserver(view, self)
        self.task_widget = FreeCADGui.Control.showDialog(self)

    def accept(self):
        """Called by FreeCAD when the dialog's 'OK' or 'Accept' button is clicked."""
        if self.observer:
            self.observer.save_placements()
        self.cleanup()
        return True

    def reject(self):
        """Called by FreeCAD when the dialog is closed or 'Cancel' is clicked."""
        if self.observer:
            self.observer.cancel()
        self.cleanup()
        return True

    def cleanup(self):
        """Resets the command's panel instance and removes the observer."""
        if self.observer:
            self.observer.cleanup()
        # Use an absolute import from the workbench's root package 'Nesting'
        # to break a potential circular dependency.
        from nesting_commands.command_transform_parts import TransformPartsCommand
        TransformPartsCommand._task_panel = None