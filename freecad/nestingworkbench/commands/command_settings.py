# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/commands/command_settings.py

"""FreeCAD command that opens the Nesting Workbench settings dialog."""

import FreeCAD
import FreeCADGui
from PySide import QtWidgets

from freecad.nestingworkbench import nw_logger
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, make_checkbox

_CTX = "SettingsCommand"
_TITLE = QT_TRANSLATE_NOOP(_CTX, "Nesting Settings")
_GROUP_DIAG = QT_TRANSLATE_NOOP(_CTX, "Diagnostics")
_CRASH_LABEL = QT_TRANSLATE_NOOP(_CTX, "Enable crash logs")
_CRASH_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Write every log message to Nesting.log in FreeCAD's user data folder. "
          "Errors are always written, even when this is off.")
_DEBUG_LABEL = QT_TRANSLATE_NOOP(_CTX, "Enable debug logging")
_DEBUG_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Print detailed diagnostic messages to the Report view. Leave off unless "
          "you are investigating a problem.")


class SettingsDialog(QtWidgets.QDialog):
    """Workbench-wide preferences; changes apply on OK and are discarded on Cancel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        tr = QtWidgets.QApplication.translate
        self.setWindowTitle(tr(_CTX, _TITLE))

        self.crash_check = make_checkbox(
            tr(_CTX, _CRASH_LABEL), nw_logger.get_enable_crash_log(), tr(_CTX, _CRASH_TIP))
        self.debug_check = make_checkbox(
            tr(_CTX, _DEBUG_LABEL), nw_logger.get_enable_debug_log(), tr(_CTX, _DEBUG_TIP))

        group = QtWidgets.QGroupBox(tr(_CTX, _GROUP_DIAG))
        form = QtWidgets.QFormLayout(group)
        form.addRow(self.crash_check)
        form.addRow(self.debug_check)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(group)
        layout.addWidget(buttons)

    def _on_accept(self):
        """Persist the checkboxes, then close."""
        nw_logger.set_enable_crash_log(self.crash_check.isChecked())
        nw_logger.set_enable_debug_log(self.debug_check.isChecked())
        self.accept()


class SettingsCommand:
    """Opens the Nesting Workbench settings dialog."""

    def GetResources(self):
        return {
            'Pixmap': 'preferences-system',
            'MenuText': QT_TRANSLATE_NOOP(_CTX, 'Nesting Settings'),
            'ToolTip': QT_TRANSLATE_NOOP(_CTX, 'Configure Nesting Workbench settings such as logging.'),
        }

    def Activated(self):
        SettingsDialog(FreeCADGui.getMainWindow()).exec_()

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_Settings', SettingsCommand())
