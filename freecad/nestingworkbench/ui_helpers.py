# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/ui_helpers.py

"""
UI helper functions and shared layout constants for the Nesting Workbench.
"""

import os
import re

import FreeCADGui
from PySide import QtCore, QtGui, QtWidgets

from freecad.nestingworkbench import nw_logger

TOOLTIP_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "Resources", "tooltips")
_SVG_WIDTH = re.compile(r'<svg\b[^>]*?\swidth="(\d+)"')


class _JumpSlider(QtWidgets.QSlider):
    """A QSlider whose handle jumps to the clicked point on the groove."""

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            style = self.style()
            groove = style.subControlRect(
                QtWidgets.QStyle.CC_Slider, self._option(), QtWidgets.QStyle.SC_SliderGroove, self)
            handle = style.subControlRect(
                QtWidgets.QStyle.CC_Slider, self._option(), QtWidgets.QStyle.SC_SliderHandle, self)
            if not handle.contains(event.pos()):
                span = groove.width() - handle.width()
                pos = event.pos().x() - groove.x() - handle.width() // 2
                self.setValue(QtWidgets.QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(), pos, span))
        super().mousePressEvent(event)

    def _option(self):
        option = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(option)
        return option


def make_slider(minimum, maximum, value, tooltip=None, ticks=False):
    """Create a horizontal slider that is easy to grab.

    The slider is at least 24 px tall, so the handle is easy to hit, and a click on the
    groove jumps the handle there. A drag on the handle still works.

    Args:
        minimum, maximum, value: the integer range and starting value.
        tooltip: an already-built `rich_tooltip` string, or None.
        ticks: True draws a tick under every step.
    """
    slider = _JumpSlider(QtCore.Qt.Horizontal)
    slider.setRange(minimum, maximum)
    slider.setValue(value)
    slider.setMinimumHeight(24)
    slider.setStyleSheet(
        "QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: palette(mid); }"
        "QSlider::handle:horizontal { width: 16px; margin: -7px 0; border-radius: 8px;"
        " background: palette(highlight); }")
    if ticks:
        slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        slider.setTickInterval(1)
        slider.setPageStep(1)
    if tooltip:
        slider.setToolTip(tooltip)
    return slider


def rich_tooltip(context, text, image=None):
    """Translate a tooltip source string and return it as rich text.

    Args:
        context: translation context, the same one given to QT_TRANSLATE_NOOP.
        text: the QT_TRANSLATE_NOOP-marked source. Each "\n" starts a new line.
        image: optional diagram file name in Resources/tooltips/.

    Returns:
        HTML with one <p> per line and the diagram last. Qt word-wraps it. With a
        diagram, the tooltip is exactly the diagram's width and the text wraps to it.
    """
    translated = QtWidgets.QApplication.translate(context, text)
    parts = ["<p style='margin:0 0 4px 0'>%s</p>" % line for line in translated.split("\n") if line]
    if not image:
        return "".join(parts)
    path = os.path.normpath(os.path.join(TOOLTIP_DIR, image))
    if not os.path.isfile(path):
        # Qt draws a broken-image box for a missing file and says nothing.
        nw_logger.warn(f"[ui_helpers] Tooltip image missing: {path}")
        return "".join(parts)
    parts.append("<p style='margin:4px 0 0 0'><img src='%s'></p>" % path.replace("\\", "/"))
    with open(path, encoding="utf-8") as f:
        match = _SVG_WIDTH.search(f.read())
    if match is None:
        nw_logger.warn(f"[ui_helpers] Tooltip image has no width attribute: {path}")
        return "".join(parts)
    # Qt sizes a rich tooltip from its text, not its image, so a diagram sits in a wide
    # popup. A fixed-width table makes the popup the diagram's width.
    return "<table width='%s' cellspacing='0' cellpadding='0'><tr><td>%s</td></tr></table>" % (
        match.group(1), "".join(parts))


