# FreeCAD Nesting Workbench — Coding Style Guide

This guide establishes conventions for this project. Where FreeCAD's own
conventions are established, we follow them. Where they are silent, we follow
PEP 8 with the exceptions listed below.

---

## 1. Python Version & Compatibility

- Target **Python 3.x** as shipped with the active FreeCAD release (currently 3.11+).
- Do not use features removed in Python 3 (no `print` statement, no `unicode()`, etc.).
- FreeCAD bundles its own Python interpreter. Do not assume the system Python
  is available at runtime.

---

## 2. FreeCAD API Conventions

### Console Logging
Use the FreeCAD console, not `print()`. The four levels map to:

```python
FreeCAD.Console.PrintMessage("...")   # Informational (green)
FreeCAD.Console.PrintWarning("...")   # Non-fatal issue (yellow)
FreeCAD.Console.PrintError("...")     # Error, operation aborted (red)
FreeCAD.Console.PrintLog("...")       # Debug, verbose (hidden by default)
```

All messages must end with `\n`. Include the module name in brackets:
```python
FreeCAD.Console.PrintError("[ShapePreparer] Failed to create boundary: {e}\n")
```

### FreeCAD Objects
- Use `doc.addObject("App::DocumentObjectGroup", "Name")` for group containers.
- Always check `hasattr(obj, "ViewObject")` before accessing `.ViewObject`
  (headless/CLI mode has no ViewObject).
- Prefer `obj.Label` over `obj.Name` for user-facing strings (`Name` is the
  internal ID; `Label` is what the user sees in the Model Tree).
- Call `FreeCAD.ActiveDocument.recompute()` explicitly after modifying objects
  rather than relying on implicit recompute.

### Part/Shape API
- Use `Part.makePolygon([v1, v2, ...])` for wire creation.
- Use `Part.Face(wire)` to create filled faces.
- Avoid `Part.show()` — add shapes to the document explicitly.

### Units
- All internal values are in **millimetres** unless documented otherwise.
- When displaying values to the user, always append the unit string: `f"{value} mm"`.
- Use `FreeCAD.Units.Quantity(value, "mm")` when creating spreadsheet cells.

---

## 3. Naming Conventions

| Entity | Convention | Example |
|--------|-----------|---------|
| Module / file | `snake_case` | `shape_preparer.py` |
| Class | `PascalCase` | `NestingController` |
| Public function/method | `snake_case` | `get_final_placement()` |
| Private function/method | `_snake_case` (single underscore) | `_build_boundary()` |
| Constant (module-level) | `UPPER_SNAKE_CASE` | `DEFAULT_SHEET_WIDTH` |
| Local variable | `snake_case` | `part_polygon` |
| FreeCAD document object name | `PascalCase` string | `"Layout_temp"`, `"PartsToPlace"` |
| Boolean variable | prefix with `is_`, `has_`, `can_` | `is_valid`, `has_placement` |

