# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
from freecad.nestingworkbench.Tools.Exporter import exporter
from PySide import QtWidgets
import os

class ExportSheetsCommand:
    """The command to export each sheet as an SVG file."""
    def GetResources(self):
        return {
            'Pixmap': 'Nesting_DXF_Icon.svg',
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

        export_dir = QtWidgets.QFileDialog.getExistingDirectory(None, "Select Export Directory", default_export_dir)

        if not export_dir:
            return

        # Get export options
        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle("Export Options")
        layout = QtWidgets.QVBoxLayout(dialog)
        checkbox = QtWidgets.QCheckBox("Delete 2D Views after DXF Export")
        checkbox.setChecked(True)
        layout.addWidget(checkbox)
        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(button_box)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)

        if dialog.exec_() == QtWidgets.QDialog.Accepted:
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