# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/commands/command_settings.py

"""FreeCAD command that opens the Nesting Workbench settings dialog."""

import FreeCAD
import FreeCADGui
from PySide import QtGui, QtWidgets

from freecad.nestingworkbench import nw_logger, nw_settings
from freecad.nestingworkbench.constants import (
    PREF_FRONT_SHEET_COLOR, PREF_BACK_SHEET_COLOR,
    PREF_DXF_PART_COLOR, PREF_DXF_LABEL_COLOR, PREF_DXF_SHEET_COLOR,
    PREF_DXF_BOUND_COLOR)
from freecad.nestingworkbench.ui_helpers import (
    MARGINS_NONE, QT_TRANSLATE_NOOP, CollapsibleSection, make_checkbox, rich_tooltip)

_CTX = "SettingsCommand"
_TITLE = QT_TRANSLATE_NOOP(_CTX, "Nesting Settings")
_SECTION_COSTS = QT_TRANSLATE_NOOP(_CTX, "Costs")
_SECTION_RUNS = QT_TRANSLATE_NOOP(_CTX, "Nesting runs")
_SECTION_DIAG = QT_TRANSLATE_NOOP(_CTX, "Diagnostics")
_CURRENCY_LABEL = QT_TRANSLATE_NOOP(_CTX, "Currency symbol:")
_CURRENCY_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Symbol written before every price: the Sheets tab, the sheet editor and the "
          "status line at the end of a run. Clear it for no symbol.")
# {0} is the locale's symbol, e.g. "Use system default ($)".
_CURRENCY_RESET = QT_TRANSLATE_NOOP(_CTX, "Use system default ({0})")
_CURRENCY_RESET_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Forget the symbol typed here and follow FreeCAD's number format "
          "(Preferences > General), as it was before you changed it.")
_SOUND_LABEL = QT_TRANSLATE_NOOP(_CTX, "Play a sound when nesting finishes")
_SOUND_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Beep when a nesting run ends, so you can work elsewhere while it runs.")
_CRASH_LABEL = QT_TRANSLATE_NOOP(_CTX, "Enable crash logs")
_CRASH_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Write every log message to Nesting.log in FreeCAD's user data folder. "
          "Errors are always written, even when this is off.")
_DEBUG_LABEL = QT_TRANSLATE_NOOP(_CTX, "Enable debug logging")
_DEBUG_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Print detailed diagnostic messages to the Report view. Leave off unless "
          "you are investigating a problem.")
_VERBOSE_LABEL = QT_TRANSLATE_NOOP(_CTX, "Verbose run logging")
_VERBOSE_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Write each nesting run's and layout reload's progress to the Report view as "
          "normal messages. Debug logging covers the whole workbench; this covers only runs.")
_SECTION_COLORS = QT_TRANSLATE_NOOP(_CTX, "Colors")
_FRONT_COLOR_LABEL = QT_TRANSLATE_NOOP(_CTX, "Sheet:")
_FRONT_COLOR_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Color of each sheet in the 3D view. Applies to layouts drawn from now on.")
_BACK_COLOR_LABEL = QT_TRANSLATE_NOOP(_CTX, "Sheet back side:")
_BACK_COLOR_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Color of a flipped sheet's back side in the 3D view. Applies to layouts "
          "drawn from now on.")
_DXF_PART_LABEL = QT_TRANSLATE_NOOP(_CTX, "DXF part outlines:")
_DXF_PART_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Color of part outlines in exported DXF files. Each part is its own layer; "
          "this sets the layer's color.")
_DXF_LABEL_LABEL = QT_TRANSLATE_NOOP(_CTX, "DXF labels:")
_DXF_LABEL_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Color of part labels in exported DXF files.")
_DXF_SHEET_LABEL = QT_TRANSLATE_NOOP(_CTX, "DXF sheet border:")
_DXF_SHEET_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Color of the sheet outline in exported DXF files.")
_DXF_BOUND_LABEL = QT_TRANSLATE_NOOP(_CTX, "DXF part bounds:")
_DXF_BOUND_TIP = QT_TRANSLATE_NOOP(
    _CTX, "Color of part bounds in exported DXF files, when Export Options includes them.")
