# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/task_panel_manager.py

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
    def __init__(self, cleanup_callback=None):
        self.form = NestingPanel()
        self.controller = getattr(self.form, "controller", None)
        self._cleanup_callback = cleanup_callback
        self.task_widget = FreeCADGui.Control.showDialog(self)

    def isAllowedAlterDocument(self):
        # 26.3 RC1 does not honour this for the commands tried (R5-1), so finalize_job handles the edits itself.
        ctrl = getattr(self, "controller", None)
        if ctrl is None:
            return True
        return not (getattr(ctrl, "current_job", None) or getattr(ctrl, "is_running", False))
    
    def accept(self):
        """Called by FreeCAD when the dialog's 'OK' button is clicked."""
        try:
            ret = self.form.accept() if hasattr(self.form, "accept") else True
        except Exception:
            self.cleanup()
            raise
        # False keeps the panel open; None (no return value) closes it, as before FCU-032.
        if ret is False:
            return False
        self.cleanup()
        return True

    def reject(self):
        """Called by FreeCAD when the dialog is closed or 'Cancel' is clicked."""
        try:
            if hasattr(self.form, "reject"):
                self.form.reject()
        finally:
            self.cleanup()
        return True

    def cleanup(self):
        """Resets the command's panel instance to allow it to be reopened."""
        if self._cleanup_callback:
            self._cleanup_callback()
