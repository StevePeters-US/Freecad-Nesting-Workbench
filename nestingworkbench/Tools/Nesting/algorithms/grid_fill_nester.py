from .base_nester import BaseNester
import math

from ....datatypes.vert_state import VertState
class GridFillNester(BaseNester):
    """
    A nester that places parts on a grid, as described by the user.
    The algorithm creates a grid on the sheet and places parts, starting with the largest,
    on the first available grid points.
    """
    def __init__(self, width, height, rotation_steps=1, **kwargs):
        super().__init__(width, height, rotation_steps, **kwargs)
        # User-defined grid resolution
        self._grid_resolution = kwargs.get("gridresolution", 25.4)
        self._view_grid = kwargs.get("view_grid", False)

    def _try_place_part_on_sheet(self, part_to_place, sheet, update_callback):
        """
        Tries to place a single part on the sheet by finding the first available
        set of grid points that can accommodate the part's bounding box.
        """
        part_bounds = part_to_place.bounding_box()
        part_w = part_bounds[2]
        part_h = part_bounds[3]

        # Calculate the part's size in grid units
        grid_w = int(math.ceil(part_w / self._grid_resolution))
        grid_h = int(math.ceil(part_h / self._grid_resolution))
        
        # Ensure a grid is initialized for the sheet, in case it's a new sheet
        # created by the base nester.
        if not sheet.grid:
            sheet.initialize_grid(self._grid_resolution)

        cols = int(sheet.width // self._grid_resolution)
        rows = int(sheet.height // self._grid_resolution)
        grid = sheet.grid

        # Iterate through each grid point to use as a potential bottom-left placement corner
        for r_start in range(rows):
            for c_start in range(cols):
                if grid[r_start][c_start] != VertState.EMPTY:
                    continue

                place_x = c_start * self._grid_resolution
                place_y = r_start * self._grid_resolution

                # Check if the part extends beyond the sheet boundaries
                if place_x + part_w > sheet.width or place_y + part_h > sheet.height:
                    continue

                # Check if all grid cells required by the part are available
                can_place = True
                for r in range(grid_h):
                    for c in range(grid_w):
                        if r_start + r >= rows or c_start + c >= cols:
                            can_place = False; break
                        if grid[r_start + r][c_start + c] != VertState.EMPTY:
                            can_place = False; break
                    if not can_place:
                        break
                
                if can_place:
                    # If placement is possible, move the part
                    part_to_place.move_to(place_x, place_y)

                    # Mark the grid cells as occupied, leaving the top and right edges open
                    # as per the user's placement strategy.
                    for r in range(grid_h - 1):
                        for c in range(grid_w - 1):
                            grid[r_start + r][c_start + c] = VertState.FILLED # Mark as occupied
                    
                    if update_callback:
                        sheet_index = sheet.id
                        grid_info = None
                        if self._view_grid:
                            grid_info = {'cell_w': self._grid_resolution, 'cell_h': self._grid_resolution, 'cols': cols, 'rows': rows}
                        current_bounds = [p.shape.shape_bounds for p in sheet.parts]
                        update_callback({sheet_index: current_bounds + [part_to_place.shape_bounds]}, moving_part=part_to_place, current_sheet_id=sheet_index, grid_info=grid_info)

                    return part_to_place

        # Return None if no suitable position is found on the sheet
        return None