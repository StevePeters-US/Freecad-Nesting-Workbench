# TODO — FreeCAD Nesting Workbench

---

## How to Write a Task

```
### TASK-XXX: <Short title>

| Field       | Value                |
|-------------|----------------------|
| Complexity  | Low / Medium / High  |
| Component   | <folder or module>   |
| Depends on  | TASK-YYY (optional)  |

**Context** — Why this matters and any background a new contributor needs.

**Acceptance criteria**

1. First concrete, testable outcome.
2. Second concrete, testable outcome.

**Hints** — (optional) Pointers to files, functions, or patterns.
```

---

## Tasks — Ordered by Difficulty (Low → High)

---

### TASK-001: Fix duplicate code in `cam_manager.py`

| Field       | Value |
|-------------|-------|
| Complexity  | Low |
| Component   | `nestingworkbench/Tools/Cam/cam_manager.py` |

**Context** — `_create_job_for_sheet()` has copy-paste duplication at the end of the function. Lines ~258–265 contain two identical `self.doc.recompute()` calls and two identical `FreeCAD.Console.PrintMessage(...)` lines, causing every CAM job to print its success message twice and recompute twice.

**What to do**

1. Open `cam_manager.py`, go to `_create_job_for_sheet()`.
2. Find the two consecutive blocks near the end that both call `self.doc.recompute()` followed by `FreeCAD.Console.PrintMessage(f"Created CAM job...")`.
3. Delete the second occurrence of both lines (keep one `recompute()` and one `PrintMessage`).

**Acceptance criteria**

1. Only one `doc.recompute()` call remains after CAM job setup.
2. Only one `PrintMessage` confirmation prints per sheet.

---

### TASK-002: Fix `algo_kwargs` vs `current_algo_kwargs` bug in GA nesting

| Field       | Value |
|-------------|-------|
| Complexity  | Low |
| Component   | `nestingworkbench/Tools/Nesting/nesting_controller.py` |

**Context** — In `_execute_ga_nesting()`, line ~677 creates `current_algo_kwargs` by copying `algo_kwargs` and removing `progress_callback` for GA mode. However, line ~690 passes the original `algo_kwargs` (with the callback still present) to `nest()` instead of `current_algo_kwargs`. This means the progress-callback suppression for GA mode never actually takes effect.

**What to do**

1. Open `nesting_controller.py`, find `_execute_ga_nesting()`.
2. On line ~690, change `**algo_kwargs` to `**current_algo_kwargs`.

**Acceptance criteria**

1. In GA mode (population > 1 or generations > 1), per-part progress spam is suppressed.
2. In single-run mode (pop=1, gen=1), granular progress still works.

---

### TASK-003: Remove duplicate `progress_callback` assignment in `Nester`

| Field       | Value |
|-------------|-------|
| Complexity  | Low |
| Component   | `nestingworkbench/Tools/Nesting/algorithms/nesting_strategy.py` |

**Context** — In `Nester.__init__()`, `self.progress_callback` is assigned twice on consecutive lines (lines 195 and 197). The second assignment overwrites the first. This is harmless but confusing.

**What to do**

1. Open `nesting_strategy.py`, find `Nester.__init__()`.
2. Delete the duplicate `self.progress_callback = kwargs.get("progress_callback")` on line 197 (keep line 195).

**Acceptance criteria**

1. Only one `self.progress_callback` assignment exists in `__init__`.

---

### TASK-004: Replace bare `except:` blocks with specific exception types

| Field       | Value |
|-------------|-------|
| Complexity  | Low |
| Component   | Multiple files |

**Context** — Several files use bare `except:` (no exception type), which swallows all errors silently, including `KeyboardInterrupt` and `SystemExit`. These make debugging extremely difficult.

**What to do**

1. Search the entire project for `except:` (exactly, with no type after it).
2. Locations include:
   - `nesting_logic.py` lines 54, 65–66, 78–79 — change to `except Exception:`.
   - `nesting_controller.py` line 895 — change to `except Exception:`.
   - `transform_tool.py` line 178 — change to `except Exception:`.
