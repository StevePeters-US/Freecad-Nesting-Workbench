# SPDX-License-Identifier: LGPL-2.1-or-later
"""
LayoutManager - Handles creation, cloning, and management of Layout objects.

This class is responsible for:
- Creating new layouts with master shapes and part instances
- Cloning layouts for GA population members
- Deleting layouts and all their child objects
- Calculating layout efficiency

Separates layout management from the nesting algorithm for cleaner architecture.
"""

import copy
from .shape_preparer import ShapePreparer
from ...datatypes.shape import Shape
from ...freecad_helpers import recursive_delete
from freecad.nestingworkbench import nw_logger
from .algorithms.genetic_utils import set_warning_logger
from .algorithms.fitness import FitnessWeights, score_layout

set_warning_logger(nw_logger.warn)


class Layout:
    """
    Represents a single layout attempt (population member in GA).
    Contains references to the FreeCAD objects and the parts list.
    
    Attributes:
        genome: search_genome.Genome holding the placer parameters this member
                nests with. Every member nests the same parts.
    """
    def __init__(self, layout_group, parts_group, parts, master_shapes_group=None, label=None):
        self.layout_group = layout_group  # The Layout_xxx group object
        self.parts_group = parts_group    # The PartsToPlace group
        self.parts = parts                # List of Shape objects for nesting
        self.master_shapes_group = master_shapes_group
        self.sheets = []                  # Filled after nesting
        self.unplaced = []                # Parts nesting could not place
        self.fitness = float('inf')
        self.efficiency = 0.0
        self.genome = None                # search_genome.Genome; set by create_ga_population / breeding
        self.label = label                # name when the layout owns no document group
    
    @property
    def name(self):
        if self.layout_group:
            return self.layout_group.Label
        return self.label or "unknown"

