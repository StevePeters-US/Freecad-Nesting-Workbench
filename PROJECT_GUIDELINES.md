# Project Guidelines — FreeCAD Nesting Workbench

---

## 1 · Project Description

The **FreeCAD Nesting Workbench** is a workbench add‑on for FreeCAD that performs **2D bin‑packing** ("nesting") of 3D parts onto flat material sheets. It converts arbitrary 3D geometry into 2D boundary polygons using Shapely, then uses a **Minkowski‑Sum / No‑Fit‑Polygon** (NFP) placement algorithm combined with a **Genetic Algorithm** (GA) optimizer to find dense, collision‑free layouts.

Key differentiators:

* **NFP‑based placement** – exact geometric collision detection instead of raster/grid methods.
* **GPU acceleration** – optional Taichi‑powered NFP kernel for complex parts.
* **Deep FreeCAD integration** – results are live FreeCAD objects that can be further processed with the CAM workbench to generate toolpaths.
* **Per‑part controls** – individual rotation steps, up‑direction projection, and "fill‑sheet" mode.

---

## 2 · Folder Structure

```
Freecad-Nesting-Workbench/
├── InitGui.py                          # Workbench entry point – dependency check, command registration
├── README.md                           # User-facing installation & usage guide
├── PROJECT_GUIDELINES.md               # THIS FILE – contributor reference
├── TODO.md                             # Pending tasks
├── COMPLETED.md                        # Archived completed tasks
│
├── nesting_commands/                   # FreeCAD GUI command wrappers
│   ├── __init__.py
│   ├── command_nest.py                 # Opens the main nesting task panel
│   ├── command_create_cam_job.py       # Creates a CAM job from a layout
│   ├── command_create_silhouette.py    # Generates 2D outlines from 3D parts
│   ├── command_export_sheets.py        # Exports sheets to DXF files
│   ├── command_install_dependencies.py # Installs optional 'taichi' library
│   ├── command_stack_sheets.py         # Toggles sheet stacking
│   └── command_transform_parts.py      # Manual drag-and-drop part transform
│
├── nestingworkbench/                   # Core Python package
│   ├── __init__.py
│   ├── task_panel_manager.py           # FreeCAD Task Panel lifecycle management
│   ├── freecad_helpers.py              # Shared utilities (recursive_delete, get_layout_group, etc.)
│   │
│   ├── datatypes/                      # Data model classes
│   │   ├── shape.py                    # Shape – wrapper around a Shapely polygon + FreeCAD object
│   │   ├── sheet.py                    # Sheet – placed‑parts list, drawing, fill‑% calc
│   │   ├── placed_part.py             # PlacedPart – post‑placement snapshot
│   │   ├── shape_object.py            # ShapeObject / ViewProviderShape – scripted FreeCAD object
│   │   └── label_object.py            # LabelObject / ViewProviderLabel – text label
│   │
│   └── Tools/
│       ├── Nesting/                    # Main nesting tool
│       │   ├── ui_nesting.py           # NestingPanel – Qt widget with all inputs
│       │   ├── nesting_controller.py   # NestingController – orchestrator (sandbox, GA, draw)
│       │   ├── nesting_logic.py        # nest() entry point, simulation callbacks, efficiency calc
│       │   ├── layout_manager.py       # Layout / LayoutManager – GA population management
│       │   ├── shape_preparer.py       # ShapePreparer – master shape creation & instancing
│       │   ├── spreadsheet_utils.py    # Writes a FreeCAD Spreadsheet with layout params
│       │   └── algorithms/
│       │       ├── __init__.py
│       │       ├── nesting_strategy.py # Nester + PlacementOptimizer – greedy NFP placement
│       │       ├── minkowski_engine.py # MinkowskiEngine – NFP caching, GPU dispatch
│       │       ├── minkowski_utils.py  # Convex decomposition, Minkowski sum/difference helpers
│       │       ├── shape_processor.py  # 2D profile extraction (mesh → Shapely polygon)
│       │       ├── genetic_utils.py    # GA operators: crossover, mutation, tournament select
│       │       └── nfp_gpu_taichi.py   # Taichi kernel for GPU Minkowski sum
│       │
│       ├── Cam/
│       │   └── cam_manager.py          # CAMManager – creates FreeCAD CAM jobs from layouts
│       │
│       ├── Exporter/
│       │   └── exporter.py             # SheetExporter – DXF export of sheets
│       │
│       ├── Silhouette/
│       │   ├── __init__.py
│       │   └── silhouette_creator.py   # Cross‑section and projection silhouette creation
│       │
│       └── Transform/
│           ├── __init__.py
│           ├── ui_transform.py         # TransformToolUI – simple info panel
│           ├── transform_tool.py       # TransformToolObserver – mouse event handler
│           └── transform_panel_manager.py  # TransformTaskPanel
│
├── Resources/
│   └── icons/                          # SVG/PNG toolbar icons
│       ├── Nesting_Workbench.svg
│       ├── Nest_Icon.png
│       ├── CNC_Icon.png
│       ├── DXF_Icon.png
│       ├── Stack_Icon.png
│       ├── Silhouette_Icon.svg
│       ├── Nesting_Transform.svg
│       └── Transform_Icon.png
│
└── fonts/                              # Bundled label fonts
    ├── PoiretOne-Regular.ttf
    └── Roboto_Condensed-Black.ttf
```

