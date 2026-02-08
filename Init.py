"""
This file initializes the Nesting workbench in FreeCAD.
It is executed when FreeCAD starts and registers the workbench.
"""

import sys
import os

path = os.path.dirname(__file__)
if path not in sys.path:
    sys.path.append(path)