3. For each, replace `except:` with `except Exception:` (or a more specific type if obvious from context).

**Acceptance criteria**

1. Zero bare `except:` blocks remain in the codebase.
2. `KeyboardInterrupt` and `SystemExit` are no longer silently caught.

---

### TASK-005: Implement `_ensure_target_layout()` selection inference

| Field       | Value |
|-------------|-------|
| Complexity  | Low |
| Component   | `nestingworkbench/Tools/Nesting/nesting_controller.py` |

**Context** — `_ensure_target_layout()` has a `pass` placeholder on line ~955 where it should infer the parent layout from the currently selected shapes. Right now, if no `current_layout` is set, it always creates a new `Layout_NNN` group, even if the selected shapes already belong to an existing layout.

**What to do**

1. Open `nesting_controller.py`, find `_ensure_target_layout()`.
2. Replace the `pass` block (lines ~952–955) with logic that:
   a. Iterates over `self.ui.selected_shapes_to_process`.
   b. For each, walks up `obj.InList` looking for a parent whose `Label` starts with `"Layout_"`.
   c. If exactly one unique layout is found, uses that as the target.
   d. If multiple different layouts are found, logs a warning and falls through to create a new one.

**Acceptance criteria**

1. Re-nesting parts from an existing layout reuses that layout (no orphaned empty layouts).
2. Parts from multiple layouts trigger a warning and create a new layout.

---

### TASK-006: Add DXF export option to include sheet boundary rectangle

| Field       | Value |
|-------------|-------|
| Complexity  | Low |
| Component   | `nestingworkbench/Tools/Exporter/exporter.py` |

**Context** — `SheetExporter.export_sheets()` filters out objects whose label starts with `"bound_"`, so the sheet boundary rectangle is never included in the DXF. CNC operators need the boundary for alignment.

**What to do**

1. Add an `include_boundary=False` parameter to `export_sheets()`.
2. When `True`, do not filter out `"bound_"` objects from the projection list.
3. Update `command_export_sheets.py` to pass this flag (optionally via a checkbox dialog, or just default to `True`).

**Acceptance criteria**

1. Exported DXF files contain the sheet boundary rectangle when the option is enabled.
2. Boundary is on a separate DXF layer or has a distinct line style.

---

### TASK-007: Redesign GA fitness function with weighted multi-objective scoring

| Field       | Value |
|-------------|-------|
| Complexity  | Medium |
| Component   | `nestingworkbench/Tools/Nesting/layout_manager.py` |

**Context** — The current fitness function in `calculate_efficiency()` is:

```python
fitness = sheets * sheet_area + last_sheet_bbox - contact_bonus
```

This has several problems:
- **Density** is only indirectly measured (via sheet count and bbox).
- **Free space** (wasted area between parts and edges of last sheet) is not penalized.
- **Contact score** (`_calculate_contact_score()`) uses a hardcoded 0.5mm buffer distance and sums raw intersection lengths, which have no upper bound and vary wildly between part sets—meaning the contact term's weight is unpredictable relative to the area terms.
- **Edge contact percentage** (shared cut lines that speed up CNC cutting) is not measured at all. Two parts sharing a straight edge means the CNC can cut both in one pass, which should be strongly rewarded.

**What to do**

1. In `layout_manager.py`, replace `calculate_efficiency()` with a normalized, multi-objective fitness function. Design with these components:

   a. **Density score** (0–1): `total_parts_area / total_sheet_area`. Higher = better.
   b. **Free-space penalty** (0–1): On the last sheet, compute `used_bounding_box_area / sheet_area`. The closer to the density score, the less wasted space. Penalize wide gaps between placed parts and the sheet edges.
   c. **Edge contact ratio** (0–1): For each pair of adjacent parts, compute the length of _shared boundary_ (edges within `spacing` distance that are approximately collinear). Divide by `total_perimeter / 2` to normalize. Higher = better.

