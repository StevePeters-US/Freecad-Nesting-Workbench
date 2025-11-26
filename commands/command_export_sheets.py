import FreeCAD
import FreeCADGui
from nestingworkbench.Tools.Exporter import exporter
from PySide import QtGui
import os

class ExportSheetsCommand:
    """The command to export each sheet as an SVG file."""
    def GetResources(self):
        return {
            'Pixmap': 'DXF_Icon.png',
            'MenuText': 'Export Sheets as DXF',
            'ToolTip': 'Exports each sheet in the layout to a separate DXF file.'
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
            FreeCAD.Console.PrintMessage("Please select a layout group to export.\n")
            return

        # Get export directory
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        default_export_dir = os.path.join(downloads_dir, f"{layout_group.Label}_DXF_Export")
        if not os.path.exists(default_export_dir):
            os.makedirs(default_export_dir)

        export_dir = QtGui.QFileDialog.getExistingDirectory(None, "Select Export Directory", default_export_dir)

        if not export_dir:
            return

        # Get export options
        dialog = QtGui.QDialog()
        dialog.setWindowTitle("Export Options")
        layout = QtGui.QVBoxLayout(dialog)
        checkbox = QtGui.QCheckBox("Delete 2D Views after DXF Export")
        checkbox.setChecked(True)
        layout.addWidget(checkbox)
        button_box = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
        layout.addWidget(button_box)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)

        if dialog.exec_() == QtGui.QDialog.Accepted:
            delete_generated = checkbox.isChecked()
            exporter_instance = exporter.SheetExporter(layout_group=layout_group)
            exporter_instance.export_sheets(export_dir=export_dir, delete_generated_objects=delete_generated)

    def IsActive(self):
        """Active only if a document is open and a layout group is selected."""
        if not FreeCAD.ActiveDocument: return False
        selection = FreeCADGui.Selection.getSelection()
        if not selection: return False
        selected = selection[0]
        return selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_")

if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_Export', ExportSheetsCommand())