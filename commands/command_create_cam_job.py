import FreeCAD
import FreeCADGui
from ..nestingworkbench.Tools.Cam import cam_manager

class CreateCAMJobCommand:
    """The command to create a CAM job from a layout."""
    def GetResources(self):
        return {
            'Pixmap': 'Nesting/Resources/icons/Nesting_CAM.svg',
            'MenuText': 'Create CAM Job',
            'ToolTip': 'Creates a CAM job from the selected layout.'
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        selection = FreeCADGui.Selection.getSelection()
        layout_group = None
        if selection:
            selected = selection[0]
            if selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_"):
                layout_group = selected

        if not layout_group:
            FreeCAD.Console.PrintMessage("Please select a layout group to create a CAM job from.\n")
            return

        cam_manager_instance = cam_manager.CAMManager(layout_group=layout_group)
        cam_manager_instance.create_cam_job()

    def IsActive(self):
        """Active only if a document is open and a layout group is selected."""
        if not FreeCAD.ActiveDocument: return False
        selection = FreeCADGui.Selection.getSelection()
        if not selection: return False
        selected = selection[0]
        return selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_")

if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_CreateCAMJob', CreateCAMJobCommand())