# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/ManualNester/manual_nester_tool.py

"""
This module contains the ManualNesterToolObserver class, which implements a
simple drag-and-drop functionality for manually nesting parts in a layout.
"""

import FreeCAD
import FreeCADGui
from PySide import QtCore
import math
from .ui_manual_nester import ManualNesterToolUI
from .physics_engine import PhysicsEngine
from .collision_resolver import CollisionResolver
from .input_manager import InputManager

def _compute_physics_frame(
    working_cache,
    working_base,
    selected_key,
    parts_keys,
    drag_center,
    drag_len,
    sheet_bbox,
    physics_params,
):
    """Pure-Python physics frame computation — no FreeCAD or Coin3D calls.

    Runs displacement math + all collision resolution on snapshot data.
    Safe to call from any thread.

    Args:
        working_cache: mutable {key: {'bbox': {...}, 'poly': ...}} — modified in-place
        working_base:  mutable {key: (x, y)} — modified in-place
        selected_key:  integer id-key of the dragged object
        parts_keys:    list of integer id-keys for the other sheet parts
        drag_center:   (cx, cy) float tuple — center of dragged part
        drag_len:      float — magnitude of drag delta this frame
        sheet_bbox:    (xmin, xmax, ymin, ymax) or None
        physics_params:(radius, strength, curve_exponent)

    Returns:
        dict with keys:
            'valid'            — bool, False if placement is impossible
            'displaced_keys'   — set of keys that moved this frame
            'final_positions'  — {key: (x, y)} absolute positions for all displaced keys
    """
    from .collision_resolver import CollisionResolver
    from .physics_engine import PhysicsEngine

    radius, strength, curve_exponent = physics_params
    engine = PhysicsEngine(radius=radius, curve_exponent=curve_exponent, strength=strength)

    resolver = CollisionResolver()
    resolver._cache = working_cache
    resolver._base_cache = working_base

    # Build (key, center_tuple) list from cache for displacement computation.
    parts_centers = []
    for key in parts_keys:
        entry = working_cache.get(key)
        if entry:
            bb = entry['bbox']
            parts_centers.append((key, (bb['center_x'], bb['center_y'])))

    displacements = engine.compute_raw(drag_center, drag_len, parts_centers)

    # Apply displacements; snapshot pre-frame positions for revert.
    pre_frame = {}
    displaced_keys = []
    for key, (dx, dy) in displacements:
        if abs(dx) > 0.0001 or abs(dy) > 0.0001:
            cached = working_base.get(key)
            pre_frame[key] = cached if cached else (0.0, 0.0)
            resolver._translate_key(key, dx, dy)
            displaced_keys.append(key)

    total_peers = len(parts_centers)
    peers_in_range = len(displaced_keys)

    if not displaced_keys:
        return {
            'valid': True, 'displaced_keys': set(), 'final_positions': {},
            'total_peers': total_peers, 'peers_in_range': peers_in_range,
        }

    displaced_set = set(displaced_keys)
    static_keys = [k for k in parts_keys if k not in displaced_set]

    # Initial separation of each displaced part from the dragged part only.
    # We deliberately do NOT separate against statics here so that displaced parts
    # can overlap statics — those overlaps are what we detect for cascade promotion.
    for key in list(displaced_set):
        resolver.separate_overlapping_by_keys(key, [selected_key], max_iterations=2)
        if sheet_bbox:
            resolver.clamp_to_sheet_by_key(key, *sheet_bbox)

    # Resolution + cascade loop.
    # Cascade works by detecting displaced→static overlaps BEFORE any separation from
    # statics, then promoting overlapping statics to displaced and resolving bi-directionally.
    for _pass in range(12):
        # Resolve overlaps among currently displaced parts (STRtree-accelerated).
        pairs = CollisionResolver.find_overlapping_pairs(list(displaced_set), working_cache)
        for key_a, key_b in pairs:
            resolver.resolve_bi_by_keys(key_a, key_b)

        # Cascade: check for displaced→static overlaps within the influence radius.
        # Parts outside the radius are treated as immovable walls — this prevents
        # cascade chains from propagating far beyond the dragged part.
        dc_x, dc_y = drag_center
        radius_sq = radius * radius
        newly_promoted = []
        for k in static_keys:
            if not resolver.overlaps_any_by_keys(k, list(displaced_set)):
                continue
            entry = working_cache.get(k)
            if not entry:
                continue
            bb = entry['bbox']
            ddx, ddy = bb['center_x'] - dc_x, bb['center_y'] - dc_y
            if ddx * ddx + ddy * ddy <= radius_sq:
                newly_promoted.append(k)

        if not newly_promoted and not pairs:
            break

        # Promote overlapping statics: record pre-frame position, add to displaced set.
        for k in newly_promoted:
            pre_frame[k] = working_base.get(k, (0.0, 0.0))
            displaced_set.add(k)
            static_keys.remove(k)

        # Bi-directionally resolve each newly promoted part against all displaced
        # (this is what actually moves them away from the parts that hit them).
        for new_key in newly_promoted:
            for other_key in list(displaced_set):
                if other_key != new_key:
                    resolver.resolve_bi_by_keys(new_key, other_key)

        # Clamp all displaced to sheet and keep them off the dragged part.
        if sheet_bbox:
            for key in displaced_set:
                resolver.clamp_to_sheet_by_key(key, *sheet_bbox)
        for key in displaced_set:
            resolver.separate_overlapping_by_keys(key, [selected_key], max_iterations=1)

    # Validate: any displaced part still overlapping dragged or remaining statics is impossible.
    placement_valid = True
    for key in displaced_set:
        neighbours = [selected_key] + static_keys + [k for k in displaced_set if k != key]
        if resolver.overlaps_any_by_keys(key, neighbours):
            placement_valid = False
            break

    if not placement_valid:
        # Return best-effort pushed positions without reverting displaced parts.
        # valid=False tells the main thread to revert/highlight the dragged part.
        final_positions = {key: working_base.get(key, (0.0, 0.0)) for key in displaced_set}
        return {
            'valid': False, 'displaced_keys': displaced_set, 'final_positions': final_positions,
            'total_peers': total_peers, 'peers_in_range': peers_in_range,
        }

    final_positions = {key: working_base.get(key, (0.0, 0.0)) for key in displaced_set}
    return {
        'valid': True, 'displaced_keys': displaced_set, 'final_positions': final_positions,
        'total_peers': total_peers, 'peers_in_range': peers_in_range,
    }

try:
    from pivy import coin
except ImportError:
    coin = None

