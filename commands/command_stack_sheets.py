import FreeCAD
import FreeCADGui
from nestingworkbench.Tools.Stacker import stacker

class StackSheetsCommand:
    """The command to stack and unstack packed sheets."""
    def GetResources(self):
        return {
            'Pixmap': 'Nesting/Resources/icons/Nesting_Stack.svg',
            'MenuText': 'Stack/Unstack Sheets',
            'ToolTip': 'Toggles sheet layout between stacked and unstacked.'
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        selection = FreeCADGui.Selection.getSelection()
        layout_group = None
        if selection:
            selected = selection[0]
            if selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_"):
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
        return selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_")

if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_StackSheets', StackSheetsCommand())