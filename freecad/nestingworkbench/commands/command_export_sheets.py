# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
from freecad.nestingworkbench.Tools.Exporter import exporter
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP
from freecad.nestingworkbench.freecad_helpers import is_layout_group
from PySide import QtWidgets
import os

_SELECT_DIR_TITLE = QT_TRANSLATE_NOOP("ExportSheetsCommand", "Select Export Directory")
_EXPORT_OPTIONS_TITLE = QT_TRANSLATE_NOOP("ExportSheetsCommand", "Export Options")
_DELETE_2D_VIEWS_TEXT = QT_TRANSLATE_NOOP("ExportSheetsCommand", "Delete 2D Views after DXF Export")

class ExportSheetsCommand:
    """The command to export each sheet as an SVG file."""
    def GetResources(self):
        return {
            'Pixmap': 'Nesting_DXF_Icon.svg',
            'MenuText': QT_TRANSLATE_NOOP('ExportSheetsCommand', 'Export Sheets as DXF'),
            'ToolTip': QT_TRANSLATE_NOOP('ExportSheetsCommand', 'Exports each sheet in the layout to a separate DXF file.')
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        selection = FreeCADGui.Selection.getSelection()
        layout_group = None
        if selection:
            selected = selection[0]
            if is_layout_group(selected):
                layout_group = selected

        if not layout_group:
            FreeCAD.Console.PrintMessage("Please select a layout group to export.\n")
            return

        tr = QtWidgets.QApplication.translate

        # Get export directory
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        default_export_dir = os.path.join(downloads_dir, f"{layout_group.Label}_DXF_Export")
        created_default = False
        if not os.path.exists(default_export_dir):
            os.makedirs(default_export_dir)
            created_default = True

        def _discard_default():
            if created_default:
                try:
                    os.rmdir(default_export_dir)  # only succeeds while still empty
                except OSError:
                    # Directory not empty (files were exported) — keep it.
                    pass

        export_dir = QtWidgets.QFileDialog.getExistingDirectory(
            None, tr("ExportSheetsCommand", _SELECT_DIR_TITLE), default_export_dir
        )

        if not export_dir:
            _discard_default()
            return

        def _same_dir(a, b):
            # normcase: Windows may return the same folder with a different
            # drive-letter case, which normpath alone treats as different.
            return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))

        if not _same_dir(export_dir, default_export_dir):
            _discard_default()

        # Get export options
        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle(tr("ExportSheetsCommand", _EXPORT_OPTIONS_TITLE))
        layout = QtWidgets.QVBoxLayout(dialog)
        checkbox = QtWidgets.QCheckBox(tr("ExportSheetsCommand", _DELETE_2D_VIEWS_TEXT))
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
        else:
            _discard_default()

    def IsActive(self):
        """Active only if a document is open and a layout group is selected."""
        if not FreeCAD.ActiveDocument: return False
        selection = FreeCADGui.Selection.getSelection()
        if not selection: return False
        selected = selection[0]
        return is_layout_group(selected)

if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_Export', ExportSheetsCommand())