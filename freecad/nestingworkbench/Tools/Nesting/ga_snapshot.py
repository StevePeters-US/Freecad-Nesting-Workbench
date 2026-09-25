# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Pure-geometry snapshot structures and marshaling for GA population members.
Enables nesting population members in worker processes without FreeCAD dependencies.
"""
from dataclasses import dataclass
import time
import zlib
import random
import shapely
from shapely.affinity import rotate, translate
from .algorithms.nesting_strategy import Nester
from .algorithms.genetic_utils import compute_layout_fitness

UNPLACED_PENALTY_FACTOR = 10.0  # each unplaced part costs 10 sheet-areas of fitness


@dataclass(frozen=True)
class PartSnapshot:
    part_id: str          # was Shape.id
    type_label: str       # was source_freecad_object.Label — the NFP cache key field
    polygon_wkb: bytes    # shapely.to_wkb(shape.original_polygon)
    gene_angle: float | None  # None means "no gene — sweep all rotations"
    rotation_steps: int
    spacing: float
    deflection: float
    simplification: float
    fill_sheet: bool


@dataclass(frozen=True)
class MemberTask:
    member_idx: int
    generation: int
    rng_seed: int          # derived from (seed, generation, member_idx) for reproducible worker-process randomness
    parts: tuple           # of PartSnapshot, in chromosome order
    direction: tuple | None
    sheet_width: float
    sheet_height: float
    rotation_steps: int
    compactness_weight: float
    sort: bool = False


@dataclass(frozen=True)
class MemberResult:
    member_idx: int
    fitness: float
    efficiency: float
    sheet_count: int
    placements: tuple      # (part_id, sheet_index, x, y, angle)
    unplaced_ids: tuple
    genes: tuple
    elapsed: float


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
        if snap.gene_angle is not None:
            self.gene_angle = snap.gene_angle
            self._angle = snap.gene_angle
        else:
            self._angle = 0.0

        self.original_polygon = shapely.from_wkb(snap.polygon_wkb)
        if snap.gene_angle is not None and snap.gene_angle != 0.0:
            center = self.original_polygon.centroid
            self.polygon = rotate(self.original_polygon, snap.gene_angle, origin=center)
        else:
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


def snapshot_member(layout, ui_params: dict, gen: int, idx: int, seed: int,
                    search_direction=(0, -1)) -> MemberTask:
    """Snapshots one population layout into a FreeCAD-free MemberTask."""
    part_snapshots = []
    # Regular parts only for generations (fill deferred to winner)
    regular_parts = [p for p in layout.parts if not getattr(p, 'fill_sheet', False)]
    for p in regular_parts:
        # None and 0.0 are different: None means "no gene, sweep every
        # rotation"; 0.0 means "the chromosome pins this part to 0deg".
        # An `or` chain collapses them and silently kills the sweep.
        _gene = getattr(p, 'gene_angle', None)
        snap = PartSnapshot(
            part_id=p.id,
            type_label=getattr(p, 'type_label', None) or (p.source_freecad_object.Label if getattr(p, 'source_freecad_object', None) else "unknown"),
            polygon_wkb=shapely.to_wkb(p.original_polygon),
            gene_angle=None if _gene is None else float(_gene),
            rotation_steps=int(getattr(p, 'rotation_steps', 1) or 1),
            spacing=float(getattr(p, 'spacing', 0.0) or 0.0),
            deflection=float(getattr(p, 'deflection', 0.05) or 0.05),
            simplification=float(getattr(p, 'simplification', 1.0) or 1.0),
            fill_sheet=bool(getattr(p, 'fill_sheet', False)),
        )
        part_snapshots.append(snap)

    rng_seed = zlib.crc32(f"{seed}:{gen}:{idx}".encode())
    sort = False if getattr(layout, 'genes', None) else True

    return MemberTask(
        member_idx=idx,
        generation=gen,
        rng_seed=rng_seed,
        parts=tuple(part_snapshots),
        direction=(layout.direction if layout.direction is not None
                   else search_direction),
        sheet_width=float(ui_params.get('sheet_width', 300.0)),
        sheet_height=float(ui_params.get('sheet_height', 300.0)),
        rotation_steps=int(ui_params.get('rotation_steps', 1)),
        compactness_weight=float(ui_params.get('compactness_weight', 0.0)),
        sort=sort,
    )


def nest_from_snapshot(task: MemberTask) -> MemberResult:
    """Nests a single MemberTask in pure geometry (worker process or serial)."""
    t0 = time.perf_counter()
    shapes = [SnapshotShape(snap) for snap in task.parts]

    rng = random.Random(task.rng_seed)
    nester_kwargs = {
        'width': task.sheet_width,
        'height': task.sheet_height,
        'rotation_steps': task.rotation_steps,
        'quiet': True,
        'rng': rng,
    }
    nester_kwargs['search_direction'] = task.direction

    nester = Nester(**nester_kwargs)

    sheets, unplaced = nester.nest(shapes, sort=task.sort)

    fitness, efficiency = compute_layout_fitness(
        sheets, task.sheet_width, task.sheet_height, compactness_weight=task.compactness_weight
    )

    unplaced_regular = [p for p in unplaced if not getattr(p, 'fill_sheet', False)]
    if unplaced_regular:
        fitness += len(unplaced_regular) * task.sheet_width * task.sheet_height * UNPLACED_PENALTY_FACTOR

    placements = []
    for s_idx, s in enumerate(sheets):
        for p in s.parts:
            placements.append((p.shape.id, s_idx, p.x, p.y, p.angle))

    # Genes record the order the nester CONSUMED parts, not the input order.
    # Member 0 of every population nests with sort=True, so the nester picks
    # the order itself; an input-order chromosome then fails to reproduce its
    # own layout and every child bred from the champion inherits a genome that
    # nests worse than the parent it came from.
    gene_map = {p.shape.id: getattr(p.shape, '_angle', 0.0) for s in sheets for p in s.parts}
    gene_ids = {p.id for p in shapes if not getattr(p, 'fill_sheet', False)}
    angles = {p.id: getattr(p, '_angle', 0.0) for p in shapes}
    ordered = [pid for pid in getattr(nester, 'last_consumption_order', [])
               if pid in gene_ids]
    seen_ids = set(ordered)
    ordered += [p.id for p in shapes if p.id in gene_ids and p.id not in seen_ids]
    genes = tuple((pid, gene_map.get(pid, angles.get(pid, 0.0))) for pid in ordered)

    unplaced_ids = tuple(p.id for p in unplaced)
    elapsed = time.perf_counter() - t0

    return MemberResult(
        member_idx=task.member_idx,
        fitness=fitness,
        efficiency=efficiency,
        sheet_count=len(sheets),
        placements=tuple(placements),
        unplaced_ids=unplaced_ids,
        genes=genes,
        elapsed=elapsed,
    )


def apply_result(layout, result: MemberResult, ui_params: dict | None = None):
    """
    Main-thread only. Writes MemberResult back onto live FreeCAD Shape objects,
    reconstructing Sheet and PlacedPart instances.
    Replaces ga_coordinator.py:426-442.
    """
    from ...datatypes.sheet import Sheet
    from ...datatypes.placed_part import PlacedPart

    width = ui_params.get('sheet_width', 300.0) if ui_params else 300.0
    height = ui_params.get('sheet_height', 300.0) if ui_params else 300.0
    spacing = ui_params.get('spacing', 0.0) if ui_params else 0.0

    original_parts_map = {p.id: p for p in layout.parts}

    sheet_placements = {}
    for part_id, sheet_idx, x, y, angle in result.placements:
        sheet_placements.setdefault(sheet_idx, []).append((part_id, x, y, angle))

    missing = []
    sheets = []
    num_sheets = max(result.sheet_count, (max(sheet_placements.keys()) + 1) if sheet_placements else 0)
    for s_idx in range(num_sheets):
        sheet = Sheet(s_idx, width, height, spacing=spacing)
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
        sheets.append(sheet)

    if missing:
        import FreeCAD  # main-thread only; module stays FreeCAD-free for workers
        FreeCAD.Console.PrintWarning(
            f"[GA] {len(missing)} placement(s) had no matching part and were dropped: "
            f"{', '.join(map(str, missing[:5]))}{' …' if len(missing) > 5 else ''}\n")


    layout.sheets = sheets
    layout.unplaced = [p for p in layout.parts if p.id in result.unplaced_ids]
    layout.fitness = result.fitness
    layout.efficiency = result.efficiency
    layout.genes = list(result.genes)