def set_status_tips(command_ids):
    """Give each command with a rich ToolTip a plain status-bar tip: its first line.

    FreeCAD copies ToolTip into the status bar and shows HTML there as raw tags, and it
    ignores a Python command's StatusTip key. Call from Workbench.Activated(), after the
    toolbars exist.
    """
    for cmd_id in command_ids:
        command = FreeCADGui.Command.get(cmd_id)
        tip = command.getInfo()["toolTip"]
        if not tip.startswith(("<p", "<table")):
            continue
        document = QtGui.QTextDocument()
        document.setHtml(tip)
        # A table starts the plain text with an empty line.
        first_line = next(line for line in document.toPlainText().split("\n") if line.strip())
        for action in command.getAction():
            action.setStatusTip(first_line)


# Zero margins for nested composite layouts
MARGINS_NONE = (0, 0, 0, 0)

DIRECTION_DETENT_STEPS = (0.0, 5.0, 15.0, 30.0, 45.0)  # degrees; slider stops (0.0 = Off)
DIRECTION_DETENT_DEFAULT = 15.0
_DIAL_UNITS_PER_DEGREE = 2                                   # dial ints are half-degrees
_DIAL_MAX_UNITS = 360 * _DIAL_UNITS_PER_DEGREE - 1           # 719
_DRAG_UNITS_WHEN_OFF = _DIAL_UNITS_PER_DEGREE                # Off drags in whole degrees
_DIAL_ZERO_OFFSET_DEG = 270                                  # the QDial draws its value 0 at Down; maths angle 0 is Right


def make_double_spinbox(value, minimum, maximum, step=None, decimals=None, suffix="", tooltip=None):
    """Factory creating a configured QDoubleSpinBox."""
    spin = QtWidgets.QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    if step is not None:
        spin.setSingleStep(step)
    if decimals is not None:
        spin.setDecimals(decimals)
    if suffix:
        spin.setSuffix(suffix)
    if tooltip:
        spin.setToolTip(tooltip)
    return spin


LENGTH_DECIMALS = 3  # owner standard: every displayed length, in whatever unit is shown


def _user_length_unit(mm, unit=None):
    """Return (unit string, mm per unit) that the current FreeCAD schema shows for this length.

    `unit` is a FreeCAD unit (None means Length); `mm` is then in that unit's base (mm or mm²).
    """
    import FreeCAD
    if unit is None:
        unit = FreeCAD.Units.Length
    _text, factor, shown = FreeCAD.Units.Quantity(float(mm), unit).getUserPreferred()
    return shown, float(factor)


def length_to_text(mm, decimals=LENGTH_DECIMALS, unit=None):
    """Format a length in mm (or an area in mm² with `unit` = Area) in the user's unit system.

    `decimals` places are shown in the shown unit. `unit` None means FreeCAD.Units.Length.
    """
    import FreeCAD
    if unit is None:
        unit = FreeCAD.Units.Length
    quantity = FreeCAD.Units.Quantity(float(mm), unit)
    fmt = quantity.Format
    fmt["Precision"] = int(decimals)
    quantity.Format = fmt  # Format is a copy (probe P8); the write-back is what applies it
    return quantity.UserString


def text_to_length(text, current_mm, unit=None):
    """Parse typed text to mm (or mm² for `unit` = Area). A bare number is read in the unit the box
    currently shows. `unit` None means FreeCAD.Units.Length.

    Raises ValueError for text of the wrong kind (e.g. "3 kg", or "3 mm" for an area) or that doesn't parse.
    """
    import FreeCAD
    if unit is None:
        unit = FreeCAD.Units.Length
    kind = "an area" if unit == FreeCAD.Units.Area else "a length"
    quantity = FreeCAD.Units.Quantity(text.strip())
    if quantity.Unit == FreeCAD.Units.Unit():
        _shown, factor = _user_length_unit(current_mm, unit)
        return quantity.Value * factor
    if quantity.Unit != unit:
        raise ValueError(f"not {kind}: {text!r}")
    return quantity.Value


