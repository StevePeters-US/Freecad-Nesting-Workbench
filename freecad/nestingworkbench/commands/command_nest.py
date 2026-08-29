# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
from freecad.nestingworkbench import task_panel_manager

# --- FreeCAD Command Classes ---

class NestingCommand:
    """The command that opens the main nesting task panel."""
    _task_panel = None

    def GetResources(self):
        """Defines the command's appearance in FreeCAD."""
        return {
            'Pixmap': 'Nesting_Nest_Icon.svg',
            'MenuText': 'Run Nesting Tool',
            'ToolTip': 'Opens the 2D nesting task panel.'
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        # Manages its own instance to prevent multiple panels
        if NestingCommand._task_panel is None:
            NestingCommand._task_panel = task_panel_manager.NestingTaskPanel(
                cleanup_callback=lambda: setattr(NestingCommand, "_task_panel", None)
            )

    def IsActive(self):
        """Can only be active if a document is open."""
        return FreeCAD.ActiveDocument is not None

# --- Command Registration ---
# This is where the commands are officially made known to FreeCAD.
if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_Run', NestingCommand())
