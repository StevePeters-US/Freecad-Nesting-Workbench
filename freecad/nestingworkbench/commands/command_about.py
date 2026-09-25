# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/commands/command_about.py

"""FreeCAD command that shows an About dialog for the Nesting Workbench."""

import html
import os
import xml.etree.ElementTree as ET

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets

from freecad.nestingworkbench import ADDON_DIR
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP

PATREON_URL = "https://www.patreon.com/cw/AttackPotato"

_ABOUT_TITLE = QT_TRANSLATE_NOOP("AboutCommand", "About Nesting Workbench")
_VERSION_LABEL = QT_TRANSLATE_NOOP("AboutCommand", "Version:")
_SUPPORT_LINK = QT_TRANSLATE_NOOP("AboutCommand", "Support development on Patreon")


def _get_version_string():
    """Returns the released version from the addon's package.xml."""
    try:
        root = ET.parse(os.path.join(ADDON_DIR, "package.xml")).getroot()
        ns = {"pm": "https://wiki.freecad.org/Package_Metadata"}
        version_el = root.find("pm:version", ns)
        if version_el is not None and version_el.text:
            return version_el.text.strip()
    except (OSError, ET.ParseError) as e:
        FreeCAD.Console.PrintLog(f"[About] Could not read package.xml version: {e}\n")
    return "unknown"


class AboutCommand:
    """Shows version information and a Patreon link for the workbench."""

    def GetResources(self):
        return {
            'Pixmap': 'Nesting_Workbench.svg',
            'MenuText': QT_TRANSLATE_NOOP('AboutCommand', 'About Nesting Workbench'),
            'ToolTip': QT_TRANSLATE_NOOP('AboutCommand', 'Shows the workbench version and a link to support development.')
        }

    def Activated(self):
        version = _get_version_string()
        tr = QtWidgets.QApplication.translate
        dialog = QtWidgets.QDialog()
        dialog.setWindowTitle(tr("AboutCommand", _ABOUT_TITLE))

        layout = QtWidgets.QVBoxLayout(dialog)
        version_label = tr("AboutCommand", _VERSION_LABEL)
        support_link = tr("AboutCommand", _SUPPORT_LINK)
        escaped_version = html.escape(version)
        label = QtWidgets.QLabel(
            "<h3>Nesting Workbench</h3>"
            f"<p>{version_label} {escaped_version}</p>"
            f'<p><a href="{PATREON_URL}">{support_link}</a></p>'
        )
        label.setTextFormat(QtCore.Qt.RichText)
        label.setOpenExternalLinks(True)
        layout.addWidget(label)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        button_box.accepted.connect(dialog.accept)
        layout.addWidget(button_box)

        dialog.exec_()

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_About', AboutCommand())
