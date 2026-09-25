# FreeCAD Nesting Workbench

A FreeCAD workbench that nests 2D shapes, or flat 3D parts, onto material sheets. Placement uses No-Fit Polygons (Minkowski sums), and a genetic algorithm can optionally improve the result. Finished layouts can be exported to DXF or turned straight into a CAM job.

[![Patreon](https://img.shields.io/badge/Support-Patreon-F96854?style=flat&logo=patreon)](https://www.patreon.com/cw/AttackPotato)

See [CHANGELOG.md](CHANGELOG.md) for what's new in each release.

## Requirements

*   FreeCAD 1.0 or later
*   The **CAM** workbench, for **Create CAM Job** only

## Installation

Install **Nesting** from FreeCAD's Addon Manager (**Tools → Addon manager**) and restart FreeCAD.

## Usage Guide

### 1. Selecting parts
Select the 3D parts or 2D shapes to nest, in the Tree View or 3D View.

### 2. Opening the Nesting panel
Click **Run Nesting Tool** on the toolbar, or choose it from the **Nesting** menu. The Nesting task panel opens with your selection already in the parts table. To change the list later, use **Add Selected** and **Remove Selected**.

To re-nest an existing layout, select its `Layout_*` group before you open the panel. The layout's settings and parts are loaded back in. If the document is missing any saved settings, the status line and Report view name each one that fell back to a default.

### 3. Configuring options

**Nesting Algorithm:** **Minkowski** (the default) places parts using No-Fit Polygons. **Physics** drops parts in and shakes them together with a physics simulation.

#### Sheet Setup
*   **Sheet Width / Height:** Size of the material sheet.
*   **Sheet Thickness:** Thickness of the material, used for the 3D view and CAM.
*   **Part Spacing:** Minimum gap between nested parts.
*   **Bounds Resolution:**
    *   **Curve:** How closely curved edges are approximated. Small angles (5–10°) give smoother curves but nest more slowly. Large angles (30°+) are faster but coarser.
    *   **Simplify:** Tolerance (mm) for removing redundant boundary points. Setting it to your machine's precision (e.g. 1 mm for a router) speeds up nesting.

#### Minkowski Nesting Settings
*   **Nesting Direction:** The direction parts are packed toward (Down, Left, Up or Right).
*   **Use Random Direction:** Gives each part a randomised placement weighting.
*   **Rotation Angle:** The global rotation step parts may be tried at.
*   **Genetic Algorithm:**
    *   **Preset:** **Default** (1 population, 1 generation) is a single fast pass. **Balanced** (10 × 10) and **Thorough** (10 × 20) search for tighter layouts and take longer. Hover over each field for the measured trade-offs.
    *   **Generations / Population Size:** Set these directly for a custom run. Values above 20 generations or 40 population gave no measurable improvement in testing.
    *   **Compactness:** Keeps the leftover material on the last sheet together as one large offcut. 0 turns it off. It only has an effect when Generations and Population Size are both above 1.

#### Physics Nesting Settings
These cover gravity direction, step size, spawn attempts, and an **Annealing (Shake)** section for the shake phase. Hover over any field to see what it does.

#### Labels
*   **Identifier Font:** The font file (`.ttf` / `.otf`) used for part labels.
*   **Add Identifier Labels:** Engraves an identifier on each part, at the given **Size** and **Height (Z)**.

#### Advanced Options
*   **Simulate Nesting:** Shows each trial placement live. Slower.
*   **Verbose Logging:** Writes detailed progress to the Report view.
*   **Clear NFP Cache:** Recomputes every No-Fit Polygon from scratch. Slower, but it rules out stale cache data.
*   **Show Bounds** and **Play sound on completion**.

#### Parts table
*   **Quantity:** How many copies of the part to nest.
*   **Rotations:** Tick the box to override the global rotation for this part and set its own step count. 0 or 1 means the part is never rotated.
*   **Up Dir:** The axis (Z+, Y+, X− …) treated as "up" when the 3D part is projected to 2D.
*   **Fill:** The **Quantity** is always placed. If Fill is ticked, extra copies are then added to fill any remaining space.

### 4. Generating the layout
Click **Run Nesting**. You can stop a run at any time with **Cancel Nesting**.
*   A `Layout_*` group is created in the tree, holding one `Sheet` per sheet used, with the nested parts on each.
*   Click **OK** to keep the layout, or **Cancel** to discard it.

## Other tools

*   **Stack/Unstack Sheets:** Toggles the selected layout's sheets between stacked at the origin and laid out side by side.
*   **Export Sheets as DXF:** Exports each sheet of the selected layout to its own DXF file, in a folder you choose.
*   **Create CAM Job:** Builds a CAM job from the selected layout, with parts, labels and sheet outlines organised for machining.
*   **Create Silhouette:** Creates a 2D outline of a 3D part, for use in a CAM job.
*   **Manual Nester:** (Experimental) Move and rotate parts in a nested layout by hand. Drag a part to move it; hold **Shift** to rotate it instead. **X** / **Y** lock the movement to one axis. **Esc** or a right-click cancels the current drag, and **Enter** confirms it. Three modes are available: push nearby parts out of the way, allow valid (non-overlapping) positions only, or auto-rotate to fit. **NOTE: This tool is still under construction. It may not work correctly, and future versions may change its behaviour or break layouts saved with it.**
*   **About Nesting Workbench** (Nesting menu): Shows the installed version.

## Known issues

*   After a **Balanced** or **Thorough** run that stops early, leftover `Layout_GA_*` groups (discarded optimizer candidates) can stay in the document. Delete them, and make sure you select the final `Layout_*` group before you export or stack.

Please report bugs on the [issue tracker](https://github.com/StevePeters-US/Freecad-Nesting-Workbench/issues).

## License

Licensed under the GNU Lesser General Public License v2.1 or later. See [LICENSE](LICENSE) for the full text. Bundled assets are under the licenses listed in [LICENSE-Assets](LICENSE-Assets).
