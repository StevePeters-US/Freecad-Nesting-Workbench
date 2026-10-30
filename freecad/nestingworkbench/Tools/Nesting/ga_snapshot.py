# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Pure-geometry snapshot structures and marshaling for GA population members.
Enables nesting population members in worker processes without FreeCAD dependencies.
"""
from dataclasses import dataclass
import time
import shapely
from shapely.affinity import rotate, translate
from .algorithms.placers import make_placer
from .algorithms.search_genome import Genome, placer_kwargs
from .algorithms.fitness import FitnessWeights, score_layout, weights_from_params
from .algorithms.minkowski_engine import DEFAULT_CANDIDATE_SPACING
from freecad.nestingworkbench import nw_logger


from .algorithms import sheet_sequence


@dataclass(frozen=True)
class PartSnapshot:
    part_id: str          # was Shape.id
    type_label: str       # was source_freecad_object.Label — the NFP cache key field
    polygon_wkb: bytes    # shapely.to_wkb(shape.original_polygon)
    rotation_steps: int
    spacing: float
    deflection: float
    simplification: float
    fill_sheet: bool


@dataclass(frozen=True)
class MemberTask:
    member_idx: int
    generation: int
    parts: tuple           # of PartSnapshot (regular parts only)
    algorithm: str         # ALGO_GREEDY or ALGO_BEAM
    genome: Genome         # the member's placer parameters
    fixed_kwargs: tuple    # sorted (key, value) pairs of the non-gene placer settings
    sheet_sizes: tuple     # of (w, h) tuples
    rotation_steps: int
    fitness_weights: FitnessWeights   # the panel's Fitness weights (NLA ruling 14)
    candidate_spacing: float
    stream: bool = False


@dataclass(frozen=True)
class MemberResult:
    member_idx: int
    fitness: float
    efficiency: float
    sheet_count: int
    placements: tuple      # (part_id, sheet_index, x, y, angle)
    unplaced_ids: tuple
    elapsed: float
    sheet_specs: tuple = ()  # one spec_index per output sheet, in sheet order
    diagnostics: tuple = ()  # (level, line) logged in the worker; nw_logger.drain_worker_messages


class SnapshotShape:
    """Pure-geometry Shape stand-in reconstructed from PartSnapshot for worker nesting."""

    def __init__(self, snap: PartSnapshot):
        self.id = snap.part_id
        self.type_label = snap.type_label
        self.rotation_steps = snap.rotation_steps
        self.spacing = snap.spacing
        self.deflection = snap.deflection
        self.simplification = snap.simplification
        self.fill_sheet = snap.fill_sheet
        self._angle = 0.0
        self.original_polygon = shapely.from_wkb(snap.polygon_wkb)
        self.polygon = self.original_polygon
        self.source_freecad_object = None

    @property
    def area(self):
        return self.polygon.area if self.polygon else 0.0

    @property
    def centroid(self):
        return self.polygon.centroid if self.polygon else None

    @property
    def angle(self):
        return self._angle

    def set_rotation(self, angle, reposition=True):
        if self.original_polygon:
            if reposition:
                min_x, min_y, _, _ = self.bounding_box()
            self._angle = angle
            center = self.original_polygon.centroid
            self.polygon = rotate(self.original_polygon, angle, origin=center)
            if reposition:
                self.move_to(min_x, min_y)

    def move(self, dx, dy):
        if self.polygon:
            self.polygon = translate(self.polygon, xoff=dx, yoff=dy)

    def move_to(self, x, y):
        if self.polygon:
            min_x, min_y, _, _ = self.bounding_box()
            self.move(x - min_x, y - min_y)

    def bounding_box(self):
        if not self.polygon:
            return (0.0, 0.0, 0.0, 0.0)
        min_x, min_y, max_x, max_y = self.polygon.bounds
        return min_x, min_y, max_x - min_x, max_y - min_y

    def get_final_placement(self, origin=(0, 0, 0)):
        return None


def snapshot_member(layout, ui_params: dict, gen: int, idx: int,
                    fixed_kwargs=(),
                    candidate_spacing=DEFAULT_CANDIDATE_SPACING,
                    stream: bool = False) -> MemberTask:
    """Snapshots one population layout into a FreeCAD-free MemberTask."""
    part_snapshots = []
    # Regular parts only for generations (fill deferred to winner)
    regular_parts = [p for p in layout.parts if not getattr(p, 'fill_sheet', False)]
    for p in regular_parts:
        snap = PartSnapshot(
            part_id=p.id,
            type_label=getattr(p, 'type_label', None) or (p.source_freecad_object.Label if getattr(p, 'source_freecad_object', None) else "unknown"),
            polygon_wkb=shapely.to_wkb(p.original_polygon),
            rotation_steps=int(getattr(p, 'rotation_steps', 1) or 1),
            spacing=float(getattr(p, 'spacing', 0.0) or 0.0),
            deflection=float(getattr(p, 'deflection', 0.05) or 0.05),
            simplification=float(getattr(p, 'simplification', 1.0) or 1.0),
            fill_sheet=bool(getattr(p, 'fill_sheet', False)),
        )
        part_snapshots.append(snap)

    return MemberTask(
        member_idx=idx,
        generation=gen,
        parts=tuple(part_snapshots),
        algorithm=ui_params['algorithm'],
        genome=layout.genome,
        fixed_kwargs=tuple(sorted(dict(fixed_kwargs).items())),
        sheet_sizes=tuple(sheet_sequence.sizes(ui_params['sheet_sequence'])),
        rotation_steps=int(ui_params.get('rotation_steps', 1)),
        fitness_weights=weights_from_params(ui_params),
        candidate_spacing=float(candidate_spacing),
        stream=stream,
    )


def nest_from_snapshot(task: MemberTask, placement_sink=None) -> MemberResult:
    """Nests a single MemberTask in pure geometry (worker process or serial).
    placement_sink(msg) receives (generation, member_idx, sheet_index, part_id, x, y, angle) after each placement."""
    t0 = time.perf_counter()
    shapes = [SnapshotShape(snap) for snap in task.parts]

    nester_kwargs = {
        **dict(task.fixed_kwargs),
        **placer_kwargs(task.genome),
        'rotation_steps': task.rotation_steps,
        'candidate_spacing': task.candidate_spacing,
        'fitness_weights': task.fitness_weights,
        'quiet': True,
    }

    nester = make_placer(task.algorithm, list(task.sheet_sizes), **nester_kwargs)
    if placement_sink is not None:
        def publish(part, sheet):
            c = part.polygon.centroid
            placement_sink((task.generation, task.member_idx, sheet.id,
                            part.id, c.x, c.y, part.angle))
        nester.update_callback = publish

    sheets, unplaced = nester.nest(shapes)

    unplaced_regular = [p for p in unplaced if not getattr(p, 'fill_sheet', False)]
    fitness, efficiency = score_layout(sheets, len(unplaced_regular), task.sheet_sizes,
                                       task.fitness_weights)

    placements = []
    for s_idx, s in enumerate(sheets):
        for p in s.parts:
            placements.append((p.shape.id, s_idx, p.x, p.y, p.angle))

    unplaced_ids = tuple(p.id for p in unplaced)
    elapsed = time.perf_counter() - t0

    return MemberResult(
        member_idx=task.member_idx,
        fitness=fitness,
        efficiency=efficiency,
        sheet_count=len(sheets),
        placements=tuple(placements),
        unplaced_ids=unplaced_ids,
        elapsed=elapsed,
        sheet_specs=tuple(s.spec_index for s in sheets),
    )


def apply_result(layout, result: MemberResult, ui_params: dict):
    """
    Main-thread only. Writes MemberResult back onto live FreeCAD Shape objects,
    reconstructing Sheet and PlacedPart instances.
    Replaces ga_coordinator.py:426-442.
    """
    from ...datatypes.sheet import Sheet
    from ...datatypes.placed_part import PlacedPart

    sizes = sheet_sequence.sizes(ui_params['sheet_sequence'])
    spacing = float(ui_params.get('spacing', 0.0))

    original_parts_map = {p.id: p for p in layout.parts}

    sheet_placements = {}
    for part_id, sheet_idx, x, y, angle in result.placements:
        sheet_placements.setdefault(sheet_idx, []).append((part_id, x, y, angle))

    missing = []
    sheets = []
    num_sheets = max(result.sheet_count, (max(sheet_placements.keys()) + 1) if sheet_placements else 0)
    for s_idx in range(num_sheets):
        spec_idx = result.sheet_specs[s_idx] if s_idx < len(result.sheet_specs) else s_idx
        w, h = sheet_sequence.row_for_index(sizes, spec_idx)
        sheet = Sheet(s_idx, w, h, spacing=spacing)
        sheet.spec_index = spec_idx
        sheets.append(sheet)

    sheet_sequence.assign_origins(sheets)

    for s_idx, sheet in enumerate(sheets):
        for part_id, x, y, angle in sheet_placements.get(s_idx, []):
            original_part = original_parts_map.get(part_id)
            if original_part is None:
                missing.append(part_id)
                continue
            original_part.set_rotation(angle, reposition=False)
            curr = original_part.polygon.centroid
            original_part.move(x - curr.x, y - curr.y)
            original_part.placement = original_part.get_final_placement(sheet.get_origin())
            placed = PlacedPart(original_part)
            sheet.add_part(placed)

    if missing:
        nw_logger.warn(
            f"[GA] {len(missing)} placement(s) had no matching part and were dropped: "
            f"{', '.join(map(str, missing[:5]))}{' …' if len(missing) > 5 else ''}")

    layout.sheets = sheets
    layout.unplaced = [p for p in layout.parts if p.id in result.unplaced_ids]
    layout.fitness = result.fitness
    layout.efficiency = result.efficiency