class LengthSpinBox(QtWidgets.QDoubleSpinBox):
    """QDoubleSpinBox holding mm that shows and accepts the user's FreeCAD unit system.

    value()/setValue()/valueChanged stay plain floats in mm, so callers treat it exactly
    like a QDoubleSpinBox. The display refreshes on the next value change, not when the
    unit preference changes while the box is open.
    """

    _INTERNAL_DECIMALS = 6  # mm resolution kept by the box; display places are separate
    _UNIT = "Length"  # attribute name on FreeCAD.Units for the quantity this box holds
    _FALLBACK = "mm"  # suffix shown when FreeCAD.Units is unusable (headless)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._display_decimals = LENGTH_DECIMALS
        self.setDecimals(self._INTERNAL_DECIMALS)

    def setDisplayDecimals(self, decimals):
        self._display_decimals = int(decimals)

    def _unit(self):
        """The FreeCAD unit this box holds. Needs FreeCAD; callers catch its absence."""
        import FreeCAD
        return getattr(FreeCAD.Units, self._UNIT)

    def textFromValue(self, mm):
        try:
            return length_to_text(mm, self._display_decimals, unit=self._unit())
        except Exception:
            # FreeCAD.Units unusable (headless): plain base units are still a correct display.
            return f"{mm:.{self._display_decimals}f} {self._FALLBACK}"

    def valueFromText(self, text):
        # Re-reading the box's own rounded display must not move the value
        # (2440 mm shows as "96.063 in", which parses back to 2440.0002).
        if text == self.textFromValue(self.value()):
            return self.value()
        try:
            return text_to_length(text, self.value(), unit=self._unit())
        except Exception:
            try:
                return float(text.replace(self._FALLBACK, "").strip())
            except ValueError:
                return self.value()

    def validate(self, text, pos):
        try:
            mm = text_to_length(text, self.value(), unit=self._unit())
        except Exception:
            return (QtGui.QValidator.Intermediate, text, pos)
        if self.minimum() <= mm <= self.maximum():
            return (QtGui.QValidator.Acceptable, text, pos)
        return (QtGui.QValidator.Intermediate, text, pos)


class AreaSpinBox(LengthSpinBox):
    """QDoubleSpinBox holding mm² that shows and accepts the user's FreeCAD area units.

    Same contract as LengthSpinBox: value() is a plain float in mm², and a bare number is read
    in the shown unit.
    """

    _UNIT = "Area"
    _FALLBACK = "mm²"


def make_quantity_spinbox(value, minimum, maximum, step=None, decimals=None, tooltip=None):
    """Factory creating a LengthSpinBox: a length in mm shown in the user's unit system."""
    spin = LengthSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    if step is not None:
        spin.setSingleStep(step)
    if decimals is not None:
        spin.setDisplayDecimals(decimals)
    if tooltip:
        spin.setToolTip(tooltip)
    return spin


def make_area_spinbox(value, minimum, maximum, step=None, decimals=None, tooltip=None):
    """Factory creating an AreaSpinBox: an area in mm² shown in the user's unit system."""
    spin = AreaSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    if step is not None:
        spin.setSingleStep(step)
    if decimals is not None:
        spin.setDisplayDecimals(decimals)
    if tooltip:
        spin.setToolTip(tooltip)
    return spin


def format_length(mm):
    """Format a length given in mm for display in the user's FreeCAD unit system."""
    try:
        return length_to_text(mm, LENGTH_DECIMALS)
    except Exception:
        # FreeCAD.Units unusable (headless): plain mm is still a correct display.
        return f"{float(mm):.{LENGTH_DECIMALS}f} mm"


def make_int_spinbox(value, minimum, maximum, step=None, tooltip=None):
    """Factory creating a configured QSpinBox."""
    spin = QtWidgets.QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    if step is not None:
        spin.setSingleStep(step)
    if tooltip:
        spin.setToolTip(tooltip)
    return spin


def make_checkbox(text, checked=False, tooltip=None):
    """Factory creating a configured QCheckBox."""
    box = QtWidgets.QCheckBox(text)
    box.setChecked(checked)
    if tooltip:
        box.setToolTip(tooltip)
    return box


