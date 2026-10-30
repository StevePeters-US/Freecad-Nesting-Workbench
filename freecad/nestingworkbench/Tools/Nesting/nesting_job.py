# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
from ...freecad_helpers import recursive_delete
from ...constants import (
    PROP_ADD_LABELS, PROP_ALGORITHM, PROP_BEAM_ITERATIVE, PROP_BEAM_WIDTH, PROP_BEAM_WIDTH_DECAY, PROP_BOOL, PROP_CANDIDATE_SPACING, PROP_DEFLECTION_ANGLE, PROP_FILE, PROP_FLOAT, PROP_FITNESS_MIN_OFFCUT, WEIGHT_PROPS, PROP_PART_DIRECTION,
    PROP_FONT_FILE, PROP_GENERATIONS, PROP_GLOBAL_ROTATION_STEPS, PROP_INTEGER, PROP_LABEL_HEIGHT, PROP_LABEL_SIZE,
    PROP_LENGTH, PROP_MAX_PART_BRANCHES, PROP_MAX_ROT_BRANCHES, PROP_NESTING_DIRECTION, PROP_PART_SPACING, PROP_POPULATION_SIZE, PROP_GA_WINNERS, PROP_MUTATION_RATE, PROP_SHEET_HEIGHT,
    PROP_SHEET_SEQUENCE, PROP_SHEET_THICKNESS, PROP_SHEET_WIDTH, PROP_SHOW_BOUNDS, PROP_SHOW_OFFCUTS, PROP_SIMPLIFICATION, PROP_STRING,
    ALGO_GREEDY,
)
from freecad.nestingworkbench import nw_logger
from .algorithms import sheet_sequence

# Defaults for a layout saved before the four Scoring weights were added (NLA ruling 25).
_WEIGHT_DEFAULTS = {"gravity": 50.0, "contact": 0.0, "direction": 0.0, "offcut": 50.0}
from .algorithms.minkowski_engine import DEFAULT_CANDIDATE_SPACING