2. Combine into: `fitness = -(w_density * density + w_freespace * (1 - freespace_waste) + w_contact * contact_ratio)`. Negative because lower fitness = better. Expose weights as constants at the top of the file (default: `w_density=0.5`, `w_freespace=0.2`, `w_contact=0.3`).

3. In `_calculate_contact_score()`:
   - Replace the 0.5mm hardcoded buffer with `self.spacing` (or pass spacing as a parameter).
   - Instead of summing raw `intersection.length`, compute the ratio: `total_shared_length / (sum of all part perimeters / 2)`. This normalizes contact to a 0–1 range.
   - To detect _collinear shared edges_ (not just touching): for each intersection segment, check that the two touching boundary segments are approximately parallel (angle < 5°). Only count those segments. This is the "shared cut line" metric.

4. Update `Layout` class to store the individual component scores for logging.

**Acceptance criteria**

1. Fitness function produces values in a predictable range regardless of part count/scale.
2. Layouts where parts share straight edges score better than layouts where parts only touch at corners.
3. GA reliably produces different results across generations (testable by running 5+ generations).

---

### TASK-008: Fix GA diversity — wire up crossover and seeding strategies

| Field       | Value |
|-------------|-------|
| Complexity  | Medium |
| Component   | `nestingworkbench/Tools/Nesting/nesting_controller.py`, `genetic_utils.py`, `layout_manager.py` |

**Context** — The GA currently has **zero variation between runs** because:

1. `ordered_crossover()` and `tournament_selection()` in `genetic_utils.py` are **never called** anywhere. They exist but are dead code.
2. Each new generation (line ~769) creates brand-new layouts with `random.shuffle`, discarding all genetic information from the previous generation's winners.
3. The search direction is set once per `Nester` instance and never varies between population members. All layouts in a generation use the same packing direction, so they only differ in part ordering/rotation.
4. `Python's `random.shuffle` with no seed means consecutive runs of the same generation can produce identical orderings (especially with small part counts).

**What to do**

1. **Step A — Use crossover between generations.** In `_execute_ga_nesting()`, after sorting layouts by fitness at the end of a generation:
   a. Keep the winner (elite) as-is.
   b. For each remaining slot in the next generation's population, call `tournament_selection()` to select two parents, then `ordered_crossover()` to produce a child ordering.
   c. Call `mutate_chromosome()` on the child.
   d. Create the new layout with `chromosome_ordering` set from the child's `(part_id, angle)` tuples, using `layout_manager.create_layout(..., chromosome_ordering=child_genes)`.
   This means the `create_layout + _apply_ordering` path (which already exists in `LayoutManager`) is used instead of creating a fresh layout and `random.shuffle`-ing it.

2. **Step B — Vary the search direction per layout.** In `_execute_ga_nesting()`, when building `current_algo_kwargs` for each layout:
   a. For the elite (index 0), keep the user-configured direction.
   b. For others, override `search_direction` to `None` (which makes `PlacementOptimizer.find_best_placement()` pick a random direction per part, line 49 of `nesting_strategy.py`).
   c. Alternatively, assign each layout a random fixed direction: `angle = random.uniform(0, 2*pi); direction = (cos(angle), sin(angle))`.

3. **Step C — Seed randomness properly.** At the top of `_execute_ga_nesting()`, seed `random` with `time.time_ns()` so consecutive runs always differ. Store the seed in the Layout for reproducibility logging.

4. **Step D — Store genes on the Layout after nesting** (currently done at line 655) AND on the winner so crossover has access to them.

**Acceptance criteria**

1. `ordered_crossover()` and `tournament_selection()` are called in the GA loop.
2. Running the same nesting twice with population ≥ 3, generations ≥ 3 produces measurably different results.
3. Each layout in a generation may use a different packing direction.
4. The GA converges over generations (fitness improves monotonically for the best layout).

---

### TASK-009: Persist and reload NFP cache across sessions

| Field       | Value |
|-------------|-------|
| Complexity  | Medium |
| Component   | `nestingworkbench/datatypes/shape.py`, `nesting_controller.py` |

**Context** — `Shape.nfp_cache` is a class-level `dict` that is lost when FreeCAD restarts. Recomputing NFPs is the single most expensive operation (often 80%+ of nesting time). Serializing the cache to disk would make re-nesting the same parts near-instant.

**What to do**

1. In `shape.py`, add two class methods:
   a. `save_nfp_cache(filepath)` — Iterates `Shape.nfp_cache`, serializes each NFP polygon to WKT (`shapely.wkt.dumps`), and writes a JSON file `{cache_key_as_string: {"polygon_wkt": "...", "exterior_points": [...], ...}}`.
   b. `load_nfp_cache(filepath)` — Reads the JSON, deserializes WKT back to Shapely polygons with `shapely.wkt.loads`, and populates `Shape.nfp_cache`.
2. In `nesting_controller.py`, at the end of `finalize_job()`, call `Shape.save_nfp_cache(cache_path)` where `cache_path` is `<document_directory>/<doc_name>_nfp_cache.json`.
3. At the start of `_run_nesting()`, call `Shape.load_nfp_cache(cache_path)` if the file exists.
4. When the user clicks "Clear NFP Cache", also delete the on-disk file.

**Acceptance criteria**

1. After nesting and committing, a `*_nfp_cache.json` file exists next to the `.FCStd`.
2. Restarting FreeCAD and re-nesting the same parts skips NFP computation (visible in log: "NFP cache hit").
3. "Clear NFP Cache" button deletes both in-memory and on-disk caches.

---

### TASK-010: Implement fill-sheet mode for filler parts

| Field       | Value |
|-------------|-------|
| Complexity  | Medium |
| Component   | `nestingworkbench/Tools/Nesting/nesting_strategy.py`, `nesting_controller.py` |

**Context** — The UI has a "Fill" checkbox per part and a `fill_sheet` property on `Shape`, but the nesting algorithm completely ignores it. The intent is: parts marked as filler should be replicated to fill remaining space _after_ all primary parts are placed.

**What to do**

1. In `Nester._nest_standard()`, after the main placement loop (line ~298), add a second pass:
   a. Separate parts into `primary_parts` (where `fill_sheet` is `False`) and `filler_parts` (where `fill_sheet` is `True`).
   b. Run the main loop only on `primary_parts`.
   c. After all primaries are placed, for each sheet and each filler part (in area-descending order):
      - Repeatedly `copy.deepcopy()` the filler and attempt placement via `_attempt_placement_on_sheet()`.
      - Stop when placement fails (no valid position left on this sheet).
      - Assign unique IDs: `filler.id = f"{original_id}_fill_{counter}"`.
2. In `nesting_controller.py`, when collecting job params, ensure `fill_sheet` is passed through the `quantities` dict (it already is, at line ~1048).
3. Style filler parts differently: in `Sheet.draw()`, check `placed_part.shape.fill_sheet` and set `ViewObject.Transparency = 60` on filler part FreeCAD objects.

**Acceptance criteria**

1. A part with Fill=True is replicated into remaining gaps after primaries are placed.
2. Filler placement stops when no more valid positions exist (no infinite loop).
3. Filler parts are visually distinguishable (higher transparency).

---

### TASK-011: Batch GPU NFP computation for multiple rotation angles

| Field       | Value |
|-------------|-------|
| Complexity  | Medium |
| Component   | `nestingworkbench/Tools/Nesting/algorithms/minkowski_engine.py`, `nfp_gpu_taichi.py` |

**Context** — The GPU path in `_calculate_and_cache_nfp_gpu()` dispatches one kernel call per `(A, B, angle)` triple. The Taichi kernel already supports batched rotations (`rotations_deg` is a list), but the engine always passes `[angle_B]` (a single-element list). The TODO comment on line ~301 of `minkowski_engine.py` acknowledges this.

**What to do**

1. Add a new method `MinkowskiEngine.precompute_all_nfps_gpu(part_to_place, placed_parts, rotation_steps)`:
   a. Compute all rotation angles: `[i * 360/steps for i in range(steps)]`.
   b. For each unique `placed_label`, collect all `relative_angle` values.
   c. Call `nfp_gpu_taichi.compute_nfp_batch(parts_A, parts_B_reflected, all_angles)` once per unique (A, B) pair.
   d. Unpack the results and populate `Shape.nfp_cache` with one entry per angle.
2. Call this from `Nester._nest_standard()` before the main placement loop (or at the start of each new sheet).
3. Ensure the CPU fallback still works when Taichi is unavailable.

**Acceptance criteria**

1. For parts with 4+ rotation steps, only one GPU kernel dispatch occurs per (A, B) pair.
2. Benchmarks show measurable speedup vs. the current per-angle dispatch.
3. CPU-only path is unaffected.

---

### TASK-012: Rename Transform Tool to "Manual Nester" and redesign architecture

| Field       | Value |
|-------------|-------|
| Complexity  | High |
| Component   | `nestingworkbench/Tools/Transform/` (entire directory), `nesting_commands/command_transform_parts.py` |

**Context** — The current transform tool is broken and has fundamental design issues:
- The proxy check at line 69 of `transform_tool.py` has `or True`, disabling all filtering.
- It can only drag parts already placed on sheets — there's no way to drag from `MasterShapes`.
- No snapping, no collision avoidance.
- The README marks it as "under construction."

This task is a full redesign. Rename to "Manual Nester" and rebuild the tool to allow users to manually compose layouts by dragging master shapes onto sheets.

**What to do — Phase 1: Rename and clean up**

1. Rename the files:
   - `command_transform_parts.py` → `command_manual_nester.py`
   - Class `TransformPartsCommand` → `ManualNesterCommand`
   - Internal name `Nesting_TransformParts` → `Nesting_ManualNester`
   - `ui_transform.py` → `ui_manual_nester.py`
   - `transform_tool.py` → `manual_nester_tool.py`
   - `transform_panel_manager.py` → `manual_nester_panel_manager.py`
2. Update `InitGui.py` to register the new command name and label it "Manual Nester" in the menu/toolbar.
3. Remove the `or True` guard on line 69. Replace the proxy check with: `if container.TypeId == "App::Part" or hasattr(obj, "Shape")`.
4. Remove all commented-out `print(f"DEBUG: ...")` lines.

**What to do — Phase 2: Master-to-sheet drag and drop**

5. In the new `ManualNesterTool` (née `TransformToolObserver`):
   a. On activation, if no layout exists, create one (reuse `_ensure_target_layout()` from the controller).
   b. Display the `MasterShapes` group to the left of the sheets (they're already arranged by `ShapePreparer._arrange_masters`).
   c. **Add an empty sheet** at the end of the layout. Store a reference to it. The empty sheet acts as a "drop zone" for new parts that don't fit on existing sheets.
   d. On cleanup (accept or cancel), if the last sheet is still empty (no parts placed), delete it.
   e. When the user clicks on a master shape and drags onto a sheet, **clone** a new instance (same as `ShapePreparer._create_nesting_instances()` does for one part) and place it at the drop position.
   f. Track cloned instances so "Cancel" can revert (delete clones, restore positions).

**What to do — Phase 3: Grid snapping**

6. In the UI panel (`ui_manual_nester.py`):
   a. Add a "Snap to Grid" checkbox (default on) and a "Grid Size" spinbox (default 5mm).
   b. Add a "Snap to Parts" checkbox (default on) and a "Snap Distance" spinbox (default 2mm).
7. In `handle_move()`:
   a. If "Snap to Grid" is on, round the drop position to the nearest grid increment.
   b. If "Snap to Parts" is on, check if the part's bounding box edge is within `snap_distance` of any other part's edge and snap to align.

**What to do — Phase 4: Collision push (field effect)**

8. Add a "Push Mode" section to the UI panel:
   a. "Push on Drop" checkbox (default off).
   b. "Push Radius" spinbox (default 0mm = push all overlapping, nonzero = push parts within radius).
9. In `handle_release()` (or `finish_operation()`), if "Push on Drop" is on:
   a. After placing a part, find all other parts on the same sheet whose polygons overlap or are within `push_radius`.
   b. For each overlapping part, compute a repulsion vector (from the dropped part's centroid toward the overlapping part's centroid).
   c. Move the overlapping part along that vector until it no longer overlaps (use binary search: start with `overlap_distance * 1.5`, halve until no overlap).
   d. If the pushed part would go off-sheet, clamp it to the sheet boundary.
   e. Repeat push for parts displaced by the chain reaction (limit iterations to prevent infinite loops, e.g., max 10 rounds).

**Acceptance criteria**

1. Tool is accessible via menu as "Manual Nester" with updated icon label.
2. Master shapes are visible; clicking on one and dragging to a sheet creates a new instance.
3. An empty sheet is appended on tool activation and removed if unused on close.
4. Grid snapping rounds placement to the configured grid size.
5. Part-edge snapping aligns edges when close.
6. Push mode displaces overlapping parts outward from the drop point.
7. Cancel reverts all changes (clones removed, positions restored).

---

### TASK-013: Move GA loop to a background thread

| Field       | Value |
|-------------|-------|
| Complexity  | High |
| Component   | `nestingworkbench/Tools/Nesting/nesting_controller.py` |

**Context** — The GA loop in `_execute_ga_nesting()` runs on the main thread, blocking the FreeCAD UI completely. The current workaround is calling `QtGui.QApplication.processEvents()` periodically, which is fragile (can cause re-entrant signals) and still makes the UI sluggish.

**What to do**

1. Create a new class `NestingWorker(QThread)` (or `QObject` + `moveToThread`):
   - `__init__` receives all parameters currently passed to `_execute_ga_nesting()`.
   - `run()` contains the GA loop body (lines ~638–823 moved wholesale).
   - Signals:
     - `progress_updated(str)` — status text.
     - `generation_complete(int, float, float)` — gen number, best efficiency, best contact.
     - `nesting_finished(Layout)` — the winning layout, ready for `NestingJob.from_ga_result()`.
     - `nesting_error(str)` — error message.
2. In `NestingController._execute_ga_nesting()`:
   a. Create the worker, connect its signals to UI update slots.
   b. Connect the Cancel button to `worker.requestInterruption()`.
   c. Start the worker with `worker.start()`.
   d. Return immediately (the rest happens in signal handlers).
3. Remove all `QtGui.QApplication.processEvents()` calls from the GA loop.
4. FreeCAD document modifications (creating/deleting layouts, drawing sheets) must happen on the main thread. Use `QMetaObject.invokeMethod()` with `Qt.QueuedConnection` or emit a signal for the main thread to perform the document mutation.

**Acceptance criteria**

1. The UI remains fully responsive during multi-generation GA nesting.
2. The Cancel button aborts nesting within 2 seconds.
3. Progress updates appear in the status label without `processEvents()`.
4. No crashes from cross-thread FreeCAD document access.

---

### TASK-014: Add unit tests for core algorithmic code

| Field       | Value |
|-------------|-------|
| Complexity  | High |
| Component   | `tests/` (new directory) |

**Context** — There are currently zero automated tests. The algorithmic core (Minkowski, NFP, GA, Shape) can be tested independently of FreeCAD by operating on raw Shapely polygons, making FreeCAD mocking unnecessary for most tests.

**What to do**

1. Create a `tests/` directory at the project root.
2. Create `tests/conftest.py` with common test fixtures:
   - A mock `FreeCAD` module (minimal: `Console.PrintMessage = print`, `Vector`, `Rotation`, `Placement`).
   - Helper functions to create simple Shapely rectangles and L-shapes.
3. Create the following test files:

   **`tests/test_minkowski_utils.py`**:
   - Test `minkowski_sum_convex` with two unit squares → result is a 2×2 square centered at (1,1).
   - Test `decompose_if_needed` with a convex polygon → returns itself; with an L-shape → returns triangles.
   - Test `minkowski_difference_convex` that a large square eroded by a small one has positive area.

   **`tests/test_genetic_utils.py`**:
   - Test `ordered_crossover` preserves all part IDs (no duplicates, no missing).
   - Test `mutate_chromosome` modifies at least one part (with high mutation rate).
   - Test `tournament_selection` returns a valid chromosome from the population.

   **`tests/test_shape.py`** (mock FreeCAD):
   - Test `Shape.set_rotation()` rotates the polygon correctly.
   - Test `Shape.move()` translates the polygon.
   - Test `Shape.bounding_box()` returns correct `(minx, miny, width, height)`.
   - Test `Shape.area` matches the Shapely polygon's area.

4. Add a `pytest.ini` or `pyproject.toml` section so `pytest` discovers the `tests/` directory.
5. Ensure tests can run with `python -m pytest tests/` without FreeCAD installed (mock the import).

**Acceptance criteria**

1. `pytest tests/` passes with ≥ 15 test cases.
2. Tests run without a FreeCAD installation.
3. Each test file covers at least the functions listed above.

---

### TASK-015: Implement undo/redo transaction support

| Field       | Value |
|-------------|-------|
| Complexity  | High |
| Component   | `nestingworkbench/Tools/Nesting/nesting_controller.py` |

**Context** — Currently, nesting operations create and delete FreeCAD objects directly without wrapping them in `doc.openTransaction()` / `doc.commitTransaction()`. This means Ctrl+Z cannot revert a nesting run, and users who accidentally commit a bad layout have to manually delete objects.

**What to do**

1. In `_run_nesting()`, before any object creation:
   ```python
   self.doc.openTransaction("Nesting Run")
   ```
2. At the end of `_run_nesting()` (success path):
   ```python
   self.doc.commitTransaction()
   ```
3. In the `except` clause:
   ```python
   self.doc.abortTransaction()
   ```
4. Similarly wrap `finalize_job()` and `cancel_job()` in their own transactions.
5. Test that Ctrl+Z after commit cleanly reverts (the layout group and all children are removed).

**Acceptance criteria**

1. After running nesting and pressing OK, Ctrl+Z removes the committed layout.
2. After Cancel, no residual objects remain.
3. The FreeCAD undo history shows named entries like "Nesting Run", "Nesting Commit".

---

### TASK-016: Support non-rectangular (custom) sheet shapes

| Field       | Value |
|-------------|-------|
| Complexity  | High |
| Component   | `nestingworkbench/Tools/Nesting/ui_nesting.py`, `nesting_strategy.py`, `minkowski_engine.py` |

**Context** — The bin polygon is always `Polygon([(0,0),(w,0),(w,h),(0,h)])`. Users cutting from irregularly shaped off-cuts (remnants from previous jobs) cannot describe their stock shape. This is a competitive differentiator — most nesting tools only support rectangles.

**What to do**

1. In `NestingPanel` (`ui_nesting.py`), add a "Custom Sheet Shape" section:
   - A "Use Selected Wire" button that reads the currently selected FreeCAD wire/sketch and converts it to a Shapely polygon.
   - Store as `self.custom_sheet_polygon`.
   - When active, disable the Width/Height inputs (they're overridden).
2. In `_collect_ui_params()`, pass `custom_sheet_polygon` through the params dict.
3. In `MinkowskiEngine.__init__()`, accept an optional `bin_polygon` parameter. If provided, use it instead of creating a rectangle.
4. The `bin_polygon.contains(test_poly)` check in `PlacementOptimizer._evaluate_rotation()` already works with arbitrary polygons — no change needed there.
5. In `Sheet.__init__()`, accept an optional `boundary_polygon` for drawing the custom shape instead of a rectangle.

**Acceptance criteria**

1. A user can select a closed wire/sketch, click "Use Selected Wire", and nest parts inside it.
2. Parts are placed only within the custom boundary (no overlap with edges).
3. The custom boundary is drawn in the FreeCAD 3D view.
