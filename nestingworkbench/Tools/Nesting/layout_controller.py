# Nesting/nesting/layout_controller.py

"""
This module contains the LayoutController class, which is responsible for managing
and drawing a complete nesting layout, including both final and preview states.
"""

# Standard library imports
import copy
import math

# FreeCAD imports
import FreeCAD
import Part

# Local application/library specific imports
from Nesting.nestingworkbench.datatypes.shape_object import create_shape_object
from Nesting.nestingworkbench.datatypes.sheet_object import create_sheet
from Nesting.nestingworkbench.datatypes.label_object import create_label_object

try:
    from shapely.affinity import translate
except ImportError:
    translate = None

try:
    import Draft
except ImportError:
    Draft = None

class LayoutController:
    """
    Manages the state and representation of a nested layout, including all
    sheets and the parts placed on them.
    """
    def __init__(self, obj):
        """
        This is now the constructor for the scripted object proxy.
        It's called when a new LayoutObject is created.
        """
        obj.Proxy = self
        self.obj = obj
        self.sheets = []
        self.master_shapes = []
        self.ui_params = {}
        self.unplaced_parts = []

    def setup(self, sheets, ui_params, master_shapes, unplaced_parts):
        """A method to pass run-time data to the proxy before execution."""
        self.sheets = sheets
        self.ui_params = ui_params
        self.master_shapes = master_shapes
        self.unplaced_parts = unplaced_parts

    def calculate_sheet_fills(self):
        """Calculates the fill percentage for each sheet in the layout."""
        return [sheet.calculate_fill_percentage() for sheet in self.sheets]

    def _create_layout_group(self):
        """
        Creates or finds the main layout group and populates it with properties
        and parameters from the nesting run.
        """
        parent_group = self.obj # The scripted object itself is the group
        parent_group.addProperty("App::PropertyBool", "IsStacked", "Nesting").IsStacked = False
        parent_group.addProperty("App::PropertyMap", "OriginalPlacements", "Nesting")

        # Store parameters directly on the layout group object
        parent_group.addProperty("App::PropertyFloat", "SheetWidth", "Nesting").SheetWidth = self.ui_params.get('sheet_w', 0)
        parent_group.addProperty("App::PropertyFloat", "SheetHeight", "Nesting").SheetHeight = self.ui_params.get('sheet_h', 0)
        parent_group.addProperty("App::PropertyFloat", "PartSpacing", "Nesting").PartSpacing = self.ui_params.get('spacing', 0)
        parent_group.addProperty("App::PropertyFile", "FontFile", "Nesting").FontFile = self.ui_params.get('font_path', '')

        # Store efficiencies in a PropertyMap
        sheet_fills = self.calculate_sheet_fills()
        if sheet_fills:
            parent_group.addProperty("App::PropertyMap", "SheetEfficiencies", "Nesting")
            efficiencies_map = {f"Sheet_{i+1}": f"{eff:.2f}%" for i, eff in enumerate(sheet_fills)}
            parent_group.SheetEfficiencies = efficiencies_map
        return parent_group

    def execute(self, fp):
        """Creates the final layout group and draws all sheets and their contents."""
        # fp is the feature python object (self.obj)
        parent_group = fp

        spacing = self.ui_params.get('spacing', 0)

        # Iterate through the sheets and delegate drawing to the Sheet object itself
        for sheet in self.sheets:
            # The logic is now unified: parts always exist in the 'PartsToPlace' group.
            # We need to calculate the sheet's origin before drawing.
            sheet_origin = sheet.get_origin(spacing)
            sheet.draw(
                fp.Document,
                sheet_origin,
                self.ui_params,
                parent_group
            )
        
        # The 'PartsToPlace' group is now empty and can be removed.
        parts_to_place_group = fp.getObject("PartsToPlace")
        if parts_to_place_group:
            fp.Document.removeObject(parts_to_place_group.Name)

    @property
    def doc(self):
        return self.obj.Document