class NestingJob:
    """
    Manages a single nesting session using the Sandbox Pattern.
    Receives a completed GA layout via from_ga_result() and either commits
    it to the target layout or discards it on cancel.

    NOTE: Direct instantiation is not supported. Always use from_ga_result().
    """
    @classmethod
    def from_ga_result(cls, doc, target_layout, params, preparer, layout_group, parts_group, sheets):
        """Creates a NestingJob from a completed GA layout.

        This is the sole entry point. The GACoordinator and LayoutManager own
        the sandbox lifecycle; this class only handles commit/cancel.
        """
        job = cls.__new__(cls)
        job.doc = doc
        job.target_layout = target_layout
        job.params = params
        job.preparer = preparer
        job.temp_layout = layout_group
        job.parts_group = parts_group
        job.sheets = sheets

        job._owned_object_names = set()
        if layout_group: job._owned_object_names.add(layout_group.Name)
        if parts_group: job._owned_object_names.add(parts_group.Name)

        return job

    def commit(self):
        """Promotes the temporary results to the target layout."""
        
        to_remove = []
        for child in self.target_layout.Group:
            if child.Label.startswith("Sheet_"):
                to_remove.append(child)
        
        for child in to_remove:
            recursive_delete(self.doc, child)
            
        temp_masters = next((c for c in self.temp_layout.Group if c.Label.startswith("MasterShapes")), None)
        
        if temp_masters and len(temp_masters.Group) > 0:
            old_masters = next((c for c in self.target_layout.Group if c.Label.startswith("MasterShapes")), None)
            if old_masters:
                recursive_delete(self.doc, old_masters)
            
            # Sanitize labels before move
            temp_masters.Label = "MasterShapes"
            for m in temp_masters.Group:
                if m.Label.startswith("temp_master_"):
                    m.Label = m.Label.replace("temp_master_", "master_")
            
            self.temp_layout.removeObject(temp_masters)
            self.target_layout.addObject(temp_masters)
            
        else:
            if temp_masters:
                recursive_delete(self.doc, temp_masters)

        # IMPORTANT: explicitly removeObject from temp first, because FreeCAD's addObject
        # does NOT automatically remove from the old group. If sheets remain in
        # temp_layout.Group when cleanup() calls recursive_delete(temp_layout), it will
        # walk into the sheets' children and delete Shapes_ groups and nested_xxx containers.
        sheets_to_move = [c for c in self.temp_layout.Group if c.Label.startswith("Sheet_")]
        for sheet in sheets_to_move:
            self.temp_layout.removeObject(sheet)
            self.target_layout.addObject(sheet)

        self.cleanup()
        
        self._apply_properties(self.target_layout)
        
        return self.target_layout

    def cleanup(self):
        """Destroys the sandbox."""
        for name in list(self._owned_object_names):
            obj = self.doc.getObject(name)
            if obj:
                recursive_delete(self.doc, obj)
        self._owned_object_names.clear()
        
        self.temp_layout = None
        self.parts_group = None

    def _apply_properties(self, target_layout):
        p = self.params
        self._set_prop(target_layout, PROP_STRING, PROP_SHEET_SEQUENCE,
                       sheet_sequence.to_json(p['sheet_sequence']))
        for stale in (PROP_SHEET_WIDTH, PROP_SHEET_HEIGHT):
            if hasattr(target_layout, stale):
                target_layout.removeProperty(stale)
        self._set_prop(target_layout, PROP_LENGTH, PROP_PART_SPACING, p['spacing'])
        self._set_prop(target_layout, PROP_LENGTH, PROP_SHEET_THICKNESS, p['sheet_thickness'])
        self._set_prop(target_layout, PROP_FLOAT, PROP_DEFLECTION_ANGLE, p.get('deflection_angle', 30))
        self._set_prop(target_layout, PROP_LENGTH, PROP_SIMPLIFICATION, p.get('simplification', 1.0))
        self._set_prop(target_layout, PROP_FILE, PROP_FONT_FILE, p['font_path'])
        self._set_prop(target_layout, PROP_BOOL, PROP_SHOW_BOUNDS, p['show_bounds'])
        self._set_prop(target_layout, PROP_BOOL, PROP_SHOW_OFFCUTS, p.get('show_offcuts', True))
        self._set_prop(target_layout, PROP_BOOL, PROP_ADD_LABELS, p['add_labels'])
        self._set_prop(target_layout, PROP_LENGTH, PROP_LABEL_HEIGHT, p['label_height'])
        self._set_prop(target_layout, PROP_LENGTH, PROP_LABEL_SIZE, p['label_size'])
        self._set_prop(target_layout, PROP_INTEGER, PROP_GLOBAL_ROTATION_STEPS, p['rotation_steps'])
        self._set_prop(target_layout, PROP_INTEGER, PROP_GENERATIONS, p.get('generations', 1))
        self._set_prop(target_layout, PROP_INTEGER, PROP_POPULATION_SIZE, p.get('population_size', 1))
        self._set_prop(target_layout, PROP_INTEGER, PROP_GA_WINNERS, p.get('ga_winners', 2))
        self._set_prop(target_layout, PROP_FLOAT, PROP_MUTATION_RATE, float(p.get('mutation_rate', 0.1)))
        for prop, key in WEIGHT_PROPS:
            self._set_prop(target_layout, PROP_FLOAT, prop, float(p.get(f"{key}_weight", _WEIGHT_DEFAULTS[key])))
        self._set_prop(target_layout, PROP_FLOAT, PROP_FITNESS_MIN_OFFCUT, float(p.get('min_offcut', 100000.0)))
        self._set_prop(target_layout, PROP_INTEGER, PROP_BEAM_WIDTH, p.get('beam_width', 1))
        self._set_prop(target_layout, PROP_INTEGER, PROP_MAX_PART_BRANCHES, p.get('max_part_branches', 2))
        self._set_prop(target_layout, PROP_INTEGER, PROP_MAX_ROT_BRANCHES, p.get('max_rot_branches', 3))
        self._set_prop(target_layout, PROP_BOOL, PROP_BEAM_ITERATIVE, p.get('beam_iterative', False))
        self._set_prop(target_layout, PROP_FLOAT, PROP_BEAM_WIDTH_DECAY, float(p.get('beam_width_decay', 1.0)))
        self._set_prop(target_layout, PROP_LENGTH, PROP_CANDIDATE_SPACING,
                       p.get('candidate_spacing', DEFAULT_CANDIDATE_SPACING))
        self._set_prop(target_layout, PROP_STRING, PROP_ALGORITHM,
                       str(p.get('algorithm', ALGO_GREEDY)))
        self._set_prop(target_layout, PROP_FLOAT, PROP_NESTING_DIRECTION, float(p.get('nesting_direction', 270.0)))
        self._set_prop(target_layout, PROP_FLOAT, PROP_PART_DIRECTION, float(p.get('part_direction', 0.0)))

    def _set_prop(self, obj, type_str, name, val):
        if not hasattr(obj, name):
            obj.addProperty(type_str, name, "Layout", "")
        setattr(obj, name, val)