---

## 3 · Workbench Goals

1. Provide a one‑click 2D nesting workflow inside FreeCAD (select parts → configure → run → get layout).
2. Use **No‑Fit Polygons** (Minkowski Sum) for exact collision‑free placement.
3. Support a **Genetic Algorithm** optimizer to improve packing across multiple generations.
4. Allow **per‑part** rotation steps, up‑direction, and fill‑sheet overrides.
5. Produce FreeCAD‑native output (groups, Part::Feature objects) for downstream CAM toolpath generation.
6. Optionally leverage **GPU acceleration** (Taichi) for NFP computation on complex parts.
7. Support 2D parts (Draft, Sketcher) and 3D solids (projected to 2D via mesh tessellation).

---

## 4 · Workbench Toolbar Layout

### Nesting Menu / Toolbar

| Icon | Command Name | Internal Name | Description |
|------|-------------|---------------|-------------|
| Nest_Icon.png | Run Nesting | `Nesting_Run` | Opens the main nesting task panel |
| CNC_Icon.png | Create CAM Job | `Nesting_CreateCAMJob` | Creates a CAM job from the selected layout |
| Silhouette_Icon.svg | Create Silhouette | `Nesting_CreateSilhouette` | Generates 2D outlines from selected 3D objects |
| DXF_Icon.png | Export Sheets | `Nesting_Export` | Exports each sheet to a DXF file |
| Stack_Icon.png | Stack/Unstack Sheets | `Nesting_StackSheets` | Toggles stacking of sheets at origin |
| Transform_Icon.png | Transform Parts | `Nesting_TransformParts` | Manual drag‑and‑drop to move/rotate placed parts |
| — | Install Dependencies | `Nesting_InstallDependencies` | Installs the optional `taichi` GPU library |

---

## 5 · Default Settings (UI)

| Setting | Default | Unit | Description |
|---------|---------|------|-------------|
| Sheet Width | 600 | mm | Width of the material sheet |
| Sheet Height | 400 | mm | Height of the material sheet |
| Sheet Thickness | 3.0 | mm | Used for 3D drawing and CAM stock height |
| Part Spacing | 2.0 | mm | Minimum gap between nested parts |
| Curve Angle (Deflection) | 10 | ° | Controls curve discretization quality |
| Simplification | 0.5 | mm | Polygon vertex reduction tolerance |
| Rotation Steps | 4 | — | Global: 0°, 90°, 180°, 270° |
| Add Labels | ✓ | — | Add text labels to placed parts |
| Label Height | 1.0 | mm | Extrusion height of labels |
| Label Size | 5.0 | mm | Font size for labels |
| GA Generations | 1 | — | Number of GA generations (1 = single‑pass greedy) |
| GA Population | 1 | — | Number of layout candidates per generation |
| Use GPU | ✗ | — | Enable Taichi GPU acceleration |

