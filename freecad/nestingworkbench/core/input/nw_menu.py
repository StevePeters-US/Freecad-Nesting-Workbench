# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/core/input/nw_menu.py

"""
Dynamic Qt context menu manager for Nesting Workbench.

Constructs and executes QMenu instances from declarative descriptions,
deferring presentation via QTimer.singleShot to avoid event-loop deadlocks.
"""

import FreeCAD
from PySide import QtCore, QtGui, QtWidgets


class NWMenuManager:
    """Handles dynamic viewport context menus."""

    _instance = None

    @classmethod
    def get_instance(cls):
        """Return singleton instance of NWMenuManager."""
        if cls._instance is None:
            cls._instance = NWMenuManager()
        return cls._instance

    def __init__(self):
        self._menu_open = False
        self._active_menu = None

    def is_menu_active(self):
        """Return True if a dynamic menu is currently displayed."""
        return self._menu_open

    def _build_dynamic_menu(self, menu, items):
        """Recursively populate menu from declarative item structures."""
        for item in items:
            if item == "-" or item is None:
                menu.addSeparator()
            elif isinstance(item, tuple):
                if len(item) == 2:
                    name, action = item
                    if isinstance(action, list):
                        submenu = menu.addMenu(name)
                        self._build_dynamic_menu(submenu, action)
                    else:
                        menu.addAction(name, action)
                elif len(item) == 3:
                    name, action, is_checked = item
                    act = menu.addAction(name)
                    act.setCheckable(True)
                    act.setChecked(is_checked)
                    act.toggled.connect(action)

    def _on_menu_hide(self):
        """Cleanup state when menu is dismissed."""
        self._menu_open = False
        self._active_menu = None

    def trigger_dynamic_menu(self, items):
        """Build and asynchronously show a QMenu from declarative items."""
        if not items or self._menu_open:
            return False

        try:
            self._active_menu = QtWidgets.QMenu()
            self._build_dynamic_menu(self._active_menu, items)
            if hasattr(self._active_menu, "aboutToHide"):
                self._active_menu.aboutToHide.connect(self._on_menu_hide)
            self._menu_open = True
            pos_getter = getattr(QtGui.QCursor, "pos", lambda: (0, 0))
            QtCore.QTimer.singleShot(
                0,
                lambda: self._active_menu.exec_(pos_getter()) if self._active_menu else None
            )
            return True
        except Exception as e:
            FreeCAD.Console.PrintError(f"[NWMenuManager] Error triggering dynamic menu: {e}\n")
            self._menu_open = False
            self._active_menu = None
            return False
