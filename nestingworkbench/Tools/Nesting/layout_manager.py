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
    
    Attributes:
        genes: List of (part_id, angle) tuples representing the ordering and rotation
               of parts. Can be used to recreate the exact same layout.
    """
    def __init__(self, layout_group, parts_group, parts, master_shapes_group=None):
        self.layout_group = layout_group  # The Layout_xxx group object
        self.parts_group = parts_group    # The PartsToPlace group
        self.parts = parts                # List of Shape objects for nesting
        self.master_shapes_group = master_shapes_group
        self.sheets = []                  # Filled after nesting
        self.fitness = float('inf')
        self.efficiency = 0.0
        self.genes = []                   # (part_id, angle) tuples - the "DNA" of this layout
        self.contact_score = 0.0          # How much parts touch each other
    
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
        Removes a layout group and ALL its children from the document.
        Must recursively delete children first since FreeCAD doesn't do this automatically.
        
        Args:
            layout: Layout object to delete
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
        except:
            pass
        
        layout_label = layout.name if hasattr(layout, 'name') else "unknown"
        
        # Mark as deleted immediately to prevent re-entry
        layout._deleted = True
        layout.layout_group = None
        layout.sheets = []
        layout.parts = []
        
        # Recursively delete the group and all children
        if group_obj:
            self._recursive_delete(group_obj)
            FreeCAD.Console.PrintMessage(f"  Deleted: {layout_label}\n")
    
    def _recursive_delete(self, obj):
        """
        Recursively deletes an object and all its children.
        Must delete children first since FreeCAD doesn't cascade deletes.
        """
        if not obj:
            return
        
        try:
            obj_name = obj.Name
        except:
            return  # Object already deleted
        
        # First, recursively delete all children (if it's a group)
        if hasattr(obj, 'Group'):
            for child in list(obj.Group):  # Copy list to avoid modification during iteration
                self._recursive_delete(child)
        
        # Delete the object itself
        try:
            if self.doc.getObject(obj_name):
                self.doc.removeObject(obj_name)
        except:
            pass  # Already deleted
    
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
        
        # Contact score: reward parts that touch each other
        # Lower fitness = better, so we subtract contact bonus
        contact_bonus = self._calculate_contact_score(layout)
        fitness -= contact_bonus
        
        layout.fitness = fitness
        layout.efficiency = efficiency
        layout.contact_score = contact_bonus
        
        return fitness, efficiency
    
    def _calculate_contact_score(self, layout) -> float:
        """
        Calculate how much parts are in contact with each other.
        Higher score = more contact = better packing.
        
        Uses buffer/touches approach: if buffered polygon touches another, they're in contact.
        """
        try:
            from shapely.ops import unary_union
        except ImportError:
            return 0.0
        
        total_contact = 0.0
        buffer_distance = 0.5  # Small buffer to detect "almost touching"
        
        for sheet in layout.sheets:
            # Get parts that have a valid polygon (Shape.polygon, not bounds_polygon)
            parts = [p for p in sheet.parts if hasattr(p, 'shape') and p.shape and p.shape.polygon]
            
            for i, part_a in enumerate(parts):
                poly_a = part_a.shape.polygon
                if not poly_a or poly_a.is_empty:
                    continue
                buffered_a = poly_a.buffer(buffer_distance)
                
                for part_b in parts[i+1:]:
                    poly_b = part_b.shape.polygon
                    if not poly_b or poly_b.is_empty:
                        continue
                    
                    # Check if they touch or are very close
                    if buffered_a.intersects(poly_b):
                        # Calculate contact length (intersection of boundaries)
                        try:
                            intersection = buffered_a.intersection(poly_b)
                            if intersection.is_empty:
                                continue
                            # Use length of intersection boundary as contact score
                            if hasattr(intersection, 'length'):
                                total_contact += intersection.length
                            elif hasattr(intersection, 'area'):
                                # For area-based contact, use sqrt to normalize
                                total_contact += intersection.area ** 0.5
                        except:
                            # Simple fallback: just count the contact
                            total_contact += 10.0
        
        return total_contact
    
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
