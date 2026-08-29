# SPDX-License-Identifier: LGPL-2.1-or-later
"""Nesting Workbench package.

Also resolves the on-disk locations of the addon's data directories. The
package lives at ``<addon>/freecad/nestingworkbench``, so the addon root is
three levels up from this file.
"""

import os

ADDON_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESOURCES_DIR = os.path.join(ADDON_DIR, "Resources")
ICONS_DIR = os.path.join(RESOURCES_DIR, "icons")
FONTS_DIR = os.path.join(ADDON_DIR, "fonts")
DEFAULT_FONT = os.path.join(FONTS_DIR, "PoiretOne-Regular.ttf")