### Avoid
- Single-letter variables except loop counters (`i`, `j`) and mathematical
  notation (match the reference paper's notation and add a comment).
- Abbreviations that are not universally understood: use `polygon` not `poly`,
  `placement` not `plmnt`.

---

## 4. File & Module Organisation

```
nestingworkbench/
  __init__.py              # Public API exports only
  freecad_helpers.py       # Thin wrappers over FreeCAD API
  task_panel_manager.py    # Task panel lifecycle
  datatypes/               # Pure data structures (no FreeCAD UI)
    shape.py
    sheet.py
    placed_part.py
    ...
  Tools/
    Nesting/               # Nesting algorithm and orchestration
      algorithms/          # Pure algorithmic code (no FreeCAD imports if possible)
      ...
    ManualNester/
    Silhouette/
    Stacker/
    Exporter/
    Cam/
nesting_commands/          # FreeCAD command objects (thin wrappers)
tests/                     # pytest test suite
```

Rules:
- **`algorithms/`** modules must not import FreeCAD. They operate on plain
  Python objects and Shapely geometries only. This makes them unit-testable.
- **`datatypes/`** modules may import FreeCAD for type hints but must not call
  GUI APIs.
- **`nesting_commands/`** modules are thin entry points. They must not contain
  business logic — delegate to the appropriate Tool class.

---

## 5. Imports

Order imports as follows (with a blank line between each group):

```python
# 1. Standard library
import os
import math
import traceback
from typing import Optional, List

# 2. FreeCAD modules
import FreeCAD
import FreeCADGui
import Part
from PySide import QtGui, QtCore

# 3. Third-party (numpy, shapely, etc.)
import numpy as np
from shapely.geometry import Polygon

# 4. Local project imports (relative)
from ...datatypes.shape import Shape
from .layout_manager import LayoutManager
```

- Always use **relative imports** within the `nestingworkbench` package.
- Never use `import *`.
- Remove all unused imports before committing (run `pyflakes` or `flake8 -F`).

---

## 6. Exception Handling

### Rules
1. **Never silently swallow exceptions.** At minimum, log the error.
2. **Catch specific exception types.** Use `except ValueError:` not `except Exception:`.
   Use `except Exception as e:` only as a last resort, and always log.
3. **Always include context** in error messages: the object name, the operation
   that failed, and the exception message.
4. **Include tracebacks** for unexpected errors:
   ```python
   import traceback
   FreeCAD.Console.PrintError(f"[Module] Operation failed: {e}\n{traceback.format_exc()}\n")
   ```
5. **Do not use** `except:` (bare except — catches `SystemExit` and `KeyboardInterrupt`).

### Pattern
```python
# Good
try:
    result = compute_nfp(part_a, part_b)
except (ValueError, GeometryError) as e:
    FreeCAD.Console.PrintError(f"[MinkowskiEngine] NFP failed for '{part_a.label}': {e}\n")
    return None

# Bad
try:
    result = compute_nfp(part_a, part_b)
except:
    pass
```

---

## 7. Documentation

### Docstrings
- Use Google-style docstrings for all public classes and functions.
- Private helpers (`_prefixed`) need at least a one-line docstring explaining
  their purpose.
- Every module must have a module-level docstring stating its purpose.

```python
def calculate_inner_fit_polygon(stationary: Polygon, moving: Polygon) -> Polygon:
    """Return the Inner-Fit Polygon (IFP) for placing `moving` inside `stationary`.

    Args:
        stationary: The container polygon (sheet boundary).
        moving: The part polygon to fit inside the container.

    Returns:
        A Shapely Polygon representing all valid reference-point positions
        for `moving` to remain fully inside `stationary`.

    Raises:
        ValueError: If either polygon is empty or invalid.
    """
```

### Inline Comments
- Comment the *why*, not the *what*. The code already says what it does.
- Use comments to explain algorithm choices, non-obvious FreeCAD behaviour,
  or references to external papers/formulas.

---

## 8. Code Structure

### Function Length
- Aim for functions under **50 lines**.
- If a function exceeds 80 lines, split it into clearly named helpers.
- Each function should do **one thing**.

### Class Design
- Prefer **composition over inheritance**.
- Classes should have a single, clear responsibility.
- If a class handles both UI events and business logic, split it.

### Global State
- Avoid module-level mutable state (global variables).
- If state must persist across calls, encapsulate it in a class instance.
- Pass dependencies explicitly (dependency injection) rather than importing
  a singleton.

### Constants
- Define all magic numbers as named module-level constants:
  ```python
  DEFAULT_SHEET_WIDTH = 600.0    # mm
  DEFAULT_PART_SPACING = 12.5    # mm gap between parts
  MAX_ROTATION_STEPS = 36        # maximum rotation divisions for NFP
  ```

---

## 9. Testing

- Test files live in `tests/` and are named `test_<module>.py`.
- Use **pytest** as the test runner.
- Tests for `algorithms/` modules must be runnable **without FreeCAD installed**.
  Mock the FreeCAD module using `conftest.py` stubs.
- Structure tests with Arrange / Act / Assert:
  ```python
  def test_inner_fit_polygon_returns_valid_polygon():
      # Arrange
      sheet = Polygon([(0,0), (100,0), (100,100), (0,100)])
      part = Polygon([(0,0), (10,0), (10,10), (0,10)])

      # Act
      result = calculate_inner_fit_polygon(sheet, part)

      # Assert
      assert result is not None
      assert result.is_valid
      assert result.area > 0
  ```
- Test edge cases: empty inputs, degenerate geometry, zero-area polygons,
  parts larger than the sheet, single-element lists.

---

## 10. PEP 8 Deviations

This project follows PEP 8 with these exceptions:

| Rule | Deviation | Reason |
|------|-----------|--------|
| E501 line length 79 | Allow up to **120 chars** | FreeCAD community practice |
| No deviation on E302 | Two blank lines between top-level defs | Enforced |
| W503/W504 | Line break before binary operator | Follow Black formatter default |

---

## 11. FreeCAD-Specific Gotchas

1. **Recompute**: After modifying document objects, call `doc.recompute()`.
   Not doing so can leave the model in an inconsistent state visible to the user.

2. **ViewObject in headless mode**: FreeCAD can run without a GUI.
   Always guard: `if hasattr(obj, "ViewObject") and obj.ViewObject:`.

3. **Label vs Name**: `obj.Name` is the internal FreeCAD ID (immutable after
   creation). `obj.Label` is the user-visible name (mutable). Use `Label` for
   display; use `Name` for lookups.

4. **Thread safety**: FreeCAD's document model is not thread-safe.
   All document modifications must happen on the **main thread**.
   Use `FreeCADGui.updateGui()` to yield to the GUI event loop from long operations.

5. **Placement**: FreeCAD uses `FreeCAD.Placement(base, rotation)` where `base`
   is a `Vector` in mm and `rotation` is a `Rotation`. The rotation is stored as
   a quaternion internally. Always use the API; do not build placement matrices manually.

6. **Units in Spreadsheets**: When writing to a `Spreadsheet` object, pass values
   as `FreeCAD.Units.Quantity` or append the unit string — otherwise the cell may
   interpret the value as dimensionless.

---

## 12. Git Commit Conventions

Use the following prefixes:

| Prefix | Use for |
|--------|---------|
| `fix:` | Bug fix |
| `feat:` | New feature |
| `refactor:` | Code restructure without behaviour change |
| `test:` | Adding or updating tests |
| `docs:` | Documentation only |
| `chore:` | Tooling, dependencies, configuration |
| `style:` | Formatting, naming, no logic change |

Keep the subject line under 72 characters. Use imperative mood:
`fix: add traceback to shape boundary error` not `fixed the error`.

---

*Last updated: 2026-03-09*
