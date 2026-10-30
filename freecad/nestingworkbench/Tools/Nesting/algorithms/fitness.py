# SPDX-License-Identifier: LGPL-2.1-or-later
"""The one fitness model (NLA ruling 6): scores a whole layout for GA ranking and
Beam's pick between passes, and scores partial layouts for Beam's pruning.
Runs inside worker processes; imports no FreeCAD.
"""
import math
from dataclasses import dataclass

from .genetic_utils import compute_layout_fitness, unplaced_penalty

# mm², 0.1 m² (ruling 18)
MIN_OFFCUT_DEFAULT = 100_000.0


@dataclass(frozen=True)
class FitnessWeights:
    """The panel's Scoring weights (NLA rulings 25-28), in percent; only their ratios matter.
    gravity/contact/direction also score each placement (placement_scorers.make_scorer), and
    all four blend the layout tie-break. Sheet count and unplaced parts are not weighted: they
    always rank first. The defaults reproduce compute_layout_fitness(sheets).

    part_direction_deg is the Direction target; min_offcut is a threshold, not a weight: the
    smallest free piece (mm²) that counts as usable offcut (ruling 18).
    """
    gravity: float = 100.0
    contact: float = 0.0
    direction: float = 0.0
    offcut: float = 0.0
    part_direction_deg: float = 0.0
    min_offcut: float = MIN_OFFCUT_DEFAULT

    def __post_init__(self):
        for name in ('gravity', 'contact', 'direction', 'offcut'):
            v = getattr(self, name)
            if not (math.isfinite(v) and v >= 0.0):
                raise ValueError(f"FitnessWeights.{name} must be finite and >= 0, got {v!r}")
        if not math.isfinite(self.part_direction_deg):
            raise ValueError(f"FitnessWeights.part_direction_deg must be finite, got {self.part_direction_deg!r}")
        if not (math.isfinite(self.min_offcut) and self.min_offcut > 0.0):
            raise ValueError(f"FitnessWeights.min_offcut must be finite and > 0, got {self.min_offcut!r}")


def weights_from_params(params) -> FitnessWeights:
    """FitnessWeights from ui_params' 'gravity_weight', 'contact_weight', 'direction_weight',
    'offcut_weight', 'part_direction' and 'min_offcut'; a missing key takes the FitnessWeights
    default (gravity 100, the others 0)."""
    return FitnessWeights(gravity=float(params.get('gravity_weight', 100.0)),
                          contact=float(params.get('contact_weight', 0.0)),
                          direction=float(params.get('direction_weight', 0.0)),
                          offcut=float(params.get('offcut_weight', 0.0)),
                          part_direction_deg=float(params.get('part_direction', 0.0)),
                          min_offcut=float(params.get('min_offcut', MIN_OFFCUT_DEFAULT)))


def score_layout(sheets, n_unplaced, sheet_sizes, weights: FitnessWeights) -> tuple:
    """(fitness, efficiency_percent) of a whole layout; lower fitness is better.

    fitness = total area of the sheets used
              + (Σ_w w * term_w) / (Σ w) over w in gravity, contact, direction, offcut
              + unplaced_penalty(n_unplaced, sheet_sizes)

    The four terms are: gravity, the last sheet's bounding box area; contact, the last
    sheet's area times (1 - layout_contact_fraction); direction, the last sheet's area times
    layout_direction_deviation; offcut, A_max - offcut_value (offcuts.offcut_value). A_max
    is the largest sheet's area among sheets. The middle term is the tie-break: a weighted
    average of terms that are each at most A_max, so it never exceeds the largest sheet's
    area. With mixed sheet sizes the base term is the total area of the sheets used, not a
    count times one sheet's area. All weights 0 make the tie-break 0. sheet_sizes is read
    only when n_unplaced > 0.
    """
    fitness, efficiency = compute_layout_fitness(sheets, weights)
    if n_unplaced > 0:
        fitness += unplaced_penalty(n_unplaced, sheet_sizes)
    return fitness, efficiency


def _sheet_bbox_area(sheet):
    """Bounding-box area of all placed parts on *sheet*, or 0.0 when empty."""
    if not sheet.parts:
        return 0.0
    min_x, min_y = float('inf'), float('inf')
    max_x, max_y = float('-inf'), float('-inf')
    for pp in sheet.parts:
        bx, by, bw, bh = pp.shape.bounding_box()
        if bx < min_x:
            min_x = bx
        if by < min_y:
            min_y = by
        if bx + bw > max_x:
            max_x = bx + bw
        if by + bh > max_y:
            max_y = by + bh
    return max(0.0, (max_x - min_x) * (max_y - min_y))


def score_partial(sheets, unplaced, gravity_sum):
    """Lexicographic partial-layout score (lower is better).

    1. Fewest unplaced parts.
    2. Smallest total open sheet area (rewards fitting into an existing sheet
       before opening a new sheet).
    3. Largest total placed part area (-total_placed_area) so branches that
       place smaller parts on the same sheet do not prune branches that placed
       larger parts.
    4. Active-sheet packing: while establishing the first 1-2 parts on the
       active sheet (len < 3), rank by gravity_sum first so parts spread along
       the gravity edge; once >= 3 parts are on the sheet, rank by bounding-box
       area first to reward multi-part interlocking.

    Weights don't enter (NLA ruling 15): the Offcuts term is a whole-layout measure, so Beam
    prunes on the bounding box alone, and the weighted tie-break is the bounding box only.
    """
    non_empty = [s for s in sheets if s.parts]
    if not non_empty:
        return (len(unplaced), 0.0, 0.0, 0.0, float(gravity_sum))
    total_sheet_area = sum(s.width * s.height for s in non_empty)
    total_placed_area = sum(s.used_area for s in non_empty)
    last_sheet = non_empty[-1]
    last_bbox = _sheet_bbox_area(last_sheet)
    if len(last_sheet.parts) >= 3:
        return (len(unplaced), float(total_sheet_area), -float(total_placed_area), float(last_bbox), float(gravity_sum))
    return (len(unplaced), float(total_sheet_area), -float(total_placed_area), float(gravity_sum), float(last_bbox))
