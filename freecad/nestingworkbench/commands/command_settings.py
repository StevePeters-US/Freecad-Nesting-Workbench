# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/commands/command_settings.py

"""FreeCAD command that opens the Nesting Workbench settings dialog."""

import FreeCAD
import FreeCADGui
from PySide import QtWidgets

from freecad.nestingworkbench import nw_logger
from freecad.nestingworkbench.constants import PREFS_PATH, PREF_CURRENCY_SYMBOL
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, make_checkbox, rich_tooltip

_CTX = "SettingsCommand"
_TITLE = QT_TRANSLATE_NOOP(_CTX, "Nesting Settings")
_GROUP_DIAG = QT_TRANSLATE_NOOP(_CTX, "Diagnostics")
_CRASH_LABEL = QT_TRANSLATE_NOOP(_CTX, "Enable crash logs")
_CRASH_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Writes every log message to Nesting.log in FreeCAD's user data folder. "
          "Errors are always written, even when this is off.")
_DEBUG_LABEL = QT_TRANSLATE_NOOP(_CTX, "Enable debug logging")
_DEBUG_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Prints detailed diagnostic messages to the Report view. Leave off unless "
          "investigating a problem.")
_CURRENCY_LABEL = QT_TRANSLATE_NOOP(_CTX, "Currency symbol")
_CURRENCY_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Sets the prefix symbol for sheet prices (for example $, € or £).")


class SettingsDialog(QtWidgets.QDialog):
    """Workbench-wide preferences; changes apply on OK and are discarded on Cancel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        tr = QtWidgets.QApplication.translate
        self.setWindowTitle(tr(_CTX, _TITLE))

        self.crash_check = make_checkbox(
            tr(_CTX, _CRASH_LABEL), nw_logger.get_enable_crash_log(), rich_tooltip(_CTX, _CRASH_TIP))
        self.debug_check = make_checkbox(
            tr(_CTX, _DEBUG_LABEL), nw_logger.get_enable_debug_log(), rich_tooltip(_CTX, _DEBUG_TIP))

        curr = ""
        try:
            curr = FreeCAD.ParamGet(PREFS_PATH).GetString(PREF_CURRENCY_SYMBOL, "")
        except Exception as e:
            nw_logger.debug(f"Failed to read currency symbol: {e}")
        self.currency_input = QtWidgets.QLineEdit(curr)
        self.currency_input.setMaxLength(5)
        self.currency_input.setToolTip(rich_tooltip(_CTX, _CURRENCY_TIP))

        group = QtWidgets.QGroupBox(tr(_CTX, _GROUP_DIAG))
        form = QtWidgets.QFormLayout(group)
        form.addRow(self.crash_check)
        form.addRow(self.debug_check)
        form.addRow(tr(_CTX, _CURRENCY_LABEL), self.currency_input)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(group)
        layout.addWidget(buttons)

    def _on_accept(self):
        """Persist the checkboxes and preferences, then close."""
        nw_logger.set_enable_crash_log(self.crash_check.isChecked())
        nw_logger.set_enable_debug_log(self.debug_check.isChecked())
        try:
            FreeCAD.ParamGet(PREFS_PATH).SetString(PREF_CURRENCY_SYMBOL, self.currency_input.text().strip())
        except Exception as e:
            nw_logger.warn(f"Failed to save currency symbol: {e}")
        self.accept()


class SettingsCommand:
    """Opens the Nesting Workbench settings dialog."""

    def GetResources(self):
        return {
            'Pixmap': 'preferences-system',
            'MenuText': QT_TRANSLATE_NOOP(_CTX, 'Nesting Settings'),
            'ToolTip': rich_tooltip(
                _CTX,
                QT_TRANSLATE_NOOP(_CTX, 'Sets the currency symbol and diagnostic logging options for the workbench.'),
            ),
        }

    def Activated(self):
        SettingsDialog(FreeCADGui.getMainWindow()).exec_()

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_Settings', SettingsCommand())
