# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
from freecad.nestingworkbench.Tools.Stacker import stacker
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP
from freecad.nestingworkbench.freecad_helpers import is_layout_group

class StackSheetsCommand:
    """The command to stack and unstack packed sheets."""
    def GetResources(self):
        return {
            'Pixmap': 'Nesting_Stack_Icon.svg',
            'MenuText': QT_TRANSLATE_NOOP('StackSheetsCommand', 'Stack/Unstack Sheets'),
            'ToolTip': QT_TRANSLATE_NOOP('StackSheetsCommand', 'Toggles sheet layout between stacked and unstacked.')
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        selection = FreeCADGui.Selection.getSelection()
        layout_group = None
        if selection:
            selected = selection[0]
            if is_layout_group(selected):
                layout_group = selected
        
        sheet_stacker = stacker.SheetStacker(layout_group=layout_group)
        sheet_stacker.toggle_stack()

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
    FreeCADGui.addCommand('Nesting_StackSheets', StackSheetsCommand())