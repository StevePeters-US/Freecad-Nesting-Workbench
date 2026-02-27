# COMPLETED — FreeCAD Nesting Workbench

Archive of completed tasks. Move tasks here from `TODO.md` when they are done.

---

- [x] **TASK-001**: Fix duplicate code in `cam_manager.py` (**2026-02-26**)
- [x] **TASK-002**: Fix `algo_kwargs` vs `current_algo_kwargs` bug in GA nesting (**2026-02-26**)
- [x] **TASK-003**: Remove duplicate `progress_callback` assignment in `Nester` (**2026-02-26**)

<details>
<summary>Details</summary>

| Field       | Value |
|-------------|-------|
| Complexity  | Low |
| Component   | `nestingworkbench/Tools/Nesting/algorithms/nesting_strategy.py` |

**Context** — In `Nester.__init__()`, the `self.progress_callback` attribute was assigned twice. This was cleaned up for better code quality.

**What was done**

1. Opened `nesting_strategy.py` and found `Nester.__init__()`.
2. Deleted the redundant assignment `self.progress_callback = kwargs.get("progress_callback")` (line ~197).
</details>

- [x] **TASK-004**: Replace bare `except:` blocks with specific exception types (**2026-02-26**)

<details>
<summary>Details</summary>

| Field       | Value |
|-------------|-------|
| Complexity  | Low |
| Component   | Multiple files |

**Context** — Bare `except:` blocks were replaced with `except Exception:` to follow Python best practices and avoid catching system-level signals.

**What was done**

1. Identified bare `except:` blocks in `ui_nesting.py`, `nesting_logic.py`, and `nfp_gpu_taichi.py`.
2. Replaced them with `except Exception:`.
</details>

- [x] **TASK-014**: Add unit tests for core algorithmic code (**2026-02-26**)

<details>
<summary>Details</summary>

| Field       | Value |
|-------------|-------|
| Complexity  | High |
| Component   | `tests/` directory |

**Context** — Added a comprehensive testing suite for the core algorithms, using a mock FreeCAD environment to allow tests to run without the full application.

**What was done**

1. Created `tests/conftest.py` with mock `FreeCAD`, `FreeCADGui`, and `Part` modules and Shapely fixtures.
2. Implemented `tests/test_minkowski_utils.py` for decomposition and Minkowski operations.
3. Implemented `tests/test_genetic_utils.py` for GA operators (crossover, mutation, selection).
4. Implemented `tests/test_shape.py` for the `Shape` class geometry and state management.
5. Verified all 14 tests pass with `pytest`.
</details>
---

### TASK-019: Manual Nester Axis Constraints (Blender-style)

| Field       | Value                |
|-------------|----------------------|
| Complexity  | Medium               |
| Component   | `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` |

**Context** — To improve precision during manual nesting, we want a control scheme similar to Blender. Users should be able to constrain movement to the X or Y axis using keyboard shortcuts.

**What to do**

- [x] Implement `Shift + X` to constrain translation to the X-axis.
- [x] Implement `Shift + Y` to constrain translation to the Y-axis.
- [x] Update the `ManualNesterToolObserver` to handle these modifiers.
- [x] Provide visual feedback (axis lines) when constraints are active.

**Acceptance criteria**

- [x] Pressing `Shift+X` during a "Grab" (G) operation limits movement to the X axis.
- [x] Pressing `Shift+Y` limits movement to the Y axis.
- [x] Pressing the key again or Esc clears the constraint.

---
