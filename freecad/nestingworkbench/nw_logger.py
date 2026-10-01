# SPDX-License-Identifier: LGPL-2.1-or-later
"""The workbench's only logging entry point — see .claude/skills/logging/SKILL.md."""
import logging
import logging.handlers
import multiprocessing
import os
import sys
import threading
import time
import traceback

try:
    import FreeCAD
except ImportError:  # plain-Python runs; GA workers usually DO have FreeCAD (see _in_worker)
    FreeCAD = None

from .constants import PREFS_PATH

_LOG_NAME = "Nesting.log"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUPS = 3
_THROTTLE_MAX_KEYS = 1024

_lock = threading.Lock()
_settings = None          # {"debug": bool, "crash_log": bool}; None = not loaded
_file_handler = None
_throttle = {}
_worker_messages = []     # (level, line) logged in a GA worker; see drain_worker_messages
_worker_dropped = 0       # lines beyond _THROTTLE_MAX_KEYS, reported on drain
_WORKER_BUFFERED = ("WARN", "ERROR")


def _stderr(msg: str) -> None:
    """Report the logger's own failures without recursing into it."""
    try:
        sys.__stderr__.write(f"[nw_logger] {msg}\n")
    except (AttributeError, OSError):
        pass  # no stderr at all (pythonw); nothing further is possible


def _load_settings() -> dict:
    """Read the debug and crash-log preferences once; defaults when unavailable."""
    global _settings
    if _settings is None:
        # No FreeCAD: no crash log. Workers are kept out of the file by _in_worker().
        loaded = {"debug": False, "crash_log": FreeCAD is not None}
        if FreeCAD is not None:
            try:
                prefs = FreeCAD.ParamGet(PREFS_PATH)
                loaded = {"debug": bool(prefs.GetBool("EnableDebugLog", False)),
                          "crash_log": bool(prefs.GetBool("EnableCrashLog", True))}
            except Exception as e:
                _stderr(f"preference read failed, using defaults: {e}")
        _settings = loaded
    return _settings


def refresh_settings() -> None:
    """Re-read the preferences on the next log call (after the user changes them)."""
    global _settings
    _settings = None


def get_enable_crash_log() -> bool:
    """Current EnableCrashLog preference (default True)."""
    return _load_settings()["crash_log"]


def set_enable_crash_log(value: bool) -> None:
    """Persist EnableCrashLog and apply it to the next log call."""
    _set_pref("EnableCrashLog", value)


def get_enable_debug_log() -> bool:
    """Current EnableDebugLog preference (default False)."""
    return _load_settings()["debug"]


def set_enable_debug_log(value: bool) -> None:
    """Persist EnableDebugLog and apply it to the next log call."""
    _set_pref("EnableDebugLog", value)


def _set_pref(name: str, value: bool) -> None:
    """Write one boolean preference, then drop the cached settings."""
    if FreeCAD is None:
        _stderr(f"cannot set {name} without FreeCAD")
        return
    try:
        FreeCAD.ParamGet(PREFS_PATH).SetBool(name, bool(value))
    except Exception as e:
        _stderr(f"preference write {name} failed: {e}")
    refresh_settings()


def get_log_path() -> str:
    """Return <UserAppData>/Nesting.log, or ~/Nesting.log without FreeCAD."""
    if FreeCAD is not None:
        try:
            if hasattr(FreeCAD, "getUserAppDataDir"):
                return os.path.join(FreeCAD.getUserAppDataDir(), _LOG_NAME)
            elif hasattr(FreeCAD, "ConfigGet"):
                return os.path.join(FreeCAD.ConfigGet("UserAppData"), _LOG_NAME)
        except Exception as e:
            _stderr(f"getUserAppDataDir failed: {e}")
    return os.path.join(os.path.expanduser("~"), _LOG_NAME)


def _in_worker() -> bool:
    """True inside a multiprocessing child (a GA worker); they never write the log file."""
    return multiprocessing.parent_process() is not None


def _write_file(level: str, lines: list) -> None:
    """Append to the rotating log file; errors always, others when crash log is on."""
    global _file_handler
    if _in_worker():
        return
    if level != "ERROR" and not _load_settings()["crash_log"]:
        return
    try:
        with _lock:
            if _file_handler is None:
                path = get_log_path()
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                _file_handler = logging.handlers.RotatingFileHandler(
                    path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for line in lines:
                _file_handler.emit(logging.makeLogRecord(
                    {"msg": f"{stamp} [{level}] {line}", "levelno": logging.INFO}))
    except Exception as e:
        _stderr(f"log file write failed: {e}")


def _emit(level: str, msg, console_fn: str, tag: str = "") -> None:
    """Send each line of msg to the console (or stderr) and to the log file."""
    lines = str(msg).splitlines() or [""]
    if level in _WORKER_BUFFERED and _in_worker():
        # A worker's console reaches nothing the user sees; the GUI replays these.
        _buffer_worker_lines(level, lines)
        return
    for line in lines:
        text = f"{tag}{line}\n"
        if FreeCAD is None:
            _stderr(text.rstrip("\n"))
            continue
        try:
            getattr(FreeCAD.Console, console_fn)(text)
        except Exception as e:
            _stderr(f"Console.{console_fn} failed: {e}; message: {line}")
    _write_file(level, lines)


def _buffer_worker_lines(level: str, lines: list) -> None:
    """Keep a worker's warnings and errors for the GUI process to replay."""
    global _worker_dropped
    with _lock:
        for line in lines:
            if len(_worker_messages) < _THROTTLE_MAX_KEYS:
                _worker_messages.append((level, line))
            else:
                _worker_dropped += 1


def drain_worker_messages() -> tuple:
    """Return this worker's buffered (level, line) pairs and clear the buffer."""
    global _worker_dropped
    with _lock:
        out = list(_worker_messages)
        _worker_messages.clear()
        if _worker_dropped:
            out.append(("WARN", f"{_worker_dropped} more worker messages suppressed"))
            _worker_dropped = 0
    return tuple(out)


def replay_worker_messages(messages, prefix: str = "[Worker] ") -> None:
    """Re-emit worker (level, line) pairs in this process, each distinct pair once."""
    seen = set()
    for level, line in messages:
        if (level, line) in seen:
            continue
        seen.add((level, line))
        (error if level == "ERROR" else warn)(f"{prefix}{line}")


def log(msg) -> None:
    """Console.PrintLog: hidden unless the Report view shows Log messages."""
    _emit("LOG", msg, "PrintLog")


def info(msg) -> None:
    """Console.PrintMessage."""
    _emit("INFO", msg, "PrintMessage")


def debug(msg) -> None:
    """PrintMessage with a [DEBUG] tag, only when EnableDebugLog is on."""
    if _load_settings()["debug"]:
        _emit("DEBUG", msg, "PrintMessage", tag="[DEBUG] ")


def warn(msg) -> None:
    """Console.PrintWarning."""
    _emit("WARN", msg, "PrintWarning")


def error(msg) -> None:
    """Console.PrintError; always written to the log file."""
    _emit("ERROR", msg, "PrintError")


def exception(header: str = "") -> None:
    """error() the header followed by the current exception's traceback."""
    error(f"{header}\n{traceback.format_exc()}" if header else traceback.format_exc())


def debug_throttled(key: str, msg, interval: float = 0.5) -> None:
    """debug(), at most once per interval seconds per key (for mouse-move handlers)."""
    now = time.monotonic()
    with _lock:
        if now - _throttle.get(key, float("-inf")) < interval:
            return
        if len(_throttle) >= _THROTTLE_MAX_KEYS:
            _throttle.clear()
        _throttle[key] = now
    debug(msg)
