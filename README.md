# FreeCAD Nesting Workbench

A workbench for 2D nesting of shapes in FreeCAD, utilizing the Minkowski Sum algorithm for efficient packing.

[![Patreon](https://img.shields.io/badge/Support-Patreon-F96854?style=flat&logo=patreon)](https://www.patreon.com/cw/AngrySasquatch)

## Usage Guide

### 1. Preparing Parts
Select the 3D parts or 2D shapes you wish to nest from the Tree View or 3D View.

### 2. Running the Nester
Click the **Run Nesting** icon (or access via the Nesting menu). This opens the Nesting Task Panel.

### 3. Configuring Options

#### Sheet Settings
*   **Sheet Width/Height:** Dimensions of the material sheet.
*   **Sheet Thickness:** Thickness of the material (used for 3D visualization and CAM).
*   **Part Spacing:** Minimum distance between nested parts.

#### Bounds Resolution (Advanced)
*   **Curve Angle (Quality):** Controls how smooth curved edges are approximated. Lower angles (5-10°) give smoother curves but are slower. Higher angles (30°+) are faster but coarser.
*   **Simplification:** Tolerance for reducing determining points on a polygon. Higher values (1.0mm+) speed up nesting by removing tiny details.

#### Minkowski Nester Settings
*   **Packing Direction:** Choose the primary direction to gravity-pack parts (Down, Left, Up, Right).
*   **Use Random Direction:** If checked, randomizes placement heuristics for potentially better (or worse) results.
*   **Clear NFP Cache:** Forces recalculation of No-Fit Polygons. Useful if you suspect caching issues, but slower.
*   **Generations / Population Size:** Settings for the Genetic Algorithm optimizer. Increase these for complex nests to find better solutions over time (default is 1 for a single pass).

#### Part Options (In the Table)
*   **Quantity:** How many copies of this part to nest.
*   **Rotations:** Global setting for rotation steps (e.g., 4 steps = 0°, 90°, 180°, 270°).
*   **Override:** Check this to set specific rotation behavior for individual parts.
*   **Up Dir:** Define which axis is "Up" (Z+, Y+, etc.) for projecting the 3D part to 2D.
*   **Fill:** Mark a part as "filler" to be placed in gaps after main parts are nested.

### 4. Generating the Layout
Click **Run Nesting** at the bottom of the panel.
*   The tool will process the shapes and generate a `Layout` group in the tree.
*   This group contains `Sheet` objects with the nested parts.

## Other Tools

*   **Stack Sheets:** Stacks the sheets at origin.
*   **Export Sheets:** Export the nested sheets to DXF or SVG files.
*   **Create CAM Job:** Generates a Path/CAM job from the nested layout, organizing parts, labels, and outlines for machining.
*   **Create Silhouette:** Generates a 2D projection (outline) of a 3D part which can be used in a cam job.
*   **Transform Parts:** (Experimental) A manual tool to move/rotate nested parts. **NOTE: This tool is currently under construction and may not function correctly.**

## License

Licensed under the GNU Lesser General Public License v2.1 or later. See [LICENSE](LICENSE) for the full text.
