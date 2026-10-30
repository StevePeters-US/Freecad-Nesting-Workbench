# SPDX-License-Identifier: LGPL-2.1-or-later
"""Iterative Beam Search nester for 2D irregular polygons.

Explores a decision tree of partial sheet layouts depth-by-depth (depth d = d
regular parts processed). At each depth, up to `beam_width` partial states
survive. Child states clone only the modified sheet via `clone_sheet_state`,
reusing both `Shape.nfp_cache` and the parent sheet's incremental candidate
cache (`_cand_cache`) from depth d-1.

Pure geometry module — must NEVER import FreeCAD.
"""

import copy
from dataclasses import dataclass
import math
import time

from ....datatypes.placed_part import PlacedPart
from .... import nw_logger
from .fitness import score_layout, score_partial
from . import sheet_sequence
from .minkowski_engine import (
    DEFAULT_CANDIDATE_SPACING,
    MinkowskiEngine,
    clone_sheet_state,
)
from .nesting_strategy import Nester, PlacementOptimizer


@dataclass
class _BeamNode:
    sheets: list
    remaining: tuple       # tuple of unplaced regular Shape objects
    unplaced: tuple        # tuple of regular Shape objects that fit no sheet
    consumed_ids: tuple    # tuple of (part_id, angle) in consumption order
    gravity_sum: float     # cumulative placement metric for tie-breaking
    score: tuple           # partial-layout score tuple (lower is better)


def _clone_part_at(part, angle, x, y):
    """Return a lightweight copy of *part* rotated to *angle* and moved to centroid (x, y)."""
    cls = part.__class__
    p = cls.__new__(cls)
    p.__dict__.update(part.__dict__)
    p.set_rotation(angle, reposition=False)
    curr = p.centroid
    p.move(x - curr.x, y - curr.y)
    return p


def _state_signature(sheets, unplaced):
    """Canonical deduplication key across all non-empty sheets so symmetric
    permutations of identical part types are deduplicated while distinct
    placements on earlier open sheets are preserved."""
    non_empty = [s for s in sheets if s.parts]
    if not non_empty:
        return (len(unplaced), 0, ())
    sheets_sig = tuple(
        (round(s.used_area, 1), frozenset(
            (getattr(pp.shape, 'type_label', None) or pp.shape.id,
             round(pp.x, 1), round(pp.y, 1), round(pp.angle % 360.0, 1))
            for pp in s.parts
        ))
        for s in non_empty
    )
    return (len(unplaced), len(non_empty), sheets_sig)


