# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/init_gui.py


import os

import FreeCADGui

from freecad.nestingworkbench import ICONS_DIR


class NestingWorkbench(FreeCADGui.Workbench):
    """
    Defines the Nesting Workbench.
    """
    MenuText = "Nesting"
    ToolTip = "A workbench for 2D nesting of shapes."
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
        # Import the command modules. This executes the FreeCADGui.addCommand()
        # in each file, making the commands available to FreeCAD.
        from freecad.nestingworkbench.commands import command_nest
        from freecad.nestingworkbench.commands import command_stack_sheets
        from freecad.nestingworkbench.commands import command_manual_nester
        from freecad.nestingworkbench.commands import command_export_sheets
        from freecad.nestingworkbench.commands import command_create_cam_job
        from freecad.nestingworkbench.commands import command_create_silhouette
        # Create Menu (Dropdown)
        self.appendMenu(["Nesting"], [
            'Nesting_Run',
            'Nesting_StackSheets',
            'Nesting_ManualNester',
            'Nesting_Export',
            'Nesting_CreateCAMJob',
            'Nesting_CreateSilhouette'
        ])
        self.appendToolbar("Nesting", [
            'Nesting_Run',
            'Nesting_StackSheets',
            'Nesting_ManualNester',
            'Nesting_Export',
            'Nesting_CreateCAMJob',
            'Nesting_CreateSilhouette'
        ])

    def Activated(self):
        """This function is executed when the workbench is activated."""
        return

    def Deactivated(self):
        """This function is executed when the workbench is deactivated."""
        return

# Add the workbench to FreeCAD's list of available workbenches
FreeCADGui.addWorkbench(NestingWorkbench())
