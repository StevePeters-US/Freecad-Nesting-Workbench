# Manual Nester Enhancement — Task List

## Overview

Enhance the manual nesting tool with:
1. **Physics-based part interaction** — dragged parts push nearby parts away in real-time
2. **Proportional editing falloff** — push force decreases with distance (like Blender)
3. **Auto-sheet management** — always create a fresh sheet on tool open; delete unused sheets on close
4. **UI controls** — falloff radius, curve type, enable/disable physics

## Architecture

```
ManualNester/
├── manual_nester_tool.py      ← existing drag/drop observer (modify)
├── manual_nester_panel_manager.py ← existing panel lifecycle (modify)
├── ui_manual_nester.py        ← existing UI (modify)
├── physics_engine.py          ← NEW: repulsion + falloff computation
└── collision_resolver.py      ← NEW: overlap resolution using BoundBox
```

**Key design decisions:**
- Physics engine operates on FreeCAD `Placement.Base` vectors directly (no Shapely dependency)
- Uses `BoundBox` for broad-phase collision detection (fast, already available on all shapes)
- Falloff function: `strength = max(0, 1 - (distance / radius) ^ curve_exp)`
- Physics runs synchronously in `handle_move()` — no threads, no timers
- Parts are clamped to their parent sheet boundary after displacement

## Reference Code

- **Deleted gravity nester** (commit `dca0e89~1`): `GravityNester._apply_gravity_to_part()` shows step-by-step movement with collision checks. The pattern of "move → check validity → revert if invalid" is reusable.
- **Current manual nester**: `manual_nester_tool.py` — `handle_move()` at line 325 is the integration point for physics.
- **Sheet management**: `_ensure_drop_zone_sheet()` at line 660, `_add_new_sheet()` at line 671.

---

## Tier 1 — Core Physics Engine (no UI wiring yet)

### M-001: Create `physics_engine.py` with falloff computation
- [ ] **File**: `nestingworkbench/Tools/ManualNester/physics_engine.py` (NEW)
- **What**: Create a `PhysicsEngine` class that computes displacement vectors for parts near a dragged part.
- **Interface**:
  ```python
  class PhysicsEngine:
      def __init__(self, radius=200.0, curve_exponent=2.0, strength=1.0):
          """
          radius: max influence distance (mm) from dragged part center
          curve_exponent: falloff curve power (1=linear, 2=quadratic, 3=cubic)
          strength: global multiplier on displacement
          """
          self.radius = radius
          self.curve_exponent = curve_exponent
          self.strength = strength

      def compute_falloff(self, distance):
          """Returns falloff factor in [0, 1]. 0 = no influence, 1 = full influence."""
          if distance >= self.radius:
              return 0.0
          if distance <= 0:
              return 1.0
          return max(0.0, 1.0 - (distance / self.radius) ** self.curve_exponent)

      def compute_displacements(self, dragged_center, drag_delta, parts_with_centers):
          """
          Compute displacement vectors for all parts based on proximity to dragged part.

          Args:
              dragged_center: FreeCAD.Vector — current center of the dragged part
              drag_delta: FreeCAD.Vector — how much the dragged part moved this frame
              parts_with_centers: list of (obj, FreeCAD.Vector) — other parts and their centers

          Returns:
              list of (obj, FreeCAD.Vector) — each part and its displacement vector
          """
  ```
- **Falloff formula**: `factor = max(0, 1 - (dist / radius) ^ exponent) * strength`
- **Displacement**: `delta = drag_delta * factor` (parts move in the same direction as the drag, scaled by falloff)
- **Tests**: Pure math, no FreeCAD dependency needed. Add `tests/test_physics_engine.py` with tests for:
  - `compute_falloff()` at distance=0, distance=radius, distance=radius/2, distance>radius
  - `compute_displacements()` with 3 parts at varying distances
- **Lines**: ~60

### M-002: Create `collision_resolver.py` with boundary clamping
- [ ] **File**: `nestingworkbench/Tools/ManualNester/collision_resolver.py` (NEW)
- **What**: A utility that clamps part positions to stay within sheet boundaries and resolves overlaps with simple separation.
- **Interface**:
  ```python
  class CollisionResolver:
      def clamp_to_sheet(self, obj, sheet_bbox):
          """
          Adjusts obj.Placement.Base so obj's BoundBox stays within sheet_bbox.

          Args:
              obj: FreeCAD object with .Shape.BoundBox and .Placement
              sheet_bbox: FreeCAD.BoundBox of the sheet boundary

          Returns:
              True if position was clamped, False if already within bounds.
          """

      def separate_overlapping(self, moved_obj, other_objs, max_iterations=5):
          """
          Iteratively separates moved_obj from overlapping other_objs using
          BoundBox intersection checks and minimal displacement.

          For each overlap:
            1. Compute BoundBox intersection
            2. Find shortest separation axis (X or Y)
            3. Push moved_obj along that axis by the overlap amount

          Args:
              moved_obj: FreeCAD object that was just displaced
              other_objs: list of FreeCAD objects to check against
              max_iterations: retry count for cascading overlaps

          Returns:
              True if all overlaps resolved, False if some remain.
          """
  ```
