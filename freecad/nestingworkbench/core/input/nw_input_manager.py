# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/core/input/nw_input_manager.py

"""
Single-owner Qt-native viewport input manager for Nesting Workbench.

Installs an event filter on QApplication to capture mouse, keyboard, and
context-menu events in the 3D viewport, dispatching them to an active tool
handler. Eliminates the dual-pipeline conflicts inherent in Coin3D callbacks.
"""

import FreeCAD
try:
    import FreeCADGui
except ImportError:
    FreeCADGui = None

from PySide import QtCore, QtGui, QtWidgets


def _qt_alive(widget):
    """True if widget's C++ object still exists."""
    if widget is None:
        return False
    try:
        return bool(widget.isWidgetType())
    except (RuntimeError, AttributeError):
        return False


def _is_qwindow(obj):
    """True if obj is a QWindow."""
    qwindow_cls = getattr(QtGui, "QWindow", None)
    if isinstance(qwindow_cls, type):
        try:
            return isinstance(obj, qwindow_cls)
        except TypeError:
            return False
    return False


def _is_text_input(obj):
    """True if obj is an active text input widget."""
    if obj is None:
        return False
    text_classes = (
        getattr(QtWidgets, "QLineEdit", None),
        getattr(QtWidgets, "QTextEdit", None),
        getattr(QtWidgets, "QPlainTextEdit", None),
        getattr(QtWidgets, "QAbstractSpinBox", None),
    )
    valid_classes = tuple(c for c in text_classes if isinstance(c, type))
    if valid_classes:
        try:
            if isinstance(obj, valid_classes):
                return True
        except TypeError:
            # Shim/mocked Qt class isn't a real type — treat as not a text widget.
            pass
    return False


def _wheel_delta(event):
    """Extract vertical scroll delta from QWheelEvent."""
    if hasattr(event, "angleDelta"):
        try:
            ad = event.angleDelta()
            if hasattr(ad, "y"):
                return ad.y()
        except Exception:
            # Not a Qt5+ wheel event — try the Qt4 delta() below.
            pass
    if hasattr(event, "delta"):
        try:
            return event.delta()
        except Exception:
            # No usable delta — treat as no scroll.
            pass
    return 0


