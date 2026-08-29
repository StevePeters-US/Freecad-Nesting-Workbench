# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/datatypes/placed_part.py

"""
This module contains the PlacedPart class, which represents the result of
a successful placement of a part by a nesting algorithm.
"""

class PlacedPart:
    """
    A data container that holds the final placement information for a part.
    It links the original part's geometry to its final position and orientation
    on a specific sheet.
    """
    def __init__(self, shape):
        self.shape = shape # The original Shape object
        centroid = shape.centroid
        self.x = centroid.x if centroid else 0.0
        self.y = centroid.y if centroid else 0.0
        self.angle = shape.angle

    def __repr__(self):
        return f"<PlacedPart: {self.shape.id}, pos=({self.x:.2f}, {self.y:.2f}), angle={self.angle}>"