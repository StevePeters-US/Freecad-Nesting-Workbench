# SPDX-License-Identifier: LGPL-2.1-or-later
"""GA chromosome over placement-search parameters (NLA rulings 2026-10-28).

A genome is the parameter set one population member's placer runs with. It
never encodes part order or a pinned angle: every member nests the same
parts, sorted by its sort key. It does choose how many of each part type's
allowed rotations the placer tries (always a divisor of the allowed count, so
every tried angle stays on the precomputed NFP grid).
"""
import math
from dataclasses import dataclass, replace

from ....constants import ALGO_BEAM
from .nesting_strategy import DEFAULT_SORT_KEY, SORT_KEYS


@dataclass(frozen=True)
class GenomeBounds:
    """What the panel allows the GA to vary, and member 0's values."""
    algorithm: str
    direction_deg: float          # the dial (maths angle: 0 = right, 90 = up)
    evolve_direction: bool        # "Let the GA Vary Direction"
    detent_deg: float             # dial detent; quantises direction for dedup
    beam_width: int               # panel Beam Width: every step width's maximum
    width_decay: float            # panel Width Decay: member 0's width schedule
    n_steps: int                  # regular parts in the run = beam placement steps
    part_types: tuple = ()        # ((type_label, allowed_steps), ...) for regular parts, sorted


@dataclass(frozen=True)
class Genome:
    """Greedy: direction_deg + sort_key. Beam: widths (one per placement step).
    Both: rotations, one (type_label, steps) pair per part type, in bounds order.
    The other placer's fields stay None so a genome can't silently carry
    parameters its placer ignores."""
    direction_deg: float | None = None
    sort_key: str | None = None
    widths: tuple | None = None
    rotations: tuple = ()


def _divisors(n):
    """All divisors of *n*, ascending."""
    return [d for d in range(1, n + 1) if n % d == 0]


def _decay_schedule(b):
    """Member 0's per-step widths: max(1, round(W * decay^k))."""
    if b.width_decay >= 1.0:
        return (b.beam_width,) * b.n_steps
    return tuple(max(1, int(round(b.beam_width * b.width_decay ** k))) for k in range(b.n_steps))


def seed_genome(b: GenomeBounds) -> Genome:
    """Member 0: exactly the panel's values, so population 1 is a single run."""
    rotations = tuple((t, s) for t, s in b.part_types)
    if b.algorithm == ALGO_BEAM:
        return Genome(widths=_decay_schedule(b), rotations=rotations)
    return Genome(direction_deg=b.direction_deg, sort_key=DEFAULT_SORT_KEY, rotations=rotations)


def _random_gene(field, index, b, rng):
    """A fresh value for one gene, uniformly within its bounds (ruling 11)."""
    if field == "direction_deg":
        return rng.uniform(0.0, 360.0) if b.evolve_direction else b.direction_deg
    if field == "sort_key":
        return rng.choice(sorted(SORT_KEYS))
    if field == "widths":
        return rng.randint(1, b.beam_width)
    if field == "rotations":
        t, allowed = b.part_types[index]
        return (t, rng.choice(_divisors(allowed)))
    raise ValueError(field)


def _fields(b):
    """(field, length) for each gene group this placer uses; length None = scalar."""
    groups = [("rotations", len(b.part_types))]
    if b.algorithm == ALGO_BEAM:
        return [("widths", b.n_steps)] + groups
    return [("direction_deg", None), ("sort_key", None)] + groups


def random_genome(b: GenomeBounds, rng) -> Genome:
    """Every gene drawn uniformly within its bounds (generation 0, members 1...)."""
    values = {}
    for field, length in _fields(b):
        values[field] = (_random_gene(field, None, b, rng) if length is None
                         else tuple(_random_gene(field, i, b, rng) for i in range(length)))
    return Genome(**values)


def crossover(a: Genome, b_: Genome, rng) -> Genome:
    """Uniform crossover, gene by gene: each scalar, and each element of a vector
    gene, comes from parent A or parent B with equal chance (ruling 10)."""
    values = {}
    for field in Genome.__dataclass_fields__:
        va, vb = getattr(a, field), getattr(b_, field)
        if isinstance(va, tuple) and isinstance(vb, tuple) and len(va) == len(vb):
            values[field] = tuple(x if rng.random() < 0.5 else y for x, y in zip(va, vb))
        else:
            values[field] = va if rng.random() < 0.5 else vb
    return Genome(**values)


def mutate(g: Genome, rate: float, b: GenomeBounds, rng) -> Genome:
    """Replaces each gene, with probability *rate*, by a fresh random value within
    its bounds (ruling 11). Vector genes mutate element by element."""
    values = {}
    for field, length in _fields(b):
        cur = getattr(g, field)
        if length is None:
            values[field] = _random_gene(field, None, b, rng) if rng.random() < rate else cur
        else:
            values[field] = tuple(_random_gene(field, i, b, rng) if rng.random() < rate else v
                                  for i, v in enumerate(cur))
    return replace(g, **values)


def canonical(g: Genome, b: GenomeBounds) -> tuple:
    """Dedup key. Direction is quantised to the dial detent: members closer than
    that nest alike, and re-nesting one is wasted work."""
    step = b.detent_deg if b.detent_deg > 0 else 1.0
    if g.direction_deg is None:
        direction = None
    else:
        direction = round(g.direction_deg / step) % max(1, round(360.0 / step))
    return (direction, g.sort_key, g.widths, g.rotations)


def placer_kwargs(g: Genome) -> dict:
    """The kwargs nest() / the worker pass to the chosen placer for this genome."""
    kw = {'rotation_steps_by_type': g.rotations}
    if g.widths is not None:
        kw.update(beam_width=max(g.widths), step_widths=g.widths)
        return kw
    rad = math.radians(g.direction_deg)
    kw.update(search_direction=(math.cos(rad), math.sin(rad)), sort_key=g.sort_key)
    return kw


def describe(g: Genome) -> str:
    """One-line, human-readable genome for the verbose log."""
    rot = ", ".join(f"{t}:{s}" for t, s in g.rotations)
    if g.widths is not None:
        return f"widths {list(g.widths)}; rotations {rot}"
    return f"direction {g.direction_deg:.1f}°, sort {g.sort_key}; rotations {rot}"
