# SPDX-License-Identifier: LGPL-2.1-or-later
"""Placement scoring from the Scoring weights (NLA rulings 24-28).

A scorer takes (pts, valid, ctx) and returns (best_idx, metric), or (None, inf) when no
candidate is valid; lower metric is better, and ties take the first candidate (ruling 19).
With no Contact and no Direction weight it is MinkowskiEngine.score_gravity unchanged.
FreeCAD-free. Never union placed parts or their NFPs here (AGENTS.md).
"""
from dataclasses import dataclass

import numpy as np

from .contact import CONTACT_TOL, direction_deviation, shared_fractions
from .fitness import FitnessWeights
from .minkowski_engine import MinkowskiEngine

_CHUNK = 32


@dataclass(frozen=True)
class ScoreContext:
    """What a scorer may read about the placement being scored."""
    direction: tuple      # gravity unit vector (gx, gy)
    part: object          # the Shape being placed
    angle: float
    sheet: object
    engine: object        # MinkowskiEngine
    extents: tuple        # rotated part's (min_x, min_y, max_x, max_y) relative to its centroid
    rotated_poly: object  # the part's spaced outline at angle (shapely)
    centroid: tuple       # rotated_poly's centroid (x, y)


def score_gravity(pts, valid, ctx):
    """Today's scorer: MinkowskiEngine.score_gravity on the gravity direction."""
    return MinkowskiEngine.score_gravity(pts, valid, ctx.direction)


def score_weighted(pts, valid, ctx, gravity, contact, direction, part_direction_deg):
    """(gravity·G + contact·(1 − C) + direction·D) / (gravity + contact + direction) (ruling 26).

    G is the gravity projection over the sheet's extent along the direction, C the shared
    outline fraction (contact.shared_fractions), D the deviation from part_direction_deg. C is
    computed best-first: candidates are taken by their G/D score, in chunks, and the scan stops
    once even C = 1 can't beat the best found, so the answer is exact.
    """
    total = gravity + contact + direction
    gx, gy = ctx.direction
    extent = abs(gx) * ctx.sheet.width + abs(gy) * ctx.sheet.height or 1.0
    partial = (gravity * (-(pts[:, 0] * gx + pts[:, 1] * gy)) / extent
               + direction * direction_deviation(ctx.angle, part_direction_deg)) / total
    partial = np.where(valid, partial, np.inf)
    if not len(partial) or not np.isfinite(partial).any():
        return None, float('inf')
    if contact <= 0:
        i = int(np.argmin(partial))
        return i, float(partial[i])
    cw = contact / total
    order = np.argsort(partial, kind='stable')
    best_i, best = None, float('inf')
    for start in range(0, len(order), _CHUNK):
        chunk = order[start:start + _CHUNK]
        chunk = chunk[np.isfinite(partial[chunk]) & (partial[chunk] <= best)]
        if not len(chunk):
            break
        c = shared_fractions(pts[chunk], ctx.rotated_poly, ctx.centroid, ctx.extents,
                             ctx.sheet, CONTACT_TOL)
        m = partial[chunk] + cw * (1.0 - c)
        for i, v in zip(chunk, m):
            if v < best or (v == best and i < best_i):
                best_i, best = int(i), float(v)
    return best_i, best


def make_scorer(weights=None):
    """The placement scorer for *weights* (FitnessWeights; None = defaults). Offcuts has no
    per-placement term; with Contact and Direction both 0 the scorer is score_gravity."""
    w = weights or FitnessWeights()
    if w.contact <= 0 and w.direction <= 0:
        return score_gravity
    g, c, d, target = w.gravity, w.contact, w.direction, w.part_direction_deg
    return lambda pts, valid, ctx: score_weighted(pts, valid, ctx, g, c, d, target)
