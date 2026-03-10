# Nesting Workbench — Code Review Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file and line numbers to change.

---

## Tier 1 — Code Quality (Do First)

### Exception Handling

- [ ] **T-001** `nestingworkbench/Tools/Nesting/nesting_logic.py` lines 53-56
  Replace `except Exception: pass` with a log statement:
  `FreeCAD.Console.PrintWarning(f"[nesting_logic] Draw failed: {e}\n")`

- [ ] **T-002** `nestingworkbench/Tools/Nesting/nesting_logic.py` lines 77-80
  Same as T-001 — replace silent `except Exception: pass` with a warning log.

- [ ] **T-003** `nestingworkbench/Tools/Nesting/shape_preparer.py` line 89
  Add `import traceback` at top of file (if missing). Append
  `traceback.format_exc()` to the existing error message string so the full
  stack trace is printed to the FreeCAD console.

- [ ] **T-004** `nestingworkbench/Tools/Nesting/shape_preparer.py` line 221
  Same as T-003 — add traceback to the shape-reload failure message.

- [ ] **T-005** `nestingworkbench/Tools/Nesting/layout_manager.py` line 212
  Replace bare `except Exception:` with a logged warning that includes the
  part name and the exception message. Do not silently swallow the error.

- [ ] **T-006** `nestingworkbench/Tools/ManualNester/manual_nester_tool.py`
  Search for `except Exception:` or `except:` with `pass` body and add a
  `FreeCAD.Console.PrintWarning(...)` log line to each one.

### Magic Numbers → Named Constants

- [ ] **T-007** `nestingworkbench/Tools/Nesting/ui_nesting.py` lines 54-67
  At the top of the file (after imports), add a `_DEFAULTS` dict:
  ```python
  _DEFAULTS = {
      "sheet_width": 600.0,
      "sheet_height": 600.0,
      "part_spacing": 12.5,
      "sheet_thickness": 3.0,
      "deflection_angle": 30.0,
      "rotation_angles": [360, 180, 120, 90, 45, 30, 15, 10, 5, 1],
  }
  ```
  Replace each hardcoded literal in the widget initialisation code with the
  corresponding `_DEFAULTS[...]` key.

- [ ] **T-008** `nestingworkbench/Tools/Nesting/ui_nesting.py` line 118
  Replace the hardcoded dial range `(0, 359)` with a named constant
  `_MINKOWSKI_DIR_MAX = 359` defined at module top.

### Dead Code / Unused Imports

- [ ] **T-009** `nestingworkbench/datatypes/sheet.py` line 14
  Remove the commented-out import:
  `# from shapely.ops import unary_union`

- [ ] **T-010** `nestingworkbench/Tools/Nesting/algorithms/nesting_strategy.py`
  Verify whether `from collections import defaultdict` (line 6) is actually
  used in the file. If unused, remove the import.

### Naming Clarity

- [ ] **T-011** `nestingworkbench/Tools/Nesting/nesting_logic.py`
  Rename internal helper `_draw_trial_bounds` → `_visualize_trial_placement`.
  Update every call site in the same file.

- [ ] **T-012** `nestingworkbench/Tools/Nesting/algorithms/minkowski_utils.py`
  Rename `minkowski_difference` → `calculate_inner_fit_polygon`.
  Update every call site across the project
  (`minkowski_engine.py`, any other callers found via grep).

- [ ] **T-013** `nestingworkbench/Tools/Nesting/layout_manager.py`
  Add a one-line docstring to `_calculate_contact_score()` explaining:
  - what the return value represents
  - whether higher or lower is better

### Documentation

- [ ] **T-014** `nestingworkbench/datatypes/shape.py` — `Shape.__init__`
  Add a class-level docstring that explains:
  - `polygon` — the buffered/offset polygon used for nesting gap calculations
  - `original_polygon` — the true polygon boundary before buffering
  - `unbuffered_polygon` — (if present) same as original; clarify which is canonical

- [ ] **T-015** `nestingworkbench/Tools/Nesting/algorithms/minkowski_utils.py`
  Add a module-level docstring at line 1 explaining that this module computes
  No-Fit Polygons (NFP) and Inner-Fit Polygons (IFP) using Minkowski sums/differences.

---

## Tier 2 — Test Coverage (Do Second)

### New test files

- [ ] **T-016** Create `tests/test_sheet.py`
  Add unit tests for `Sheet.is_placement_valid()`:
  - Valid placement (part fits inside sheet)
  - Invalid placement (part out of bounds)
  - Overlapping placement (two parts occupying the same space)

- [ ] **T-017** Create `tests/test_sheet.py` (extend T-016)
  Add unit tests for `Sheet.calculate_fill_percentage()`:
  - Empty sheet → 0%
  - Fully covered sheet → ~100%
  - Partially covered sheet → mid-range value

- [ ] **T-018** Extend `tests/test_shape.py`
  Add tests for `Shape.get_final_placement()`:
  - 0° rotation
  - 90° rotation
  - 180° rotation
  - Negative rotation value

- [ ] **T-019** Create `tests/test_nesting_logic.py`
  Add integration tests for the `nest()` function:
  - Empty parts list → returns empty result without error
  - Single part, valid sheet → part is placed
  - Part larger than sheet → no placement found (graceful failure)

