# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
from PySide import QtWidgets
from freecad.nestingworkbench import task_panel_manager
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, show_warning_dialog

_REQUIRED_MODULES = ("shapely", "numpy")
_MISSING_DEPS_TITLE = QT_TRANSLATE_NOOP("NestingCommand", "Missing Python packages")
_MISSING_DEPS_TEXT = QT_TRANSLATE_NOOP(
    "NestingCommand",
    "The nesting tool needs these Python packages, which are not installed: {}")
_MISSING_DEPS_HINT = QT_TRANSLATE_NOOP(
    "NestingCommand",
    "Install them into FreeCAD's Python (for example with the Addon Manager's "
    "dependency installer), then restart FreeCAD.")


def _missing_dependencies():
    import importlib.util
    return [m for m in _REQUIRED_MODULES if importlib.util.find_spec(m) is None]


def _warn_missing(names):
    tr = QtWidgets.QApplication.translate
    show_warning_dialog(
        None,
        tr("NestingCommand", _MISSING_DEPS_TITLE),
        tr("NestingCommand", _MISSING_DEPS_TEXT).format(", ".join(names)),
        tr("NestingCommand", _MISSING_DEPS_HINT),
    )

# --- FreeCAD Command Classes ---

class NestingCommand:
    """The command that opens the main nesting task panel."""
    _task_panel = None

    def GetResources(self):
        """Defines the command's appearance in FreeCAD."""
        return {
            'Pixmap': 'Nesting_Nest_Icon.svg',
            'MenuText': QT_TRANSLATE_NOOP('NestingCommand', 'Run Nesting Tool'),
            'ToolTip': QT_TRANSLATE_NOOP('NestingCommand', 'Opens the 2D nesting task panel.')
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        # Manages its own instance to prevent multiple panels
        if NestingCommand._task_panel is None:
            missing = _missing_dependencies()
            if missing:
                _warn_missing(missing)
                return
            try:
                NestingCommand._task_panel = task_panel_manager.NestingTaskPanel(
                    cleanup_callback=lambda: setattr(NestingCommand, "_task_panel", None)
                )
            except ImportError as e:
                _warn_missing([e.name or str(e)])
                return

    def IsActive(self):
        """Can only be active if a document is open."""
        return FreeCAD.ActiveDocument is not None

# --- Command Registration ---
# This is where the commands are officially made known to FreeCAD.
if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_Run', NestingCommand())
