# SPDX-License-Identifier: LGPL-2.1-or-later
"""Materials tab of the Library window: the names a sheet's Material is picked from."""

from typing import Optional

from PySide import QtCore, QtWidgets

from ...sheet_library import model, store
from ...ui_helpers import MARGINS_NONE, QT_TRANSLATE_NOOP, rich_tooltip, show_warning_dialog
from .ui_sheet_library import BANNER_STYLE

translate = QtWidgets.QApplication.translate


class MaterialsTab(QtWidgets.QWidget):
    """Add, rename and delete the materials the Edit Sheet dialog offers."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None, sheets_root: Optional[str] = None):
        super().__init__(parent)
        self.sheets_root = sheets_root
        self.library = None
        self.materials = []
        self.problem = None

        layout = QtWidgets.QVBoxLayout(self)

        self.banner = QtWidgets.QLabel()
        self.banner.setStyleSheet(BANNER_STYLE)
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        layout.addWidget(self.banner)

        self.tree = QtWidgets.QTreeWidget(self)
        self.tree.setRootIsDecorated(False)
        self.tree.setHeaderLabels([
            translate("MaterialsTab", "Material"),
            translate("MaterialsTab", "Sheets"),
        ])
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.tree.currentItemChanged.connect(self._update_button_states)
        self.tree.itemDoubleClicked.connect(self._on_rename)
        layout.addWidget(self.tree)

        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setContentsMargins(*MARGINS_NONE)

        self.new_button = QtWidgets.QPushButton(translate("MaterialsTab", "New…"))
        self.new_button.setToolTip(rich_tooltip("MaterialsTab", QT_TRANSLATE_NOOP(
            "MaterialsTab", "Adds a material to the list sheets pick from.")))
        self.new_button.clicked.connect(self._on_new)
        btn_layout.addWidget(self.new_button)

        self.rename_button = QtWidgets.QPushButton(translate("MaterialsTab", "Rename…"))
        self.rename_button.setToolTip(rich_tooltip("MaterialsTab", QT_TRANSLATE_NOOP(
            "MaterialsTab", "Renames the selected material across every sheet that uses it.")))
        self.rename_button.clicked.connect(self._on_rename)
        btn_layout.addWidget(self.rename_button)

        self.del_button = QtWidgets.QPushButton(translate("MaterialsTab", "Delete"))
        self.del_button.setToolTip(rich_tooltip("MaterialsTab", QT_TRANSLATE_NOOP(
            "MaterialsTab",
            "Removes the selected material; sheets that use it are kept with no material.")))
        self.del_button.clicked.connect(self._on_delete)
        btn_layout.addWidget(self.del_button)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.reload()

    def reload(self, reselect: Optional[str] = None):
        """Reload the list and the sheets (for the usage counts)."""
        self.library = store.load_library(self.sheets_root)
        self.materials, self.problem = store.load_materials()

        if self.problem:
            self.banner.setText(translate(
                "MaterialsTab", "The materials list can't be read, so it can't be edited: %1"
            ).replace("%1", self.problem))
            self.banner.setVisible(True)
        else:
            self.banner.setText("")
            self.banner.setVisible(False)

        self.tree.clear()
        for name in self.materials:
            item = QtWidgets.QTreeWidgetItem(self.tree)
            item.setText(0, name)
            item.setText(1, str(len(model.material_effective_users(self.library, name))))
            item.setData(0, QtCore.Qt.UserRole, name)
            if name == reselect:
                self.tree.setCurrentItem(item)
        self._update_button_states()

    def selected_material(self) -> Optional[str]:
        item = self.tree.currentItem()
        return item.data(0, QtCore.Qt.UserRole) if item is not None else None

    def _update_button_states(self, *args):
        editable = self.problem is None
        has_sel = self.selected_material() is not None
        self.new_button.setEnabled(editable)
        self.rename_button.setEnabled(editable and has_sel)
        self.del_button.setEnabled(editable and has_sel)

    def _ask_name(self, title, text=""):
        """The stripped name typed, or None on Cancel."""
        name, ok = QtWidgets.QInputDialog.getText(
            self, title, translate("MaterialsTab", "Name:"), text=text)
        return name.strip() if ok else None

    def _on_new(self, *args):
        if self.problem is not None:
            return
        title = translate("MaterialsTab", "New Material")
        name = self._ask_name(title)
        if name is None:
            return
        errors = model.validate_material_name(name, self.materials)
        if errors:
            show_warning_dialog(self, title, "; ".join(errors))
            return
        try:
            store.save_materials(self.materials + [name])
        except OSError as e:
            show_warning_dialog(self, translate("MaterialsTab", "Error"), translate(
                "MaterialsTab", "Failed to save the materials list: {error}").format(error=e))
            return
        self.reload(reselect=name)

    def _on_rename(self, *args):
        old = self.selected_material()
        if old is None or self.problem is not None:
            return
        title = translate("MaterialsTab", "Rename Material")
        name = self._ask_name(title, old)
        if name is None or name == old:
            return
        errors = model.validate_material_name(name, self.materials, old=old)
        if errors:
            show_warning_dialog(self, title, "; ".join(errors))
            return
        try:
            store.replace_material(self.library, old, name, self.materials, root=self.sheets_root)
        except OSError as e:
            show_warning_dialog(self, translate("MaterialsTab", "Error"), translate(
                "MaterialsTab",
                "Failed to rename the material: {error}. Rename it again to finish.").format(error=e))
            self.reload(reselect=old)
            return
        self.reload(reselect=name)

    def _on_delete(self, *args):
        name = self.selected_material()
        if name is None or self.problem is not None:
            return
        users = model.material_effective_users(self.library, name)
        if users:
            text = translate(
                "MaterialsTab",
                "'{name}' is assigned to {count} sheet(s): {sheets}. Deleting it leaves those "
                "sheets with no material; their size, thickness and price are kept. Layouts "
                "already nested keep their saved material.",
            ).format(name=name, count=len(users),
                     sheets=", ".join(str(s.get("name", "")) for s in users))
        else:
            text = translate(
                "MaterialsTab", "Delete the material '{name}'? No sheet uses it.").format(name=name)
        ret = show_warning_dialog(
            self, translate("MaterialsTab", "Delete Material"), text,
            buttons=QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
        if ret != QtWidgets.QMessageBox.Ok:
            return
        try:
            store.replace_material(self.library, name, "", self.materials, root=self.sheets_root)
        except OSError as e:
            show_warning_dialog(self, translate("MaterialsTab", "Error"), translate(
                "MaterialsTab",
                "Failed to delete the material: {error}. Delete it again to finish.").format(error=e))
        self.reload()
