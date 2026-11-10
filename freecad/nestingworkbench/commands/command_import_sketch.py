# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/commands/command_import_sketch.py

"""
FreeCAD command to import DXF/SVG drawings as sketches (one per part).

Opens a file chooser, then calls sketch_importer.import_file on each selected path.
"""

import os
import FreeCAD
import FreeCADGui
from PySide import QtWidgets

from freecad.nestingworkbench import nw_logger
from freecad.nestingworkbench.constants import PREFS_PATH
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip

_IMPORT_DIALOG_TITLE = QT_TRANSLATE_NOOP("ImportSketchCommand", "Import DXF / SVG as Sketches")
_IMPORT_DIALOG_FILTER = QT_TRANSLATE_NOOP(
    "ImportSketchCommand", "Drawings (*.dxf *.svg);;DXF (*.dxf);;SVG (*.svg)"
)


class ImportSketchCommand:
    """Command to import DXF/SVG files as one sketch per part (ISK-009)."""

    def GetResources(self):
        tr = QtWidgets.QApplication.translate
        return {
            'Pixmap': 'Nesting_ImportSketch_Icon.svg',
            'MenuText': tr('ImportSketchCommand', 'Import as Sketches...'),
            'ToolTip': rich_tooltip(
                'ImportSketchCommand',
                QT_TRANSLATE_NOOP(
                    'ImportSketchCommand',
                    'Imports DXF or SVG drawings as one sketch per part, keeping holes and turning open geometry into construction lines.\nOpen a document first.',
                ),
            ),
        }

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            nw_logger.error("No active document")
            return

        param_grp = FreeCAD.ParamGet(PREFS_PATH)
        default_dir = param_grp.GetString("SketchImportDir", "")

        tr = QtWidgets.QApplication.translate
        files, _selected_filter = QtWidgets.QFileDialog.getOpenFileNames(
            None,
            tr("ImportSketchCommand", _IMPORT_DIALOG_TITLE),
            default_dir,
            tr("ImportSketchCommand", _IMPORT_DIALOG_FILTER)
        )

        if not files:
            return

        param_grp.SetString("SketchImportDir", os.path.dirname(files[0]))

        from freecad.nestingworkbench.Tools.SketchImport.sketch_importer import import_file

        for path in files:
            try:
                import_file(doc, path)
            except Exception as e:
                nw_logger.error(f"Failed to import '{path}': {e}")

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None


if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_ImportSketch', ImportSketchCommand())
