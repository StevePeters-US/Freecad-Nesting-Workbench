from PySide import QtGui
import FreeCAD
import copy

from .algorithms import (
    genetic_nester,
    gravity_nester,
    minkowski_nester,
    sat_nester)

class NestingDependencyError(Exception):
    """Custom exception for missing optional dependencies like Shapely."""
    pass

try:
    # Check for shapely availability without importing specific functions
    import shapely
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

# --- Public Function ---
def nest(parts, width, height, rotation_steps=1, algorithm='Grid Fill', **kwargs):
    """Convenience function to run the nesting algorithm."""
    # If simulation is enabled, the nester needs the original list of parts
    # that are linked to the visible FreeCAD objects (fc_object).
    # If simulation is disabled, we MUST use a deepcopy to prevent the nester
    # from modifying the original part objects that the controller will use for
    # the final drawing step. This prevents state corruption.
    if 'update_callback' in kwargs:
        parts_to_process = parts # Use original list for simulation
    else:
        parts_to_process = copy.deepcopy(parts) # Use a copy for stability

    steps = 0
    sheets = []
    unplaced = []
    nester_class = {
        'Genetic': genetic_nester.GeneticNester,
        'Gravity': gravity_nester.GravityNester,
        'Minkowski': minkowski_nester.MinkowskiNester,
        'SAT': sat_nester.SatNester
    }.get(algorithm)

    if nester_class is None:
        raise NotImplementedError(f"The algorithm '{algorithm}' is not supported.")

    SHAPELY_ALGORITHMS = ['Genetic', 'Gravity', 'Minkowski', 'SAT']
    if algorithm in SHAPELY_ALGORITHMS and not SHAPELY_AVAILABLE:
        show_shapely_installation_instructions()
        raise NestingDependencyError("The selected algorithm requires the 'Shapely' library, which is not installed.")

    # The controller now passes a fresh list of all parts to be nested.
    # The nester algorithms are responsible for the full multi-sheet nesting run.
    nester = nester_class(width, height, rotation_steps, **kwargs)
    result = nester.nest(parts_to_process)
    # Some nesters may return a 3-tuple (sheets, unplaced, steps), while others
    # may return a 2-tuple (sheets, unplaced). We handle both cases here.
    if len(result) == 3:
        sheets, unplaced, steps = result
    else:
        sheets, unplaced = result

    return sheets, unplaced, steps

def show_shapely_installation_instructions():
    msg_box = QtGui.QMessageBox()
    msg_box.setIcon(QtGui.QMessageBox.Warning)
    msg_box.setWindowTitle("Shapely Library Not Found")
    msg_box.setText("The selected nesting algorithm requires the 'Shapely' library, but it is not installed.")
    msg_box.setInformativeText(
        "To use this algorithm, you need to install the 'shapely' library into FreeCAD's Python environment.\n\n"
        "1. **Find FreeCAD's Python Executable:**\n"
        "   Open the Python console in FreeCAD and run:\n"
        "   `import sys; print(sys.executable)`\n"
        "   Copy the path that is printed.\n\n"
        "2. **Open a Command Prompt:**\n"
        "   Open a Windows Command Prompt (cmd.exe).\n\n"
        "3. **Install Shapely:**\n"
        "   In the command prompt, use the path you copied to run the following command (don't forget the quotes):\n"
        "   `\"<path_to_python_exe>\" -m pip install shapely`\n\n"
        "After installation, please restart FreeCAD."
    )
    msg_box.setStandardButtons(QtGui.QMessageBox.Ok)
    msg_box.exec_()
