import pytest
from unittest.mock import MagicMock
from nestingworkbench.Tools.Nesting.algorithms.genetic_utils import (
    create_random_chromosome,
    tournament_selection,
    ordered_crossover,
    mutate_chromosome
)

@pytest.fixture
def mock_parts():
    parts = []
    for i in range(5):
        p = MagicMock()
        p.id = f"part_{i}"
        p._angle = 0
        p.set_rotation = MagicMock()
        parts.append(p)
    return parts

def test_create_random_chromosome(mock_parts):
    # Should return a list of same length
    result = create_random_chromosome(mock_parts)
    assert len(result) == len(mock_parts)
    # IDs should match
    result_ids = {p.id for p in result}
    original_ids = {p.id for p in mock_parts}
    assert result_ids == original_ids

def test_tournament_selection():
    # ranked_population: [(fitness, chromosome)]
    # Lower fitness is better
    pop = [
        (10.0, "worse"),
        (1.0, "best"),
        (5.0, "mid")
    ]
    # k=2, should eventually pick the best if sample includes it
    # But let's test k=3 which is the full pop
    winner = tournament_selection(pop, k=3)
    assert winner == "best"

def test_ordered_crossover(mock_parts):
    p1 = mock_parts
    p2 = list(reversed(mock_parts))
    child = ordered_crossover(p1, p2)
    
    assert len(child) == len(p1)
    child_ids = [p.id for p in child]
    assert len(set(child_ids)) == len(p1) # All unique

def test_mutate_chromosome_swap(mock_parts):
    # Set high mutation rate to force a change
    chromo = list(mock_parts)
    original_order = [p.id for p in chromo]
    
    # We repeat until a swap happens (since it's random)
    # or just mock random.random()
    import random
    random.seed(42) # Deterministic for test
    
    mutate_chromosome(chromo, mutation_rate=1.0, rotation_steps=1)
    new_order = [p.id for p in chromo]
    
    # At least one operator should have fired
    assert new_order != original_order

def test_mutate_single_gene():
    # T-020: Mutation on single-gene chromosome should do nothing/not fail
    chromo = [MagicMock()]
    mutate_chromosome(chromo, mutation_rate=1.0, rotation_steps=4)
    assert len(chromo) == 1

def test_ordered_crossover_identical(mock_parts):
    # T-020: Crossover where both parents are identical
    p1 = mock_parts
    p2 = [p for p in mock_parts] # Shallow copy of list, same objects
    child = ordered_crossover(p1, p2)
    
    assert len(child) == len(p1)
    child_ids = [p.id for p in child]
    p1_ids = [p.id for p in p1]
    # Even with identical parents, the order might be different depending on crossover points
    # but the SET of IDs must be the same.
    assert set(child_ids) == set(p1_ids)

def test_tournament_selection_size_1():
    # T-020: Selection on a population of size 1
    pop = [(1.0, "only_one")]
    winner = tournament_selection(pop, k=3)
    assert winner == "only_one"