- [ ] **T-020** Extend `tests/test_genetic_utils.py`
  Add edge-case tests:
  - Mutation on single-gene chromosome
  - Crossover where both parents are identical
  - Selection on a population of size 1

- [ ] **T-021** Create `tests/test_shape_processor.py`
  Add tests for any public functions in
  `nestingworkbench/Tools/Nesting/algorithms/shape_processor.py`.
  At minimum test the primary public entry point with valid and invalid geometry.

---

## Tier 3 — Architecture (Do Third — larger changes)

### Extract global visualization state

- [ ] **T-022** Create `nestingworkbench/Tools/Nesting/visualization_manager.py`
  Define class `VisualizationManager` with:
  ```python
  class VisualizationManager:
      def __init__(self):
          self._trial_viz_obj = None
          self._highlighted_master = None
      def draw_trial_placement(self, doc, bounds): ...
      def clear_trial_placement(self, doc): ...
      def highlight_master(self, obj): ...
      def clear_highlight(self): ...
  ```

- [ ] **T-023** `nestingworkbench/Tools/Nesting/nesting_logic.py`
  Replace module-level globals `_trial_viz_obj` and `_current_highlighted_master`
  with an instance of `VisualizationManager` (import from T-022).
  Update all functions that reference those globals to use the instance.

### Split large functions

- [ ] **T-024** `nestingworkbench/Tools/Nesting/shape_preparer.py` — `_handle_new_master()`
  This function is ~170 lines. Extract into three private helpers:
  - `_rebuild_2d_shape(master_obj, ...)` — handles 2D object path
  - `_center_3d_shape(master_obj, ...)` — handles 3D object centering
  - `_create_boundary_object(master_obj, ...)` — creates the bounding shape
  `_handle_new_master()` should call these three in sequence.

- [ ] **T-025** `nestingworkbench/datatypes/sheet.py` — `_draw_single_part()`
  This function is ~140 lines. Extract:
  - `_draw_final_part(part, ...)` — final drawing path
  - `_draw_simulation_part(part, ...)` — simulation/preview path
  Keep `_draw_single_part()` as a dispatcher between the two.

### Remove runtime circular import

- [ ] **T-026** `nestingworkbench/task_panel_manager.py` lines 36-38
  The `cleanup()` method currently does a runtime import of `NestingCommand`
  to break a circular dependency. Replace this with a callback pattern:
  - Add `__init__(self, cleanup_callback=None)` parameter
  - In `cleanup()`, call `self._cleanup_callback()` if set
  - In `command_nest.py`, pass a lambda: `TaskPanelManager(cleanup_callback=lambda: NestingCommand._task_panel = None)` (or equivalent)

### Extract NFP cache

- [ ] **T-027** Create `nestingworkbench/Tools/Nesting/algorithms/nfp_cache.py`
  Move all NFP caching logic from `MinkowskiEngine` into a new `NFPCache` class:
  ```python
  class NFPCache:
      def __init__(self): self._cache = {}
      def get(self, key): ...
      def set(self, key, value): ...
      def invalidate(self, key): ...
      def clear(self): ...
  ```
  Update `MinkowskiEngine` to hold an `NFPCache` instance instead of a raw dict.

### PlacementCalculator utility

- [ ] **T-028** Create `nestingworkbench/Tools/Nesting/placement_utils.py`
  Extract repeated placement/centering logic from `shape_preparer.py`,
  `sheet.py`, and `shape.py` into standalone functions:
  - `calculate_label_placement(label_size, rotation_deg, offset)`
  - `calculate_container_centroid(polygon, sheet_origin)`
  Replace inline copies with calls to these functions.

---

## Tier 4 — Style / Housekeeping (Do Last)

- [ ] **T-029** Add `__all__` exports to `nestingworkbench/__init__.py`
  Export the primary public symbols:
  ```python
  from .datatypes.shape import Shape
  from .datatypes.sheet import Sheet
  from .datatypes.placed_part import PlacedPart
  ```

- [ ] **T-030** Add `__all__` exports to
  `nestingworkbench/Tools/Nesting/algorithms/__init__.py`
  Export `MinkowskiEngine`, `NestingStrategy`.

- [ ] **T-031** Run `pyflakes` (or `flake8 --select=F`) against all `.py` files
  Fix every reported unused import (F401) and undefined name (F821).
  Do not fix style issues (E/W codes) in this pass — those are covered by the style guide.

- [ ] **T-032** Ensure every public class and function in the following files
  has at least a one-line docstring:
  - `nestingworkbench/datatypes/shape.py`
  - `nestingworkbench/datatypes/sheet.py`
  - `nestingworkbench/datatypes/placed_part.py`
  - `nestingworkbench/Tools/Nesting/algorithms/minkowski_utils.py`

---

## Agent Skills

See `.claude/commands/` for project-specific slash commands:

| Command | Purpose |
|---------|---------|
| `/fix-task` | Fix a single task from this list by ID (e.g. `/fix-task T-007`) |
| `/review-module` | Run a targeted code review of one module |
| `/add-tests` | Scaffold test file for an untested module |
