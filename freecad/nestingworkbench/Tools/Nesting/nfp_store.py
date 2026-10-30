# SPDX-License-Identifier: LGPL-2.1-or-later
"""Saves a layout's NFPs beside its document and reloads them, so reopening the
layout in a new session does not recompute them.

One file per layout: <doc dir>/<doc name>.nwcache/<layout Name>.npz. The file is
disposable; deleting it only costs a recompute. It holds no pickles (a document
and its sidecar may be shared, and unpickling runs code): keys are JSON, polygons
are WKB, local points are plain float arrays.

Shape.nfp_cache keys lead with the part types' type_label (label plus an outline
hash, NFC-001), so a stored entry can only match a part whose outline is unchanged.
"""
import json
import os

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry

from freecad.nestingworkbench import nw_logger
from ...datatypes.shape import Shape
from . import shape_processor

FORMAT_VERSION = 2  # version 2 is the ARS-027 sampler (local_points include the ring vertices)


def cache_path(doc, layout):
    """The sidecar path for *layout*, or None when the document was never saved."""
    file_name = getattr(doc, "FileName", "") or ""
    if not file_name:
        return None
    stem, _ = os.path.splitext(file_name)
    return os.path.join(stem + ".nwcache", f"{layout.Name}.npz")


def used_type_labels(sheets):
    """The type_label of every part placed on *sheets*."""
    labels = set()
    for sheet in sheets or []:
        for placed in sheet.parts:
            label = getattr(placed.shape, "type_label", None)
            if label:
                labels.add(label)
    return labels


def _entries_for(type_labels):
    with Shape.nfp_cache_lock:
        return {
            key: value for key, value in Shape.nfp_cache.items()
            if key[0] in type_labels and key[1] in type_labels
            and isinstance(value.get("polygon"), BaseGeometry)
        }


def save(doc, layout, sheets):
    """Writes the NFPs between the part types placed on *sheets*. Returns the
    count saved, or 0 when nothing was written (the reason is logged)."""
    path = cache_path(doc, layout)
    if path is None:
        nw_logger.warn("The document has not been saved, so this layout's NFPs "
                       "were not stored. Save the document and accept again to keep them.")
        return 0
    entries = _entries_for(used_type_labels(sheets))
    if not entries:
        return 0
    arrays = {
        "version": np.array(FORMAT_VERSION),
        "outline_version": np.array(shape_processor.OUTLINE_VERSION),
        "keys": np.array(json.dumps([list(k) for k in entries])),
    }
    for i, value in enumerate(entries.values()):
        arrays[f"poly_{i}"] = np.frombuffer(shapely.to_wkb(value["polygon"]), dtype=np.uint8)
        arrays[f"pts_{i}"] = np.asarray(value.get("local_points", np.empty((0, 2))), dtype=np.float64)
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "wb") as f:
            np.savez(f, **arrays)
        os.replace(tmp, path)
    except OSError as e:
        nw_logger.warn(f"Could not store this layout's NFPs at {path}: {e}")
        try:
            os.remove(tmp)
        except OSError as remove_error:
            nw_logger.debug(f"[NFPStore] Could not remove the temporary file {tmp}: {remove_error}")
        return 0
    nw_logger.info(f"Stored {len(entries)} NFPs for layout '{layout.Label}'.")
    return len(entries)


def load(doc, layout):
    """Merges the layout's stored NFPs into Shape.nfp_cache. Returns the count
    added. A missing file is normal; an unreadable one is logged."""
    path = cache_path(doc, layout)
    if path is None or not os.path.isfile(path):
        return 0
    try:
        with np.load(path, allow_pickle=False) as data:
            version = int(data["version"])
            if version != FORMAT_VERSION:
                nw_logger.warn(f"Ignored the stored NFPs at {path}: format version {version}, "
                               f"expected {FORMAT_VERSION}; they will be recomputed.")
                return 0
            # The NFPs were computed from the outline of this version (ROS-004). A file
            # without the key predates the stamp and reads as None.
            outline_version = int(data["outline_version"]) if "outline_version" in data.files else None
            if outline_version != shape_processor.OUTLINE_VERSION:
                nw_logger.warn(f"Ignored the stored NFPs at {path}: outline version {outline_version}, "
                               f"expected {shape_processor.OUTLINE_VERSION}; they will be recomputed.")
                return 0
            keys =[tuple(k) for k in json.loads(str(data["keys"]))]
            loaded = {
                key: {
                    "polygon": shapely.from_wkb(data[f"poly_{i}"].tobytes()),
                    "local_points": data[f"pts_{i}"],
                }
                for i, key in enumerate(keys)
            }
    except (OSError, ValueError, KeyError) as e:
        nw_logger.warn(f"Ignored the stored NFPs at {path}, they will be recomputed: {e}")
        return 0
    added = 0
    with Shape.nfp_cache_lock:
        for key, value in loaded.items():
            if key not in Shape.nfp_cache:
                Shape.nfp_cache[key] = value
                added += 1
    nw_logger.info(f"Loaded {added} stored NFPs for layout '{layout.Label}'.")
    return added
