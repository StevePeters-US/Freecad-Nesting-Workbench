import random
import copy
import math
import FreeCAD
from .base_nester import BaseNester
from ....datatypes.sheet import Sheet
from ....datatypes.placed_part import PlacedPart
from shapely.ops import unary_union

# --- Genetic Packer ---
class GeneticNester(BaseNester):
    """
    A nester that uses a genetic algorithm to find an optimal layout.
    It evolves a population of solutions (part order and rotation) over
    several generations to minimize the used space.
    """
    def __init__(self, width, height, rotation_steps=1, **kwargs):
        super().__init__(width, height, rotation_steps, **kwargs)
        # --- Algorithm-specific parameters ---
        self.population_size = kwargs.get("population_size", 10)
        self.generations = kwargs.get("generations", 20)
        self.mutation_rate = kwargs.get("mutation_rate", 0.1)
        self.elite_size = max(1, int(self.population_size * 0.1)) # Keep the top 10%

    def nest(self, parts_to_place, update_callback=None):
        """
        Main genetic algorithm loop.
        """
        if not self.parts_to_place:
            return [], []

        population = self._create_initial_population()
        best_solution_overall = None

        for gen in range(self.generations):
            # --- 1. Evaluate Fitness ---
            # We calculate fitness for the entire population.
            # A solution is a tuple: (fitness_score, chromosome)
            ranked_population = []
            for chromosome in population:
                fitness = self._calculate_fitness(chromosome)
                ranked_population.append((fitness, chromosome))

            # Sort by fitness (lower is better)
            ranked_population.sort(key=lambda x: x[0])

            # Update the best solution found so far
            if best_solution_overall is None or ranked_population[0][0] < best_solution_overall[0]:
                best_solution_overall = ranked_population[0]
            
            FreeCAD.Console.PrintMessage(f"Generation {gen+1}/{self.generations}, Best Fitness: {best_solution_overall[0]:.2f}\n")

            # --- 2. Create Next Generation ---
            next_population = []

            # Elitism: Carry over the best individuals to the next generation
            elites = [sol[1] for sol in ranked_population[:self.elite_size]]
            next_population.extend(elites)

            # Create the rest of the new population through crossover and mutation
            while len(next_population) < self.population_size:
                parent1 = self._tournament_selection(ranked_population)
                parent2 = self._tournament_selection(ranked_population)
                offspring = self._crossover(parent1, parent2)
                self._mutate(offspring)
                next_population.append(offspring)

            population = next_population

        # --- Final Placement ---
        # After all generations, use the best chromosome found to generate the final layout.
        final_sheets, unplaced = self._placer(best_solution_overall[1])
        return final_sheets, unplaced

    def _create_initial_population(self):
        """Creates a population of random chromosomes."""
        population = []
        for _ in range(self.population_size):
            # A chromosome is a list of Part objects with rotation set.
            # We shuffle the order and assign random rotations.
            chromosome = [copy.deepcopy(p) for p in self.parts_to_place]
            random.shuffle(chromosome)
            for part in chromosome:
                if self.rotation_steps > 1:
                    angle = random.randrange(self.rotation_steps) * (360.0 / self.rotation_steps)
                    part.set_rotation(angle)
            population.append(chromosome)
        return population

    def _calculate_fitness(self, chromosome):
        """
        Calculates the fitness of a chromosome. Lower is better.
        Fitness is primarily determined by the number of sheets used, and
        secondarily by the compactness of the last sheet.
        """
        sheets, unplaced = self._placer(chromosome)
        if not sheets:
            return float('inf')

        # Main fitness component: number of sheets used.
        fitness = len(sheets) * self._bin_width * self._bin_height

        # Secondary component: bounding box area of the last sheet.
        last_sheet = sheets[-1]
        if last_sheet.parts:
            all_polys = [p.shape.get_polygon() for p in last_sheet.parts]
            bounds = unary_union(all_polys).bounds
            fitness += (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])

        # Penalty for unplaced parts
        if unplaced:
            fitness += len(unplaced) * self._bin_width * self._bin_height * 10 # Heavy penalty

        return fitness

    def _placer(self, chromosome):
        """
        A simple, deterministic placer function that lays out parts based on
        the order and rotation specified in a chromosome. This is a simplified
        version of the GridFillNester.
        """
        sheets = []
        unplaced_parts = []
        parts_to_place = [copy.deepcopy(p) for p in chromosome]

        while parts_to_place:
            part = parts_to_place.pop(0)
            placed = False
            for sheet in sheets:
                if self._try_place_on_sheet_placer(part, sheet):
                    placed = True
                    break
            if not placed:
                new_sheet = Sheet(len(sheets), self._bin_width, self._bin_height)
                if self._try_place_on_sheet_placer(part, new_sheet):
                    sheets.append(new_sheet)
                else:
                    unplaced_parts.append(part)
        return sheets, unplaced_parts

    def _try_place_on_sheet_placer(self, part, sheet):
        """A stateless grid-fill placement attempt for the fitness function."""
        cursor_x, cursor_y, row_height = 0, 0, 0
        if sheet.parts:
            last_part = sheet.parts[-1]
            last_bb = last_part.shape.bounding_box()
            cursor_y = last_bb[1]
            for p in reversed(sheet.parts):
                p_bb = p.shape.bounding_box()
                if abs(p_bb[1] - cursor_y) < 1e-6:
                    row_height = max(row_height, p_bb[3])
                    cursor_x = max(cursor_x, p_bb[0] + p_bb[2])
                else: break

        part_w, part_h = part.bounding_box()[2:]

        # Try current row
        if (cursor_x + part_w) <= self._bin_width and (cursor_y + part_h) <= self._bin_height:
            part.move_to(cursor_x, cursor_y)
            if sheet.is_placement_valid(part):
                sheet.add_part(PlacedPart(part))
                return True

        # Try new row
        if sheet.parts:
            new_row_y = cursor_y + row_height
            if (0 + part_w) <= self._bin_width and (new_row_y + part_h) <= self._bin_height:
                part.move_to(0, new_row_y)
                if sheet.is_placement_valid(part):
                    sheet.add_part(PlacedPart(part))
                    return True
        return False

    def _tournament_selection(self, ranked_population, k=3):
        """Selects a parent from the population using tournament selection."""
        tournament = random.sample(ranked_population, k)
        tournament.sort(key=lambda x: x[0])
        return tournament[0][1] # Return the chromosome of the winner

    def _crossover(self, parent1, parent2):
        """Performs ordered crossover (OX1) on the part order."""
        child_p = [None] * len(parent1)
        
        # Get part IDs for matching
        p1_ids = [p.id for p in parent1]
        p2_ids = [p.id for p in parent2]

        # Ensure random.sample has at least 2 elements to choose from
        if len(p1_ids) > 1:
            start, end = sorted(random.sample(range(len(p1_ids)), 2))
        else:
            start, end = 0, 1
        
        # Copy the slice from parent1
        child_p[start:end] = parent1[start:end]
        child_ids_set = {p.id for p in child_p if p is not None}

        # Fill the rest from parent2
        p2_idx = 0
        for i in range(len(child_p)):
            if child_p[i] is None:
                while parent2[p2_idx].id in child_ids_set:
                    p2_idx += 1
                child_p[i] = parent2[p2_idx]
                p2_idx += 1 # Increment index for the next iteration
        return child_p

    def _mutate(self, chromosome):
        """Applies mutation to a chromosome."""
        # Mutation 1: Swap two parts in the order
        if random.random() < self.mutation_rate:
            i, j = random.sample(range(len(chromosome)), 2)
            chromosome[i], chromosome[j] = chromosome[j], chromosome[i]

        # Mutation 2: Change the rotation of a random part
        if self.rotation_steps > 1 and random.random() < self.mutation_rate:
            part_to_mutate = random.choice(chromosome)
            new_angle = random.randrange(self.rotation_steps) * (360.0 / self.rotation_steps)
            part_to_mutate.set_rotation(new_angle)
