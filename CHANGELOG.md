# Changelog

All notable changes to the FreeCAD Nesting Workbench will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Releases are named by month and year (e.g. 9-26); the manifest version is year.month.patch (e.g. 2026.9.0).

## [Unreleased]

### Changed
- The genetic algorithm now starts one worker process per physical CPU core instead of per logical processor, with no fixed limit of 8, and workers no longer start idle math-library threads. The Minkowski settings' Advanced section has a GA worker processes setting that shows the count Auto will use.

### Fixed
- On Linux, GA worker processes died on start-up and every run silently fell back to one layout at a time. Workers are now started fresh on every platform instead of forked from FreeCAD.
- GA worker processes started one at a time, and the pool reached its full size only after several generations. Every worker now starts at once before the first generation, which makes population evaluation about 2.9 times faster than serial on a 16-core machine.
- Warnings raised inside GA worker processes now appear in the Report view and the log file.
- The genetic algorithm no longer builds and deletes a full set of FreeCAD objects for every population member, which made a 10×4 run about 3.25× faster.
- Runs that stop early or are cancelled no longer leave `Layout_GA_*` groups in the document.
- Part labels are built about 6× faster, which takes about 2 s off the end of a genetic-algorithm run with 80 labelled parts.

## [9-26] - 2026-09-25

Version 2026.9.0.

### Added
- **About Nesting Workbench** in the Nesting menu, showing the installed version and a link to support development.
- Translation support. All panel, menu and dialog text can now be translated.
- Genetic-algorithm presets (Default / Balanced / Thorough), sized from benchmark runs, with tooltips giving the measured trade-offs. A warning appears when population or generations go past the point of diminishing returns.
- **Simulate Nesting** now previews each trial placement on the sheet being packed.
- Reopening a layout now reports which settings the document did not carry, instead of silently falling back to defaults. A single warning names each missing setting, and per-part detail is available with **Verbose Logging**.
- That report also flags a saved label font whose file no longer exists. Before, the nest quietly used a different font.

### Changed
- The Nesting panel is regrouped into collapsible sections (Genetic Algorithm, Annealing, Advanced Options).
- No-Fit Polygon computation is about 27× faster.
- Rotations already shown not to fit on a sheet are no longer retried, which skips most rotation sweeps with identical results.
- The Manual Nester now uses Qt-native viewport input and has a right-click context menu (Finish Nesting / Cancel).
- **Fill** now always places the full **Quantity** as normal parts, then adds extra copies to fill the remaining space.

### Fixed
- Reopening a saved layout no longer crashes with a `TypeError` (#19).
- Re-nesting a reopened layout no longer puts each part on its own sheet (#19).
- A layout saved with the Physics algorithm now reopens on Physics.
- Running a second re-nest no longer crashes FreeCAD.
- Fill mode no longer produces broken placements or drops required copies of a part.
- Parts placed inside another part's holes no longer overlap it.
- The genetic algorithm no longer collapses to a population of identical layouts, and a chromosome-ordering bug is fixed.
- Parallel genetic-algorithm runs no longer ignore some panel settings.
- The master-shape outline is now drawn from the correct layout.
- DXF export of solid parts now produces flat 2D outlines instead of a 3D wireframe.
- Cross-section silhouettes no longer fill in holes.
- Curves imported from the Fields workbench no longer cause an error when nested.
- In the Manual Nester, a right-click no longer triggers both cancel and the context menu, and Shift no longer stays held from one session to the next.

### Known issues
- When a Balanced or Thorough run stops early, leftover `Layout_GA_*` groups (discarded optimizer candidates) stay in the document. Delete them, and make sure you select the final `Layout_*` group before exporting or stacking.

## [1.0.1] - 2026-08-29

### Changed
- All toolbar icons converted from PNG to SVG for crisp rendering at any size.
- Icons renamed with a `Nesting_` prefix so they cannot collide with other
  addons in FreeCAD's global icon search path.
- `FreeCADGui.addIconPath()` moved out of module scope into `Initialize()`, so
  the workbench no longer affects icon lookup unless it is activated.

### Added
- Explicit `__init__.py` in the six packages that previously relied on implicit
  namespace packages.

## [1.0.0] - 2026-08-06

### Added
- 2D bin-packing nesting of 3D parts onto flat material sheets.
- Minkowski-Sum / No-Fit Polygon (NFP) placement engine with GA optimizer.
- Interactive Manual Nester tool with drag-and-drop and proximity physics repulsion.
- Direct FreeCAD CAM job creation from nested layouts.
- Multi-sheet DXF export utility.
- 2D projection silhouette generator for complex 3D geometry.
- Addon manifest `package.xml` and LGPL-2.1 license.
