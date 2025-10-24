from enum import Enum

class VertState(Enum):
    """
    Represents the state of a vertex on a grid relative to a shape.
    """
    EMPTY = 0
    EDGE = 1
    FILLED = 2