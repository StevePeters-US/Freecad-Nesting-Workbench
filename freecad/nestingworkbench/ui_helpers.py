# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/ui_helpers.py

"""
UI helper functions and shared layout constants for the Nesting Workbench.
"""

from PySide import QtCore, QtWidgets

# Zero margins for nested composite layouts
MARGINS_NONE = (0, 0, 0, 0)


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

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)

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


def show_info_dialog(parent, title, text):
    """
    Displays an informational message dialog.

    Args:
        parent (QWidget or None): Parent widget for the dialog.
        title (str): Window title.
        text (str): Main text or HTML body.
    """
    msg_box = QtWidgets.QMessageBox(parent) if parent is not None else QtWidgets.QMessageBox()
    msg_box.setIcon(QtWidgets.QMessageBox.Information)
    msg_box.setWindowTitle(title)
    msg_box.setText(text)
    msg_box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    return msg_box.exec_()


def show_warning_dialog(parent, title, text, informative_text=None):
    """
    Displays a warning message dialog.

    Args:
        parent (QWidget or None): Parent widget for the dialog.
        title (str): Window title.
        text (str): Main warning message.
        informative_text (str, optional): Additional guidance or steps.
    """
    msg_box = QtWidgets.QMessageBox(parent) if parent is not None else QtWidgets.QMessageBox()
    msg_box.setIcon(QtWidgets.QMessageBox.Warning)
    msg_box.setWindowTitle(title)
    msg_box.setText(text)
    if informative_text:
        msg_box.setInformativeText(informative_text)
    msg_box.setStandardButtons(QtWidgets.QMessageBox.Ok)
    return msg_box.exec_()


def closest_angle_index(angles, target_angle):
    """Index of the entry in *angles* nearest to *target_angle*. Ties take the lower index."""
    if not angles:
        return 0
    return min(range(len(angles)), key=lambda i: abs(angles[i] - target_angle))
