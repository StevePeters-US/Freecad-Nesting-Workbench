# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sheets tab of the Library window and the sheet edit dialog."""

import copy
import os
from typing import Any, Dict, List, Optional
import uuid

from PySide import QtCore, QtGui, QtWidgets

from ... import ICONS_DIR
from ...sheet_library import model, store
from ...sheet_library.model import Library
from ...ui_helpers import (
    MARGINS_NONE,
    QT_TRANSLATE_NOOP,
    make_checkbox,
    make_double_spinbox,
    make_quantity_spinbox,
    format_length,
    rich_tooltip,
    show_warning_dialog,
)

translate = QtWidgets.QApplication.translate

BANNER_STYLE = (
    "background-color: #fef3c7; color: #92400e; border: 1px solid #f59e0b; "
    "padding: 6px; border-radius: 4px;"
)


class SheetEditDialog(QtWidgets.QDialog):
    """Create or edit one library sheet."""

    # Form order; the repair message lists fields in this order too.
    _FIELD_ORDER = ("name", "material", "width", "height", "thickness", "cost", "grain_angle", "flip", "notes")

    def __init__(self, sheet: Dict[str, Any], library: Library, materials: List[str],
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._sheet = copy.deepcopy(sheet)
        self._library = library
        self._materials = list(materials)
        pid = self._sheet.get("parent_id")
        self._parent_id = pid if isinstance(pid, str) else None
        self._is_variant = self._parent_id is not None
        self._parent = library.sheets.get(self._parent_id) if self._is_variant else None
        self._inherited = {}
        if self._parent is not None:
            effective, _error = model.resolve_sheet(self._parent, library)
            self._inherited = effective or {}

        if self._is_variant:
            self.setWindowTitle(translate("SheetsTab", "Edit Variant"))
        else:
            self.setWindowTitle(translate("SheetsTab", "Edit Sheet"))

        self._inherited_keys = set()
        self._prepare_display_copy()
        self._build_ui()
        self._validate()

    def _prepare_display_copy(self):
        default = model.new_sheet(self._sheet.get("id", ""))
        self._display = {}
        repaired_keys = []
        self._inherited_keys = set()

        for key in self._FIELD_ORDER:
            if self._is_variant and key in model.INHERITABLE_FIELDS:
                if key not in self._sheet:
                    # Key absent: inherited
                    self._inherited_keys.add(key)
                    val = self._inherited.get(key)
                    if key == "material":
                        valid = isinstance(val, str)
                    elif key in ("width", "height", "thickness", "cost"):
                        valid = model._is_number(val)
                    elif key == "grain_angle":
                        valid = (val is None) or model._is_number(val)
                    elif key == "flip":
                        valid = val in model.FLIP_CHOICES
                    else:
                        valid = False
                    self._display[key] = val if valid else default[key]
                    # Not a repair
                    continue
                else:
                    # Key present on variant: overridden
                    val = self._sheet[key]
                    if key == "material":
                        valid = isinstance(val, str)
                    elif key in ("width", "height", "thickness", "cost"):
                        valid = model._is_number(val)
                    elif key == "grain_angle":
                        valid = (val is None) or model._is_number(val)
                    elif key == "flip":
                        valid = val in model.FLIP_CHOICES
                    else:
                        valid = False

                    if valid:
                        self._display[key] = self._sheet[key]
                    else:
                        self._display[key] = default[key]
                        repaired_keys.append(key)
                    continue

            # Root or non-inheritable (name, notes)
            if key not in self._sheet:
                valid = False
            else:
                val = self._sheet[key]
                if key in ("name", "material", "notes"):
                    valid = isinstance(val, str)
                elif key in ("width", "height", "thickness", "cost"):
                    valid = model._is_number(val)
                elif key == "grain_angle":
                    valid = (val is None) or model._is_number(val)
                elif key == "flip":
                    valid = val in model.FLIP_CHOICES
                else:
                    valid = False

            if valid:
                self._display[key] = self._sheet[key]
            else:
                self._display[key] = default[key]
                repaired_keys.append(key)

        self._repaired_keys = repaired_keys

    def _row_overridden(self, key: str) -> bool:
        if not self._is_variant:
            return True
        cb = self.override_cbs.get(key)
        return cb.isChecked() if cb is not None else True

    def _set_row_enabled(self, key: str, enabled: bool):
        if key == "material":
            self.material_combo.setEnabled(enabled)
        elif key == "width":
            self.width_spin.setEnabled(enabled)
        elif key == "height":
            self.height_spin.setEnabled(enabled)
        elif key == "thickness":
            self.thickness_spin.setEnabled(enabled)
        elif key == "cost":
            self.price_spin.setEnabled(enabled)
        elif key == "grain_angle":
            self.no_grain_cb.setEnabled(enabled)
            self.grain_spin.setEnabled(enabled and not self.no_grain_cb.isChecked())
        elif key == "flip":
            self.flip_combo.setEnabled(enabled)

    def _on_override_toggled(self, key: str, checked: bool):
        if not checked:
            # Unticked: reset row editor(s) to inherited display value, then disable
            default = model.new_sheet(self._sheet.get("id", ""))
            val = self._inherited.get(key)
            if key == "material":
                valid = isinstance(val, str)
                target = val if valid else default[key]
                idx = self.material_combo.findData(target)
                if idx < 0 and target:
                    self.material_combo.addItem(target, target)
                    idx = self.material_combo.findData(target)
                self.material_combo.setCurrentIndex(max(0, idx))
            elif key == "width":
                valid = model._is_number(val)
                self.width_spin.setValue(float(val if valid else default[key]))
            elif key == "height":
                valid = model._is_number(val)
                self.height_spin.setValue(float(val if valid else default[key]))
            elif key == "thickness":
                valid = model._is_number(val)
                self.thickness_spin.setValue(float(val if valid else default[key]))
            elif key == "cost":
                valid = model._is_number(val)
                self.price_spin.setValue(float(val if valid else default[key]))
            elif key == "grain_angle":
                valid = (val is None) or model._is_number(val)
                grain = val if valid else default[key]
                self.no_grain_cb.setChecked(grain is None)
                self.grain_spin.setValue(float(grain) if grain is not None else 0.0)
            elif key == "flip":
                valid = val in model.FLIP_CHOICES
                target = val if valid else default[key]
                self.flip_combo.setCurrentIndex(max(0, self.flip_combo.findData(target)))
            self._set_row_enabled(key, False)
        else:
            self._set_row_enabled(key, True)
        self._validate()

    def _build_ui(self):
        sheet = self._display
        main_layout = QtWidgets.QVBoxLayout(self)

        if self._is_variant:
            header_label = QtWidgets.QLabel()
            header_label.setWordWrap(True)
            if self._parent is not None:
                parent_name = self._parent.get("name", "")
                header_label.setText(translate(
                    "SheetsTab",
                    "Variant of '%1'. Tick Override to change a value here; the rest follow '%1'."
                ).replace("%1", parent_name))
            else:
                header_label.setText(translate("SheetsTab", "Variant of a sheet that is no longer in the library."))
            main_layout.addWidget(header_label)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight)

        self.override_cbs: Dict[str, QtWidgets.QCheckBox] = {}

        def wrap_row(key: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
            if not self._is_variant:
                return widget
            row_widget = QtWidgets.QWidget()
            layout = QtWidgets.QHBoxLayout(row_widget)
            layout.setContentsMargins(*MARGINS_NONE)
            layout.addWidget(widget)
            cb = make_checkbox(
                translate("SheetsTab", "Override"),
                checked=(key not in self._inherited_keys),
                tooltip=rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
                    "SheetsTab", "Gives this variant its own value instead of following the parent sheet."))
            )
            self.override_cbs[key] = cb
            cb.toggled.connect(lambda chk, k=key: self._on_override_toggled(k, chk))
            layout.addWidget(cb)
            return row_widget

        self.name_edit = QtWidgets.QLineEdit(sheet["name"])
        self.name_edit.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab", "Sets the name of this sheet as shown in the panel's Sheet list.")))
        self.name_edit.textChanged.connect(self._validate)
        form.addRow(translate("SheetsTab", "Name:"), self.name_edit)

        stored = sheet["material"].strip()
        listed = model.find_material(self._materials, stored)
        self.material_combo = QtWidgets.QComboBox()
        self.material_combo.addItem(translate("SheetsTab", "(none)"), "")
        for name in self._materials:
            self.material_combo.addItem(name, name)
        self.material_note = QtWidgets.QLabel()
        self.material_note.setWordWrap(True)
        if stored and listed is None:
            # Not in the list: offer the stored name as is, so saving never drops it.
            self.material_combo.addItem(stored, stored)
            listed = stored
            # Only show note if key is set by record itself (root or overridden variant)
            if not self._is_variant or "material" in self._sheet:
                self.material_note.setText(translate(
                    "SheetsTab",
                    "'%1' is not in the materials list. Add it on the Materials tab, or pick another material."
                ).replace("%1", stored))
        self.material_note.setVisible(bool(self.material_note.text()))
        self.material_combo.setCurrentIndex(self.material_combo.findData(listed or ""))
        self.material_combo.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab",
            "Selects the material this sheet is made of.\nAdd, rename or delete materials on the Materials tab.")))
        self.material_combo.currentIndexChanged.connect(self._validate)
        form.addRow(translate("SheetsTab", "Material:"), wrap_row("material", self.material_combo))

        self.width_spin = make_quantity_spinbox(
            sheet["width"], 1.0, 100000.0,
            tooltip=rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP("SheetsTab", "Sets the sheet width (X).")))
        self.width_spin.valueChanged.connect(self._validate)
        form.addRow(translate("SheetsTab", "Width:"), wrap_row("width", self.width_spin))

        self.height_spin = make_quantity_spinbox(
            sheet["height"], 1.0, 100000.0,
            tooltip=rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP("SheetsTab", "Sets the sheet height (Y).")))
        self.height_spin.valueChanged.connect(self._validate)
        form.addRow(translate("SheetsTab", "Height:"), wrap_row("height", self.height_spin))

        self.thickness_spin = make_quantity_spinbox(
            sheet["thickness"], 0.1, 1000.0,
            tooltip=rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
                "SheetsTab", "Sets the sheet thickness.")))
        self.thickness_spin.valueChanged.connect(self._validate)
        form.addRow(translate("SheetsTab", "Thickness:"), wrap_row("thickness", self.thickness_spin))

        self.price_spin = make_double_spinbox(
            sheet["cost"], 0.0, 1e7, decimals=2,
            tooltip=rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
                "SheetsTab", "Sets the price paid for one sheet.\n0 means free or unknown.")))
        symbol = store.currency_symbol()
        if symbol:
            self.price_spin.setPrefix(f"{symbol} ")
        self.price_spin.valueChanged.connect(self._validate)
        form.addRow(translate("SheetsTab", "Price:"), wrap_row("cost", self.price_spin))

        grain = sheet["grain_angle"]
        grain_row = QtWidgets.QWidget()
        grain_layout = QtWidgets.QHBoxLayout(grain_row)
        grain_layout.setContentsMargins(*MARGINS_NONE)
        self.grain_spin = make_double_spinbox(
            grain if grain is not None else 0.0, 0.0, 179.9, step=1.0, decimals=1, suffix="°",
            tooltip=rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
                "SheetsTab",
                "Sets the grain angle in ° measured from the sheet's width (X) edge.\n"
                "0° and 180° are the same line; nesting does not use it yet.")))
        self.grain_spin.valueChanged.connect(self._validate)
        self.no_grain_cb = make_checkbox(
            translate("SheetsTab", "No grain"),
            tooltip=rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
                "SheetsTab", "Marks this sheet as having no grain direction, such as MDF or acrylic.")))
        self.no_grain_cb.setChecked(grain is None)
        self.grain_spin.setEnabled(grain is not None)
        self.no_grain_cb.toggled.connect(self._on_no_grain_toggled)
        grain_layout.addWidget(self.grain_spin)
        grain_layout.addWidget(self.no_grain_cb)
        form.addRow(translate("SheetsTab", "Grain:"), wrap_row("grain_angle", grain_row))

        self.flip_combo = QtWidgets.QComboBox()
        self.flip_combo.addItem(translate("SheetsTab", "None (one side)"), "none")
        self.flip_combo.addItem(translate("SheetsTab", "About X (top edge to bottom edge)"), "x")
        self.flip_combo.addItem(translate("SheetsTab", "About Y (left edge to right edge)"), "y")
        self.flip_combo.setCurrentIndex(self.flip_combo.findData(sheet["flip"]))
        self.flip_combo.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab",
            "Turns this sheet over after the first side is cut to machine its back.\n"
            "The layout shows a back side above each sheet, and Create CAM Job makes a second job for it.")))
        self.flip_combo.currentIndexChanged.connect(self._validate)
        form.addRow(translate("SheetsTab", "Flip:"), wrap_row("flip", self.flip_combo))

        self.notes_edit = QtWidgets.QLineEdit(sheet["notes"])
        self.notes_edit.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab", "Stores optional notes for this sheet, such as the supplier.")))
        form.addRow(translate("SheetsTab", "Notes:"), self.notes_edit)

        main_layout.addLayout(form)
        main_layout.addWidget(self.material_note)

        # Initial enabled state for variant rows
        if self._is_variant:
            for key in model.INHERITABLE_FIELDS:
                self._set_row_enabled(key, key not in self._inherited_keys)

        # SHLIB-066: Check if numerical widgets cannot display stored values exactly
        numeric_spins = {
            "width": self.width_spin,
            "height": self.height_spin,
            "thickness": self.thickness_spin,
            "cost": self.price_spin,
        }
        for key, spin in numeric_spins.items():
            if key in self._sheet and model._is_number(self._sheet[key]):
                if abs(spin.value() - self._sheet[key]) > 1e-9:
                    if key not in self._repaired_keys:
                        self._repaired_keys.append(key)

        if "grain_angle" in self._sheet and self._sheet["grain_angle"] is not None:
            if model._is_number(self._sheet["grain_angle"]):
                if abs(self.grain_spin.value() - self._sheet["grain_angle"]) > 1e-9:
                    if "grain_angle" not in self._repaired_keys:
                        self._repaired_keys.append("grain_angle")

        self._repaired_keys = [k for k in self._FIELD_ORDER if k in self._repaired_keys]

        key_labels = {
            "name": translate("SheetsTab", "Name"),
            "material": translate("SheetsTab", "Material"),
            "width": translate("SheetsTab", "Width"),
            "height": translate("SheetsTab", "Height"),
            "thickness": translate("SheetsTab", "Thickness"),
            "cost": translate("SheetsTab", "Price"),
            "grain_angle": translate("SheetsTab", "Grain"),
            "flip": translate("SheetsTab", "Flip"),
            "notes": translate("SheetsTab", "Notes"),
        }

        self.repair_label = QtWidgets.QLabel()
        self.repair_label.setWordWrap(True)
        if self._repaired_keys:
            labels_str = ", ".join(key_labels[k] for k in self._repaired_keys)
            self.repair_label.setText(translate(
                "SheetsTab",
                "These fields were missing, unreadable or out of range, and now show other values: %1. Check them before saving."
            ).replace("%1", labels_str))
            self.repair_label.setVisible(True)
        else:
            self.repair_label.setText("")
            self.repair_label.setVisible(False)
        main_layout.addWidget(self.repair_label)

        self.error_label = QtWidgets.QLabel()
        self.error_label.setStyleSheet("color: red;")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        main_layout.addWidget(self.error_label)

        self.button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)

    def _on_no_grain_toggled(self, checked: bool):
        self.grain_spin.setEnabled(not checked and self._row_overridden("grain_angle"))
        self._validate()

    def record(self) -> Dict[str, Any]:
        """The edited sheet. Keys the form doesn't show (id, created, the feature lists) pass through."""
        rec = copy.deepcopy(self._sheet)
        rec["parent_id"] = self._parent_id
        rec["name"] = self.name_edit.text().strip()
        rec["notes"] = self.notes_edit.text()

        inheritable_values = {
            "material": self.material_combo.itemData(self.material_combo.currentIndex()),
            "width": float(self.width_spin.value()),
            "height": float(self.height_spin.value()),
            "thickness": float(self.thickness_spin.value()),
            "cost": float(self.price_spin.value()),
            "grain_angle": None if self.no_grain_cb.isChecked() else float(self.grain_spin.value()),
            "flip": self.flip_combo.itemData(self.flip_combo.currentIndex()),
        }

        for key, val in inheritable_values.items():
            if self._row_overridden(key):
                rec[key] = val
            else:
                rec.pop(key, None)

        return rec

    def _validate(self, *args):
        if not hasattr(self, "button_box"):
            return
        errors = model.validate_sheet(self.record(), self._library)
        self.error_label.setText("\n".join(errors))
        self.error_label.setVisible(bool(errors))
        ok_button = self.button_box.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_button:
            ok_button.setEnabled(not errors)


