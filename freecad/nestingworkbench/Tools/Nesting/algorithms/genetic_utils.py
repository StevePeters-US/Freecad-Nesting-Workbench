# SPDX-License-Identifier: LGPL-2.1-or-later
import random
import copy


# This module runs inside worker processes and must import no FreeCAD.
# layout_manager installs the real sink at import time; workers fall back
# to warnings.warn so nothing is ever swallowed silently.
_warning_sink = None


def set_warning_logger(fn):
    global _warning_sink
    _warning_sink = fn


def _warn(msg):
    if _warning_sink is not None:
        _warning_sink(msg)
    else:
        import warnings
        warnings.warn(msg, RuntimeWarning)


UNPLACED_PENALTY_FACTOR = 10.0  # each unplaced part costs 10 sheet-areas of fitness


def unplaced_penalty(count, sheet_sizes):
    """Fitness cost of *count* unplaced regular parts: each costs
    UNPLACED_PENALTY_FACTOR of the LARGEST listed sheet's area, so leaving a
    part out never beats opening any sheet for it."""
    if count <= 0:
        return 0.0
    return count * max(w * h for w, h in sheet_sizes) * UNPLACED_PENALTY_FACTOR


def compute_layout_fitness(sheets, weights=None) -> tuple:
    """
    Calculates (fitness, efficiency_percent) for a placed layout's sheets.
    weights is a FitnessWeights; None takes FitnessWeights().
    """
    # Local import: fitness.py imports this module.
    from .fitness import FitnessWeights
    if weights is None:
        weights = FitnessWeights()
    if not sheets:
        return float('inf'), 0.0

    total_sheet_area = sum(s.width * s.height for s in sheets)
    total_parts_area = 0.0
    for sheet in sheets:
        for part in sheet.parts:
            if hasattr(part, 'shape') and part.shape:
                total_parts_area += part.shape.area

    efficiency = (total_parts_area / total_sheet_area * 100.0) if total_sheet_area > 0 else 0.0

    fitness = float(total_sheet_area)
    last_sheet = sheets[-1]
    if last_sheet.parts:
        min_x, min_y = float('inf'), float('inf')
        max_x, max_y = float('-inf'), float('-inf')
        found_valid = False

        for p in last_sheet.parts:
            try:
                bx, by, bw, bh = p.shape.bounding_box()
                min_x = min(min_x, bx)
                min_y = min(min_y, by)
                max_x = max(max_x, bx + bw)
                max_y = max(max_y, by + bh)
                found_valid = True
            except Exception as e:
                part_id = getattr(getattr(p, 'shape', None), 'id', 'unknown')
                _warn(f"[compute_layout_fitness] Bounding box failed for part '{part_id}': {e}")
                continue

        if found_valid:
            bbox_area = (max_x - min_x) * (max_y - min_y)
            w = weights
            total_w = w.gravity + w.contact + w.direction + w.offcut
            tie_break = 0.0
            if total_w > 0:
                largest_sheet_area = max(s.width * s.height for s in sheets)
                # Fractions first: 0.5 * a + 0.5 * b equals the old (a + b) / 2 exactly.
                if w.gravity > 0:
                    tie_break += (w.gravity / total_w) * bbox_area
                if w.offcut > 0:
                    # Local imports: offcuts/contact need numpy and shapely, which this module does not import.
                    from .offcuts import offcut_value
                    tie_break += (w.offcut / total_w) * (largest_sheet_area - offcut_value(sheets, w.min_offcut))
                if w.contact > 0:
                    from .contact import layout_contact_fraction
                    tie_break += (w.contact / total_w) * largest_sheet_area * (1.0 - layout_contact_fraction(sheets))
                if w.direction > 0:
                    from .contact import layout_direction_deviation
                    tie_break += (w.direction / total_w) * largest_sheet_area * layout_direction_deviation(sheets, w.part_direction_deg)
            fitness += tie_break

    return fitness, efficiency