class BeamNester(Nester):
    """Level-by-level Beam Search nester with optional iterative widening."""

    def __init__(self, sheet_sizes, rotation_steps=1, **kwargs):
        self.beam_width = max(1, int(kwargs.pop("beam_width", 4)))
        self.width_decay = min(1.0, max(0.5, float(kwargs.pop("width_decay", 1.0))))
        # Per placement step widths from the GA genome (NLA ruling 12); empty = decay.
        self.step_widths = tuple(int(w) for w in kwargs.pop("step_widths", ()))
        self.iterative = bool(kwargs.pop("iterative", False))
        self.max_part_branches = max(1, int(kwargs.pop("max_part_branches", 2)))
        self.max_rot_branches = max(1, int(kwargs.pop("max_rot_branches", 3)))
        update_cb = kwargs.pop("update_callback", None)
        super().__init__(sheet_sizes, rotation_steps=rotation_steps, **kwargs)
        if update_cb is not None:
            self.update_callback = update_cb

    def nest(self, parts, sort=True):
        """Run single-pass or iterative anytime beam search on *parts*."""
        if self.beam_width == 1 and not self.iterative:
            return self._nest_standard(parts, sort=sort)

        all_parts = list(parts)
        regular_parts = [p for p in all_parts if getattr(p, 'fill_sheet', False) is not True]
        fill_parts = [p for p in all_parts if getattr(p, 'fill_sheet', False) is True]
        if sort:
            regular_parts.sort(key=lambda p: p.area, reverse=True)
            fill_parts.sort(key=lambda p: p.area, reverse=True)

        self.engine.reset_perf_stats()

        if not regular_parts:
            sheets, unplaced = [], []
            if fill_parts:
                self._nest_fill_parts(sheets, fill_parts, unplaced, self.quiet)
            sheets = sheet_sequence.compact_sheets(sheets)
            for sheet in sheets:
                for pp in sheet.parts:
                    pp.shape.placement = pp.shape.get_final_placement(sheet.get_origin())
            self.last_consumption_order = []
            return sheets, unplaced

        if self.iterative and self.beam_width > 1:
            widths = []
            w = 1
            while w < self.beam_width:
                widths.append(w)
                w *= 2
            widths.append(self.beam_width)
        else:
            widths = [self.beam_width]

        best_node = None
        best_fitness = float('inf')

        for pass_w in widths:
            if self.cancel_callback and self.cancel_callback():
                break
            p_branches = 1 if pass_w == 1 else self.max_part_branches
            r_branches = 1 if pass_w == 1 else min(self.max_rot_branches, max(1, self.optimizer.rotation_steps))
            node = self._run_beam_pass(regular_parts, pass_w, p_branches, r_branches)
            if node is None:
                # Cancelled mid-pass; keep best_node from earlier completed pass
                break
            compacted = sheet_sequence.compact_sheets([clone_sheet_state(s) for s in node.sheets])
            fit, _ = score_layout(compacted, len(node.unplaced), self.sheet_sizes, self.fitness_weights)
            if best_node is None or fit < best_fitness:
                best_fitness = fit
                best_node = node

        if best_node is None:
            return [], list(regular_parts)

        # Sync the winning placements back onto the caller's Shape instances
        parts_by_id = {p.id: p for p in regular_parts}
        final_sheets = sheet_sequence.compact_sheets(best_node.sheets)
        for sheet in final_sheets:
            for pp in sheet.parts:
                orig = parts_by_id.get(pp.shape.id)
                if orig is not None:
                    orig.set_rotation(pp.angle, reposition=False)
                    curr = orig.centroid
                    orig.move(pp.x - curr.x, pp.y - curr.y)
                    pp.shape = orig
                if self.update_callback:
                    self.update_callback(pp.shape, sheet)
                if self.part_end_callback:
                    self.part_end_callback(pp.shape, True)

        unplaced_parts = [parts_by_id.get(p.id, p) for p in best_node.unplaced]
        self.last_consumption_order = [pid for pid, _ in best_node.consumed_ids]

        was_cancelled = self.cancel_callback and self.cancel_callback()
        if fill_parts and not was_cancelled:
            self._nest_fill_parts(final_sheets, fill_parts, unplaced_parts, self.quiet)

        final_sheets = sheet_sequence.compact_sheets(final_sheets)
        for sheet in final_sheets:
            for placed_part in sheet.parts:
                placed_part.shape.placement = placed_part.shape.get_final_placement(sheet.get_origin())

        return final_sheets, unplaced_parts

    def _width_at(self, pass_width, depth):
        """Beam width after placing part *depth* (0-based) in a pass of *pass_width*.

        With per-step widths from the GA genome (NLA ruling 12), step *depth* keeps
        its own width, capped by the pass width so iterative passes still widen
        W=1, 2, 4...; past the end of the tuple the last width holds. Otherwise
        geometric decay: wide early, narrowing as the sheet fills; decay=1.0 keeps
        a constant width.
        """
        if self.step_widths:
            w = self.step_widths[min(depth, len(self.step_widths) - 1)]
            return max(1, min(pass_width, w))
        if self.width_decay >= 1.0:
            return pass_width
        return max(1, int(round(pass_width * self.width_decay ** depth)))

    def _run_beam_pass(self, regular_parts, beam_width, max_part_branches, max_rot_branches):
        """Run one beam search pass of width *beam_width*. Returns best _BeamNode, or None if cancelled."""
        root = _BeamNode(
            sheets=[],
            remaining=tuple(regular_parts),
            unplaced=(),
            consumed_ids=(),
            gravity_sum=0.0,
            score=(0, 0.0, 0.0, 0.0, 0.0),
        )
        beam = [root]
        total_parts = len(regular_parts)

        for depth in range(total_parts):
            if self.cancel_callback and self.cancel_callback():
                return None

            if not self.quiet and self.progress_callback:
                self.progress_callback(depth + 1, total_parts, f"Beam search (W={beam_width}) part {depth + 1}/{total_parts}...")

            next_beam = []
            seen_sigs = set()

            for state in beam:
                if not state.remaining:
                    next_beam.append(state)
                    continue

                # Select up to max_part_branches distinct part types from state.remaining
                candidate_indices = []
                seen_types = set()
                for idx, part in enumerate(state.remaining):
                    t_label = getattr(part, 'type_label', None) or part.id
                    if t_label not in seen_types:
                        seen_types.add(t_label)
                        candidate_indices.append(idx)
                        if len(candidate_indices) >= max_part_branches:
                            break

                for pick_idx in candidate_indices:
                    part = state.remaining[pick_idx]
                    next_remaining = state.remaining[:pick_idx] + state.remaining[pick_idx + 1:]
                    children = self._expand_part_on_state(state, part, next_remaining, max_rot_branches)
                    for child in children:
                        sig = _state_signature(child.sheets, child.unplaced)
                        if sig not in seen_sigs:
                            seen_sigs.add(sig)
                            next_beam.append(child)

            if not next_beam:
                return None

            next_beam.sort(key=lambda n: n.score)
            beam = next_beam[:self._width_at(beam_width, depth)]

        return beam[0] if beam else None

    def _expand_part_on_state(self, state, part, next_remaining, max_rot_branches):
        """Branch on placing *part* onto *state.sheets* (or opening a new sheet)."""

        # 1. Try existing sheets in order (matching Nester._nest_standard:272-284)
        for s_idx, sheet in enumerate(state.sheets):
            if (sheet.width * sheet.height - sheet.used_area) < part.area:
                continue
            top = self.optimizer.find_top_placements(part, sheet, max_results=max_rot_branches)
            if top:
                children = []
                for res in top:
                    new_sheet = clone_sheet_state(sheet)
                    placed_shape = _clone_part_at(part, res['angle'], res['x'], res['y'])
                    new_sheet.add_part(PlacedPart(placed_shape))
                    new_sheets = list(state.sheets)
                    new_sheets[s_idx] = new_sheet
                    g_sum = state.gravity_sum + res['metric']
                    children.append(_BeamNode(
                        sheets=new_sheets,
                        remaining=next_remaining,
                        unplaced=state.unplaced,
                        consumed_ids=state.consumed_ids + ((part.id, res['angle']),),
                        gravity_sum=g_sum,
                        score=score_partial(new_sheets, state.unplaced, g_sum),
                    ))
                return children

        # 2. Open new sheet(s) from the sequence (matching Nester._nest_standard:286-306)
        sheets_working = list(state.sheets)
        while True:
            new_sheet = sheet_sequence.open_sheet(sheets_working, self.sheet_sizes, self.spacing)
            top = self.optimizer.find_top_placements(part, new_sheet, max_results=max_rot_branches)
            if top:
                children = []
                for res in top:
                    branch_sheet = clone_sheet_state(new_sheet)
                    placed_shape = _clone_part_at(part, res['angle'], res['x'], res['y'])
                    branch_sheet.add_part(PlacedPart(placed_shape))
                    branch_sheets = list(sheets_working) + [branch_sheet]
                    g_sum = state.gravity_sum + res['metric']
                    children.append(_BeamNode(
                        sheets=branch_sheets,
                        remaining=next_remaining,
                        unplaced=state.unplaced,
                        consumed_ids=state.consumed_ids + ((part.id, res['angle']),),
                        gravity_sum=g_sum,
                        score=score_partial(branch_sheets, state.unplaced, g_sum),
                    ))
                return children

            if sheet_sequence.is_repeat_index(self.sheet_sizes, new_sheet.id):
                new_unplaced = state.unplaced + (part,)
                return [_BeamNode(
                    sheets=sheets_working,
                    remaining=next_remaining,
                    unplaced=new_unplaced,
                    consumed_ids=state.consumed_ids + ((part.id, getattr(part, 'angle', 0.0)),),
                    gravity_sum=state.gravity_sum,
                    score=score_partial(sheets_working, new_unplaced, state.gravity_sum),
                )]
            sheets_working.append(new_sheet)