class LinkedSliderSpinBox(QtWidgets.QWidget):
    """A QSlider and QSpinBox two-way bound to the same integer value."""

    valueChanged = QtCore.Signal(int)

    def __init__(self, value, minimum, maximum, tooltip=None, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(*MARGINS_NONE)

        self.slider = make_slider(minimum, maximum, value, tooltip=tooltip)

        self.spinbox = QtWidgets.QSpinBox()
        self.spinbox.setRange(minimum, maximum)
        self.spinbox.setValue(value)
        if tooltip:
            self.spinbox.setToolTip(tooltip)

        layout.addWidget(self.slider)
        layout.addWidget(self.spinbox)

        self.slider.valueChanged.connect(self.spinbox.setValue)
        self.spinbox.valueChanged.connect(self.slider.setValue)
        self.slider.valueChanged.connect(self.valueChanged.emit)

    def value(self):
        return self.spinbox.value()

    def setValue(self, v):
        self.spinbox.setValue(v)


class LinkedSliderDoubleSpinBox(QtWidgets.QWidget):
    """A QSlider and QDoubleSpinBox two-way bound to the same float value.

    The slider counts in steps of 10**-decimals: 0.0-10.0 at one decimal is 0-100.
    valueChanged is emitted from the spinbox only, once per change, whichever was moved.
    """

    valueChanged = QtCore.Signal(float)

    def __init__(self, value, minimum, maximum, decimals=1, tooltip=None, parent=None):
        super().__init__(parent)
        self._scale = 10 ** decimals
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(*MARGINS_NONE)

        self.slider = make_slider(round(minimum * self._scale), round(maximum * self._scale),
                                  round(value * self._scale))
        self.spinbox = make_double_spinbox(value, minimum, maximum, step=1.0 / self._scale,
                                           decimals=decimals, tooltip=tooltip)
        if tooltip:
            self.slider.setToolTip(tooltip)

        layout.addWidget(self.slider)
        layout.addWidget(self.spinbox)

        self.slider.valueChanged.connect(self._on_slider)
        self.spinbox.valueChanged.connect(self._on_spinbox)

    def _on_slider(self, units):
        # The spinbox echoes back through _on_spinbox; equal values stop the loop.
        if round(self.spinbox.value() * self._scale) != units:
            self.spinbox.setValue(units / self._scale)

    def _on_spinbox(self, value):
        units = round(value * self._scale)
        if self.slider.value() != units:
            self.slider.setValue(units)
        self.valueChanged.emit(value)

    def value(self):
        return self.spinbox.value()

    def setValue(self, v):
        self.spinbox.setValue(v)


class WeightSliders(QtWidgets.QWidget):
    """Percent rows that always sum to 100, each with a lock toggle (NLA ruling 25).

    Moving one row spreads the difference over the unlocked other rows in proportion to their
    values (evenly when they are all 0), rounded by largest remainder so the sum stays exactly
    100. A locked row keeps its value and its slider is disabled; a row can't rise above 100
    minus the locked rows, and with no unlocked row to give way it can't move at all.
    valuesChanged(dict) is emitted once per change, after every row is updated.
    """

    valuesChanged = QtCore.Signal(dict)

    def __init__(self, rows, parent=None):
        """rows: [(key, label, value, tooltip)] in display order; values should sum to 100."""
        super().__init__(parent)
        self._order = [key for key, *_ in rows]
        self._values = {key: int(round(value)) for key, _l, value, _t in rows}
        self._locked = {key: False for key in self._order}
        self._updating = False
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(*MARGINS_NONE)
        self._sliders, self._locks = {}, {}
        for r, (key, label, value, tooltip) in enumerate(rows):
            slider = LinkedSliderDoubleSpinBox(value, 0.0, 100.0, decimals=0, tooltip=tooltip)
            slider.spinbox.setSuffix("%")
            lock = QtWidgets.QToolButton()
            lock.setCheckable(True)
            lock.setText("🔓")
            lock.setToolTip(rich_tooltip(
                "NestingPanel",
                QT_TRANSLATE_NOOP("NestingPanel", "Locks this weight so moving the others leaves it unchanged.")))
            lock.toggled.connect(lambda on, k=key: self.setLocked(k, on))
            slider.valueChanged.connect(lambda v, k=key: self._on_row(k, v))
            grid.addWidget(QtWidgets.QLabel(label), r, 0)
            grid.addWidget(slider, r, 1)
            grid.addWidget(lock, r, 2)
            self._sliders[key], self._locks[key] = slider, lock

    def values(self):
        return dict(self._values)

    def setValues(self, values):
        """Sets every row. Values that don't sum to 100 are scaled to 100 (all zero: the first
        row takes 100). Locks are kept. Emits valuesChanged."""
        raw = [max(0.0, float(values.get(k, 0.0))) for k in self._order]
        total = sum(raw)
        shares = [100.0 * v / total for v in raw] if total > 0 else [100.0] + [0.0] * (len(raw) - 1)
        for k, v in zip(self._order, _largest_remainder(shares, 100)):
            self._values[k] = v
        self._push()

    def isLocked(self, key):
        return self._locked[key]

    def setLocked(self, key, locked):
        self._locked[key] = bool(locked)
        lock = self._locks[key]
        if lock.isChecked() != bool(locked):
            lock.setChecked(bool(locked))
        lock.setText("🔒" if locked else "🔓")
        self._sliders[key].setEnabled(not locked)

    def _on_row(self, key, value):
        if self._updating:
            return
        others = [k for k in self._order if k != key]
        free = [k for k in others if not self._locked[k]]
        cap = 100 - sum(self._values[k] for k in others if self._locked[k])
        new = self._values[key] if not free else max(0, min(int(round(value)), cap))
        current = [self._values[k] for k in free]
        rest = cap - new
        total = sum(current)
        shares = ([rest * c / total for c in current] if total > 0
                  else [rest / len(free)] * len(free)) if free else []
        self._values[key] = new
        for k, v in zip(free, _largest_remainder(shares, rest)):
            self._values[k] = v
        self._push()

    def _push(self):
        self._updating = True
        try:
            for k in self._order:
                self._sliders[k].setValue(self._values[k])
        finally:
            self._updating = False
        self.valuesChanged.emit(self.values())


def _largest_remainder(shares, total):
    """Whole numbers summing to total, each the floor of its share plus one for the largest
    remainders (ties to the earlier row)."""
    floors = [int(s) for s in shares]
    order = sorted(range(len(shares)), key=lambda i: (-(shares[i] - floors[i]), i))
    for i in order[:max(0, total - sum(floors))]:
        floors[i] += 1
    return floors


class DirectionDial(QtWidgets.QWidget):
    """A direction QDial that snaps to a user-chosen step ("detent").

    The detent steps are Off, 5, 15, 30 and 45 degrees (Off drags in whole degrees). The
    dial's integer unit is half a degree, so every step is a whole number of units and the
    manual angle field can hold half-degree values. Angles are maths angles: 0 = Right,
    counter-clockwise (90 = Up, 180 = Left, 270 = Down). degrees() / setDegrees() are the
    only API callers should use.
    """

    degreesChanged = QtCore.Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._step_units = int(DIRECTION_DETENT_DEFAULT * _DIAL_UNITS_PER_DEGREE)
        self._setting_degrees = False

        self.dial = QtWidgets.QDial()
        self.dial.setRange(0, _DIAL_MAX_UNITS)
        self.dial.setWrapping(True)
        self.dial.setNotchesVisible(True)
        self.dial.setToolTip(rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the target direction; snaps to the Snap step.")))

        self.angle_input = make_double_spinbox(
            0.0, 0.0, 359.5, step=1.0, decimals=1, suffix="°",
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP(
                "NestingPanel", "Sets the exact direction in ° (0° = Right, 90° = Up, 180° = Left, 270° = Down).")))
        self.angle_input.setWrapping(True)
        self.angle_input.setKeyboardTracking(False)

        self.step_slider = make_slider(
            0, len(DIRECTION_DETENT_STEPS) - 1, 0, ticks=True,
            tooltip=rich_tooltip("NestingPanel", QT_TRANSLATE_NOOP("NestingPanel", "Sets the Detent step: Off, 5°, 15°, 30° or 45°.\n<b>Default:</b> 15°.")))
        self.step_label = QtWidgets.QLabel()
        self.step_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.step_label.setFixedWidth(self.step_label.fontMetrics().horizontalAdvance("45°") + 6)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(*MARGINS_NONE)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", "Angle:")), 0, 0)
        grid.addWidget(self.angle_input, 0, 1, 1, 2)          # spans the slider + value columns
        grid.addWidget(QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", "Snap:")), 1, 0)
        grid.addWidget(self.step_slider, 1, 1)
        grid.addWidget(self.step_label, 1, 2)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(*MARGINS_NONE)
        layout.addWidget(self.dial, 0, QtCore.Qt.AlignHCenter)
        layout.addLayout(grid)

        self.dial.valueChanged.connect(self._on_dial_changed)
        self.step_slider.valueChanged.connect(self._on_step_slider_changed)
        self.angle_input.valueChanged.connect(self._on_angle_input_changed)
        self.setDetentStep(DIRECTION_DETENT_DEFAULT)
        self.angle_input.blockSignals(True)
        self.angle_input.setValue(self.degrees())
        self.angle_input.blockSignals(False)

    def degrees(self):
        """The direction as a maths angle: 0 = Right, counter-clockwise, 90 = Up."""
        raw_deg = self.dial.value() / _DIAL_UNITS_PER_DEGREE
        deg = (_DIAL_ZERO_OFFSET_DEG - raw_deg) % 360
        return 0.0 if deg == 0.0 else deg

    def setDegrees(self, degrees):
        """Set the direction exactly (no snapping: a restored value must round-trip)."""
        raw_deg = (_DIAL_ZERO_OFFSET_DEG - float(degrees)) % 360
        units = int(round(raw_deg * _DIAL_UNITS_PER_DEGREE)) % (_DIAL_MAX_UNITS + 1)
        old_val = self.dial.value()
        self._setting_degrees = True
        try:
            self.dial.setValue(units)
        finally:
            self._setting_degrees = False
        if old_val == units:
            self.angle_input.blockSignals(True)
            self.angle_input.setValue(self.degrees())
            self.angle_input.blockSignals(False)

    def detentStep(self):
        return float(DIRECTION_DETENT_STEPS[self.step_slider.value()])

    def setDetentStep(self, degrees):
        """Set the snap step to the nearest allowed stop."""
        index = min(range(len(DIRECTION_DETENT_STEPS)),
                    key=lambda i: abs(DIRECTION_DETENT_STEPS[i] - float(degrees)))
        if self.step_slider.value() != index:
            self.step_slider.setValue(index)
        self._on_step_slider_changed(index)

    def setDisabled(self, disabled):
        super().setDisabled(disabled)
        self.dial.setDisabled(disabled)
        self.step_slider.setDisabled(disabled)
        self.angle_input.setDisabled(disabled)

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        self.dial.setEnabled(enabled)
        self.step_slider.setEnabled(enabled)
        self.angle_input.setEnabled(enabled)

    def _on_step_slider_changed(self, index):
        step = DIRECTION_DETENT_STEPS[index]
        self._step_units = _DRAG_UNITS_WHEN_OFF if step == 0.0 else int(round(step * _DIAL_UNITS_PER_DEGREE))
        self.dial.setSingleStep(self._step_units)
        self.dial.setPageStep(self._step_units)
        self.dial.setNotchTarget(max(self._step_units, 1))
        if step == 0.0:
            self.step_label.setText(QT_TRANSLATE_NOOP("NestingPanel", "Off"))
        else:
            self.step_label.setText(f"{step:g}°")

    def _on_angle_input_changed(self, value):
        if self._setting_degrees:
            return
        self.setDegrees(value)

    def _on_dial_changed(self, units):
        # Snapping stays in raw units: 270 and 360 are both multiples of 5, 15, 30 and 45,
        # so a snapped raw angle is a snapped maths angle without conversion before snapping.
        if not self._setting_degrees:
            # Snap to the nearest multiple of the step; wrap 360° back to 0°.
            snapped = int(round(units / self._step_units)) * self._step_units
            snapped %= (_DIAL_MAX_UNITS + 1)
            if snapped != units:
                self.dial.setValue(snapped)   # re-enters this handler once, then is a no-op
                return
        self.angle_input.blockSignals(True)
        self.angle_input.setValue(self.degrees())
        self.angle_input.blockSignals(False)
        self.degreesChanged.emit(self.degrees())


class CollapsibleSection(QtWidgets.QWidget):
    """A collapsible section widget with a styled toggle button header and QFormLayout content area."""

    def __init__(self, title="", expanded=True, parent=None):
        super().__init__(parent)

        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 8)
        self.main_layout.setSpacing(0)

        self.toggle_button = QtWidgets.QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        self.toggle_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)

        self.toggle_button.setStyleSheet("""
            QToolButton {
                background-color: rgba(128, 128, 128, 40);
                border: 1px solid palette(mid);
                border-radius: 4px;
                font-weight: bold;
                padding: 6px;
                text-align: left;
            }
            QToolButton:hover {
                background-color: palette(highlight);
                color: palette(highlighted-text);
                border-color: palette(highlight);
            }
            QToolButton:checked {
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                background-color: rgba(128, 128, 128, 60);
            }
        """)

        # Parent it now: setVisible(True) on a parentless widget makes it a
        # top-level window, which flashes on screen until a layout reparents it.
        self.content_area = QtWidgets.QWidget(self)
        self.content_area.setObjectName("content_area")
        self.content_area.setStyleSheet("""
            QWidget#content_area {
                background-color: transparent;
                border: 1px solid palette(mid);
                border-top: none;
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
            }
        """)
        self.content_area.setVisible(expanded)

        self.content_layout = QtWidgets.QFormLayout(self.content_area)
        self.content_layout.setContentsMargins(12, 10, 12, 10)
        self.content_layout.setSpacing(8)

        self.main_layout.addWidget(self.toggle_button)
        self.main_layout.addWidget(self.content_area)

        self.toggle_button.clicked.connect(self.toggle)

    def toggle(self, checked=None):
        if checked is None:
            checked = self.toggle_button.isChecked()
        if checked:
            self.toggle_button.setArrowType(QtCore.Qt.DownArrow)
            self.content_area.setVisible(True)
        else:
            self.toggle_button.setArrowType(QtCore.Qt.RightArrow)
            self.content_area.setVisible(False)

    def setExpanded(self, expanded):
        self.toggle_button.setChecked(expanded)
        self.toggle(expanded)

    def addRow(self, label, widget=None):
        if widget is not None:
            self.content_layout.addRow(label, widget)
        else:
            self.content_layout.addRow(label)