Settings are persisted in `User parameter:BaseApp/Preferences/NestingWorkbench`.

---

## 6 · Nesting Pipeline

```
┌──────────┐      ┌──────────────────┐      ┌──────────────────┐
│ UI Panel │─────▸│ NestingController │─────▸│  ShapePreparer   │
│ (config) │      │   (orchestrator)  │      │ (master shapes)  │
└──────────┘      └────────┬─────────┘      └──────────────────┘
                           │
            ┌──────────────┴──────────────┐
            ▼ GA Loop                     ▼ Single-pass
   ┌──────────────────┐          ┌──────────────────┐
   │  LayoutManager   │─────────▸│   nesting_logic   │
   │ (population mgmt)│          │     .nest()       │
   └──────────────────┘          └────────┬──────────┘
                                          │
                                 ┌────────▼──────────┐
                                 │      Nester       │
                                 │ (greedy strategy) │
                                 └────────┬──────────┘
                                          │
                               ┌──────────▼───────────┐
                               │ PlacementOptimizer   │
                               │ (parallel rotations) │
                               └──────────┬───────────┘
                                          │
                               ┌──────────▼───────────┐
                               │  MinkowskiEngine     │
                               │ (NFP calc + cache)   │
                               └──────────────────────┘
```

**Steps:**

1. **UI collects parameters** – `NestingPanel` gathers sheet size, spacing, rotation, per‑part overrides.
2. **Controller creates a sandbox** – `NestingJob` creates a temporary `Layout_temp_*` group so the original layout is untouched until commit.
3. **ShapePreparer builds masters** – For each unique part: project to 2D (`shape_processor`), buffer for spacing, create master `Part::Feature` + boundary.
4. **Instances are cloned** – Each quantity copy gets its own `Part::Feature` in a `PartsToPlace` group.
5. **GA loop** (if generations > 1) – `LayoutManager.create_ga_population()` creates N shuffled/rotated copies; each is nested; fitness is `sheets × area + bounding box − contact bonus`; GA operators (crossover, mutation) produce the next generation; worst layouts are deleted.
6. **Greedy placement** – `Nester._nest_standard()` sorts parts by area (largest first), tries each on existing sheets, creates new sheets as needed. For each part, `PlacementOptimizer.find_best_placement()` evaluates all rotation angles in parallel threads.
7. **NFP calculation** – `MinkowskiEngine` computes pairwise NFPs via convex decomposition + Minkowski sum. Results are cached in `Shape.nfp_cache` (class‑level dict, thread‑safe). Optionally dispatched to GPU via `nfp_gpu_taichi`.
8. **Drawing** – `Sheet.draw()` places FreeCAD objects at computed positions inside `Sheet_N` groups with `Shapes_N` and `Text_N` sub‑groups.
9. **Commit / Cancel** – `NestingJob.commit()` renames `Layout_temp` to `Layout_NNN` and hides the `MasterShapes` group; `.cleanup()` reverts everything.

---

## 7 · Code Formatting & Style

| Rule | Convention |
|------|-----------|
| Language | Python 3.8+ |
| Indentation | 4 spaces (no tabs) |
| Line length | ~120 chars soft limit |
| Naming | `snake_case` for functions/variables; `PascalCase` for classes |
| Imports | `import FreeCAD` at top; workbench‑relative imports use `from ...datatypes.shape import Shape` |
| Docstrings | Google‑style (`Args:`, `Returns:`) on public methods |
| Guard clauses | Prefer early `return` / `continue` over deep nesting |
| Error handling | `try/except` around FreeCAD API calls that may fail on deleted objects; log via `FreeCAD.Console.Print*` |
| UI toolkit | PySide (`from PySide import QtGui, QtCore`) |

---

## 8 · Logging

All log output goes through **`FreeCAD.Console`**:

| Level | Method | When |
|-------|--------|------|
| Info | `FreeCAD.Console.PrintMessage(msg + "\n")` | Normal progress |
| Warning | `FreeCAD.Console.PrintWarning(msg + "\n")` | Non‑fatal issues |
| Error | `FreeCAD.Console.PrintError(msg + "\n")` | Failures |

