# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
from freecad.nestingworkbench.Tools.ManualNester import manual_nester_panel_manager
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP
from freecad.nestingworkbench.freecad_helpers import is_layout_group

class ManualNesterCommand:
    """The command to manually nest parts in a layout."""
    _task_panel = None
    
    def GetResources(self):
        return {
            'Pixmap': 'Nesting_Transform_Icon.svg',
            'MenuText': QT_TRANSLATE_NOOP('ManualNesterCommand', 'Manual Nester'),
            'ToolTip': QT_TRANSLATE_NOOP('ManualNesterCommand', 'Activates a tool to manually nest parts in the selected layout.')
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        view = FreeCADGui.ActiveDocument.ActiveView
        if ManualNesterCommand._task_panel is None:
            ManualNesterCommand._task_panel = manual_nester_panel_manager.ManualNesterTaskPanel(view)

    def IsActive(self):
        """Active only if a document is open and a layout group is selected."""
        if not FreeCAD.ActiveDocument:
            return False
        selection = FreeCADGui.Selection.getSelection()
        if not selection:
            return False
        selected = selection[0]
        return is_layout_group(selected)


if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_ManualNester', ManualNesterCommand())