def QT_TRANSLATE_NOOP(context, text):
    """
    Marks a string literal for translation extraction tools (lupdate-style).

    This is a true no-op: it always returns the original source text unchanged.
    Actual translation happens later, at display time, via FreeCAD's own Qt
    translation machinery (the .qm catalog loaded through addLanguagePath).
    Translating eagerly here would bake in whatever language was active at
    call time and break code that compares widget text against the English
    source literal (e.g. currentText() == "Physics").
    """
    return text




def show_warning_dialog(parent, title, text, informative_text=None, buttons=None):
    """
    Displays a warning message dialog.

    Args:
        parent (QWidget or None): Parent widget for the dialog.
        title (str): Window title.
        text (str): Main warning message.
        informative_text (str, optional): Additional guidance or steps.
        buttons (QMessageBox.StandardButtons, optional): Buttons to show. Defaults to Ok.
    """
    msg_box = QtWidgets.QMessageBox(parent) if parent is not None else QtWidgets.QMessageBox()
    msg_box.setIcon(QtWidgets.QMessageBox.Warning)
    msg_box.setWindowTitle(title)
    msg_box.setText(text)
    if informative_text:
        msg_box.setInformativeText(informative_text)
    if buttons is not None:
        msg_box.setStandardButtons(buttons)
    else:
        msg_box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    return msg_box.exec_()


def closest_angle_index(angles, target_angle):
    """Index of the entry in *angles* nearest to *target_angle*. Ties take the lower index."""
    if not angles:
        return 0
    return min(range(len(angles)), key=lambda i: abs(angles[i] - target_angle))
