# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/nw_settings.py
"""Workbench-wide settings: the values the Nesting Settings dialog edits.

Every read and write of these preferences goes through here, so each default
lives in one place. Crash and debug logging stay in nw_logger, which must not
import the rest of the workbench. The GA worker count stays in worker_sizing,
because its control is in the panel. Values a task panel remembers between
runs are not settings and stay with that panel.
"""

try:
    import FreeCAD
except ImportError:
    FreeCAD = None

from freecad.nestingworkbench import nw_logger
from freecad.nestingworkbench.constants import (
    PREFS_PATH, PREF_CURRENCY_SYMBOL, PREF_PLAY_SOUND, PREF_VERBOSE_LOGGING,
    PREF_FRONT_SHEET_COLOR, PREF_BACK_SHEET_COLOR,
    PREF_DXF_PART_COLOR, PREF_DXF_LABEL_COLOR, PREF_DXF_SHEET_COLOR,
    PREF_DXF_BOUND_COLOR)

# GetString's default when the key is absent. Nobody can type it, so "never
# set" (follow the locale) is told apart from a saved "" (no symbol).
_UNSET = "\x00unset"


def _prefs():
    if FreeCAD is None:
        raise RuntimeError("FreeCAD preferences unavailable outside FreeCAD")
    return FreeCAD.ParamGet(PREFS_PATH)


def locale_currency_symbol():
    """The currency symbol of FreeCAD's number-format locale; "" for the C locale."""
    from PySide import QtCore
    return QtCore.QLocale().currencySymbol()


def currency_symbol_is_default():
    """True when no symbol has been saved, so the locale's is used."""
    return _prefs().GetString(PREF_CURRENCY_SYMBOL, _UNSET) == _UNSET


def get_currency_symbol():
    """The saved symbol, or the locale's when none was saved. "" means no symbol."""
    try:
        saved = _prefs().GetString(PREF_CURRENCY_SYMBOL, _UNSET)
        return locale_currency_symbol() if saved == _UNSET else saved
    except Exception as e:
        nw_logger.debug(f"Settings: failed to read the currency symbol: {e}")
        return ""


def set_currency_symbol(symbol):
    """Save *symbol* (stripped). An empty string is saved too: it means no symbol."""
    _prefs().SetString(PREF_CURRENCY_SYMBOL, symbol.strip())


def reset_currency_symbol():
    """Forget the saved symbol, so the locale's is used again."""
    _prefs().RemString(PREF_CURRENCY_SYMBOL)


def get_verbose_logging():
    """Detailed nesting-run progress in the Report view (default off)."""
    return _prefs().GetBool(PREF_VERBOSE_LOGGING, False)


def set_verbose_logging(value):
    _prefs().SetBool(PREF_VERBOSE_LOGGING, bool(value))


def get_play_sound():
    """Beep when a nesting run finishes (default on)."""
    return _prefs().GetBool(PREF_PLAY_SOUND, True)


def set_play_sound(value):
    _prefs().SetBool(PREF_PLAY_SOUND, bool(value))


# 3D-view colours, stored the way FreeCAD stores its own: unsigned 0xRRGGBBAA.
_RGB_DEFAULTS = {
    PREF_FRONT_SHEET_COLOR: (204, 204, 204),  # FreeCAD's default shape grey
    PREF_BACK_SHEET_COLOR: (148, 128, 184),
}


def _pack(rgb255):
    r, g, b = rgb255
    return (r << 24) | (g << 16) | (b << 8) | 0xFF


def get_color(key):
    """A 3D-view colour as (r, g, b) floats in 0-1, for ViewObject.ShapeColor."""
    default = _pack(_RGB_DEFAULTS[key])
    try:
        packed = _prefs().GetUnsigned(key, default)
    except Exception as e:
        nw_logger.debug(f"Settings: failed to read {key}: {e}")
        packed = default
    return tuple(((packed >> shift) & 0xFF) / 255.0 for shift in (24, 16, 8))


def set_color(key, rgb):
    """Save (r, g, b) floats in 0-1 as an opaque colour."""
    _prefs().SetUnsigned(key, _pack(tuple(round(c * 255) for c in rgb)))


# The AutoCAD colour indices a DXF layer can be given: the nine standard colours.
# R14 DXF has no true colour, so these are the choices (ruling 8).
DXF_COLORS = (1, 2, 3, 4, 5, 6, 7, 8, 9)
_DXF_DEFAULTS = {
    PREF_DXF_PART_COLOR: 7,
    PREF_DXF_LABEL_COLOR: 1,
    PREF_DXF_SHEET_COLOR: 3,
    PREF_DXF_BOUND_COLOR: 6,
}


def get_dxf_color(key):
    """A DXF layer's AutoCAD colour index; the default when unset, unreadable or not offered."""
    default = _DXF_DEFAULTS[key]
    try:
        aci = _prefs().GetInt(key, default)
    except Exception as e:
        nw_logger.debug(f"Settings: failed to read {key}: {e}")
        return default
    return aci if aci in DXF_COLORS else default


def set_dxf_color(key, aci):
    _prefs().SetInt(key, int(aci))
