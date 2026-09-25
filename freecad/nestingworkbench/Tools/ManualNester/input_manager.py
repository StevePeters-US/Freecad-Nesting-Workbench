# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/ManualNester/input_manager.py

"""
Input manager for the Manual Nester tool.

Handles Qt-native viewport events dispatched from NWInputManager and
dispatches semantic high-level actions to registered handlers. Owns all
transient input state: mode, constraints, drag detection, free-grab flag.
"""

import FreeCAD
from PySide import QtCore
import math
import time

from freecad.nestingworkbench.core.input.nw_input_manager import NWInputManager


class InputManager:
    """
    Translates viewport Qt events into high-level manual-nester actions.

    Registerable actions
    --------------------
    click(pos)            Left-button down after guards pass.
    release()             Left-button up.
    move(pos, ctrl_held, shift)  Mouse move during active drag / free-grab.
    cancel()              Escape key or right-click during operation.
    confirm()             Enter / Return key.
    scroll_radius(delta)  Ctrl + horizontal mouse move / scroll wheel (delta in mm).
    constraint_toggle(axis)  X or Y key pressed in TRANSLATE mode.
    mode_switched(pos)    Mode changed mid-drag (Shift held/released).
    context_menu(event_dict) Right-click release in viewport.
    """

    DRAG_THRESHOLD = 5              # pixels before a click becomes a drag
    REPEAT_PRESS_GUARD_S = 0.2      # ignore a second mouse-down this soon after the first
    RADIUS_MM_PER_PIXEL = 3.0       # Ctrl + horizontal drag sensitivity
    RADIUS_MM_PER_WHEEL_STEP = 25.0 # Ctrl + wheel notch

    def __init__(self, view=None):
        self.view = view
        self._handlers = {}
        self._active = False

        self.mode = "IDLE"          # IDLE | TRANSLATE | ROTATE
        self.constraint = None      # None | "X" | "Y"
        self.constraint_lock_pos = None  # FreeCAD.Vector when constraint activated
        self.is_mouse_down = False
        self.is_free_grab = False
        self.is_implicit_drag = False
        self.last_down_time = 0.0
        self.drag_start_screen_pos = (0, 0)
        self.last_known_screen_pos = (0, 0)
        self._ctrl_adjusting_radius = False  # True while Ctrl is held during TRANSLATE
        self._ctrl_prev_pos = None           # Previous screen pos for Ctrl-radius delta
        self._rmb_cancelled = False          # Latched at RMB press; cleared by RMB release/menu

    # Public API

    def on(self, action, handler):
        """Register *handler* for a high-level *action*."""
        self._handlers[action] = handler

    def activate(self):
        """Start listening for viewport events via NWInputManager."""
        if self._active:
            return
        NWInputManager.get_instance().initialize()
        NWInputManager.get_instance().set_active_handler(self)
        self._active = True

    def deactivate(self):
        """Stop listening for viewport events."""
        if not self._active:
            return
        mgr = NWInputManager.get_instance()
        mgr.clear_active_handler()
        mgr.restore()
        self._active = False

    def set_mode(self, mode):
        """Change interaction mode and clear constraints."""
        self.mode = mode
        self.constraint = None
        self.constraint_lock_pos = None
        if mode in ("TRANSLATE", "ROTATE"):
            FreeCAD.Console.PrintMessage(
                f"Manual Nester: {mode} Mode (Release to Drop)\n"
            )

    def set_constraint(self, axis, lock_pos=None):
        """Toggle an axis constraint. *lock_pos* is the object position to lock to."""
        if self.constraint == axis:
            self.constraint = None
            self.constraint_lock_pos = None
            FreeCAD.Console.PrintMessage("Constraint Cleared.\n")
        else:
            self.constraint = axis
            self.constraint_lock_pos = lock_pos
            FreeCAD.Console.PrintMessage(f"Constraint: {axis}-Axis Locked.\n")

    def set_free_grab(self, enabled):
        """Enable / disable free-grab (click-to-place) mode."""
        self.is_free_grab = enabled

    def finish(self):
        """Reset to IDLE after a successfully completed operation."""
        self.mode = "IDLE"
        self.constraint = None
        self.constraint_lock_pos = None
        self.is_implicit_drag = False
        self.is_free_grab = False
        self.is_mouse_down = False
        self._ctrl_adjusting_radius = False
        self._ctrl_prev_pos = None
        # Note: finish() and reset() must NOT clear self._rmb_cancelled.
        # They run inside the RMB press handler (via cancel_operation), between
        # the latch being set and the RMB release reading it. Clearing it here
        # would recreate the RMB double-fire defect (cancel + context menu both firing).

    def reset(self):
        """Hard-reset all input state (used on cancel)."""
        self.finish()

    # NWInputManager Tool Hooks

    def on_mouse_press(self, event_dict):
        """Handle mouse button press events."""
        pos = event_dict.get("Position", (0, 0))
        btn = event_dict.get("Button")

        if btn == QtCore.Qt.LeftButton:
            current_time = time.time()

            # Guard: rapid repeat DOWN events
            if self.is_mouse_down and (current_time - self.last_down_time < self.REPEAT_PRESS_GUARD_S):
                return True
            self.last_down_time = current_time

            # Guard: double-click
            if event_dict.get("DoubleClick", False):
                return True

            self.is_mouse_down = True
            self.drag_start_screen_pos = pos

            # Initial mode from Shift key
            shift = NWInputManager.get_instance().is_shift_down()
            if shift:
                self.set_mode("ROTATE")
            else:
                self.set_mode("TRANSLATE")

            self._emit("click", pos)
            return True

        elif btn == QtCore.Qt.RightButton:
            self._rmb_cancelled = self.mode != "IDLE" or self.is_free_grab
            if self._rmb_cancelled:
                self._emit("cancel")
                return True
            return False

        return False

    def on_mouse_release(self, event_dict):
        """Handle mouse button release events."""
        btn = event_dict.get("Button")

        if btn == QtCore.Qt.LeftButton:
            if self.is_mouse_down:
                FreeCAD.Console.PrintMessage("Manual Nester: Mouse UP received.\n")
                self.is_mouse_down = False
                self._emit("release")
            return True

        return False

    def on_mouse_move(self, event_dict):
        """Handle mouse move events."""
        pos = event_dict.get("Position", (0, 0))
        self.last_known_screen_pos = pos
        shift = NWInputManager.get_instance().is_shift_down()
        ctrl = NWInputManager.get_instance().is_ctrl_down()

        # Only process when there is an active interaction
        if not self.is_mouse_down and not self.is_free_grab:
            return False

        active_drag = self.is_implicit_drag or self.is_free_grab

        # Ctrl during TRANSLATE: horizontal mouse movement adjusts the influence radius.
        # Part stays put; drag is rebaselined when Ctrl is released.
        if active_drag and self.mode == "TRANSLATE":
            if ctrl:
                if not self._ctrl_adjusting_radius:
                    self._ctrl_adjusting_radius = True
                    self._ctrl_prev_pos = pos
                dx = pos[0] - (self._ctrl_prev_pos[0] if self._ctrl_prev_pos else pos[0])
                self._ctrl_prev_pos = pos
                if dx != 0:
                    self._emit("scroll_radius", dx * self.RADIUS_MM_PER_PIXEL)
                return True  # consume — don't move the part
            elif self._ctrl_adjusting_radius:
                # Ctrl released — exit radius-adjust and rebaseline the drag
                self._ctrl_adjusting_radius = False
                self._ctrl_prev_pos = None
                self._emit("mode_switched", pos)

        # Dynamic mode switch during an active drag (Shift → ROTATE)
        target_mode = "ROTATE" if shift else "TRANSLATE"
        if self.mode != target_mode and active_drag:
            FreeCAD.Console.PrintMessage(
                f"Manual Nester: Mode switched to {target_mode} while dragging.\n"
            )
            self.set_mode(target_mode)
            self.drag_start_screen_pos = pos
            self._emit("mode_switched", pos)

        # Drag-threshold detection (skip for free-grab)
        if not self.is_implicit_drag and not self.is_free_grab:
            dx = pos[0] - self.drag_start_screen_pos[0]
            dy = pos[1] - self.drag_start_screen_pos[1]
            if math.sqrt(dx * dx + dy * dy) > self.DRAG_THRESHOLD:
                self.is_implicit_drag = True
                FreeCAD.Console.PrintMessage(
                    f"Manual Nester: Drag threshold met in {self.mode}\n"
                )

        if not self.is_implicit_drag and not self.is_free_grab:
            return False

        self._emit("move", pos, ctrl, shift)

        if self.mode != "IDLE" or self.is_free_grab:
            return True
        return False

    def on_key_press(self, event_dict):
        """Handle keyboard press events."""
        key = event_dict.get("Key")

        if key == QtCore.Qt.Key_Escape:
            self._emit("cancel")
            return True

        if key == QtCore.Qt.Key_X and self.mode == "TRANSLATE":
            self._emit("constraint_toggle", "X")
            return True

        if key == QtCore.Qt.Key_Y and self.mode == "TRANSLATE":
            self._emit("constraint_toggle", "Y")
            return True

        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self._emit("confirm")
            return True

        return False

    def on_key_release(self, event_dict):
        """Handle keyboard release events."""
        return False

    def on_wheel(self, event_dict):
        """Handle wheel events."""
        ctrl = NWInputManager.get_instance().is_ctrl_down()
        if ctrl:
            delta_val = event_dict.get("Delta", 0)
            if delta_val == 0:
                return True  # Ctrl held, no vertical component: consume, don't resize
            delta = self.RADIUS_MM_PER_WHEEL_STEP if delta_val > 0 else -self.RADIUS_MM_PER_WHEEL_STEP
            self._emit("scroll_radius", delta)
            return True
        return False

    def on_context_menu(self, event_dict):
        """Show the context menu — only when idle; RMB cancels an active drag."""
        if self._rmb_cancelled:
            self._rmb_cancelled = False
            return
        self._emit("context_menu", event_dict)

    # Helpers

    def _emit(self, action, *args):
        """Call the registered handler for *action*, if any."""
        handler = self._handlers.get(action)
        if handler:
            handler(*args)
