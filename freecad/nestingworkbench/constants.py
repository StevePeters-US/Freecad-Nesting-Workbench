# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Centralized FreeCAD property names and type strings for the Nesting Workbench.
Import these constants instead of using hardcoded strings.
"""

# -- FreeCAD Property Type Strings --
PROP_LENGTH = "App::PropertyLength"
PROP_FLOAT = "App::PropertyFloat"
PROP_BOOL = "App::PropertyBool"
PROP_INTEGER = "App::PropertyInteger"
PROP_FILE = "App::PropertyFile"
PROP_STRING = "App::PropertyString"

# -- Layout Property Names --
PROP_SHEET_WIDTH = "SheetWidth"
PROP_SHEET_HEIGHT = "SheetHeight"
PROP_PART_SPACING = "PartSpacing"
PROP_SHEET_THICKNESS = "SheetThickness"
PROP_DEFLECTION_ANGLE = "DeflectionAngle"
PROP_SIMPLIFICATION = "Simplification"
PROP_FONT_FILE = "FontFile"
PROP_SHOW_BOUNDS = "ShowBounds"
PROP_ADD_LABELS = "AddLabels"
PROP_LABEL_HEIGHT = "LabelHeight"
PROP_LABEL_SIZE = "LabelSize"
PROP_GLOBAL_ROTATION_STEPS = "GlobalRotationSteps"
PROP_GENERATIONS = "Generations"
PROP_POPULATION_SIZE = "PopulationSize"
PROP_NESTING_DIRECTION = "NestingDirection"
PROP_ALGORITHM = "Algorithm"

# -- Master-Container (per-part) Property Names --
PROP_PART_ROTATION_OVERRIDE = "PartRotationOverride"
PROP_PART_ROTATION_STEPS = "PartRotationSteps"
PROP_UP_DIRECTION = "UpDirection"
PROP_FILL_SHEET = "FillSheet"
PROP_QUANTITY = "Quantity"

# -- FreeCAD Preferences Path --
PREFS_PATH = "User parameter:BaseApp/Preferences/NestingWorkbench"

# -- Algorithm Presets --
# Rotation angle presets (degrees). Index 0 = coarsest, last = finest.
PHYSICS_ROTATION_PRESETS = [360, 90, 45, 30, 15, 10, 5, 2, 1]
MINKOWSKI_ROTATION_PRESETS = [360, 180, 120, 90, 45, 30, 15, 10, 5, 1]

# -- Document Label Prefixes --
LAYOUT_PREFIX = "Layout_"
SHEET_BOUNDARY_PREFIX = "Sheet_Boundary_"

