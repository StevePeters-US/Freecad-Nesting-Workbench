import FreeCAD
import FreeCADGui
from nestingworkbench.Tools.Annealing import ui_anneal

class AnnealLayoutCommand:
    """The command to open the layout annealing task panel."""
    _task_panel = None

    def GetResources(self):
        return {
            'Pixmap': 'Nesting/Resources/icons/Nesting_Anneal.svg', # You'll need to create this icon
            'MenuText': 'Anneal Layout',
            'ToolTip': 'Optimizes the selected layout using Simulated Annealing.'
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        selection = FreeCADGui.Selection.getSelection()
        if not selection or not selection[0].isDerivedFrom("App::DocumentObjectGroup") or not selection[0].Label.startswith("Layout_"):
            FreeCAD.Console.PrintMessage("Please select a single Layout group to anneal.\n")
            return

        layout_group = selection[0]

        # Close any existing panel before opening a new one
        if AnnealLayoutCommand._task_panel:
            FreeCADGui.Control.closeDialog()
            AnnealLayoutCommand._task_panel = None

        AnnealLayoutCommand._task_panel = FreeCADGui.Control.showDialog(ui_anneal.AnnealUI(layout_group))

    def IsActive(self):
        """Active only if a document is open and a layout is selected."""
        if not FreeCAD.ActiveDocument: return False
        selection = FreeCADGui.Selection.getSelection()
        if not selection: return False
        selected = selection[0]
        return selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_")

if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_AnnealLayout', AnnealLayoutCommand())