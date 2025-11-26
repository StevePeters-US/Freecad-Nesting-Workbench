# Nesting/InitGui.py

try:
    import shapely
except ImportError:
    from PySide.QtGui import QMessageBox
    from PySide import QtCore

    title = "Nesting Workbench - Missing Dependency"
    message = """
<p>The 'shapely' library is not installed.</p>
<p>This is a required dependency for the Nesting Workbench to function correctly.</p>
<p><b>Instructions:</b></p>
<ol>
<li>Open a terminal (Command Prompt on Windows).</li>
<li>Navigate to the 'bin' directory of your FreeCAD installation (C:\\Program Files\\FreeCAD 1.0\\bin).</li>
<li>If 'pip' is not available, first run: <code>python -m ensurepip</code></li>
<li>Install shapely by running: <code>python -m pip install shapely</code></li>
</ol>
<p>Please restart FreeCAD after the installation is complete.</p>
"""
    msgBox = QMessageBox()
    msgBox.setWindowTitle(title)
    msgBox.setTextFormat(QtCore.Qt.RichText)
    msgBox.setText(message)
    msgBox.setStandardButtons(QMessageBox.Ok)
    msgBox.exec_()


import FreeCAD
import FreeCADGui

class NestingWorkbench(FreeCADGui.Workbench):
    """
    Defines the Nesting Workbench.
    """
    MenuText = "Nesting"
    ToolTip = "A workbench for 2D nesting of shapes."
    Icon = "Nesting/Resources/icons/Nesting_Workbench.svg"

    def GetClassName(self):
        return "Gui::PythonWorkbench"

    def Initialize(self):
        """This function is executed when the workbench is activated."""
        # Import the command modules. This executes the FreeCADGui.addCommand()
        # in each file, making the commands available to FreeCAD.
        from commands import command_nest
        from commands import command_stack_sheets
        from commands import command_transform_parts
        from commands import command_anneal_layout
        from commands import command_export_sheets
        from commands import command_create_cam_job
        
        self.appendToolbar("Nesting", [
            'Nesting_Run',
            'Nesting_StackSheets',
            'Nesting_TransformParts',
            'Nesting_AnnealLayout',
            'Nesting_Export',
            'Nesting_CreateCAMJob'
        ])

    def Activated(self):
        """This function is executed when the workbench is activated."""
        return

    def Deactivated(self):
        """This function is executed when the workbench is deactivated."""
        return

# Add the workbench to FreeCAD's list of available workbenches
FreeCADGui.addWorkbench(NestingWorkbench())
