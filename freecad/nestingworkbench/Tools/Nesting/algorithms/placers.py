# SPDX-License-Identifier: LGPL-2.1-or-later
"""Placer factory shared by nest() and the GA worker processes.

Pure Python (no FreeCAD, no Qt), so a worker process can import it.
"""
from ....constants import ALGO_BEAM, ALGO_GREEDY
from .beam_nester import BeamNester
from .nesting_strategy import Nester


def make_placer(algorithm, sheet_sizes, rotation_steps=1, **kwargs):
    """Builds the placement algorithm named by *algorithm*.

    Raises ValueError, naming the value, for anything but ALGO_GREEDY / ALGO_BEAM.
    """
    if algorithm == ALGO_BEAM:
        return BeamNester(sheet_sizes, rotation_steps, **kwargs)
    if algorithm == ALGO_GREEDY:
        return Nester(sheet_sizes, rotation_steps, **kwargs)
    raise ValueError(f"Unknown placement algorithm {algorithm!r}")
