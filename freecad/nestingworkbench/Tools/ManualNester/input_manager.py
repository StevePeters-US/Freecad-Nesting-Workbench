# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/Tools/ManualNester/input_manager.py

"""
Input manager for the Manual Nester tool.

Handles raw Coin3D events (mouse, keyboard) and dispatches high-level
actions to registered handlers. Owns all transient input state: mode,
constraints, drag detection, free-grab flag.

The tool registers callbacks for semantic actions and reads input state
(e.g. ``im.mode``, ``im.constraint``) when computing movement vectors.
"""

import FreeCAD
from PySide import QtCore, QtWidgets
import math
import time

# Coin3D event dicts do NOT reliably carry modifier-key state.
# Query Qt directly instead — same pattern as the button-state poll.
def _qt_modifiers():
    """Return (shift, ctrl) booleans from Qt's live modifier state."""
    mods = QtWidgets.QApplication.queryKeyboardModifiers()
    return (
        bool(mods & QtCore.Qt.ShiftModifier),
        bool(mods & QtCore.Qt.ControlModifier),
    )

class InputManager:
    """
    Translates Coin3D events into high-level manual-nester actions.

    Registerable actions
    --------------------
    click(pos)            Left-button down after guards pass.
    release()             Left-button up.
    move(pos, snap, shift)  Mouse move during active drag / free-grab.
    cancel()              Escape key or right-click during operation.
    confirm()             Enter / Return key.
    scroll_radius(delta)  Ctrl + scroll wheel (delta in mm).
    force_drop()          Deferred recovery from a missed mouse-up.
    constraint_toggle(axis)  X or Y key pressed in TRANSLATE mode.
    mode_switched(pos)    Mode changed mid-drag (Shift held/released).
    """

    DRAG_THRESHOLD = 5  # pixels before a click becomes a drag

    def __init__(self, view):
        self.view = view
        self._callback_ids = []
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

        # Poll timer: FreeCAD's viewport navigation can swallow the mouse-UP
        # Coin3D event. We poll Qt's actual button state to catch missed UPs.
        self._button_poll_timer = QtCore.QTimer()
        self._button_poll_timer.timeout.connect(self._poll_button_state)

    # Public API

    def on(self, action, handler):
        """Register *handler* for a high-level *action*."""
        self._handlers[action] = handler

    def activate(self):
        """Start listening for Coin3D events on the view."""
        if self._active:
            return
        for evt in ("SoMouseButtonEvent", "SoLocation2Event", "SoKeyboardEvent"):
            cb_id = self.view.addEventCallback(evt, self._make_callback(evt))
            self._callback_ids.append((evt, cb_id))
        self._active = True

    def deactivate(self):
        """Remove all Coin3D event callbacks."""
        self._button_poll_timer.stop()
        for evt, cb_id in self._callback_ids:
            try:
                self.view.removeEventCallback(evt, cb_id)
            except Exception as e:
                FreeCAD.Console.PrintWarning(
                    f"[InputManager] Could not remove {evt} callback: {e}\n"
                )
        self._callback_ids = []
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
        """Toggle an axis constraint.  *lock_pos* is the object position to lock to."""
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
        self._button_poll_timer.stop()
        self.mode = "IDLE"
        self.constraint = None
        self.constraint_lock_pos = None
        self.is_implicit_drag = False
        self.is_free_grab = False
        self.is_mouse_down = False
        self._ctrl_adjusting_radius = False
        self._ctrl_prev_pos = None

    def reset(self):
        """Hard-reset all input state (used on cancel)."""
        self.finish()

    # Internal — Coin3D callback wiring

    def _poll_button_state(self):
        """Detect missed mouse-UP events by polling Qt's actual button state."""
        if not self.is_mouse_down:
            self._button_poll_timer.stop()
            return
        try:
            buttons = QtWidgets.QApplication.mouseButtons()
            if not (buttons & QtCore.Qt.LeftButton):
                self._button_poll_timer.stop()
                self.is_mouse_down = False
                self._emit("release")
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"[InputManager] Poll error: {e}\n")
            self._button_poll_timer.stop()

    def _make_callback(self, event_type):
        """Return a closure that tags the raw dict with *event_type*."""
        def callback(event_dict):
            return self._dispatch(event_type, event_dict)
        return callback

    def _dispatch(self, event_type, event_dict):
        """Top-level dispatcher — routes to the correct sub-handler."""
        try:
            if event_type == "SoKeyboardEvent":
                return self._handle_keyboard(event_dict)
            elif event_type == "SoMouseButtonEvent":
                return self._handle_mouse_button(event_dict)
            elif event_type == "SoLocation2Event":
                return self._handle_mouse_move(event_dict)
            return False
        except Exception as e:
            FreeCAD.Console.PrintWarning(
                f"[InputManager] Event dispatch failed: {e}\n"
            )
            return False

    # Keyboard

    def _handle_keyboard(self, event_dict):
        if event_dict["State"] != "DOWN":
            return False

        key = str(event_dict["Key"]).upper()

        if key == "ESCAPE":
            self._emit("cancel")
            return True

        if key == "X" and self.mode == "TRANSLATE":
            self._emit("constraint_toggle", "X")
            return True

        if key == "Y" and self.mode == "TRANSLATE":
            self._emit("constraint_toggle", "Y")
            return True

        if key in ("RETURN", "ENTER"):
            self._emit("confirm")
            return True

        return False

    # Mouse buttons

    def _handle_mouse_button(self, event_dict):
        pos = event_dict.get("Position", (0, 0))
        btn = event_dict.get("Button")
        state = event_dict.get("State")

        if btn in ("BUTTON1", 1):
            if state == "DOWN":
                current_time = time.time()

                # Guard: rapid repeat DOWN events
                if self.is_mouse_down and (current_time - self.last_down_time < 0.2):
                    return True
                self.last_down_time = current_time

                # Guard: missed UP — force-drop via QTimer so we don't
                # modify the Coin3D scene graph inside its own callback.
                if self.is_mouse_down:
                    if self.is_implicit_drag or self.is_free_grab:
                        FreeCAD.Console.PrintMessage(
                            "Manual Nester: Forcing drop (missed UP event).\n"
                        )
                        QtCore.QTimer.singleShot(0, lambda: self._emit("force_drop"))
                        return True

                # Guard: double-click
                if event_dict.get("DoubleClick", False):
                    return True

                self.is_mouse_down = True
                self.drag_start_screen_pos = pos
                self._button_poll_timer.start(30)  # Watch for missed UP events

                # Initial mode from Shift key — read from Qt, not Coin3D event dict
                # (Coin3D event dicts don't reliably carry modifier state)
                shift, _ctrl = _qt_modifiers()
                if shift:
                    self.set_mode("ROTATE")
                else:
                    self.set_mode("TRANSLATE")

                self._emit("click", pos)
                return True

            else:  # UP
                if self.is_mouse_down:
                    FreeCAD.Console.PrintMessage("Manual Nester: Mouse UP received.\n")
                    self._button_poll_timer.stop()
                    self.is_mouse_down = False
                    self._emit("release")
                return True

        elif btn in ("BUTTON2", "BUTTON3", 2, 3):
            if state == "DOWN":
                if self.mode != "IDLE" or self.is_free_grab:
                    self._emit("cancel")
                return True
            else:
                return True  # consume UP to suppress context menu

        elif btn in ("BUTTON4", "BUTTON5", 4, 5):
            _shift, ctrl = _qt_modifiers()
            if state == "DOWN" and ctrl:
                delta = 25.0 if btn in ("BUTTON4", 4) else -25.0
                self._emit("scroll_radius", delta)
                return True
            # Without Ctrl, don't consume — let FreeCAD handle zoom

        return False

    # Mouse movement

    def _handle_mouse_move(self, event_dict):
        pos = event_dict["Position"]
        self.last_known_screen_pos = pos
        # Read modifiers from Qt — Coin3D event dict omits them during drag
        shift, ctrl = _qt_modifiers()

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
                    self._emit("scroll_radius", dx * 3.0)  # 3 mm per pixel
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

    # Helpers

    def _emit(self, action, *args):
        """Call the registered handler for *action*, if any."""
        handler = self._handlers.get(action)
        if handler:
            handler(*args)
