# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/datatypes/shape_object.py

"""
This module defines a custom FreeCAD scripted object for representing a nested shape.
"""

import FreeCAD

class ShapeObject:
    """A scripted object representing a single nested shape."""

    def __init__(self, obj):
        """Called when a new object is created."""
        obj.Proxy = self
        obj.addProperty("App::PropertyBool", "ShowShape", "Display", "Toggle visibility of the 3D shape").ShowShape = True
        obj.addProperty("App::PropertyBool", "ShowBounds", "Display", "Toggle visibility of the 2D boundary").ShowBounds = False
        obj.addProperty("App::PropertyBool", "ShowLabel", "Display", "Toggle visibility of the label").ShowLabel = True
        obj.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "Link to the boundary wire object")
        obj.addProperty("App::PropertyLink", "LabelObject", "Nesting", "Link to the label text object")
        obj.ViewObject.Proxy = 0 # Use the default view provider

    def onChanged(self, fp, prop):
        """Called when a property changes."""
        if prop == "ShowShape":
            if hasattr(fp, "ViewObject"):
                fp.ViewObject.Visibility = fp.ShowShape
        elif prop == "ShowBounds":
            if hasattr(fp, "BoundaryObject") and fp.BoundaryObject and hasattr(fp.BoundaryObject, "ViewObject"):
                fp.BoundaryObject.ViewObject.Visibility = fp.ShowBounds
        elif prop == "ShowLabel":
            if hasattr(fp, "LabelObject") and fp.LabelObject and hasattr(fp.LabelObject, "ViewObject"):
                fp.LabelObject.ViewObject.Visibility = fp.ShowLabel

    def execute(self, fp):
        """Called on recompute. Does nothing for now as shape is set externally."""
        pass

class ViewProviderShape:
    """A view provider for the ShapeObject."""

    def __init__(self, vobj):
        """Called when the view object is created."""
        vobj.Proxy = self

    def attach(self, vobj):
        """Set up the view object's properties."""
        pass # Use default properties for now

def create_shape_object(name="Shape"):
    """Helper function to create a new ShapeObject in the active document."""
    doc = FreeCAD.ActiveDocument
    obj = doc.addObject("Part::FeaturePython", name)
    ShapeObject(obj)
    if FreeCAD.GuiUp:
        ViewProviderShape(obj.ViewObject)
    return obj