Some modules accept a `log_callback` parameter (e.g., `MinkowskiEngine`, `Nester`) to route logs to both the console and the UI status label.

---

## 9 · Event Safety

FreeCAD's object model is **reference‑based**; objects can be deleted by other operations. Defensive patterns used:

* **`hasattr(obj, "ViewObject")`** before accessing visibility.
* **`try/except RuntimeError`** around object‑graph traversals (deleted objects raise `RuntimeError`).
* **`obj in doc.Objects`** check before operating on a potentially stale reference.
* **Thread safety** – `Shape.nfp_cache_lock` (a `threading.Lock`) guards the class‑level NFP cache; `sheet.nfp_cache_lock` guards per‑sheet caches.

---

## 10 · Terminology

| Term | Meaning |
|------|---------|
| **NFP** | No‑Fit Polygon – the locus of positions where part B's reference point causes B to overlap A |
| **IFP** | Inner‑Fit Polygon – valid centroid positions for B inside a hole of A |
| **Master Shape** | The canonical `Part::Feature` for a unique part, stored in `MasterShapes` group |
| **Instance** | A copy of a master used for placement; lives in `PartsToPlace` group |
| **Layout** | A `App::DocumentObjectGroup` containing sheets, master shapes, and a parameters spreadsheet |
| **Sheet** | A rectangular region; represented by `Sheet` class in code and `Sheet_N` group in the document tree |
| **Sandbox** | Temporary `Layout_temp_*` group used during nesting; deleted on cancel, renamed on commit |
| **Chromosome** | A list of `(part_id, angle)` tuples encoding part order and rotation for the GA |
| **Fitness** | GA metric; lower = better. `sheets × sheet_area + last_sheet_bbox − contact_bonus` |
| **Contact Score** | Reward for parts touching each other; computed via buffered‑polygon intersection length |

---

## 11 · Dependencies

| Package | Required? | Purpose |
|---------|-----------|---------|
| `shapely` | **Yes** | 2D polygon operations (NFP, buffering, union, containment) |
| `FreeCAD` / `FreeCADGui` | **Yes** | Host application |
| `PySide` (QtGui / QtCore) | **Yes** | UI widgets (bundled with FreeCAD) |
| `Part` / `Draft` | **Yes** | FreeCAD geometry modules (bundled) |
| `taichi` + `numpy` | Optional | GPU‑accelerated NFP via Vulkan/CUDA/OpenGL |
| `importDXF` | Optional | DXF export (bundled with FreeCAD) |
| `Spreadsheet` | Optional | Layout parameters spreadsheet (bundled workbench) |
| `CAM` (Path) | Optional | CAM job creation (FreeCAD 1.1+) |

---

## 12 · Future Work

* **Cumulative rotation mode** – union of rotation angle sets from k=2..N.
* **Assembly‑aware quantities** – auto‑detect part counts from assemblies.
* **Multi‑material support** – nest onto sheets of different sizes or materials.
* **Improved IFP handling** – better hole‑fitting for donut‑shaped parts.
* **Background threading** – move GA loop to a worker thread with progress bar.
* **Undo integration** – leverage FreeCAD's transaction system for proper undo.
* **Post‑nesting compaction** – slide parts toward each other after GA placement.

---

## 13 · Manual Nester Controls

The **Manual Nester** uses a Blender-inspired control scheme for efficient part arrangement:

| Key | Action |
|-----|--------|
| **G** | **Grab/Translate** – Start moving the selected part with the mouse. |
| **R** | **Rotate** – Start rotating the selected part based on mouse X-delta. |
| **Shift + X** | **X-Constraint** – Toggle movement constraint to the X-axis during translation. |
| **Shift + Y** | **Y-Constraint** – Toggle movement constraint to the Y-axis during translation. |
| **L-Click / Enter** | **Confirm** – Apply the current translation/rotation. |
| **Esc / R-Click** | **Cancel** – Revert the part to its previous placement. |
| **Ctrl (Hold)** | **Snap** – Enable snapping (45° for rotation, Grid for translation). |

---
