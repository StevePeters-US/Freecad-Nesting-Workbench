import FreeCAD
import FreeCADGui
from ..nestingworkbench.Tools.Transform import transform_panel_manager

class TransformPartsCommand:
    """The command to manually transform parts in a layout."""
    _task_panel = None
    
    def GetResources(self):
        return {
            'Pixmap': 'Nesting/Resources/icons/Nesting_Transform.svg', # You'll need to create this icon
            'MenuText': 'Transform Parts',
            'ToolTip': 'Activates a tool to manually transform parts in the selected layout.'
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        view = FreeCADGui.ActiveDocument.ActiveView
        if TransformPartsCommand._task_panel is None:
            TransformPartsCommand._task_panel = transform_panel_manager.TransformTaskPanel(view)

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
    FreeCADGui.addCommand('Nesting_TransformParts', TransformPartsCommand())