class ManualNesterToolObserver:
    """
    A ViewObserver that captures mouse events to allow manual nesting (dragging)
    of parts within a selected layout group.
    """
    def __init__(self, view, panel_manager):
        self.panel_manager = panel_manager
        self.view = view
        self.obj_to_move = None
        self.start_pos = None
        self.start_placement = None
        self.layout_group = None
        self.master_group = None
        self.new_objects = [] # Track objects created during this session
        self.original_placements = {}
        self.original_visibilities = {}
        self.selected_obj = None
        self.pre_drag_placements = {} # Track placements for undo/cancel (M-009)
        self.radius_indicator = None # M-010: Coin3D overlay for influence radius
        self.warned_missing_bounds = set() # M-B08: One-time debug warning for no-bounds parts

        # Physics initialization
        self.physics_engine = PhysicsEngine()
        self.collision_resolver = CollisionResolver()
        self.physics_enabled = True
        self.auto_rotate_enabled = False
        self.obj_to_sheet = {} # Track which sheet each object belongs to
        self._drag_active_sheet = None # Sheet the cursor was last over during drag
        self._dragged_original_color = None  # Saved ShapeColor for invalid-placement feedback
        self._pre_frame_dragged_pl = None  # Placement of dragged part before this frame's move

        # Physics tick rate — decoupled from mouse event frequency
        self.physics_tick_rate = 30  # Hz
        self._physics_timer = None
        self._physics_last_base = None  # dragged part position when physics last ran
        self._pending_physics_future = None
        from concurrent.futures import ThreadPoolExecutor
        self._physics_executor = ThreadPoolExecutor(max_workers=1)

        # Coin3D displacement nodes for physics-displaced parts.
        # During drag each displaced part gets a SoTranslation injected at index 0
        # of its ViewObject.RootNode so we avoid writing obj.Placement every tick.
        # On drop, _commit_displaced_placements() batch-writes and removes them.
        self._coin_disp_nodes = {}   # id(obj) -> (SoTranslation, obj)
        self._coin_disp_starts = {}  # id(obj) -> (start_x, start_y)

        self.input = InputManager(view)
        self.input.on("click", self.handle_click)
        self.input.on("release", self.handle_release)
        self.input.on("move", self.handle_move)
        self.input.on("cancel", self.cancel_operation)
        self.input.on("confirm", self.finish_operation)
        self.input.on("force_drop", self._on_force_drop)
        self.input.on("scroll_radius", self._on_scroll_radius)
        self.input.on("constraint_toggle", self._on_constraint_toggle)
        self.input.on("mode_switched", self._on_mode_switched)

        # Connect UI signals
        if hasattr(self.panel_manager, 'form'):
            ui = self.panel_manager.form
            # Initial sync from advanced controls
            self.physics_engine.radius = ui.radius_spin.value()
            self.physics_engine.curve_exponent = [1.0, 2.0, 3.0][ui.curve_dropdown.currentIndex()]
            self.physics_engine.strength = ui.strength_spin.value()
            # Derive initial mode from radio state
            self._sync_mode_from_ui(ui)

            # Mode radio buttons
            ui.radio_physics.toggled.connect(lambda _: self._sync_mode_from_ui(ui))
            ui.radio_valid.toggled.connect(lambda _: self._sync_mode_from_ui(ui))
            ui.radio_autorotate.toggled.connect(lambda _: self._sync_mode_from_ui(ui))

            # Advanced physics controls
            ui.radius_spin.valueChanged.connect(
                lambda val: setattr(self.physics_engine, 'radius', val)
            )
            ui.curve_dropdown.currentIndexChanged.connect(
                lambda idx: setattr(self.physics_engine, 'curve_exponent', [1.0, 2.0, 3.0][idx])
            )
            ui.strength_spin.valueChanged.connect(
                lambda val: setattr(self.physics_engine, 'strength', val)
            )

        selection = FreeCADGui.Selection.getSelection()
        if selection and selection[0].isDerivedFrom("App::DocumentObjectGroup") and (selection[0].Label.startswith("Layout_") or selection[0].Label == "Layout"):
            self.layout_group = selection[0]
        else:
            # Replicate _ensure_target_layout logic
            self.layout_group = self._discover_or_create_layout()

        if not self.layout_group:
            FreeCAD.Console.PrintWarning("Manual Nester: Could not find or create a Layout group.\n")
            return

        self.master_group = self._get_or_create_master_group()
        if self.master_group and hasattr(self.master_group, "ViewObject"):
            self.original_visibilities[self.master_group] = self.master_group.ViewObject.Visibility
            self.master_group.ViewObject.Visibility = True

        # Track objects in the layout
        self._track_layout_objects()

        self._add_drop_zone_sheet()

        # After changing visibilities, we need to update the GUI to reflect them.
        FreeCADGui.updateGui()

        # Activate input handling
        if self.layout_group:
            self.input.activate()
            FreeCAD.Console.PrintMessage(f"Manual Nester Activated on {self.layout_group.Label}. Drag parts to move/nest.\n")

    def _sync_mode_from_ui(self, ui):
        """Read the active radio button and set physics_enabled / auto_rotate_enabled."""
        if ui.radio_autorotate.isChecked():
            self.physics_enabled = True
            self.auto_rotate_enabled = True
        elif ui.radio_physics.isChecked():
            self.physics_enabled = True
            self.auto_rotate_enabled = False
        else:  # radio_valid
            self.physics_enabled = False
            self.auto_rotate_enabled = False

    def _discover_or_create_layout(self):
        doc = FreeCAD.ActiveDocument

        # Try finding existing layout
        for obj in doc.Objects:
            if obj.isDerivedFrom("App::DocumentObjectGroup") and obj.Label.startswith("Layout_"):
                return obj

        # Create new
        base_name = "Layout"
        i = 1
        existing_labels = [o.Label for o in doc.Objects]
        while f"{base_name}_{i:03d}" in existing_labels: i += 1
        layout = doc.addObject("App::DocumentObjectGroup", f"{base_name}_{i:03d}")
        layout.Label = f"{base_name}_{i:03d}"
        return layout

    def _get_or_create_master_group(self):
        master_shapes_group = None
        for child in self.layout_group.Group:
             if child.Label == "MasterShapes":
                 master_shapes_group = child
                 break

        if not master_shapes_group:
            # Check root of doc
            for obj in self.layout_group.Document.Objects:
                if obj.Label == "MasterShapes" and obj.isDerivedFrom("App::DocumentObjectGroup"):
                    return obj

            # Create if missing
            master_shapes_group = self.layout_group.Document.addObject("App::DocumentObjectGroup", "MasterShapes")
            master_shapes_group.Label = "MasterShapes"
            self.layout_group.addObject(master_shapes_group)

        return master_shapes_group

    def _track_layout_objects(self):
        """Walks the layout group and tracks all parts."""
        for sheet_group in self.layout_group.Group:
            if sheet_group.isDerivedFrom("App::DocumentObjectGroup") and sheet_group.Label.startswith("Sheet_"):
                # Ensure sheet boundary is visible
                sheet_boundary = next((obj for obj in sheet_group.Group if obj.Label.startswith("Sheet_Boundary_")), None)
                if sheet_boundary and hasattr(sheet_boundary, "ViewObject"):
                    self.original_visibilities[sheet_boundary] = sheet_boundary.ViewObject.Visibility
                    sheet_boundary.ViewObject.Visibility = True

                for sub_group in sheet_group.Group:
                    if sub_group.isDerivedFrom("App::DocumentObjectGroup") and sub_group.Label.startswith("Shapes_"):
                        for obj in sub_group.Group:
                            # Only track the HIGHEST level object in the Shapes group.
                            # Usually this is the App::Part or a standalone Part::Feature.
                            # We skip any object whose parent is also in this group.
                            is_top_level = True
                            if hasattr(obj, "InList"):
                                for parent in obj.InList:
                                    if parent in sub_group.Group:
                                        is_top_level = False
                                        break

                            if is_top_level and (obj.TypeId == "App::Part" or hasattr(obj, "Shape")):
                                self._track_single_object(obj, sheet_group)

                # Make existing sheet boundary unselectable
                if sheet_boundary and hasattr(sheet_boundary, "ViewObject"):
                    if hasattr(sheet_boundary.ViewObject, "Selectable"):
                        self.original_visibilities[sheet_boundary.Name + "_selectable"] = sheet_boundary.ViewObject.Selectable
                        sheet_boundary.ViewObject.Selectable = False

    def _track_single_object(self, obj, sheet_group=None):
        self.original_placements[obj] = obj.Placement.copy()
        if sheet_group:
            self.obj_to_sheet[obj] = sheet_group

        if hasattr(obj, "ViewObject"):
            self.original_visibilities[obj] = obj.ViewObject.Visibility

            # Make sure parts ARE selectable
            if hasattr(obj.ViewObject, "Selectable"):
                obj.ViewObject.Selectable = True

    # Input action handlers (registered with InputManager)

    def handle_click(self, pos):
        """On mouse down: Select object and start interaction."""

        clicked_obj = self.pick_object(pos)
        FreeCAD.Console.PrintMessage(f"Manual Nester: Picked {clicked_obj.Label if clicked_obj else 'None'}\n")

        # Check if we clicked on a Master Shape
        is_master = False
        if clicked_obj:
            p = clicked_obj
            while p:
                if p == self.master_group:
                    is_master = True
                    break
                # Walk up parents
                if p.InList: p = p.InList[0]
                else: p = None

        if is_master:
            self.start_pos = self.view.getPoint(pos[0], pos[1])
            new_obj = self._clone_part_from_master(clicked_obj)
            if new_obj:
                clicked_obj = new_obj
                # Treat as an active drag immediately: clone follows cursor,
                # release drops it (same path as hold-to-drag for existing parts).
                self.selected_obj = clicked_obj
                self.start_placement = self.selected_obj.Placement.copy()
                self.input.set_mode("TRANSLATE")
                self.input.is_implicit_drag = True
                FreeCAD.Console.PrintMessage(f"Manual Nester: Created clone {clicked_obj.Label}. Release to place.\n")
            return

        if clicked_obj:
            self.selected_obj = clicked_obj
            FreeCAD.Console.PrintMessage(f"Manual Nester: Starting interaction with {clicked_obj.Label}\n")

            # Prepare for potential drag
            self.start_pos = self.view.getPoint(pos[0], pos[1]) # 3D point
            self.start_placement = self.selected_obj.Placement.copy()

        else:
            # Clicked on empty space
            self.selected_obj = None

    def handle_move(self, pos, snap=False, _shift_held=False):
        """Process mouse movement during an active drag or free-grab."""
        if not self.selected_obj:
            return

        if self.input.mode == "TRANSLATE":
            if not self.start_pos: return

            current_pos = self.view.getPoint(pos[0], pos[1])
            move_vec = current_pos - self.start_pos
            move_vec.z = 0 # Project to XY plane for 2D nesting

            new_placement = self.start_placement.copy()
            new_pos = new_placement.Base + move_vec

            # Apply Dynamic Constraints
            if self.input.constraint == "X":
                # Lock Y to the position when X was pressed
                new_pos.y = self.input.constraint_lock_pos.y
            elif self.input.constraint == "Y":
                # Lock X to the position when Y was pressed
                new_pos.x = self.input.constraint_lock_pos.x

            # Integrate Physics
            drag_delta = new_pos - self.selected_obj.Placement.Base

            new_placement.Base = new_pos
            self.selected_obj.Placement = new_placement

            # Clamp the dragged part to whichever sheet the cursor is over.
            # If cursor is between sheets, fall back to the part's current sheet.
            self._clamp_dragged_to_cursor_sheet(current_pos)

            if drag_delta.Length > 0.001:
                if self.physics_enabled:
                    # Physics runs on a timer at physics_tick_rate Hz — not every mouse event.
                    # Dragged part already moved above; timer picks up accumulated delta.
                    self._ensure_physics_timer()
                else:
                    self._enforce_no_overlap()

            actual_pos = self.selected_obj.Placement.Base

            # M-010: Show radius indicator at the part's ACTUAL position
            # (may differ from new_pos if clamping moved it)
            if self.physics_enabled:
                self._show_radius_indicator(actual_pos, self.physics_engine.radius)
            else:
                self._hide_radius_indicator()

        elif self.input.mode == "ROTATE":
            if not self.start_placement or not self.start_pos: return

            part_origin = self.start_placement.Base
            current_pos_3d = self.view.getPoint(pos[0], pos[1])

            # Vectors from part origin to the initial click and current mouse (XY plane)
            sv_x = self.start_pos.x - part_origin.x
            sv_y = self.start_pos.y - part_origin.y
            cv_x = current_pos_3d.x - part_origin.x
            cv_y = current_pos_3d.y - part_origin.y

            # Skip if click was too close to origin to define an angle
            if math.sqrt(sv_x*sv_x + sv_y*sv_y) < 1.0 or math.sqrt(cv_x*cv_x + cv_y*cv_y) < 1.0:
                return

            angle_rad = math.atan2(sv_x * cv_y - sv_y * cv_x,
                                   sv_x * cv_x + sv_y * cv_y)
            angle_deg = math.degrees(angle_rad)

            # Snap to 45° increments with Ctrl
            if snap:
                step = 45.0
                angle_deg = round(angle_deg / step) * step

            rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), angle_deg)
            new_placement = self.start_placement.copy()
            new_placement.Rotation = rot.multiply(self.start_placement.Rotation)
            self.selected_obj.Placement = new_placement

            # M-010: Show radius indicator even in rotation mode if physics active
            if self.physics_enabled:
                self._show_radius_indicator(self.selected_obj.Placement.Base, self.physics_engine.radius)

    def handle_release(self):
        """On mouse up: place the part or finish the interaction."""
        # In free-grab mode, mouse release does NOT place the part.
        # The next left-click will handle placement via handle_click.
        if self.input.is_free_grab:
            self.input.is_mouse_down = False
            return

        # Clicked on a part without dragging — no-op (just deselect).
        # Hold-to-drag is the only interaction for existing parts.
        if not self.input.is_implicit_drag and self.selected_obj:
            # Defer finish to avoid modifying Coin3D scene graph inside callback
            QtCore.QTimer.singleShot(0, self.finish_operation)
            self.input.is_mouse_down = False
            return

        # Hold-and-drag release: place the part
        if self.input.is_implicit_drag:
            if self.selected_obj:
                FreeCAD.Console.PrintMessage(f"Manual Nester: Ending drag, attempting to place {self.selected_obj.Label}...\n")
                target_sheet_group = self._find_sheet_at_pos(self.selected_obj.Placement.Base)
                if target_sheet_group:
                    shapes_group = next((c for c in target_sheet_group.Group if c.Label.startswith("Shapes_")), None)
                    if shapes_group:
                        # Capture old sheet for cross-sheet re-parenting
                        old_sheet = self.obj_to_sheet.get(self.selected_obj)
                        old_shapes_name = None
                        if old_sheet and old_sheet != target_sheet_group:
                            old_shapes = next((c for c in old_sheet.Group if c.Label.startswith("Shapes_")), None)
                            if old_shapes:
                                old_shapes_name = old_shapes.Name
                        QtCore.QTimer.singleShot(0, lambda g=shapes_group.Name, o=self.selected_obj.Name, old=old_shapes_name: self._deferred_move_object_to_sheet(g, o, old))
                        # Update obj_to_sheet mapping immediately
                        self.obj_to_sheet[self.selected_obj] = target_sheet_group
                        if old_sheet and old_sheet != target_sheet_group:
                            FreeCAD.Console.PrintMessage(
                                f"Manual Nester: Moved {self.selected_obj.Label} "
                                f"from {old_sheet.Label} to {target_sheet_group.Label}.\n"
                            )
                        else:
                            FreeCAD.Console.PrintMessage("Manual Nester: Deferred drop onto sheet.\n")
                else:
                    if self.selected_obj in self.new_objects:
                        QtCore.QTimer.singleShot(0, lambda o=self.selected_obj.Name: self._deferred_revert_single_object(o))
                        FreeCAD.Console.PrintMessage("Dropped outside sheet: clone implicitly scheduled for removal.\n")

        # Defer finish_operation to avoid modifying Coin3D scene graph
        # (radius indicator removal) inside its own event callback.
        QtCore.QTimer.singleShot(0, self.finish_operation)
        self.input.is_mouse_down = False
        self.input.is_implicit_drag = False

    def _on_force_drop(self):
        """Deferred handler for a missed mouse-UP event."""
        try:
            if not self.layout_group:
                return
            self.handle_release()
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"[ManualNesterTool] Force-drop failed: {e}\n")
            self.finish_operation()
            self.input.is_mouse_down = False

    def _on_scroll_radius(self, delta):
        """Ctrl+scroll adjusts the physics influence radius."""
        new_radius = max(0.0, min(2000.0, self.physics_engine.radius + delta))
        self.physics_engine.radius = new_radius

        # Sync to UI
        if hasattr(self.panel_manager, 'form'):
            self.panel_manager.form.radius_spin.setValue(new_radius)

        # Update the visual indicator only — do NOT call handle_move (would move the part)
        if self.selected_obj and self.physics_enabled:
            self._show_radius_indicator(self.selected_obj.Placement.Base, new_radius)

    def _on_constraint_toggle(self, axis):
        """X or Y key toggles the axis constraint."""
        lock_pos = None
        if self.selected_obj:
            b = self.selected_obj.Placement.Base
            lock_pos = FreeCAD.Vector(b.x, b.y, b.z)
        self.input.set_constraint(axis, lock_pos)

    def _on_mode_switched(self, pos):
        """Re-base drag state when the mode switches mid-drag (Shift key)."""
        if self.selected_obj:
            self.start_placement = self.selected_obj.Placement.copy()
            self.start_pos = self.view.getPoint(pos[0], pos[1])

    # Deferred helpers (called via QTimer to avoid Coin3D scene-graph crashes)

    def _deferred_move_object_to_sheet(self, target_shapes_name, obj_name, old_shapes_name):
        """Move an object from one sheet's Shapes_ group to another."""
        try:
            doc = FreeCAD.ActiveDocument
            if self.layout_group and hasattr(self.layout_group, 'Document'):
                doc = self.layout_group.Document
            if doc:
                target_grp = doc.getObject(target_shapes_name)
                obj = doc.getObject(obj_name)
                if target_grp and obj:
                    # Remove from old group first (if cross-sheet move)
                    if old_shapes_name:
                        old_grp = doc.getObject(old_shapes_name)
                        if old_grp:
                            old_grp.removeObject(obj)
                    target_grp.addObject(obj)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"[ManualNesterTool] Deferred sheet move failed: {e}\n")

    def _deferred_revert_single_object(self, obj_name):
        try:
            doc = FreeCAD.ActiveDocument
            if self.layout_group and hasattr(self.layout_group, 'Document'):
                doc = self.layout_group.Document
            if doc:
                obj = doc.getObject(obj_name)
                if obj:
                    if obj in self.new_objects:
                        self.new_objects.remove(obj)
                    doc.removeObject(obj_name)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"[ManualNesterTool] Deferred revert failed: {e}\n")

    # Physics

    def _clamp_dragged_to_cursor_sheet(self, cursor_world_pos):
        """Clamp the dragged part to the sheet the cursor is over.

        If the cursor is over a different sheet, the part jumps to that sheet's
        boundaries (enabling cross-sheet dragging).  If the cursor is in the
        gap between sheets, keep clamping to the last sheet the cursor was over
        so the part doesn't snap back to the original sheet.
        """
        clamp_sheet = self._find_sheet_at_pos(cursor_world_pos)
        prev_sheet = self._drag_active_sheet
        if clamp_sheet:
            if clamp_sheet != prev_sheet:
                FreeCAD.Console.PrintMessage(
                    f"[Clamp] Cursor over {clamp_sheet.Label} "
                    f"(cursor=({cursor_world_pos.x:.0f},{cursor_world_pos.y:.0f}))\n"
                )
            self._drag_active_sheet = clamp_sheet
        else:
            clamp_sheet = self._drag_active_sheet or self.obj_to_sheet.get(self.selected_obj)
            if not prev_sheet:
                FreeCAD.Console.PrintMessage(
                    f"[Clamp] Cursor in gap, fallback={clamp_sheet.Label if clamp_sheet else 'None'} "
                    f"(cursor=({cursor_world_pos.x:.0f},{cursor_world_pos.y:.0f}))\n"
                )
        if not clamp_sheet:
            return
        boundary = next((c for c in clamp_sheet.Group if c.Label.startswith("Sheet_Boundary_")), None)
        if not boundary or not hasattr(boundary, "Shape") or not hasattr(boundary.Shape, "BoundBox"):
            return
        # Shape.BoundBox already includes placement (world coords)
        self.collision_resolver.clamp_to_sheet(self.selected_obj, boundary.Shape.BoundBox)

    def _get_same_sheet_others(self):
        """Return tracked objects on the same sheet as the currently dragged part, excluding it."""
        dragged_sheet = self._drag_active_sheet or self.obj_to_sheet.get(self.selected_obj)
        return [
            o for o in self.original_placements
            if o != self.selected_obj
            and (not dragged_sheet or self.obj_to_sheet.get(o) == dragged_sheet)
        ]

    def _enforce_no_overlap(self):
        """With physics off: highlight illegal positions but don't move other parts.
        If the dragged part overlaps any neighbour, snap it back to its pre-drag placement."""
        others = self._get_same_sheet_others()
        if self.collision_resolver.overlaps_any(self.selected_obj, others):
            # Snap back to where the drag started so the part can't be pushed into others
            if self.selected_obj in self.pre_drag_placements:
                self.selected_obj.Placement = self.pre_drag_placements[self.selected_obj].copy()

    def _auto_rotate(self, drag_delta):
        """Rotate the dragged part toward the weighted-average centroid of nearby parts.

        Returns True if rotation was applied (caller should invalidate cache).
        """
        MIN_ANGLE_DEG = 0.1
        SMOOTH = 0.20
        SPEED_SCALE_MM = 5.0

        dragged_info = self._get_obj_phys_info(self.selected_obj)
        if not dragged_info: return False
        dragged_center, _, _ = dragged_info

        centroid_delta_deg = self._get_centroid_attraction_delta(dragged_center, drag_delta)
        if centroid_delta_deg is None: return False

        edge_delta_deg, edge_weight = self._get_edge_alignment_delta(self.selected_obj)

        if edge_weight > 0.001:
            delta_deg = centroid_delta_deg * (1.0 - edge_weight) + edge_delta_deg * edge_weight
        else:
            delta_deg = centroid_delta_deg

        if abs(delta_deg) < MIN_ANGLE_DEG: return False

        speed_scale = min(1.0, drag_delta.Length / SPEED_SCALE_MM)
        apply_deg = delta_deg * SMOOTH * speed_scale

        if abs(apply_deg) >= MIN_ANGLE_DEG:
            rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), apply_deg)
            new_placement = self.selected_obj.Placement.copy()
            new_placement.Rotation = rot.multiply(self.selected_obj.Placement.Rotation)
            self.selected_obj.Placement = new_placement
            return True
        return False

    def _get_centroid_attraction_delta(self, dragged_center, drag_delta):
        """Calculates rotation delta to point the centroid toward nearby parts and drag direction."""
        pivot = self.selected_obj.Placement.Base
        offset_x, offset_y = dragged_center.x - pivot.x, dragged_center.y - pivot.y
        offset_len = math.sqrt(offset_x ** 2 + offset_y ** 2)
        if offset_len < 0.1: return None

        w_dir_x, w_dir_y, total_weight = 0.0, 0.0, 0.0
        for obj in self._get_same_sheet_others():
            info = self._get_obj_phys_info(obj)
            if not info: continue
            center, _, _ = info
            dx, dy = center.x - dragged_center.x, center.y - dragged_center.y
            dist = math.sqrt(dx ** 2 + dy ** 2)
            if dist < 0.001: continue
            weight = self.physics_engine.compute_falloff(dist)
            if weight < 0.001: continue
            w_dir_x += (dx / dist) * weight
            w_dir_y += (dy / dist) * weight
            total_weight += weight

        if total_weight < 0.001: return None
        wd_len = math.sqrt(w_dir_x ** 2 + w_dir_y ** 2)
        if wd_len < 0.001: return None
        w_dir_x, w_dir_y = w_dir_x / wd_len, w_dir_y / wd_len

        # Blend with drag
        if drag_delta.Length > 0.001:
            blend_x, blend_y = w_dir_x + (drag_delta.x / drag_delta.Length), w_dir_y + (drag_delta.y / drag_delta.Length)
            bl = math.sqrt(blend_x ** 2 + blend_y ** 2)
            target_dir_x, target_dir_y = (blend_x / bl, blend_y / bl) if bl > 0.001 else (w_dir_x, w_dir_y)
        else:
            target_dir_x, target_dir_y = w_dir_x, w_dir_y

        delta_deg = math.degrees(math.atan2(target_dir_y, target_dir_x) - math.atan2(offset_y, offset_x))
        return (delta_deg + 180) % 360 - 180

    def _get_edge_alignment_delta(self, obj):
        """Calculates rotation delta to align part with the nearest sheet edge."""
        edge_delta_deg, edge_weight = 0.0, 0.0
        dragged_sheet = self._drag_active_sheet or self.obj_to_sheet.get(obj)
        if not dragged_sheet: return 0.0, 0.0
        
        boundary = next((c for c in dragged_sheet.Group if c.Label.startswith("Sheet_Boundary_")), None)
        if not boundary or not hasattr(boundary, "Shape"): return 0.0, 0.0
        
        sheet_bb, part_bb = boundary.Shape.BoundBox, self._get_shape_bbox(obj)
        if not part_bb: return 0.0, 0.0
        
        cx, cy = (part_bb.XMin + part_bb.XMax) / 2.0, (part_bb.YMin + part_bb.YMax) / 2.0
        min_edge_dist = min(cx - sheet_bb.XMin, sheet_bb.XMax - cx, cy - sheet_bb.YMin, sheet_bb.YMax - cy)
        radius = self.physics_engine.radius
        
        if radius > 0.0 and min_edge_dist < radius:
            edge_weight = max(0.0, 1.0 - min_edge_dist / radius)
            near_vertical = (min_edge_dist == cx - sheet_bb.XMin or min_edge_dist == sheet_bb.XMax - cx)
            local_dims = self._get_local_bbox_dims(obj)
            if local_dims:
                local_w, local_h = local_dims
                long_axis_is_x = local_w >= local_h
                x_axis = obj.Placement.Rotation.multVec(FreeCAD.Vector(1, 0, 0))
                current_rot_deg = math.degrees(math.atan2(x_axis.y, x_axis.x))
                target_rot_deg = (90.0 if long_axis_is_x else 0.0) if near_vertical else (0.0 if long_axis_is_x else 90.0)
                raw = target_rot_deg - current_rot_deg
                edge_delta_deg = (raw + 90) % 180 - 90
        return edge_delta_deg, edge_weight

    def _ensure_physics_timer(self):
        """Start the physics timer if not already running."""
        if self._physics_timer is not None:
            return
        self._physics_timer = QtCore.QTimer()
        self._physics_timer.setInterval(max(1, int(1000 / self.physics_tick_rate)))
        self._physics_timer.timeout.connect(self._physics_tick)
        self._physics_timer.start()
        self._physics_last_base = None

    def _stop_physics_timer(self):
        if self._physics_timer is not None:
            self._physics_timer.stop()
            self._physics_timer.deleteLater()
            self._physics_timer = None
        self._physics_last_base = None
        if self._pending_physics_future is not None:
            self._pending_physics_future.cancel()
            self._pending_physics_future = None

    def _physics_tick(self):
        """Timer callback: snapshot state, submit worker frame, apply previous result."""
        if not self.selected_obj or not self.physics_enabled:
            return

        # Apply the previous frame's result if the worker finished.
        if self._pending_physics_future is not None:
            if not self._pending_physics_future.done():
                return  # worker still running — skip this tick
            try:
                result = self._pending_physics_future.result()
                self._apply_physics_result(result)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"[Physics] Worker error: {e}\n")
            self._pending_physics_future = None

        # Measure drag delta (FreeCAD API — main thread only).
        current_base = self.selected_obj.Placement.Base
        if self._physics_last_base is None:
            self._physics_last_base = FreeCAD.Vector(current_base.x, current_base.y, current_base.z)
            return
        drag_delta = current_base - self._physics_last_base
        if drag_delta.Length < 0.001:
            return

        # Bring dragged-part cache entry up to date via cheap translation.
        if not self.collision_resolver.translate_from_placement(self.selected_obj):
            self.collision_resolver.prime_cache([self.selected_obj])

        # Prime cache for sheet peers (no-op for already-cached entries).
        dragged_sheet = self._drag_active_sheet or self.obj_to_sheet.get(self.selected_obj)
        sheet_parts = [
            o for o in self.original_placements
            if o != self.selected_obj
            and (not dragged_sheet or self.obj_to_sheet.get(o) == dragged_sheet)
        ]
        sheet_label = dragged_sheet.Label if dragged_sheet else 'None'
        if not sheet_parts:
            if getattr(self, '_physics_logged_empty_sheet', None) != dragged_sheet:
                self._physics_logged_empty_sheet = dragged_sheet
                FreeCAD.Console.PrintMessage(
                    f"[Physics] No peers on sheet '{sheet_label}' — nothing to push.\n"
                )
        elif getattr(self, '_physics_logged_active_sheet', None) != dragged_sheet:
            self._physics_logged_active_sheet = dragged_sheet
            FreeCAD.Console.PrintMessage(
                f"[Physics] Active sheet '{sheet_label}' — {len(sheet_parts)} peers, "
                f"drag_len={drag_delta.Length:.1f}mm, radius={self.physics_engine.radius:.0f}mm.\n"
            )
        self.collision_resolver.prime_cache(sheet_parts)

        # Read sheet bbox while still on main thread (FreeCAD API).
        dragged_sheet_bbox = None
        if dragged_sheet:
            boundary = next(
                (c for c in dragged_sheet.Group if c.Label.startswith("Sheet_Boundary_")), None
            )
            if boundary and hasattr(boundary, "Shape"):
                dragged_sheet_bbox = boundary.Shape.BoundBox

        # Snapshot and submit worker frame.
        snapshot_args = self._snapshot_for_worker(drag_delta, sheet_parts, dragged_sheet_bbox)
        self._pending_physics_future = self._physics_executor.submit(
            _compute_physics_frame, *snapshot_args
        )

        b = self.selected_obj.Placement.Base
        self._physics_last_base = FreeCAD.Vector(b.x, b.y, b.z)

    def _snapshot_for_worker(self, drag_delta, sheet_parts, dragged_sheet_bbox):
        """Copy collision cache to plain Python for the worker thread.

        Returns the positional args for _compute_physics_frame.
        """
        # Shallow-copy cache entries (Shapely polygons are immutable for reads).
        working_cache = {
            k: {'bbox': dict(v['bbox']), 'poly': v.get('poly')}
            for k, v in self.collision_resolver._cache.items()
        }
        working_base = dict(self.collision_resolver._base_cache)

        selected_key = id(self.selected_obj)
        parts_keys = [id(o) for o in sheet_parts]

        drag_len = drag_delta.Length
        dragged_entry = working_cache.get(selected_key, {})
        bb = dragged_entry.get('bbox', {})
        drag_center = (bb.get('center_x', 0.0), bb.get('center_y', 0.0))

        sheet_bbox_snap = None
        if dragged_sheet_bbox:
            sheet_bbox_snap = (
                dragged_sheet_bbox.XMin, dragged_sheet_bbox.XMax,
                dragged_sheet_bbox.YMin, dragged_sheet_bbox.YMax,
            )

        physics_params = (
            self.physics_engine.radius,
            self.physics_engine.strength,
            self.physics_engine.curve_exponent,
        )

        return (working_cache, working_base, selected_key, parts_keys,
                drag_center, drag_len, sheet_bbox_snap, physics_params)

    def _apply_physics_result(self, result):
        """Apply worker result on the main thread: update cache and sync Coin3D nodes."""
        valid = result['valid']
        id_to_obj = {id(o): o for o in self.original_placements}

        for key, (final_x, final_y) in result['final_positions'].items():
            obj = id_to_obj.get(key)
            if not obj:
                continue
            cached = self.collision_resolver._base_cache.get(key)
            if cached:
                dx = final_x - cached[0]
                dy = final_y - cached[1]
                if abs(dx) > 0.0001 or abs(dy) > 0.0001:
                    self.collision_resolver._translate_entry(obj, dx, dy)

            if key in result['displaced_keys']:
                if obj not in self.pre_drag_placements:
                    self.pre_drag_placements[obj] = obj.Placement.copy()
                self._ensure_coin_disp_node(obj)
                self._sync_coin_disp(obj)

        active_sheet = self._drag_active_sheet or self.obj_to_sheet.get(self.selected_obj)
        if result['displaced_keys']:
            if getattr(self, '_physics_logged_result_sheet', None) != active_sheet:
                self._physics_logged_result_sheet = active_sheet
                FreeCAD.Console.PrintMessage(
                    f"[Physics] Displacing part(s) on sheet "
                    f"'{active_sheet.Label if active_sheet else 'None'}'. valid={valid}\n"
                )
        elif result.get('peers_in_range', 0) == 0 and result.get('total_peers', 0) > 0:
            if getattr(self, '_physics_logged_norange_sheet', None) != active_sheet:
                self._physics_logged_norange_sheet = active_sheet
                FreeCAD.Console.PrintMessage(
                    f"[Physics] {result['total_peers']} peer(s) on sheet "
                    f"'{active_sheet.Label if active_sheet else 'None'}' but all outside radius.\n"
                )

        self._set_part_highlight(self.selected_obj, not valid)
        if self.auto_rotate_enabled and self._physics_last_base:
            drag_delta = self.selected_obj.Placement.Base - self._physics_last_base
            if self._auto_rotate(drag_delta):
                # Rotation changed the bbox — force a full re-prime next tick.
                self.collision_resolver.invalidate(self.selected_obj)

    # Coin3D displacement nodes for physics-pushed parts

    def _ensure_coin_disp_node(self, obj):
        """Inject a SoTranslation at index 0 of obj's RootNode if not already done."""
        if not coin:
            return
        key = id(obj)
        if key in self._coin_disp_nodes:
            return
        try:
            root = obj.ViewObject.RootNode
            trans = coin.SoTranslation()
            trans.translation.setValue(0, 0, 0)
            root.insertChild(trans, 0)
            cached = self.collision_resolver._base_cache.get(key)
            if cached:
                self._coin_disp_starts[key] = (cached[0], cached[1])
            else:
                b = obj.Placement.Base
                self._coin_disp_starts[key] = (b.x, b.y)
            self._coin_disp_nodes[key] = (trans, obj)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"[Coin3D] inject failed for {obj.Label}: {e}\n")

    def _sync_coin_disp(self, obj):
        """Update obj's SoTranslation from its current cache position."""
        key = id(obj)
        entry = self._coin_disp_nodes.get(key)
        if not entry:
            return
        trans, _ = entry
        cached = self.collision_resolver._base_cache.get(key)
        start = self._coin_disp_starts.get(key)
        if cached and start:
            trans.translation.setValue(cached[0] - start[0], cached[1] - start[1], 0)

    def _detach_all_coin_disp_nodes(self):
        """Remove all injected SoTranslation nodes and reset their objects' visual offset."""
        for key, (trans, obj) in list(self._coin_disp_nodes.items()):
            try:
                obj.ViewObject.RootNode.removeChild(trans)
            except Exception:
                pass  # Coin3D RootNode or trans node already removed or torn down
        self._coin_disp_nodes.clear()
        self._coin_disp_starts.clear()

    def _commit_displaced_placements(self):
        """Write each displaced part's logical position (from cache) to obj.Placement,
        then detach the Coin3D nodes."""
        for key, (trans, obj) in list(self._coin_disp_nodes.items()):
            cached = self.collision_resolver._base_cache.get(key)
            if cached:
                try:
                    pl = obj.Placement
                    pl.Base = FreeCAD.Vector(cached[0], cached[1], pl.Base.z)
                    obj.Placement = pl
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"[Coin3D] commit failed for {obj.Label}: {e}\n")
        self._detach_all_coin_disp_nodes()

    def _set_part_highlight(self, obj, invalid):
        """Color the dragged part red to signal an impossible placement, restore otherwise."""
        if not obj or not hasattr(obj, 'ViewObject') or not obj.ViewObject:
            return
        if invalid:
            if self._dragged_original_color is None:
                try:
                    self._dragged_original_color = obj.ViewObject.ShapeColor
                except Exception:
                    pass  # ViewObject deleted or ShapeColor unavailable
            try:
                obj.ViewObject.ShapeColor = (1.0, 0.0, 0.0)
            except Exception:
                pass  # ViewObject deleted during drag
        else:
            if self._dragged_original_color is not None:
                try:
                    obj.ViewObject.ShapeColor = self._dragged_original_color
                except Exception:
                    pass  # ViewObject deleted during drag
                self._dragged_original_color = None

    def _get_shape_bbox(self, obj, parent_placement=None):
        """Returns the global BoundBox for an object, prioritizing BoundaryObject.

        Transforms local BoundBox corners through the full placement (including
        rotation) so that the returned axis-aligned BoundBox is correct even for
        rotated parts.

        IMPORTANT: Group recursion is checked BEFORE raw Shape because
        App::Part.Shape returns a compound in global coordinates (already
        includes the container's Placement).  Using it with _transform_bbox
        would double-apply the placement, causing mirror/drift on clamp.
        """
        # Accumulate placement through the hierarchy
        if parent_placement is None:
            current_placement = obj.Placement
        else:
            current_placement = parent_placement.multiply(obj.Placement)

        if hasattr(obj, "BoundaryObject") and obj.BoundaryObject and hasattr(obj.BoundaryObject.Shape, "BoundBox"):
            bb = obj.BoundaryObject.Shape.BoundBox
            # BoundaryObject placement is relative to its parent (obj)
            full_placement = current_placement.multiply(obj.BoundaryObject.Placement)
            return self._transform_bbox(bb, full_placement)

        #    because container.Shape is a global compound (double-transform bug).
        if hasattr(obj, "Group"):
            for child in obj.Group:
                result = self._get_shape_bbox(child, current_placement)
                if result:
                    return result

        if hasattr(obj, "Shape") and hasattr(obj.Shape, "BoundBox"):
            bb = obj.Shape.BoundBox
            return self._transform_bbox(bb, current_placement)

        if obj.Name not in self.warned_missing_bounds:
            FreeCAD.Console.PrintWarning(f"Manual Nester: Part '{obj.Label}' has no bounds (BoundaryObject or Shape) and will not participate in collisions.\n")
            self.warned_missing_bounds.add(obj.Name)

        return None

    @staticmethod
    def _transform_bbox(bb, placement):
        """Transform 8 local BoundBox corners through *placement* and return
        the axis-aligned BoundBox that encloses them."""
        corners = [
            FreeCAD.Vector(bb.XMin, bb.YMin, bb.ZMin),
            FreeCAD.Vector(bb.XMax, bb.YMin, bb.ZMin),
            FreeCAD.Vector(bb.XMin, bb.YMax, bb.ZMin),
            FreeCAD.Vector(bb.XMax, bb.YMax, bb.ZMin),
            FreeCAD.Vector(bb.XMin, bb.YMin, bb.ZMax),
            FreeCAD.Vector(bb.XMax, bb.YMin, bb.ZMax),
            FreeCAD.Vector(bb.XMin, bb.YMax, bb.ZMax),
            FreeCAD.Vector(bb.XMax, bb.YMax, bb.ZMax),
        ]
        transformed = [placement.multVec(c) for c in corners]
        xs = [v.x for v in transformed]
        ys = [v.y for v in transformed]
        zs = [v.z for v in transformed]
        return FreeCAD.BoundBox(min(xs), min(ys), min(zs),
                                max(xs), max(ys), max(zs))

    def _get_obj_phys_info(self, obj):
        """Returns (center_vector, width, height) in layout-global coordinates. Returns None if no bounds."""
        bb = self._get_shape_bbox(obj)
        if not bb:
            return None

        center = FreeCAD.Vector((bb.XMin + bb.XMax) / 2.0, (bb.YMin + bb.YMax) / 2.0, 0)
        return center, bb.XLength, bb.YLength

    def _get_local_bbox_dims(self, obj):
        """Returns (width, height) of obj in local (un-rotated) coordinates.

        Walks the same hierarchy as _get_shape_bbox but reads the raw Shape.BoundBox
        without applying the placement rotation, so the result reflects the canonical
        dimensions of the part's geometry rather than its current world orientation.
        Returns None if no bounds found.
        """
        if hasattr(obj, "BoundaryObject") and obj.BoundaryObject and \
                hasattr(obj.BoundaryObject.Shape, "BoundBox"):
            bb = obj.BoundaryObject.Shape.BoundBox
            return bb.XLength, bb.YLength

        if hasattr(obj, "Group"):
            for child in obj.Group:
                result = self._get_local_bbox_dims(child)
                if result:
                    return result

        if hasattr(obj, "Shape") and hasattr(obj.Shape, "BoundBox"):
            bb = obj.Shape.BoundBox
            return bb.XLength, bb.YLength

        return None

    # Operation lifecycle

    def cancel_operation(self):
        if self.input.mode == "IDLE" and not self.input.is_free_grab:
            return

        try:
            if self.selected_obj and hasattr(self, 'start_placement') and self.start_placement:
                 try:
                     self.selected_obj.Placement = self.start_placement
                 except Exception as e:
                     FreeCAD.Console.PrintWarning(f"[ManualNesterTool] Failed to revert selection: {e}\n")
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"[ManualNesterTool] Cancel selection revert failed: {e}\n")

        FreeCAD.Console.PrintMessage("Operation Cancelled.\n")

        # M-009: Revert physics-displaced parts.
        # obj.Placement was never written during Coin3D drag, so visual revert is
        # just removing the injected SoTranslation nodes.  obj.Placement is already
        # at the original position, so no Placement write is needed.
        self._detach_all_coin_disp_nodes()
        self.pre_drag_placements = {}

        # Reset input state
        self.input.reset()
        self.start_placement = None
        self.start_pos = None
        if self.selected_obj:
            self._set_part_highlight(self.selected_obj, False)
        self.selected_obj = None
        self._drag_active_sheet = None
        self._dragged_original_color = None
        self._physics_logged_empty_sheet = None
        self._physics_logged_active_sheet = None
        self._physics_logged_result_sheet = None
        self._physics_logged_norange_sheet = None
        self._stop_physics_timer()
        self.collision_resolver.clear_cache()

        # Defer scene graph modification to avoid Coin3D crash inside callback
        QtCore.QTimer.singleShot(0, self._hide_radius_indicator)

    def finish_operation(self):
        # Commit Coin3D-displaced parts' logical positions to obj.Placement before
        # clearing state, so the session is saved with correct placements.
        self._commit_displaced_placements()
        self.input.finish()
        if self.selected_obj:
            self._set_part_highlight(self.selected_obj, False)
        self.selected_obj = None # Clear selection to prevent stickiness
        self.start_placement = None
        self.start_pos = None
        self.pre_drag_placements = {} # M-009: Clear pre-drag placements
        self._drag_active_sheet = None
        self._dragged_original_color = None
        self._physics_logged_empty_sheet = None
        self._physics_logged_active_sheet = None
        self._physics_logged_result_sheet = None
        self._physics_logged_norange_sheet = None
        self._stop_physics_timer()
        self.collision_resolver.clear_cache()
        try:
            self._hide_radius_indicator() # M-010
        except Exception:
            pass  # Radius indicator scene graph node already detached or view closed
        try:
            FreeCADGui.Selection.clearSelection() # Clear visual highlight
        except Exception as e:
            FreeCAD.Console.PrintLog(f"[ManualNesterTool] Selection clear failed: {e}\n")

    # Object picking

    def pick_object(self, pos):
        """Helper to find the draggable object at screen pos."""
        info = self.view.getObjectInfo(pos)
        if info and "Object" in info:
             clicked_obj = info["Object"]
             # Resolve strings
             if isinstance(clicked_obj, str):
                 if self.layout_group and hasattr(self.layout_group, 'Document'):
                     clicked_obj = self.layout_group.Document.getObject(clicked_obj)
                 else:
                     clicked_obj = FreeCAD.ActiveDocument.getObject(clicked_obj)

             if not clicked_obj: return None

             parent_obj_from_click = info.get("ParentObject")
             # Resolve parent object if it's a string
             if isinstance(parent_obj_from_click, str):
                 parent_obj_from_click = FreeCAD.ActiveDocument.getObject(parent_obj_from_click)

             # FILTER: Ignore sheets and sheet boundaries
             if clicked_obj.Label.startswith("Sheet_"):
                 return None
             if parent_obj_from_click and parent_obj_from_click.Label.startswith("Sheet_"):
                 parent_obj_from_click = None

             draggable = self.get_draggable_parent(clicked_obj, parent_obj_from_click)
             if draggable:
                 FreeCAD.Console.PrintMessage(f"Manual Nester: Resolved draggable -> {draggable.Label}\n")
             return draggable
        return None

    def get_draggable_parent(self, obj, parent_obj_from_click=None):
        """
        Determines the actual object to drag based on what was clicked.
        Always returns the HIGHEST tracked container in the hierarchy.
        """
        visited = set()
        highest_tracked = None
        p = obj
        while p and p not in visited:
             visited.add(p)
             if p in self.original_placements:
                 highest_tracked = p # Keep updating to find the absolute highest tracked

             if hasattr(p, "InList") and p.InList:
                 # Check all parents in InList (FreeCAD objects can have multiple parents/links)
                 found_higher = False
                 for parent in p.InList:
                     if parent in visited: continue
                     if parent in self.original_placements or any(anc in self.original_placements for anc in self._get_all_ancestors(parent)):
                         p = parent
                         found_higher = True
                         break
                 if not found_higher:
                     # If no tracked ancestor found in this branch, try walking up anyway
                     p = p.InList[0]
             else:
                 p = None

        if highest_tracked:
            return highest_tracked

        for tracked_obj in self.original_placements.keys():
            if hasattr(tracked_obj, "BoundaryObject") and tracked_obj.BoundaryObject == obj:
                return tracked_obj
            if hasattr(tracked_obj, "LabelObject") and tracked_obj.LabelObject == obj:
                return tracked_obj
            if hasattr(tracked_obj, "LinkedObject") and tracked_obj.LinkedObject == obj:
                return tracked_obj

        return None

    def _get_all_ancestors(self, obj):
        """Helper to get all ancestors of an object."""
        ancestors = set()
        stack = [obj]
        while stack:
            curr = stack.pop()
            if hasattr(curr, "InList"):
                for p in curr.InList:
                    if p not in ancestors:
                        ancestors.add(p)
                        stack.append(p)
        return ancestors

    # Session save / cancel / cleanup

    def save_placements(self):
        """Saves the new placements (implicitly applied to objects)."""
        if not self.layout_group:
            return

        self._remove_empty_sheets()
        FreeCAD.Console.PrintMessage(f"Manual Nester: Saved new placements for objects.\n")
        # If sheets were stacked, this move breaks the "stacked" state
        if hasattr(self.layout_group, 'IsStacked') and self.layout_group.IsStacked:
            self.layout_group.IsStacked = False
            FreeCAD.Console.PrintWarning("Layout is no longer considered stacked due to manual adjustment.\n")

    def cancel(self):
        """Reverts any changes made to the object placements and removes new objects."""
        if not self.layout_group:
            return

        if self.original_placements:
            for obj, placement in self.original_placements.items():
                try:
                    if obj and obj.Name and obj in self.layout_group.Document.Objects:
                        obj.Placement = placement
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"[ManualNesterTool] Placement revert failed (object might be deleted): {e}\n")

        # Remove new objects and track deleted names to purge from tracking dicts
        deleted_names = set()
        for obj in reversed(self.new_objects):
            try:
                deleted_names.add(obj.Name)

                # PURGE from tracking dicts BEFORE deleting in C++
                self.original_placements.pop(obj, None)
                self.original_visibilities.pop(obj, None)

                self.layout_group.Document.removeObject(obj.Name)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"[ManualNesterTool] Failed to remove new object: {e}\n")

        self.new_objects = []

        FreeCAD.Console.PrintMessage("Manual Nester: Transformations cancelled and new items removed.\n")

    def cleanup(self):
        """Removes the event callbacks from the view and restores original visibilities."""
        self._stop_physics_timer()
        self._physics_executor.shutdown(wait=False)
        self._detach_all_coin_disp_nodes()
        # Remove Coin3D radius indicator first
        self._hide_radius_indicator()

        # Deactivate input handling
        self.input.deactivate()

        self.original_placements = {}

        # Restore sheet boundary selectability
        if self.layout_group and hasattr(self.layout_group, "Group"):
            for sheet_group in self.layout_group.Group:
                try:
                    if sheet_group.Label.startswith("Sheet_"):
                        boundary = next((obj for obj in sheet_group.Group if obj.Label.startswith("Sheet_Boundary_")), None)
                        if boundary and hasattr(boundary, "ViewObject"):
                            if hasattr(boundary.ViewObject, "Selectable"):
                                boundary.ViewObject.Selectable = True
                except Exception:
                    pass  # Sheet group or boundary object deleted during cleanup

        # Restore original visibilities — wrapped in try/except per object
        # because cancel() may have already deleted some objects
        for obj, is_visible in self.original_visibilities.items():
            try:
                if not isinstance(obj, str) and hasattr(obj, "Name") and obj.Name:
                    if hasattr(obj, "ViewObject") and obj.ViewObject:
                        obj.ViewObject.Visibility = is_visible
            except Exception:
                pass  # Object was already deleted at C++ level
        self.original_visibilities = {}

        FreeCADGui.updateGui()
        self.layout_group = None

    # Sheet management

    def _find_sheet_at_pos(self, pos):
        """Finds the sheet group containing the given point."""
        for sheet_group in self.layout_group.Group:
            if sheet_group.isDerivedFrom("App::DocumentObjectGroup") and sheet_group.Label.startswith("Sheet_"):
                # Check boundary
                boundary = next((obj for obj in sheet_group.Group if obj.Label.startswith("Sheet_Boundary_")), None)
                if boundary:
                    # Shape.BoundBox already includes placement (world coords)
                    bb = boundary.Shape.BoundBox
                    if (pos.x >= bb.XMin and pos.x <= bb.XMax and
                            pos.y >= bb.YMin and pos.y <= bb.YMax):
                        return sheet_group
        return None

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
            label = sheet_group.Label
            # Remove children first (boundary, shapes group)
            for sub in reversed(sheet_group.Group):
                doc.removeObject(sub.Name)
            doc.removeObject(sheet_group.Name)
            FreeCAD.Console.PrintMessage(f"Manual Nester: Removed empty sheet '{label}'.\n")

    def _add_drop_zone_sheet(self):
        """Adds a fresh drop-zone sheet to the layout."""
        self._add_new_sheet()

    def _get_sheet_dimensions(self):
        """Returns (width, height) from the first existing sheet, or (1000, 1000) as default."""
        for child in self.layout_group.Group:
            if child.isDerivedFrom("App::DocumentObjectGroup") and child.Label.startswith("Sheet_"):
                boundary = next((c for c in child.Group if c.Label.startswith("Sheet_Boundary_")), None)
                if boundary and hasattr(boundary, "Shape"):
                    bb = boundary.Shape.BoundBox
                    return bb.XLength, bb.YLength
        return 1000.0, 1000.0

    def _add_new_sheet(self):
        """Adds a new sheet group with a boundary, positioned after the last existing sheet."""
        doc = self.layout_group.Document
        index = len([c for c in self.layout_group.Group if c.Label.startswith("Sheet_")]) + 1

        width, height = self._get_sheet_dimensions()

        sheet_group = doc.addObject("App::DocumentObjectGroup", f"Sheet_{index}")
        sheet_group.Label = f"Sheet_{index}"
        self.layout_group.addObject(sheet_group)

        # Find the rightmost edge and spacing of existing sheets
        max_right = 0.0
        spacing = width * 0.1  # default fallback
        sheet_origins = []
        for child in self.layout_group.Group:
            if child.isDerivedFrom("App::DocumentObjectGroup") and child.Label.startswith("Sheet_") and child != sheet_group:
                b = next((c for c in child.Group if c.Label.startswith("Sheet_Boundary_")), None)
                if b and hasattr(b, "Shape"):
                    bb = b.Shape.BoundBox
                    right_edge = bb.XMax          # BoundBox already includes Placement
                    if right_edge > max_right:
                        max_right = right_edge
                    sheet_origins.append(bb.XMin)  # Use actual left edge, not Placement.Base

        # Infer spacing from existing sheets if there are at least 2
        sheet_origins.sort()
        if len(sheet_origins) >= 2:
            spacing = sheet_origins[1] - sheet_origins[0] - width

        offset_x = max_right + spacing if max_right > 0 else 0.0

        # Add Boundary
        boundary = doc.addObject("Part::Feature", f"Sheet_Boundary_{index}")
        import Part
        boundary.Shape = Part.makePlane(width, height)
        boundary.Placement = FreeCAD.Placement(FreeCAD.Vector(offset_x, 0, 0), FreeCAD.Rotation())
        sheet_group.addObject(boundary)

        # Add Shapes Group
        shapes_group = doc.addObject("App::DocumentObjectGroup", f"Shapes_{index}")
        shapes_group.Label = f"Shapes_{index}"
        sheet_group.addObject(shapes_group)

        if hasattr(boundary, "ViewObject"):
            boundary.ViewObject.Transparency = 75
            if hasattr(boundary.ViewObject, "Selectable"):
                boundary.ViewObject.Selectable = False # Make sheet unselectable natively

        self.new_objects.append(sheet_group)
        self.new_objects.append(boundary)
        self.new_objects.append(shapes_group)
        return sheet_group

    # Coin3D overlays

    def _show_radius_indicator(self, center, radius):
        """M-010: Creates/updates a Coin3D indicator for the physics radius."""
        if not coin or not self.view:
            return

        scene = self.view.getSceneGraph()
        if not scene:
            return

        if not self.radius_indicator:
            # Create the node tree
            self.radius_indicator = coin.SoSeparator()

            # Line style
            style = coin.SoDrawStyle()
            style.lineWidth = 1.0
            style.linePattern = 0xF0F0 # Dashed
            self.radius_indicator.addChild(style)

            # Color
            color = coin.SoBaseColor()
            color.rgb = (0.0, 0.5, 1.0) # Light blue
            self.radius_indicator.addChild(color)

            # Translation
            self.indicator_trans = coin.SoTranslation()
            self.radius_indicator.addChild(self.indicator_trans)

            # Circle coordinates
            self.indicator_coords = coin.SoCoordinate3()
            self.radius_indicator.addChild(self.indicator_coords)

            # Line set
            line_set = coin.SoLineSet()
            self.radius_indicator.addChild(line_set)

            scene.addChild(self.radius_indicator)
            # Fresh node — force circle geometry recomputation regardless of cached radius.
            self._indicator_last_radius = None
        else:
            try:
                if scene.findChild(self.radius_indicator) < 0:
                    # Node exists but was orphaned (e.g., scene graph rebuilt by FreeCAD).
                    scene.addChild(self.radius_indicator)
            except Exception:
                pass  # Scene graph node query/addition error on torn down scene

        # Update position
        self.indicator_trans.translation.setValue(center.x, center.y, center.z + 0.1) # Slightly above XY

        # Only recompute circle geometry when radius changes (trig is expensive per-frame)
        if getattr(self, '_indicator_last_radius', None) != radius:
            points = []
            segments = 64
            for i in range(segments + 1):
                angle = 2.0 * math.pi * i / segments
                points.append((radius * math.cos(angle), radius * math.sin(angle), 0))
            self.indicator_coords.point.setValues(0, len(points), points)
            self._indicator_last_radius = radius

    def _hide_radius_indicator(self):
        """M-010: Removes the radius indicator from the view."""
        if self.radius_indicator and self.view:
            try:
                self.view.getSceneGraph().removeChild(self.radius_indicator)
            except Exception:
                pass  # Scene graph may already be torn down
            self.radius_indicator = None

    # Master shape cloning

    def _clone_part_from_master(self, master_obj):
        """Creates a clone of a master shape to be placed in the layout."""
        doc = self.layout_group.Document

        master_container = master_obj
        if not master_obj.isDerivedFrom("App::Part"):
            if master_obj.InList:
                for p in master_obj.InList:
                    if p.isDerivedFrom("App::Part"):
                        master_container = p
                        break

        # Find the actual shape object inside the master container
        master_shape_feature = None
        for child in master_container.Group:
            if child.Label.startswith("master_shape_"):
                master_shape_feature = child
                break
        if not master_shape_feature: master_shape_feature = master_obj

        part_label = master_container.Label.replace("master_", "")
        count = len(doc.findObjects(Label=f"nested_{part_label}_*")) + 1

        container = doc.addObject("App::Part", f"nested_{part_label}_{count}")
        container.Label = f"nested_{part_label}_{count}"

        nested_part = doc.addObject("Part::Feature", f"part_{part_label}_{count}")
        nested_part.Shape = master_shape_feature.Shape.copy()
        nested_part.Placement = master_shape_feature.Placement.copy()
        container.addObject(nested_part)

        if hasattr(master_shape_feature, "BoundaryObject") and master_shape_feature.BoundaryObject:
            bound_copy = doc.addObject("Part::Feature", f"boundary_{part_label}_{count}")
            bound_copy.Shape = master_shape_feature.BoundaryObject.Shape.copy()
            container.addObject(bound_copy)
            if not hasattr(nested_part, "BoundaryObject"):
                nested_part.addProperty("App::PropertyLink", "BoundaryObject", "Nesting", "Boundary object")
            nested_part.BoundaryObject = bound_copy
            if hasattr(bound_copy, "ViewObject"): bound_copy.ViewObject.Visibility = False

        # Track for revert
        self.new_objects.append(container)
        self.new_objects.append(nested_part)
        if hasattr(nested_part, "BoundaryObject") and nested_part.BoundaryObject:
            self.new_objects.append(nested_part.BoundaryObject)

        # Initial placement at cursor
        container.Placement = FreeCAD.Placement(self.start_pos, FreeCAD.Rotation())

        # Track for dragging
        self._track_single_object(container, None)
        return container
