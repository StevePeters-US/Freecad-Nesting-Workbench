import FreeCAD
import FreeCADGui
from nestingworkbench.Tools.ManualNester import manual_nester_panel_manager

class ManualNesterCommand:
    """The command to manually nest parts in a layout."""
    _task_panel = None
    
    def GetResources(self):
        return {
            'Pixmap': 'Transform_Icon.png', # Keeping icon for now
            'MenuText': 'Manual Nester',
            'ToolTip': 'Activates a tool to manually nest parts in the selected layout.'
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
        return selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_")


if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_ManualNester', ManualNesterCommand())