# AutoCAD colour indices 1-9, in nw_settings.DXF_COLORS order.
_DXF_COLOR_NAMES = (
    QT_TRANSLATE_NOOP(_CTX, "Red"),
    QT_TRANSLATE_NOOP(_CTX, "Yellow"),
    QT_TRANSLATE_NOOP(_CTX, "Green"),
    QT_TRANSLATE_NOOP(_CTX, "Cyan"),
    QT_TRANSLATE_NOOP(_CTX, "Blue"),
    QT_TRANSLATE_NOOP(_CTX, "Magenta"),
    QT_TRANSLATE_NOOP(_CTX, "White / black"),
    QT_TRANSLATE_NOOP(_CTX, "Dark gray"),
    QT_TRANSLATE_NOOP(_CTX, "Light gray"),
)


class SettingsDialog(QtWidgets.QDialog):
    """Workbench-wide preferences; changes apply on OK and are discarded on Cancel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        tr = QtWidgets.QApplication.translate
        self.setWindowTitle(tr(_CTX, _TITLE))

        # "untouched": OK writes nothing, so an unset symbol keeps following the
        # locale. "edited": OK saves the text. "reset": OK forgets the saved symbol.
        self._currency_state = "untouched"
        self.currency_input = QtWidgets.QLineEdit(nw_settings.get_currency_symbol())
        self.currency_input.setMaxLength(5)
        self.currency_input.setToolTip(rich_tooltip(_CTX, _CURRENCY_TIP))
        self.currency_input.textEdited.connect(self._on_currency_edited)
        self.currency_reset_button = QtWidgets.QPushButton(
            tr(_CTX, _CURRENCY_RESET).format(nw_settings.locale_currency_symbol()))
        self.currency_reset_button.setToolTip(rich_tooltip(_CTX, _CURRENCY_RESET_TIP))
        self.currency_reset_button.clicked.connect(self._on_currency_reset)
        currency_row = QtWidgets.QHBoxLayout()
        currency_row.setContentsMargins(*MARGINS_NONE)
        currency_row.addWidget(self.currency_input)
        currency_row.addWidget(self.currency_reset_button)

        self.sound_check = make_checkbox(
            tr(_CTX, _SOUND_LABEL), nw_settings.get_play_sound(), rich_tooltip(_CTX, _SOUND_TIP))
        self.crash_check = make_checkbox(
            tr(_CTX, _CRASH_LABEL), nw_logger.get_enable_crash_log(), rich_tooltip(_CTX, _CRASH_TIP))
        self.debug_check = make_checkbox(
            tr(_CTX, _DEBUG_LABEL), nw_logger.get_enable_debug_log(), rich_tooltip(_CTX, _DEBUG_TIP))
        self.verbose_check = make_checkbox(
            tr(_CTX, _VERBOSE_LABEL), nw_settings.get_verbose_logging(),
            rich_tooltip(_CTX, _VERBOSE_TIP))

        # 3D-view colours: the value shown, and the keys the user picked a new one for.
        self._colors = {key: nw_settings.get_color(key)
                        for key in (PREF_FRONT_SHEET_COLOR, PREF_BACK_SHEET_COLOR)}
        self._picked_colors = set()
        self.color_buttons = {
            PREF_FRONT_SHEET_COLOR: self._make_color_button(PREF_FRONT_SHEET_COLOR, _FRONT_COLOR_TIP),
            PREF_BACK_SHEET_COLOR: self._make_color_button(PREF_BACK_SHEET_COLOR, _BACK_COLOR_TIP),
        }
        self._dxf_initial = {key: nw_settings.get_dxf_color(key)
                             for key in (PREF_DXF_PART_COLOR, PREF_DXF_LABEL_COLOR,
                                         PREF_DXF_BOUND_COLOR, PREF_DXF_SHEET_COLOR)}
        self.dxf_combos = {
            PREF_DXF_PART_COLOR: self._make_dxf_combo(PREF_DXF_PART_COLOR, _DXF_PART_TIP),
            PREF_DXF_LABEL_COLOR: self._make_dxf_combo(PREF_DXF_LABEL_COLOR, _DXF_LABEL_TIP),
            PREF_DXF_BOUND_COLOR: self._make_dxf_combo(PREF_DXF_BOUND_COLOR, _DXF_BOUND_TIP),
            PREF_DXF_SHEET_COLOR: self._make_dxf_combo(PREF_DXF_SHEET_COLOR, _DXF_SHEET_TIP),
        }

        self.costs_section = CollapsibleSection(tr(_CTX, _SECTION_COSTS))
        self.costs_section.addRow(tr(_CTX, _CURRENCY_LABEL), currency_row)
        self.runs_section = CollapsibleSection(tr(_CTX, _SECTION_RUNS))
        self.runs_section.addRow(self.sound_check)
        self.colors_section = CollapsibleSection(tr(_CTX, _SECTION_COLORS))
        self.colors_section.addRow(tr(_CTX, _FRONT_COLOR_LABEL), self.color_buttons[PREF_FRONT_SHEET_COLOR])
        self.colors_section.addRow(tr(_CTX, _BACK_COLOR_LABEL), self.color_buttons[PREF_BACK_SHEET_COLOR])
        self.colors_section.addRow(tr(_CTX, _DXF_PART_LABEL), self.dxf_combos[PREF_DXF_PART_COLOR])
        self.colors_section.addRow(tr(_CTX, _DXF_LABEL_LABEL), self.dxf_combos[PREF_DXF_LABEL_COLOR])
        self.colors_section.addRow(tr(_CTX, _DXF_BOUND_LABEL), self.dxf_combos[PREF_DXF_BOUND_COLOR])
        self.colors_section.addRow(tr(_CTX, _DXF_SHEET_LABEL), self.dxf_combos[PREF_DXF_SHEET_COLOR])
        self.diagnostics_section = CollapsibleSection(tr(_CTX, _SECTION_DIAG))
        self.diagnostics_section.addRow(self.crash_check)
        self.diagnostics_section.addRow(self.debug_check)
        self.diagnostics_section.addRow(self.verbose_check)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.costs_section)
        layout.addWidget(self.runs_section)
        layout.addWidget(self.colors_section)
        layout.addWidget(self.diagnostics_section)
        layout.addStretch()
        layout.addWidget(buttons)

    def _make_color_button(self, key, tip):
        button = QtWidgets.QPushButton()
        button.setMinimumWidth(48)
        button.setToolTip(rich_tooltip(_CTX, tip))
        button.clicked.connect(lambda *_: self._on_pick_color(key))
        self._paint_color_button(button, self._colors[key])
        return button

    @staticmethod
    def _paint_color_button(button, rgb):
        r, g, b = (round(c * 255) for c in rgb)
        button.setStyleSheet(f"background-color: rgb({r}, {g}, {b});")

    def _on_pick_color(self, key):
        """Open the colour picker; a cancelled picker returns an invalid QColor and changes nothing."""
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*self._colors[key]), self)
        if not color.isValid():
            return
        self._colors[key] = (color.redF(), color.greenF(), color.blueF())
        self._picked_colors.add(key)
        self._paint_color_button(self.color_buttons[key], self._colors[key])

    def _make_dxf_combo(self, key, tip):
        tr = QtWidgets.QApplication.translate
        combo = QtWidgets.QComboBox()
        for name in _DXF_COLOR_NAMES:
            combo.addItem(tr(_CTX, name))
        combo.setCurrentIndex(nw_settings.DXF_COLORS.index(self._dxf_initial[key]))
        combo.setToolTip(rich_tooltip(_CTX, tip))
        return combo

    def _on_currency_edited(self, _text):
        self._currency_state = "edited"

    def _on_currency_reset(self):
        # setText doesn't emit textEdited, so the state stays "reset".
        self.currency_input.setText(nw_settings.locale_currency_symbol())
        self._currency_state = "reset"

    def _on_accept(self):
        """Persist every setting, then close."""
        nw_logger.set_enable_crash_log(self.crash_check.isChecked())
        nw_logger.set_enable_debug_log(self.debug_check.isChecked())
        nw_settings.set_verbose_logging(self.verbose_check.isChecked())
        nw_settings.set_play_sound(self.sound_check.isChecked())
        for key in self._picked_colors:
            nw_settings.set_color(key, self._colors[key])
        for key, combo in self.dxf_combos.items():
            aci = nw_settings.DXF_COLORS[combo.currentIndex()]
            if aci != self._dxf_initial[key]:
                nw_settings.set_dxf_color(key, aci)
        try:
            if self._currency_state == "edited":
                nw_settings.set_currency_symbol(self.currency_input.text())
            elif self._currency_state == "reset":
                nw_settings.reset_currency_symbol()
        except Exception as e:
            nw_logger.warn(f"Failed to save currency symbol: {e}")
        self.accept()


class SettingsCommand:
    """Opens the Nesting Workbench settings dialog."""

    def GetResources(self):
        tr = QtWidgets.QApplication.translate
        return {
            'Pixmap': 'preferences-system',
            'MenuText': tr(_CTX, 'Nesting Settings'),
            'ToolTip': rich_tooltip(
                _CTX,
                QT_TRANSLATE_NOOP(_CTX, 'Configure the Nesting Workbench: currency symbol, completion sound, colors and logging.'),
            ),
        }

    def Activated(self):
        SettingsDialog(FreeCADGui.getMainWindow()).exec_()

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_Settings', SettingsCommand())
