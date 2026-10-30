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
PROP_VECTOR = "App::PropertyVector"

# -- Layout Property Names --
PROP_SHEET_SEQUENCE = "SheetSequence"
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
PROP_GA_WINNERS = "GAWinners"
PROP_MUTATION_RATE = "MutationRate"
PROP_WEIGHT_GRAVITY = "WeightGravity"
PROP_WEIGHT_CONTACT = "WeightContact"
PROP_WEIGHT_DIRECTION = "WeightDirection"
PROP_WEIGHT_OFFCUT = "WeightOffcut"
# (property, panel key) for the four Scoring weights (NLA ruling 30). The lock state
# of each is kept in prefs only, as prop + "Locked".
WEIGHT_PROPS = ((PROP_WEIGHT_GRAVITY, "gravity"), (PROP_WEIGHT_CONTACT, "contact"),
                (PROP_WEIGHT_DIRECTION, "direction"), (PROP_WEIGHT_OFFCUT, "offcut"))
PROP_PART_DIRECTION = "PartDirectionDeg"
PROP_FITNESS_MIN_OFFCUT = "FitnessMinOffcut"
PROP_BEAM_WIDTH = "BeamWidth"
PROP_MAX_PART_BRANCHES = "MaxPartBranches"
PROP_MAX_ROT_BRANCHES = "MaxRotBranches"
PROP_BEAM_ITERATIVE = "BeamIterative"
PROP_BEAM_WIDTH_DECAY = "BeamWidthDecay"
PROP_NESTING_DIRECTION = "NestingDirectionDeg"
PROP_CANDIDATE_SPACING = "CandidateSpacing"
PROP_ALGORITHM = "Algorithm"

# Values stored in PROP_ALGORITHM and carried as ui_params['algorithm']: the
# placement algorithm. The GA always wraps it (population 1 = a single run).
ALGO_GREEDY = "Greedy"
ALGO_BEAM = "Beam"

# -- Sheet-instance (Sheet_N group) Property Names --
# A Sheet_N records the library sheet it was nested on and a snapshot of that
# sheet's values, so a later library edit never changes a nested layout. It also
# carries PROP_SHEET_WIDTH / _HEIGHT / _THICKNESS. An empty library id means a
# Custom (typed-in) size, with no material and a cost of 0.
SHEET_INSTANCE_GROUP = "Sheet"          # property-editor group on Sheet_N
PROP_SHEET_LIBRARY_ID = "SheetLibraryId"
PROP_SHEET_LIBRARY_NAME = "SheetLibraryName"
PROP_SHEET_MATERIAL = "SheetMaterial"
PROP_SHEET_COST = "SheetCost"
PROP_SHEET_FLIP = "SheetFlip"

# -- Master-Container (per-part) Property Names --
PROP_PART_ROTATION_OVERRIDE = "PartRotationOverride"
PROP_PART_ROTATION_STEPS = "PartRotationSteps"
PROP_UP_VECTOR = "UpVector"
PROP_FILL_SHEET = "FillSheet"
PROP_QUANTITY = "Quantity"

# -- FreeCAD Preferences Path --
PREFS_PATH = "User parameter:BaseApp/Preferences/nestingworkbench"
PREF_CURRENCY_SYMBOL = "CurrencySymbol"
PREF_SHEET_SEQUENCE = "SheetSequence"  # the panel's sheet list (JSON)

# -- Algorithm Presets --
# Rotation angle presets (degrees). Index 0 = coarsest, last = finest.
MINKOWSKI_ROTATION_PRESETS = [360, 180, 120, 90, 45, 30, 15, 10, 5, 1]

# -- Document Label Prefixes --
LAYOUT_PREFIX = "Layout_"
SHEET_BOUNDARY_PREFIX = "Sheet_Boundary_"

# A flipped sheet's back side: a Back_<n> subgroup inside Sheet_<n> holding a
# Back_Boundary_<n> plane and one flip_<label> object per part, label and
# outline (flip_part_A, flip_label_A, flip_outline_nested_A).
BACK_GROUP_PREFIX = "Back_"
BACK_BOUNDARY_PREFIX = "Back_Boundary_"
FLIPPED_PREFIX = "flip_"
# Properties on the Back_<n> group, so a later refresh (Create Silhouette)
# rotates exactly as the draw did.
PROP_FLIP_AXIS = "FlipAxis"
PROP_FLIP_ANGLE = "FlipAngle"
PROP_FLIP_Z_MID = "FlipZMid"
# Turning a sheet over is a 180-degree rotation about the flip axis. Kept as a
# parameter: indexed rotary machining may use other angles later (SVF decision 7).
FLIP_ANGLE = 180.0
# Back-side boundary tint (RGB 0-1). A setting once NWS-010 lands.
BACK_SHEET_COLOR = (0.58, 0.50, 0.72)

# A sheet's offcuts (NLA ruling 20): an Offcuts_<n> subgroup inside Sheet_<n> holding one
# offcut_<n>_<k> plane per free rectangle. Display only: never exported or cut.
OFFCUT_GROUP_PREFIX = "Offcuts_"
OFFCUT_PREFIX = "offcut_"
OFFCUT_COLOR = (0.30, 0.75, 0.35)
OFFCUT_LINE_COLOR = (0.10, 0.45, 0.15)
OFFCUT_Z = 0.1  # mm above the sheet plane, so the tint doesn't z-fight it
PROP_SHOW_OFFCUTS = "ShowOffcuts"

# -- Simulation dropdown (NestingPanel) item indexes --
SIM_OFF = 0
SIM_SINGLE = 1
SIM_ALL = 2

# -- Tolerances --
# Part thickness vs sheet thickness match tolerance (read by CAMManager and NestingController)
THICKNESS_MATCH_TOL_MM = 0.01

