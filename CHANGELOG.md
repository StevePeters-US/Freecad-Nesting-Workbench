# Changelog

All notable changes to the FreeCAD Nesting Workbench will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
