# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/init_gui.py


import os

import FreeCADGui

from freecad.nestingworkbench import ICONS_DIR, TRANSLATIONS_DIR
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, set_status_tips

_COMMANDS = (
    'Nesting_ImportSketch',
    'Nesting_Run',
    'Nesting_StackSheets',
    'Nesting_ManualNester',
    'Nesting_Export',
    'Nesting_CreateCAMJob',
    'Nesting_CreateSilhouette',
    'Nesting_Library',
    'Nesting_Settings',
    'Nesting_About',
)

_MENU_NAME = QT_TRANSLATE_NOOP("Workbench", "Nesting")


class NestingWorkbench(FreeCADGui.Workbench):
    """
    Defines the Nesting Workbench.
    """
    MenuText = QT_TRANSLATE_NOOP("NestingWorkbench", "Nesting")
    ToolTip = QT_TRANSLATE_NOOP("NestingWorkbench", "Nests 2D shapes and 3D parts onto flat sheets.")
    # Absolute path: the workbench selector reads this at registration time,
    # before Initialize() has added the icon directory to the search path.
    Icon = os.path.join(ICONS_DIR, "Nesting_Workbench.svg")

    def GetClassName(self):
        return "Gui::PythonWorkbench"

    def Initialize(self):
        """This function is executed when the workbench is activated."""
        # Add the icon directory to the global search path here rather than at
        # import time, so an unactivated workbench does not affect icon lookup
        # for the rest of FreeCAD. All icon names are Nesting_*-prefixed.
        FreeCADGui.addIconPath(ICONS_DIR)
        if hasattr(FreeCADGui, "addLanguagePath") and os.path.isdir(TRANSLATIONS_DIR):
            FreeCADGui.addLanguagePath(TRANSLATIONS_DIR)
            if hasattr(FreeCADGui, "updateLocale"):
                FreeCADGui.updateLocale()
        # Import the command modules. This executes the FreeCADGui.addCommand()
        # in each file, making the commands available to FreeCAD.
        from freecad.nestingworkbench.commands import command_nest
        from freecad.nestingworkbench.commands import command_stack_sheets
        from freecad.nestingworkbench.commands import command_manual_nester
        from freecad.nestingworkbench.commands import command_export_sheets
        from freecad.nestingworkbench.commands import command_create_cam_job
        from freecad.nestingworkbench.commands import command_create_silhouette
        from freecad.nestingworkbench.commands import command_about
        from freecad.nestingworkbench.commands import command_settings
        from freecad.nestingworkbench.commands import command_library
        from freecad.nestingworkbench.commands import command_import_sketch
        # Create Menu (Dropdown)
        self.appendMenu(["Nesting"], [
            'Nesting_Settings',
            'Separator',
            'Nesting_ImportSketch',
            'Nesting_Run',
            'Nesting_StackSheets',
            'Nesting_ManualNester',
            'Nesting_Export',
            'Nesting_CreateCAMJob',
            'Nesting_CreateSilhouette',
            'Nesting_Library',
            'Separator',
            'Nesting_About'
        ])
        self.appendToolbar("Nesting", [
            'Nesting_ImportSketch',
            'Nesting_Run',
            'Nesting_StackSheets',
            'Nesting_ManualNester',
            'Nesting_Export',
            'Nesting_CreateCAMJob',
            'Nesting_CreateSilhouette',
            'Nesting_Library'
        ])

    def Activated(self):
        """This function is executed when the workbench is activated."""
        try:
            set_status_tips(_COMMANDS)
        except Exception as e:
            from freecad.nestingworkbench import nw_logger
            nw_logger.error(f"Nesting Activated status tips error: {e}")
        try:
            from freecad.nestingworkbench.core.input.nw_input_manager import NWInputManager
            NWInputManager.get_instance().initialize()
        except Exception as e:
            from freecad.nestingworkbench import nw_logger
            nw_logger.error(f"Nesting Activated error: {e}")

    def Deactivated(self):
        """This function is executed when the workbench is deactivated."""
        try:
            from freecad.nestingworkbench.core.input.nw_input_manager import NWInputManager
            NWInputManager.get_instance().restore()
        except Exception as e:
            from freecad.nestingworkbench import nw_logger
            nw_logger.error(f"Nesting Deactivated error: {e}")


# Add the workbench to FreeCAD's list of available workbenches
FreeCADGui.addWorkbench(NestingWorkbench())