- **Note**: Uses `obj.Shape.BoundBox` — works with any FreeCAD Part::Feature or App::Part.
- **Lines**: ~80

### M-003: Integrate physics into `handle_move()`
- [ ] **File**: `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` (MODIFY)
- **What**: After moving the dragged part in `handle_move()`, call the physics engine to displace nearby parts, then resolve collisions.
- **Changes to `handle_move()` (line 325)**:
  1. After line 368 (`self.selected_obj.Placement = new_placement`), add a call to `self._apply_physics(drag_delta)`.
  2. Add new method `_apply_physics(self, drag_delta)`:
     ```python
     def _apply_physics(self, drag_delta):
         """Push nearby parts based on proximity to the dragged part."""
         if not self.physics_engine or not self.physics_enabled:
             return

         dragged_center = self._get_obj_center(self.selected_obj)

         # Collect other parts and their centers
         parts_with_centers = []
         for obj in self.original_placements:
             if obj == self.selected_obj:
                 continue
             parts_with_centers.append((obj, self._get_obj_center(obj)))

         # Compute and apply displacements
         displacements = self.physics_engine.compute_displacements(
             dragged_center, drag_delta, parts_with_centers
         )
         for obj, displacement in displacements:
             if displacement.Length > 0.01:  # Skip negligible moves
                 obj.Placement.Base = obj.Placement.Base + displacement

         # Resolve collisions: clamp to sheets, separate overlaps
         for obj, _ in displacements:
             sheet_group = self._find_sheet_at_pos(obj.Placement.Base)
             if sheet_group:
                 boundary = next((c for c in sheet_group.Group if c.Label.startswith("Sheet_Boundary_")), None)
                 if boundary:
                     self.collision_resolver.clamp_to_sheet(obj, boundary.Shape.BoundBox)
     ```
  3. Add helper `_get_obj_center(self, obj)`:
     ```python
     def _get_obj_center(self, obj):
         """Returns the XY center of an object's bounding box as a FreeCAD.Vector."""
         bb = obj.Shape.BoundBox
         return FreeCAD.Vector(
             bb.XMin + bb.XLength / 2 + obj.Placement.Base.x,
             bb.YMin + bb.YLength / 2 + obj.Placement.Base.y,
             0
         )
     ```
  4. Initialize `self.physics_engine` and `self.collision_resolver` in `__init__()` (after line 40):
     ```python
     from .physics_engine import PhysicsEngine
     from .collision_resolver import CollisionResolver
     self.physics_engine = PhysicsEngine()
     self.collision_resolver = CollisionResolver()
     self.physics_enabled = True
     ```
  5. Compute `drag_delta` in `handle_move()` by comparing `new_placement.Base` to `self.selected_obj.Placement.Base` before the assignment.
- **Lines changed**: ~40 added

---

## Tier 2 — Sheet Management

### M-004: Always create a fresh drop-zone sheet on tool activation
- [ ] **File**: `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` (MODIFY)
- **What**: Replace `_ensure_drop_zone_sheet()` (line 660) to ALWAYS create a new empty sheet, even if sheets already exist. This gives the user a blank canvas to drag new parts onto.
- **Changes**:
  1. Rename `_ensure_drop_zone_sheet()` → `_add_drop_zone_sheet()`.
  2. Remove the `has_sheet` check — always call `self._add_new_sheet()`.
  3. Update the call site at line 68 to use the new name.
  4. Position the new sheet to the right of existing sheets (already handled by `_add_new_sheet()` line 685).
- **Lines changed**: ~5

### M-005: Delete unused sheets on tool close (accept)
- [ ] **File**: `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` (MODIFY)
- **What**: When the user clicks OK, remove any sheet that has an empty `Shapes_` group.
- **Changes**:
  1. Add method `_remove_empty_sheets()`:
     ```python
     def _remove_empty_sheets(self):
         """Removes sheet groups that contain no parts in their Shapes_ sub-group."""
         doc = self.layout_group.Document
         sheets_to_remove = []
         for child in self.layout_group.Group:
             if child.isDerivedFrom("App::DocumentObjectGroup") and child.Label.startswith("Sheet_"):
                 shapes_group = next((c for c in child.Group if c.Label.startswith("Shapes_")), None)
                 if shapes_group and len(shapes_group.Group) == 0:
                     sheets_to_remove.append(child)

         for sheet_group in sheets_to_remove:
             # Remove children first (boundary, shapes group)
             for sub in reversed(sheet_group.Group):
                 doc.removeObject(sub.Name)
             doc.removeObject(sheet_group.Name)
             FreeCAD.Console.PrintMessage(f"Manual Nester: Removed empty sheet '{sheet_group.Label}'.\n")
     ```
  2. Call `self._remove_empty_sheets()` at the top of `save_placements()` (line 569).
- **Lines changed**: ~20

