# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD command that opens the Nesting Workbench Library window."""

import FreeCAD
import FreeCADGui
from PySide import QtWidgets

from freecad.nestingworkbench.Tools.Library.ui_library import LibraryDialog
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip

_CTX = "Nesting_Library"


class LibraryCommand:
    """Opens the Nesting Workbench library management window."""

    def GetResources(self):
        tr = QtWidgets.QApplication.translate
        return {
            'Pixmap': 'Nesting_Library',
            'MenuText': tr(_CTX, 'Library'),
            'ToolTip': rich_tooltip(
                _CTX,
                QT_TRANSLATE_NOOP(
                    _CTX,
                    'Manage your saved sheets (material, size, thickness, price and grain) '
                    'and the list of materials they use.',
                ),
            ),
        }

    def Activated(self):
        parent = FreeCADGui.getMainWindow() if hasattr(FreeCADGui, "getMainWindow") else None
        LibraryDialog(parent).exec_()

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_Library', LibraryCommand())
