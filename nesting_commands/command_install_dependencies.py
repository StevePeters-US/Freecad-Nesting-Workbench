import FreeCAD
import FreeCADGui
import sys
import subprocess
from PySide import QtGui, QtCore

class InstallDependenciesCommand:
    """Command to install required dependencies (Taichi) for the Nesting Workbench."""

    def GetResources(self):
        return {
            'Pixmap': 'Nest_Icon', # Use the workbench icon
            'MenuText': 'Install Dependencies',
            'ToolTip': 'Installs required Python libraries (e.g., Taichi) for GPU acceleration.'
        }

    def Activated(self):
        """Executed when the command is run."""
        reply = QtGui.QMessageBox.question(
            FreeCADGui.getMainWindow(), 
            "Install Dependencies?", 
            "This will attempt to install the 'taichi' library using pip.\n\n"
            "This enables GPU acceleration for nesting.\n"
            "This requires an internet connection.\n\n"
            "Proceed?", 
            QtGui.QMessageBox.Yes | QtGui.QMessageBox.No
        )
        
        if reply == QtGui.QMessageBox.No:
            return
            
        # Create a progress dialog (simple version, as pip implementation blocks)
        progress = QtGui.QProgressDialog("Installing Taichi...", "Cancel", 0, 0, FreeCADGui.getMainWindow())
        progress.setWindowModality(QtCore.Qt.WindowModal)
        progress.show()
        QtGui.QApplication.processEvents()

        try:
            # sys.executable in FreeCAD often points to FreeCAD.exe.
            # We need the python.exe in the same directory (bin).
            import os
            bin_dir = os.path.dirname(sys.executable)
            python_exe = os.path.join(bin_dir, "python.exe")
            
            # Fallback if python.exe not found (e.g. Linux/Mac might be 'python' or different layout)
            if not os.path.exists(python_exe):
                 # Try to guess from sys.exec_prefix if typical python layout
                 python_exe = sys.executable 
                 # But if it is FreeCAD.exe, we can't use it for pip directly usually?
                 # Actually, FreeCAD's python environment is embedded. 
                 # Best bet: check for python.exe in bin. 
                 pass

            # Run pip install
            # Use --no-warn-script-location to avoid warnings about PATH
            subprocess.check_call([python_exe, "-m", "pip", "install", "taichi", "--no-warn-script-location"])
            
            progress.close()
            FreeCAD.Console.PrintMessage("Successfully installed taichi.\n")
            QtGui.QMessageBox.information(FreeCADGui.getMainWindow(), "Success", "Dependencies installed successfully!\nPlease restart FreeCAD.")
            
        except subprocess.CalledProcessError as e:
            progress.close()
            FreeCAD.Console.PrintError(f"Failed to install dependencies: {e}\n")
            QtGui.QMessageBox.critical(FreeCADGui.getMainWindow(), "Error", f"Failed to install dependencies.\nCheck the Report View for details.\n\nError: {e}")
        except Exception as e:
            progress.close()
            FreeCAD.Console.PrintError(f"Error installing dependencies: {e}\n")
            QtGui.QMessageBox.critical(FreeCADGui.getMainWindow(), "Error", f"An error occurred:\n{e}")

    def IsActive(self):
        """Always active."""
        return True

if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_InstallDependencies', InstallDependenciesCommand())