class NWInputManager(QtCore.QObject):
    """QApplication-level event filter managing viewport input capture."""

    _instance = None

    @classmethod
    def get_instance(cls):
        """Return singleton instance of NWInputManager."""
        if cls._instance is None:
            cls._instance = NWInputManager()
        return cls._instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_initialized = False
        self._active_handler = None
        self._shift_down = False
        self._control_down = False
        self._left_mouse_down = False
        self._middle_mouse_down = False
        self._right_mouse_down = False
        self._last_qt_pos = (0, 0)
        self._cached_viewport = None

    def _reset_state(self):
        """Forget all tracked input state — nothing is held while we're not filtering."""
        self._shift_down = False
        self._control_down = False
        self._left_mouse_down = False
        self._middle_mouse_down = False
        self._right_mouse_down = False
        self._cached_viewport = None

    # Lifecycle

    def initialize(self):
        """Install global event filter on QApplication."""
        try:
            self._reset_state()
            if not self._is_initialized:
                app = QtWidgets.QApplication.instance()
                if app:
                    app.installEventFilter(self)
                self._is_initialized = True
        except Exception as e:
            FreeCAD.Console.PrintError(f"[NWInputManager] Initialization error: {e}\n")

    def restore(self):
        """Remove global event filter from QApplication."""
        try:
            if self._is_initialized:
                app = QtWidgets.QApplication.instance()
                if app:
                    app.removeEventFilter(self)
                self._is_initialized = False
            self._reset_state()
        except Exception as e:
            FreeCAD.Console.PrintError(f"[NWInputManager] Restore error: {e}\n")

    # Handler Registration

    def set_active_handler(self, handler):
        """Set the active interactive tool handler."""
        self._active_handler = handler

    def clear_active_handler(self):
        """Clear the active interactive tool handler."""
        self._active_handler = None

    def get_active_handler(self):
        """Return the current active interactive tool handler."""
        return self._active_handler

    # State Queries (single source of truth)

    def is_shift_down(self):
        """Return True if Shift key is currently held."""
        return self._shift_down

    def is_ctrl_down(self):
        """Return True if Control key is currently held."""
        return self._control_down

    def is_left_mouse_down(self):
        """Return True if Left Mouse Button is currently held."""
        return self._left_mouse_down

    def is_right_mouse_down(self):
        """Return True if Right Mouse Button is currently held."""
        return self._right_mouse_down

    def get_mouse_pos(self, event_dict=None):
        """Return latest normalized viewport coordinates (x, y)."""
        if event_dict and "Position" in event_dict:
            self._last_qt_pos = event_dict["Position"]
        return self._last_qt_pos

    # Event Filter

    def eventFilter(self, obj, event):
        """Intercept and dispatch Qt events."""
        try:
            is_key_event = event.type() in (
                QtCore.QEvent.KeyPress,
                QtCore.QEvent.KeyRelease,
                QtCore.QEvent.ShortcutOverride,
            )
            is_mouse_event = event.type() in (
                QtCore.QEvent.MouseMove,
                QtCore.QEvent.MouseButtonPress,
                QtCore.QEvent.MouseButtonRelease,
                QtCore.QEvent.MouseButtonDblClick,
                QtCore.QEvent.Wheel,
            )
            is_context_event = (event.type() == QtCore.QEvent.ContextMenu)

            if not is_key_event and not is_mouse_event and not is_context_event:
                return False

            # Application-level filters see every input event twice: once targeted at
            # the QWidgetWindow, then again at the QWidget. Act on the widget delivery
            # only, or every dispatch fires twice.
            if _is_qwindow(obj):
                return False

            # Track modifiers and mouse buttons globally
            if is_key_event:
                if event.key() == QtCore.Qt.Key_Shift:
                    self._shift_down = (event.type() == QtCore.QEvent.KeyPress)
                elif event.key() == QtCore.Qt.Key_Control:
                    self._control_down = (event.type() == QtCore.QEvent.KeyPress)

            if event.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease):
                is_press = (event.type() == QtCore.QEvent.MouseButtonPress)
                if event.button() == QtCore.Qt.LeftButton:
                    self._left_mouse_down = is_press
                elif event.button() == QtCore.Qt.MiddleButton:
                    self._middle_mouse_down = is_press
                elif event.button() == QtCore.Qt.RightButton:
                    self._right_mouse_down = is_press

            # If typing into an input field, do not intercept keyboard events
            if is_key_event or event.type() == QtCore.QEvent.ShortcutOverride:
                focus_w = QtWidgets.QApplication.focusWidget() if hasattr(QtWidgets.QApplication, "focusWidget") else None
                if _is_text_input(focus_w) or _is_text_input(obj):
                    return False

            # Viewport Detection
            if self._cached_viewport is not None and not _qt_alive(self._cached_viewport):
                self._cached_viewport = None

            if self._cached_viewport is None and FreeCADGui is not None:
                try:
                    av = FreeCADGui.activeView()
                    if av and hasattr(av, "getWidget"):
                        self._cached_viewport = av.getWidget()
                    elif av and hasattr(av, "graphicsView"):
                        gv = av.graphicsView()
                        if gv and hasattr(gv, "viewport"):
                            self._cached_viewport = gv.viewport()

                    obj_cls = obj.metaObject().className() if hasattr(obj, "metaObject") else ""
                    if "OpenGL" in obj_cls or "Quarter" in obj_cls:
                        vp_cls = ""
                        if self._cached_viewport and hasattr(self._cached_viewport, "metaObject"):
                            vp_cls = self._cached_viewport.metaObject().className()
                        if not self._cached_viewport or "View3D" in vp_cls:
                            self._cached_viewport = obj
                except Exception as e:
                    FreeCAD.Console.PrintLog(f"[NWInputManager] Viewport detection: {e}\n")

            is_viewport_event = False
            if self._cached_viewport:
                if obj == self._cached_viewport:
                    is_viewport_event = True
                elif hasattr(self._cached_viewport, "isAncestorOf"):
                    try:
                        is_viewport_event = self._cached_viewport.isAncestorOf(obj)
                    except Exception as e:
                        FreeCAD.Console.PrintLog(f"[NWInputManager] isAncestorOf check: {e}\n")

            # Standardize coordinates relative to viewport
            if is_mouse_event and is_viewport_event:
                try:
                    if hasattr(obj, "mapToGlobal"):
                        global_pos = obj.mapToGlobal(event.pos())
                    else:
                        global_pos = event.pos()

                    if self._cached_viewport and hasattr(self._cached_viewport, "mapFromGlobal"):
                        local_pos = self._cached_viewport.mapFromGlobal(global_pos)
                    else:
                        local_pos = event.pos()

                    if hasattr(local_pos, "x") and hasattr(local_pos, "y"):
                        self._last_qt_pos = (int(local_pos.x()), int(local_pos.y()))
                    elif isinstance(local_pos, (tuple, list)):
                        self._last_qt_pos = (int(local_pos[0]), int(local_pos[1]))
                except Exception as e:
                    FreeCAD.Console.PrintLog(f"[NWInputManager] Coordinate tracking: {e}\n")

            # Middle mouse MUST always reach FreeCAD's navigation system.
            if is_mouse_event:
                mid = QtCore.Qt.MiddleButton
                if (
                    event.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease)
                    and event.button() == mid
                ):
                    return False
                if event.type() == QtCore.QEvent.MouseMove and bool(event.buttons() & mid):
                    return False

            # Right-click context menu handling
            if (
                event.type() == QtCore.QEvent.MouseButtonRelease
                and event.button() == QtCore.Qt.RightButton
                and is_viewport_event
                and self._active_handler is not None
            ):
                event_dict = {
                    "Button": event.button(),
                    "Modifiers": (
                        event.modifiers()
                        if hasattr(event, "modifiers")
                        else QtCore.Qt.NoModifier
                    ),
                    "Position": self._last_qt_pos,
                }
                if hasattr(self._active_handler, "on_context_menu"):
                    self._active_handler.on_context_menu(event_dict)
                return True

            # Dispatch to active handler
            if self._active_handler is not None:
                # Keyboard events (ungated by viewport, protected by _is_text_input)
                if is_key_event:
                    if event.type() == QtCore.QEvent.KeyPress:
                        event_dict = {
                            "Button": QtCore.Qt.NoButton,
                            "Modifiers": (
                                event.modifiers()
                                if hasattr(event, "modifiers")
                                else QtCore.Qt.NoModifier
                            ),
                            "Position": self._last_qt_pos,
                            "Key": event.key() if hasattr(event, "key") else 0,
                            "Text": event.text() if hasattr(event, "text") else "",
                        }
                        if hasattr(self._active_handler, "on_key_press"):
                            if self._active_handler.on_key_press(event_dict):
                                return True
                        return False

                    elif event.type() == QtCore.QEvent.KeyRelease:
                        event_dict = {
                            "Button": QtCore.Qt.NoButton,
                            "Modifiers": (
                                event.modifiers()
                                if hasattr(event, "modifiers")
                                else QtCore.Qt.NoModifier
                            ),
                            "Position": self._last_qt_pos,
                            "Key": event.key() if hasattr(event, "key") else 0,
                        }
                        if hasattr(self._active_handler, "on_key_release"):
                            if self._active_handler.on_key_release(event_dict):
                                return True
                        return False

                # Context menu event (suppressed in viewport when handler is active)
                if is_context_event:
                    if is_viewport_event:
                        return True
                    return False

                # Mouse events (strictly gated on is_viewport_event)
                if is_viewport_event and is_mouse_event:
                    # Belt-and-suspenders middle mouse check
                    if event.type() in (
                        QtCore.QEvent.MouseButtonPress,
                        QtCore.QEvent.MouseButtonRelease,
                        QtCore.QEvent.MouseButtonDblClick,
                    ):
                        if event.button() == QtCore.Qt.MiddleButton:
                            return False

                    if event.type() == QtCore.QEvent.MouseMove:
                        if bool(event.buttons() & QtCore.Qt.MiddleButton):
                            return False

                    event_dict = {
                        "Button": (
                            event.button()
                            if hasattr(event, "button")
                            else QtCore.Qt.NoButton
                        ),
                        "Modifiers": (
                            event.modifiers()
                            if hasattr(event, "modifiers")
                            else QtCore.Qt.NoModifier
                        ),
                        "Position": self._last_qt_pos,
                    }

                    if event.type() == QtCore.QEvent.MouseMove:
                        if hasattr(self._active_handler, "on_mouse_move"):
                            consumed = self._active_handler.on_mouse_move(event_dict)
                            return bool(consumed)
                        return False

                    elif event.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
                        if hasattr(self._active_handler, "on_mouse_press"):
                            consumed = self._active_handler.on_mouse_press(event_dict)
                            return bool(consumed)
                        return False

                    elif event.type() == QtCore.QEvent.MouseButtonRelease:
                        if hasattr(self._active_handler, "on_mouse_release"):
                            consumed = self._active_handler.on_mouse_release(event_dict)
                            return bool(consumed)
                        return False

                    elif event.type() == QtCore.QEvent.Wheel:
                        event_dict["Delta"] = _wheel_delta(event)
                        if hasattr(self._active_handler, "on_wheel"):
                            return bool(self._active_handler.on_wheel(event_dict))
                        return False

        except Exception as e:
            FreeCAD.Console.PrintError(f"[NWInputManager] Event filter error: {e}\n")

        return False
