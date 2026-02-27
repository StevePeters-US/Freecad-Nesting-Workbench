# Nesting/nestingworkbench/Tools/ManualNester/manual_nester_panel_manager.py

"""
This module contains the ManualNesterTaskPanel class, which is responsible for
creating, showing, and managing the lifecycle of the FreeCAD Task Panel
for the manual nester tool.
"""

import FreeCADGui
from .ui_manual_nester import ManualNesterToolUI
from .manual_nester_tool import ManualNesterToolObserver

class ManualNesterTaskPanel:
    """Manages the FreeCAD Task Panel dialog for the manual nester tool."""
    def __init__(self, view):
        self.form = ManualNesterToolUI()
        self.observer = ManualNesterToolObserver(view, self)
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
        from nesting_commands.command_manual_nester import ManualNesterCommand
        ManualNesterCommand._task_panel = None