class SheetsTab(QtWidgets.QWidget):
    """Sheet library management tab inside the Library window."""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget] = None,
        root: Optional[str] = None,
    ):
        super().__init__(parent)
        self.root = root
        self.library: Optional[Library] = None
        self.materials: List[str] = []

        layout = QtWidgets.QVBoxLayout(self)

        # Problems banner
        self.banner = QtWidgets.QLabel()
        self.banner.setStyleSheet(BANNER_STYLE)
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        layout.addWidget(self.banner)

        self.remove_skipped_button = QtWidgets.QPushButton(
            translate("SheetsTab", "Remove Skipped Files…"))
        self.remove_skipped_button.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab", "Deletes the unreadable sheet files listed above permanently.")))
        self.remove_skipped_button.clicked.connect(self._on_remove_skipped)
        self.remove_skipped_button.setVisible(False)
        layout.addWidget(self.remove_skipped_button, 0, QtCore.Qt.AlignLeft)

        # Tree widget
        self.tree = QtWidgets.QTreeWidget(self)
        self.tree.setRootIsDecorated(True)
        self.tree.setHeaderLabels([
            translate("SheetsTab", "Name"),
            translate("SheetsTab", "Material"),
            translate("SheetsTab", "Size"),
            translate("SheetsTab", "Thickness"),
            translate("SheetsTab", "Price"),
            translate("SheetsTab", "Grain"),
            translate("SheetsTab", "Flip"),
        ])
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.currentItemChanged.connect(self._on_current_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(self._on_edit)
        layout.addWidget(self.tree)

        # Button bar
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setContentsMargins(*MARGINS_NONE)

        self.new_button = QtWidgets.QPushButton(translate("SheetsTab", "New…"))
        self.new_button.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab", "Adds a new sheet to the library.")))
        self.new_button.clicked.connect(self._on_new)
        btn_layout.addWidget(self.new_button)

        self.variant_button = QtWidgets.QPushButton(translate("SheetsTab", "New Variant…"))
        self.variant_button.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab", "Creates a variant of the selected sheet that inherits its values until overridden.")))
        self.variant_button.clicked.connect(self._on_new_variant)
        btn_layout.addWidget(self.variant_button)

        self.dup_button = QtWidgets.QPushButton(translate("SheetsTab", "Duplicate…"))
        self.dup_button.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab", "Duplicates the selected sheet or variant.")))
        self.dup_button.clicked.connect(self._on_duplicate)
        btn_layout.addWidget(self.dup_button)

        self.edit_button = QtWidgets.QPushButton(translate("SheetsTab", "Edit…"))
        self.edit_button.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab", "Edits the selected sheet.")))
        self.edit_button.clicked.connect(self._on_edit)
        btn_layout.addWidget(self.edit_button)

        self.del_button = QtWidgets.QPushButton(translate("SheetsTab", "Delete"))
        self.del_button.setToolTip(rich_tooltip("SheetsTab", QT_TRANSLATE_NOOP(
            "SheetsTab", "Removes the selected sheet from the library; existing layouts keep their dimensions.")))
        self.del_button.clicked.connect(self._on_delete)
        btn_layout.addWidget(self.del_button)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.reload()

    def reload(self, reselect_id: Optional[str] = None):
        """Reload sheets from disk, refreshing tree and banner."""
        self.library = store.load_library(self.root)
        self.materials, materials_problem = store.load_materials()

        # Banner
        lines = list(self.library.problems)
        if materials_problem is not None:
            lines.append(materials_problem)
        self.banner.setText("\n".join(lines))
        self.banner.setVisible(bool(lines))
        self.remove_skipped_button.setVisible(bool(self.library.skipped))

        # Tree
        self.tree.clear()
        currency = store.currency_symbol()
        rows = model.sheet_rows(self.library)

        parents: List[QtWidgets.QTreeWidgetItem] = []
        for depth, sheet_id, name, sheet, error in rows:
            parent_widget = parents[depth - 1] if depth > 0 else self.tree
            item = QtWidgets.QTreeWidgetItem(parent_widget)
            del parents[depth:]
            parents.append(item)

            item.setText(0, name)
            item.setData(0, QtCore.Qt.UserRole, sheet_id)
            item.setIcon(0, QtGui.QIcon(os.path.join(ICONS_DIR, "Nesting_SheetLibrary_Icon.svg")))

            if error:
                item.setText(1, error)
                item.setToolTip(0, error)
                item.setToolTip(1, error)
                item.setForeground(1, QtGui.QBrush(QtGui.QColor("red")))
            else:
                w = sheet["width"]
                h = sheet["height"]
                item.setText(1, sheet["material"] or translate("SheetsTab", "none"))
                item.setText(2, f"{format_length(w)} × {format_length(h)}")
                item.setText(3, format_length(sheet['thickness']))
                item.setText(4, model.format_cost(sheet["cost"], currency))
                grain = sheet.get("grain_angle")
                item.setText(5, f"{grain:g}°" if grain is not None else translate("SheetsTab", "none"))
                flip_val = sheet["flip"]
                flip_text = translate("SheetsTab", "none") if flip_val == "none" else ("X" if flip_val == "x" else "Y")
                item.setText(6, flip_text)

                pid = sheet.get("parent_id")
                if pid and self.library and pid in self.library.sheets:
                    parent_name = self.library.sheets[pid].get("name", "")
                    item.setToolTip(0, translate("SheetsTab", "Variant of '%1'").replace("%1", parent_name))

            if sheet_id == reselect_id:
                self.tree.setCurrentItem(item)

        self.tree.expandAll()
        self._update_button_states()

    def selected_sheet_id(self) -> Optional[str]:
        item = self.tree.currentItem()
        if item is not None:
            return item.data(0, QtCore.Qt.UserRole)
        return None

    def _on_current_item_changed(self, current, previous):
        self._update_button_states()

    def _on_selection_changed(self):
        self._update_button_states()

    def _update_button_states(self):
        sid = self.selected_sheet_id()
        has_sel = sid is not None
        self.edit_button.setEnabled(has_sel)
        self.del_button.setEnabled(has_sel)

        has_error = False
        if has_sel and self.library and sid in self.library.sheets:
            errors = model.validate_sheet(self.library.sheets[sid], self.library)
            has_error = bool(errors)
        self.dup_button.setEnabled(has_sel and not has_error)
        self.variant_button.setEnabled(has_sel and not has_error)

    def _on_new(self):
        new_rec = model.new_sheet(str(uuid.uuid4()))
        dlg = SheetEditDialog(new_rec, self.library, self.materials, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            try:
                saved = store.save_sheet(dlg.record(), root=self.root)
                self.reload(reselect_id=saved["id"])
            except OSError as e:
                show_warning_dialog(self, translate("SheetsTab", "Error"), translate("SheetsTab", "Failed to save sheet: {error}").format(error=e))

    def _on_new_variant(self):
        sid = self.selected_sheet_id()
        if not sid or not self.library or sid not in self.library.sheets:
            return
        parent = self.library.sheets[sid]
        rec = model.make_variant(parent, f"{parent.get('name', '')} variant", str(uuid.uuid4()))
        dlg = SheetEditDialog(rec, self.library, self.materials, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            try:
                saved = store.save_sheet(dlg.record(), root=self.root)
                self.reload(reselect_id=saved["id"])
            except OSError as e:
                show_warning_dialog(self, translate("SheetsTab", "Error"), translate("SheetsTab", "Failed to save variant: {error}").format(error=e))

    def _on_duplicate(self):
        sid = self.selected_sheet_id()
        if not sid or not self.library or sid not in self.library.sheets:
            return
        orig = self.library.sheets[sid]
        dup_rec = model.duplicate_sheet(orig, f"{orig.get('name', '')} copy", str(uuid.uuid4()))
        dlg = SheetEditDialog(dup_rec, self.library, self.materials, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            try:
                saved = store.save_sheet(dlg.record(), root=self.root)
                self.reload(reselect_id=saved["id"])
            except OSError as e:
                show_warning_dialog(self, translate("SheetsTab", "Error"), translate("SheetsTab", "Failed to save duplicate: {error}").format(error=e))

    def _on_edit(self):
        sid = self.selected_sheet_id()
        if not sid or not self.library or sid not in self.library.sheets:
            return
        rec = self.library.sheets[sid]
        dlg = SheetEditDialog(rec, self.library, self.materials, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            try:
                saved = store.save_sheet(dlg.record(), root=self.root)
                self.reload(reselect_id=saved["id"])
            except OSError as e:
                show_warning_dialog(self, translate("SheetsTab", "Error"), translate("SheetsTab", "Failed to save sheet: {error}").format(error=e))

    def _on_delete(self):
        sid = self.selected_sheet_id()
        if not sid or not self.library or sid not in self.library.sheets:
            return

        variants = model.direct_variants(self.library, sid)
        sheet_name = self.library.sheets[sid].get("name", "")
        if variants:
            msg = translate(
                "SheetsTab",
                "'{name}' has {count} variant(s): {variants}. Deleting it makes them independent sheets that keep their current values. Layouts already nested on these sheets keep their saved size.",
            ).format(
                name=sheet_name,
                count=len(variants),
                variants=", ".join(v.get("name", "") for v in variants),
            )
        else:
            msg = translate(
                "SheetsTab",
                "Are you sure you want to delete '{name}'? Layouts already nested on this sheet keep their saved size.",
            ).format(name=sheet_name)

        ret = show_warning_dialog(
            self,
            translate("SheetsTab", "Delete Sheet"),
            msg,
            buttons=QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
        )
        if ret != QtWidgets.QMessageBox.Ok:
            return

        try:
            store.delete_sheet_detaching(self.library, sid, root=self.root)
            self.reload()
        except OSError as e:
            show_warning_dialog(
                self,
                translate("SheetsTab", "Error"),
                translate("SheetsTab", "Failed to delete sheet: {error}. Delete it again to finish.").format(error=e),
            )
            self.reload()

    def _on_remove_skipped(self):
        skipped = list(self.library.skipped) if self.library else []
        if not skipped:
            return
        msg = translate(
            "SheetsTab",
            "Delete these {count} file(s) from the sheet library folder? They can't be recovered.\n\n{files}",
        ).format(count=len(skipped), files="\n".join(self.library.problems))
        ret = show_warning_dialog(
            self,
            translate("SheetsTab", "Remove Skipped Files"),
            msg,
            buttons=QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
        )
        if ret != QtWidgets.QMessageBox.Ok:
            return
        failed = store.delete_skipped_files(skipped, root=self.root)
        self.reload(reselect_id=self.selected_sheet_id())
        if failed:
            show_warning_dialog(
                self,
                translate("SheetsTab", "Error"),
                translate("SheetsTab", "Could not delete: {files}").format(
                    files="; ".join(f"{name} ({error})" for name, error in failed)),
            )
