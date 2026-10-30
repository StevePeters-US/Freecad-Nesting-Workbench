# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
from PySide import QtWidgets
from freecad.nestingworkbench import task_panel_manager
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, rich_tooltip, show_warning_dialog

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

class _PanelDocumentObserver:
    def slotDeletedDocument(self, doc):
        if not doc or not hasattr(doc, "Name"):
            return
        panel = NestingCommand._task_panels.pop(doc.Name, None)
        if panel is not None:
            controller = getattr(panel, "controller", None)
            if controller is not None:
                controller.doc_closed = True
                if getattr(controller, "is_running", False):
                    controller.request_cancel()
                controller.on_panel_closed()


_panel_doc_observer = None


# --- FreeCAD Command Classes ---

class NestingCommand:
    """The command that opens the main nesting task panel."""
    _task_panels = {}

    def GetResources(self):
        """Defines the command's appearance in FreeCAD."""
        return {
            'Pixmap': 'Nesting_Nest_Icon.svg',
            'MenuText': QT_TRANSLATE_NOOP('NestingCommand', 'Run Nesting Tool'),
            'ToolTip': rich_tooltip(
                'NestingCommand',
                QT_TRANSLATE_NOOP('NestingCommand', 'Opens the 2D nesting task panel.\nOpen a document first.')),
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return
        if doc.Name in NestingCommand._task_panels:
            return

        missing = _missing_dependencies()
        if missing:
            _warn_missing(missing)
            return
        try:
            panel = task_panel_manager.NestingTaskPanel(
                cleanup_callback=lambda name=doc.Name: NestingCommand._task_panels.pop(name, None)
            )
            NestingCommand._task_panels[doc.Name] = panel
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
    if hasattr(FreeCAD, "addDocumentObserver") and _panel_doc_observer is None:
        _panel_doc_observer = _PanelDocumentObserver()
        FreeCAD.addDocumentObserver(_panel_doc_observer)
