# Nesting/nesting/task_panel_manager.py

"""
This module contains the NestingTaskPanel class, which is responsible for
creating, showing, and managing the lifecycle of the FreeCAD Task Panel.
"""

import FreeCAD
import FreeCADGui

# Import the UI panel class
from .Tools.Nesting.ui_nesting import NestingPanel

class NestingTaskPanel:
    """Manages the FreeCAD Task Panel dialog."""
    def __init__(self):
        self.form = NestingPanel()
        self.task_widget = FreeCADGui.Control.showDialog(self)
    
    def accept(self):
        """Called by FreeCAD when the dialog's 'OK' button is clicked."""
        self.form.handle_accept()
        self.cleanup()
        return True

    def reject(self):
        """Called by FreeCAD when the dialog is closed or 'Cancel' is clicked."""
        self.form.handle_cancel()
        if not self.form.accepted:
            self.form.cleanup_preview()
            for obj in self.form.hidden_originals:
                try:
                    if hasattr(obj, "ViewObject"): 
                        obj.ViewObject.Visibility = True
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"Could not restore visibility for {obj.Label}: {e}\n")
        
        self.cleanup()
        return True

    def cleanup(self):
        """Resets the command's panel instance to allow it to be reopened."""
        # Import here to break the circular dependency
        from Nesting.commands.command_nest import NestingCommand
        NestingCommand._task_panel = None
