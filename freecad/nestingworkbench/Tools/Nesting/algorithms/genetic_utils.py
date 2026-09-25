# SPDX-License-Identifier: LGPL-2.1-or-later
import random
import copy

try:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False


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


def largest_open_area(parts, sheet_width, sheet_height):
    """
    Area of the largest contiguous free region on a sheet.

    Computed with Shapely: bin polygon minus the union of the placed
    part polygons (sheet-local coordinates); the largest resulting polygon's
    area is returned. Falls back to full sheet area when Shapely is unavailable
    or geometry is degenerate.
    """
    sheet_area = sheet_width * sheet_height
    if not SHAPELY_AVAILABLE:
        return sheet_area

    polygons = []
    for p in parts:
        try:
            poly = p.shape.polygon
            if poly is not None and not poly.is_empty:
                polygons.append(poly.buffer(0))
        except Exception as e:
            part_id = getattr(getattr(p, 'shape', None), 'id', 'unknown')
            _warn(f"[largest_open_area] Failed to extract polygon for part '{part_id}': {e}")
            continue
    if not polygons:
        return sheet_area

    bin_polygon = Polygon([(0, 0), (sheet_width, 0),
                           (sheet_width, sheet_height), (0, sheet_height)])
    try:
        free_space = bin_polygon.difference(unary_union(polygons))
    except Exception as e:
        _warn(f"[largest_open_area] Failed difference computation: {e}")
        return sheet_area

    if free_space.is_empty:
        return 0.0
    regions = getattr(free_space, 'geoms', [free_space])
    return max(region.area for region in regions)


def compute_layout_fitness(sheets, sheet_width, sheet_height, compactness_weight=0.0) -> tuple:
    """
    Calculates (fitness, efficiency_percent) for a placed layout's sheets.
    """
    if not sheets:
        return float('inf'), 0.0

    total_sheet_area = len(sheets) * sheet_width * sheet_height
    total_parts_area = 0.0
    for sheet in sheets:
        for part in sheet.parts:
            if hasattr(part, 'shape') and part.shape:
                total_parts_area += part.shape.area

    efficiency = (total_parts_area / total_sheet_area * 100.0) if total_sheet_area > 0 else 0.0

    fitness = len(sheets) * sheet_width * sheet_height
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
            tie_break = bbox_area
            if compactness_weight > 0:
                open_deficit = (sheet_width * sheet_height
                                - largest_open_area(last_sheet.parts,
                                                    sheet_width, sheet_height))
                tie_break = ((bbox_area + compactness_weight * open_deficit)
                             / (1.0 + compactness_weight))
            fitness += tie_break

    return fitness, efficiency

def tournament_selection(ranked_population, k=3, rng=None):
    """
    Selects a parent from the ranked population using tournament selection.
    ranked_population: list of (fitness, chromosome) tuples.
    """
    if rng is None:
        rng = random
    # Ensure ranking is sorted (best/lowest fitness first)
    # We select k random individuals
    if len(ranked_population) < k:
        k = len(ranked_population)
    
    participants = rng.sample(ranked_population, k)
    # The one with the lowest fitness score wins
    participants.sort(key=lambda x: x[0])
    return participants[0][1]

def crossover_genes(parent1_genes, parent2_genes, rng=None):
    """
    OX1 crossover on (part_id, angle) gene tuples.
    Copies a random slice from parent1 (preserving angles), fills remaining
    positions from parent2 in order. Returns a new list of tuples.
    """
    if rng is None:
        rng = random
    size = len(parent1_genes)
    if size == 0:
        return []
    if size == 1:
        return list(parent1_genes)

    child = [None] * size
    start, end = sorted(rng.sample(range(size), 2))

    child[start:end] = parent1_genes[start:end]
    child_ids = {gene[0] for gene in child if gene is not None}

    p2_idx = 0
    for i in range(size):
        if child[i] is None:
            while parent2_genes[p2_idx][0] in child_ids:
                p2_idx += 1
            child[i] = parent2_genes[p2_idx]
            child_ids.add(parent2_genes[p2_idx][0])
            p2_idx += 1

    return child

def mutate_genes(genes, mutation_rate, rotation_steps, rng=None):
    """
    Mutation on (part_id, angle) gene tuples. Returns a new list (not in-place).

    Operators:
    - Swap: exchange two random genes
    - Segment reversal: reverse a sub-sequence
    - Adjacent swap: swap two neighboring genes
    - Rotation: per gene, replace its angle with a random valid rotation step
    """
    if rng is None:
        rng = random
    if len(genes) < 1:
        return list(genes)

    genes = list(genes)

    if len(genes) >= 2:
        if rng.random() < mutation_rate:
            i, j = rng.sample(range(len(genes)), 2)
            genes[i], genes[j] = genes[j], genes[i]

        if rng.random() < mutation_rate * 0.5:
            start = rng.randint(0, len(genes) - 2)
            end = rng.randint(start + 1, len(genes))
            genes[start:end] = list(reversed(genes[start:end]))

        if rng.random() < mutation_rate * 0.3:
            i = rng.randint(0, len(genes) - 2)
            genes[i], genes[i + 1] = genes[i + 1], genes[i]

    if rotation_steps > 1:
        # Per gene, not once per chromosome. A single draw per child changes
        # exactly one angle whether the job has 5 parts or 500, which is far
        # too little angular diversity to search a real rotation space.
        step = 360.0 / rotation_steps
        for idx, (part_id, _angle) in enumerate(genes):
            if rng.random() < mutation_rate:
                genes[idx] = (part_id, rng.randrange(rotation_steps) * step)

    return genes
