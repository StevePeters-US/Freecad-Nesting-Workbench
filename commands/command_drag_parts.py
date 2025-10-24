import FreeCAD
import FreeCADGui
from ..nestingworkbench.Tools.Transform import transform_tool

class DragPartsCommand:
    """The command to manually drag parts in a layout."""
    _observer = None

    def GetResources(self):
        return {
            'Pixmap': 'Nesting/Resources/icons/Nesting_Drag.svg', # You'll need to create this icon
            'MenuText': 'Drag Parts',
            'ToolTip': 'Activates a tool to manually drag parts in the selected layout.',
            'Checkable': True
        }

    def Activated(self, state):
        """This method is executed when the command is toggled."""
        view = FreeCADGui.ActiveDocument.ActiveView
        if state: # Command is checked
            DragPartsCommand._observer = transform_tool.TransformToolObserver(view)
            view.addEventCallback("SoMouseButtonEvent", DragPartsCommand._observer.eventCallback)
            view.addEventCallback("SoLocation2Event", DragPartsCommand._observer.eventCallback)
            FreeCAD.Console.PrintMessage("Drag tool activated. Click and drag parts in the selected layout.\n")
        else: # Command is unchecked
            if DragPartsCommand._observer:
                view.removeEventCallback("SoMouseButtonEvent", DragPartsCommand._observer.eventCallback)
                view.removeEventCallback("SoLocation2Event", DragPartsCommand._observer.eventCallback)
                DragPartsCommand._observer = None
                FreeCAD.Console.PrintMessage("Drag tool deactivated.\n")

    def IsActive(self):
        """Active only if a document is open and a layout is selected."""
        if not FreeCAD.ActiveDocument: return False
        selection = FreeCADGui.Selection.getSelection()
        if not selection: return False
        selected = selection[0]
        return selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_")

if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_DragParts', DragPartsCommand())