### M-006: Use sheet dimensions from the layout's existing sheets
- [ ] **File**: `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` (MODIFY)
- **What**: Currently `_add_new_sheet()` (line 671) hardcodes 1000x1000. Instead, read the dimensions from the first existing sheet boundary in the layout.
- **Changes**:
  1. Add method `_get_sheet_dimensions()`:
     ```python
     def _get_sheet_dimensions(self):
         """Returns (width, height) from the first existing sheet, or (1000, 1000) as default."""
         for child in self.layout_group.Group:
             if child.isDerivedFrom("App::DocumentObjectGroup") and child.Label.startswith("Sheet_"):
                 boundary = next((c for c in child.Group if c.Label.startswith("Sheet_Boundary_")), None)
                 if boundary and hasattr(boundary, "Shape"):
                     bb = boundary.Shape.BoundBox
                     return bb.XLength, bb.YLength
         return 1000, 1000
     ```
  2. In `_add_new_sheet()`, replace the hardcoded `Part.makePlane(1000, 1000)` with dimensions from `_get_sheet_dimensions()`.
  3. Update offset calculation to use the actual width + gap.
- **Lines changed**: ~15

---

## Tier 3 — UI Controls

### M-007: Add physics controls to the task panel UI
- [ ] **File**: `nestingworkbench/Tools/ManualNester/ui_manual_nester.py` (MODIFY)
- **What**: Add controls for the physics engine parameters.
- **Controls to add**:
  1. **Enable Physics** checkbox (default: checked)
  2. **Influence Radius** slider/spinbox (range 50–1000, default 200, units: mm)
  3. **Falloff Curve** dropdown: "Linear" (exp=1), "Smooth" (exp=2), "Sharp" (exp=3)
  4. **Strength** slider (range 0.1–2.0, default 1.0, step 0.1)
- **Layout**: Group these in a `QGroupBox("Physics Settings")`
- **Expose as attributes**: `self.physics_enabled_cb`, `self.radius_spin`, `self.curve_dropdown`, `self.strength_spin`
- **Lines changed**: ~40

### M-008: Wire UI controls to PhysicsEngine in the observer
- [ ] **File**: `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` (MODIFY)
- **What**: Connect UI widget signals to update `self.physics_engine` parameters live.
- **Changes to `__init__()`**:
  1. After creating `self.physics_engine`, connect signals from `self.panel_manager.form`:
     ```python
     ui = self.panel_manager.form
     ui.physics_enabled_cb.stateChanged.connect(
         lambda state: setattr(self, 'physics_enabled', bool(state))
     )
     ui.radius_spin.valueChanged.connect(
         lambda val: setattr(self.physics_engine, 'radius', val)
     )
     ui.curve_dropdown.currentIndexChanged.connect(
         lambda idx: setattr(self.physics_engine, 'curve_exponent', [1.0, 2.0, 3.0][idx])
     )
     ui.strength_spin.valueChanged.connect(
         lambda val: setattr(self.physics_engine, 'strength', val)
     )
     ```
- **Lines changed**: ~15

---

## Tier 4 — Polish & Edge Cases

### M-009: Store physics-displaced part positions for undo
- [ ] **File**: `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` (MODIFY)
- **What**: When physics displaces a part, we need to track its pre-physics position so that `cancel_operation()` reverts ALL parts, not just the dragged one.
- **Changes**:
  1. In `_apply_physics()`, before moving each part, save its current placement in a `self.pre_drag_placements` dict.
  2. In `cancel_operation()`, revert all parts in `self.pre_drag_placements` to their saved positions.
  3. In `finish_operation()`, clear `self.pre_drag_placements`.
  4. In `handle_click()`, initialize `self.pre_drag_placements = {}` and snapshot all tracked parts.
- **Lines changed**: ~20

### M-010: Visual feedback — show influence radius during drag
- [ ] **File**: `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` (MODIFY)
- **What**: While dragging with physics enabled, show a translucent circle around the dragged part indicating the influence radius.
- **Changes**:
  1. Add method `_show_radius_indicator(center, radius)` that creates/updates a Coin3D `SoSeparator` with a circle.
  2. Add method `_hide_radius_indicator()` that removes the indicator.
  3. Call `_show_radius_indicator()` in `handle_move()` when physics is active.
  4. Call `_hide_radius_indicator()` in `finish_operation()` and `cancel_operation()`.
- **Implementation**: Use `coin.SoSeparator`, `coin.SoTranslation`, `coin.SoDrawStyle`, and a `coin.SoLineSet` forming a circle.
- **Lines changed**: ~40

### M-011: Add scroll-wheel to adjust radius during drag
- [ ] **File**: `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` (MODIFY)
- **What**: While dragging, scroll wheel up/down adjusts the physics influence radius live (like Blender's proportional editing radius).
- **Changes**:
  1. Register a `SoMouseButtonEvent` handler for BUTTON4 (scroll up) and BUTTON5 (scroll down) — or check for scroll in existing handler.
  2. On scroll: adjust `self.physics_engine.radius` by ±25mm per tick.
  3. Clamp radius to [25, 2000] range.
  4. Update the UI spinbox to reflect the new value.
  5. Update the radius indicator circle.
- **Lines changed**: ~15

### ~~M-012~~: SKIP — `__init__.py` already exists
