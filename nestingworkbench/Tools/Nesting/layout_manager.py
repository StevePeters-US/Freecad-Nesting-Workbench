"""
LayoutManager - Handles creation, cloning, and management of Layout objects.

This class is responsible for:
- Creating new layouts with master shapes and part instances
- Cloning layouts for GA population members
- Deleting layouts and all their child objects
- Calculating layout efficiency

Separates layout management from the nesting algorithm for cleaner architecture.
"""

import FreeCAD
import copy
from .shape_preparer import ShapePreparer
from ...datatypes.shape import Shape


class Layout:
    """
    Represents a single layout attempt (population member in GA).
    Contains references to the FreeCAD objects and the parts list.
    """
    def __init__(self, layout_group, parts_group, parts, master_shapes_group=None):
        self.layout_group = layout_group  # The Layout_xxx group object
        self.parts_group = parts_group    # The PartsToPlace group
        self.parts = parts                # List of Shape objects for nesting
        self.master_shapes_group = master_shapes_group
        self.sheets = []                  # Filled after nesting
        self.fitness = float('inf')
        self.efficiency = 0.0
    
    @property
    def name(self):
        return self.layout_group.Label if self.layout_group else "unknown"


class LayoutManager:
    """
    Manages layout creation, cloning, and deletion.
    Acts as a factory for Layout objects used in nesting.
    """
    
    def __init__(self, doc, processed_shape_cache=None):
        self.doc = doc
        self.processed_shape_cache = processed_shape_cache or {}
        self._layout_counter = 0
    
    def create_layout(self, name, master_shapes_map, quantities, ui_params, 
                      clone_from=None, chromosome_ordering=None) -> Layout:
        """
        Creates a new layout with master shapes and part instances.
        
        Args:
            name: Name for the layout (e.g., "Layout_GA_1")
            master_shapes_map: Dict mapping labels to FreeCAD shape objects
            quantities: Dict mapping labels to (quantity, rotation_steps)
            ui_params: UI parameters dict
            clone_from: Optional Layout to clone masters from (for GA)
            chromosome_ordering: Optional list of (part_id, angle) tuples for ordering
            
        Returns:
            Layout object containing the layout group and prepared parts
        """
        # Create layout group
        layout_group = self.doc.addObject("App::DocumentObjectGroup", name)
        layout_group.Label = name
        if hasattr(layout_group, "ViewObject"):
            layout_group.ViewObject.Visibility = True
        
        # Create parts bin
        parts_group = self.doc.addObject("App::DocumentObjectGroup", "PartsToPlace")
        layout_group.addObject(parts_group)
        
        # Create shape preparer for this layout
        preparer = ShapePreparer(self.doc, self.processed_shape_cache)
        
        # Prepare parts (creates masters and instances)
        parts = preparer.prepare_parts(
            ui_params, quantities, master_shapes_map, 
            layout_group, parts_group
        )
        
        # Get master shapes group
        master_shapes_group = None
        for child in layout_group.Group:
            if child.Label == "MasterShapes":
                master_shapes_group = child
                break
        
        # Apply chromosome ordering if provided
        if chromosome_ordering and parts:
            parts = self._apply_ordering(parts, chromosome_ordering)
        
        self._layout_counter += 1
        
        return Layout(layout_group, parts_group, parts, master_shapes_group)
    
    def _apply_ordering(self, parts, chromosome_ordering):
        """
        Reorders and rotates parts according to a chromosome.
        
        Args:
            parts: List of Shape objects
            chromosome_ordering: List of (part_id, angle) tuples
            
        Returns:
            Reordered list of Shape objects with rotations applied
        """
        if not chromosome_ordering:
            return parts
        
        # Build a map of part id -> part
        parts_map = {p.id: p for p in parts}
        
        ordered_parts = []
        for part_id, angle in chromosome_ordering:
            if part_id in parts_map:
                part = parts_map[part_id]
                if angle is not None:
                    part.set_rotation(angle)
                ordered_parts.append(part)
        
        return ordered_parts
    
    def delete_layout(self, layout):
        """
        Removes a layout and all its child objects from the document.
        
        Args:
            layout: Layout object to delete
        """
        if not layout or not layout.layout_group:
            return
            
        try:
            # Recursively delete all children
            self._recursive_delete(layout.layout_group)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Error deleting layout: {e}\n")
    
    def _recursive_delete(self, obj):
        """Recursively deletes an object and all its children."""
        if hasattr(obj, "Group"):
            # Delete children first
            for child in list(obj.Group):
                self._recursive_delete(child)
        
        # Delete linked objects (like BoundaryObject)
        if hasattr(obj, "BoundaryObject") and obj.BoundaryObject:
            try:
                self.doc.removeObject(obj.BoundaryObject.Name)
            except:
                pass
        
        # Delete the object itself
        try:
            self.doc.removeObject(obj.Name)
        except Exception as e:
            pass
    
    def calculate_efficiency(self, layout, sheet_width, sheet_height) -> tuple:
        """
        Calculates the packing efficiency of a layout.
        
        Args:
            layout: Layout object with sheets populated
            sheet_width: Width of each sheet
            sheet_height: Height of each sheet
            
        Returns:
            (fitness, efficiency_percent) tuple
        """
        if not layout.sheets:
            return float('inf'), 0.0
        
        # Calculate total parts area
        total_parts_area = 0
        for sheet in layout.sheets:
            for part in sheet.parts:
                if hasattr(part, 'shape') and part.shape:
                    total_parts_area += part.shape.area
        
        # Calculate total sheet area
        total_sheet_area = len(layout.sheets) * sheet_width * sheet_height
        
        # Efficiency percentage
        efficiency = (total_parts_area / total_sheet_area) * 100 if total_sheet_area > 0 else 0
        
        # Fitness: lower is better
        # Prioritize fewer sheets, then tighter bounding box
        fitness = len(layout.sheets) * sheet_width * sheet_height
        
        # Add bounding box of last sheet
        last_sheet = layout.sheets[-1]
        if last_sheet.parts:
            try:
                min_x = min(p.shape.bounding_box()[0] for p in last_sheet.parts)
                min_y = min(p.shape.bounding_box()[1] for p in last_sheet.parts)
                max_x = max(p.shape.bounding_box()[0] + p.shape.bounding_box()[2] for p in last_sheet.parts)
                max_y = max(p.shape.bounding_box()[1] + p.shape.bounding_box()[3] for p in last_sheet.parts)
                fitness += (max_x - min_x) * (max_y - min_y)
            except:
                pass
        
        layout.fitness = fitness
        layout.efficiency = efficiency
        
        return fitness, efficiency
    
    def create_ga_population(self, master_shapes_map, quantities, ui_params, 
                             population_size, rotation_steps=1) -> list:
        """
        Creates a population of layouts for genetic algorithm.
        
        Args:
            master_shapes_map: Dict mapping labels to FreeCAD shape objects
            quantities: Dict mapping labels to (quantity, rotation_steps)
            ui_params: UI parameters dict
            population_size: Number of layouts to create
            rotation_steps: Number of rotation steps for random rotations
            
        Returns:
            List of Layout objects
        """
        import random
        
        population = []
        
        for i in range(population_size):
            name = f"Layout_GA_{i+1}"
            
            # Create the layout
            layout = self.create_layout(name, master_shapes_map, quantities, ui_params)
            
            if layout.parts and i > 0:  # First layout keeps original ordering
                # Shuffle the parts order
                random.shuffle(layout.parts)
                
                # Apply random rotations
                if rotation_steps > 1:
                    for part in layout.parts:
                        angle = random.randrange(rotation_steps) * (360.0 / rotation_steps)
                        part.set_rotation(angle)
            
            population.append(layout)
            FreeCAD.Console.PrintMessage(f"Created layout {name} with {len(layout.parts)} parts\n")
        
        return population
    
    def select_elite(self, layouts, elite_count) -> list:
        """
        Selects the best layouts based on fitness.
        
        Args:
            layouts: List of Layout objects (should be sorted by fitness already)
            elite_count: Number of best layouts to keep
            
        Returns:
            List of elite Layout objects
        """
        # Sort by fitness (lower is better)
        sorted_layouts = sorted(layouts, key=lambda l: l.fitness)
        return sorted_layouts[:elite_count]
    
    def cleanup_worst(self, layouts, keep_count):
        """
        Deletes the worst layouts, keeping only the top performers.
        
        Args:
            layouts: List of Layout objects
            keep_count: Number of best layouts to keep
        """
        # Sort by fitness (lower is better)
        sorted_layouts = sorted(layouts, key=lambda l: l.fitness)
        
        # Delete layouts beyond keep_count
        for layout in sorted_layouts[keep_count:]:
            FreeCAD.Console.PrintMessage(f"Deleting layout {layout.name} (efficiency: {layout.efficiency:.1f}%)\n")
            self.delete_layout(layout)