class LayoutManager:
    """
    Manages layout creation, cloning, and deletion.
    Acts as a factory for Layout objects used in nesting.
    """
    
    def __init__(self, doc, processed_shape_cache=None):
        self.doc = doc
        self.processed_shape_cache = processed_shape_cache or {}
        self._layout_counter = 0
        self._template = None

    def _ensure_template(self, master_shapes_map, quantities, ui_params):
        """Builds the run's one set of document objects on first call, and returns it.

        Every population member nests the same parts; only order, rotation and
        search direction differ, and those live on the Shape objects. So the
        master shapes, part instances and boundaries are created once, here, and
        every member shares them (create_layout). Before this, each member built
        and deleted its own ~190 objects, which took about 95% of a GA run's
        main-thread time.

        The winner takes over the groups (adopt_template); a run without a
        winner deletes them (discard_template). Main thread only: it creates
        document objects. One LayoutManager serves one run, so later calls pass
        the same inputs and get the cached template back.
        """
        if self._template is not None:
            return self._template
        layout_group = self.doc.addObject("App::DocumentObjectGroup", "Layout_GA")
        layout_group.Label = "Layout_GA"
        if getattr(layout_group, "ViewObject", None) is not None:
            layout_group.ViewObject.Visibility = True
        parts_group = self.doc.addObject("App::DocumentObjectGroup", "PartsToPlace")
        layout_group.addObject(parts_group)
        preparer = ShapePreparer(self.doc, self.processed_shape_cache)
        parts = preparer.prepare_parts(
            ui_params, quantities, master_shapes_map, layout_group, parts_group)
        master_shapes_group = None
        for child in layout_group.Group:
            if child.Label == "MasterShapes":
                master_shapes_group = child
                break
        self._template = Layout(layout_group, parts_group, parts, master_shapes_group,
                                label="Layout_GA")
        return self._template

    def create_layout(self, name, master_shapes_map, quantities, ui_params, 
                      genome=None, member_idx: int = 0) -> Layout:
        """Creates a population member: its own Shape objects, sharing the run's document objects.
        
        Args:
            name: Name for the layout (e.g., "Layout_GA_1")
            master_shapes_map: Dict mapping labels to FreeCAD shape objects
            quantities: Dict mapping labels to (quantity, rotation_steps)
            ui_params: UI parameters dict
            genome: Optional search_genome.Genome this member nests with
            member_idx: Index of layout within population for deterministic tracking
            
        Returns:
            Layout object containing the layout group and prepared parts
        """
        template = self._ensure_template(master_shapes_map, quantities, ui_params)
        # Copies own their geometry, rotation and placement; the document
        # objects stay shared. __deepcopy__ drops fc_object, so re-bind it by id.
        # spawn_next (fill seeds) is a function, which deepcopy returns as is,
        # so every member's fill seed spawns through the template.
        parts = copy.deepcopy(template.parts)
        doc_objects = {p.id: p.fc_object for p in template.parts}
        for part in parts:
            part.fc_object = doc_objects.get(part.id)

        self._layout_counter += 1

        layout = Layout(None, None, parts, None, label=name)
        layout.member_idx = member_idx
        layout.genome = genome
        return layout
    
    def delete_layout(self, layout, verbose=False):
        """
        Removes a layout group and ALL its children from the document.
        Must recursively delete children first since FreeCAD doesn't do this automatically.
        
        Args:
            layout: Layout object to delete
            verbose: If True, log deletion
        """
        if not layout:
            return
            
        # Check if already deleted
        if hasattr(layout, '_deleted') and layout._deleted:
            return
        
        # Get the group object before we mark it deleted
        group_obj = None
        try:
            if layout.layout_group:
                group_obj = layout.layout_group
        except Exception as e:
            nw_logger.debug(f"[LayoutManager] Stale layout_group reference during cleanup: {e}")
        
        layout_label = layout.name if hasattr(layout, 'name') else "unknown"
        
        # Mark as deleted immediately to prevent re-entry
        layout._deleted = True
        layout.layout_group = None
        layout.sheets = []
        layout.parts = []
        
        # Recursively delete the group and all children
        if group_obj:
            recursive_delete(self.doc, group_obj)
            if verbose:
                nw_logger.info(f"  Deleted: {layout_label}")

    def adopt_template(self, layout):
        """Gives the run's document groups to the winning layout.

        layout's parts already point at the template's objects (create_layout),
        so after this the winner can be drawn and handed to NestingJob like a
        layout that built its own objects. Does nothing when no template exists,
        and leaves the layout's own groups alone in that case.
        """
        template = self._template
        if template is None:
            return
        self._template = None
        layout.layout_group = template.layout_group
        layout.parts_group = template.parts_group
        layout.master_shapes_group = template.master_shapes_group

    def discard_template(self):
        """Deletes the run's document objects when no layout adopted them.

        Main thread only. Safe to call when there is no template.
        """
        template = self._template
        self._template = None
        if template is not None and template.layout_group is not None:
            recursive_delete(self.doc, template.layout_group)

    def calculate_efficiency(self, layout, weights=None, n_unplaced=0, sheet_sizes=()) -> tuple:
        """Scores *layout* with the fitness model and stores fitness and efficiency on it.

        Args:
            layout: Layout object with sheets populated
            weights: FitnessWeights; None takes the defaults (bounding box only)
            n_unplaced: unplaced regular parts, each charged the unplaced penalty
            sheet_sizes: the run's (w, h) sizes; read only when n_unplaced > 0

        Returns:
            (fitness, efficiency_percent) tuple
        """
        fitness, efficiency = score_layout(
            layout.sheets, n_unplaced, sheet_sizes,
            weights if weights is not None else FitnessWeights())
        layout.fitness = fitness
        layout.efficiency = efficiency
        return fitness, efficiency
    
    def create_ga_population(self, master_shapes_map, quantities, ui_params, 
                             population_size, rotation_steps=1, verbose=False) -> list:
        """
        Creates a population of layouts for genetic algorithm.
        
        Args:
            master_shapes_map: Dict mapping labels to FreeCAD shape objects
            quantities: Dict mapping labels to (quantity, rotation_steps)
            ui_params: UI parameters dict
            population_size: Number of layouts to create
            rotation_steps: Unused; kept for call compatibility
            
        Returns:
            List of Layout objects with genome None; the coordinator assigns genomes.
        """
        population = []
        
        for i in range(population_size):
            name = f"Layout_GA_{i+1}"
            
            # Create the layout
            layout = self.create_layout(name, master_shapes_map, quantities, ui_params,
                                        genome=None, member_idx=i)
            
            population.append(layout)
            if verbose:
                nw_logger.info(f"Created layout {name} with {len(layout.parts)} parts")
        
        return population
    
