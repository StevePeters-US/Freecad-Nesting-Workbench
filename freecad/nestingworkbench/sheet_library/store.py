# SPDX-License-Identifier: LGPL-2.1-or-later
# freecad/nestingworkbench/sheet_library/store.py
"""Disk storage for library sheets: one JSON file per sheet.

Sheets are <library_root()>/<id>.json. Validation lives in model.py; this
module only reads and writes. The materials list is one file,
<NestingWorkbench>/materials.json, beside the SheetLibrary folder (never inside
it, where every *.json is read as a sheet).
"""

import datetime
import json
import os

from freecad.nestingworkbench import nw_logger
from . import model
from .model import SCHEMA_VERSION, Library, DEFAULT_MATERIALS


def library_root():
    """The per-user sheet folder. Not created until the first save."""
    import FreeCAD
    return os.path.join(FreeCAD.getUserAppDataDir(), "NestingWorkbench", "SheetLibrary")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _load_sheets(root, problems, skipped):
    """Every readable sheet, keyed by id. Bad files go to *problems* (text)
    and *skipped* (names)."""
    records = {}
    if not os.path.isdir(root):
        return records
    for filename in sorted(os.listdir(root)):
        if not filename.endswith(".json"):
            continue  # also skips *.json.tmp and any subfolder
        try:
            with open(os.path.join(root, filename), encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError) as e:
            problems.append(f"{filename}: unreadable ({e})")
            skipped.append(filename)
            continue
        schema = record.get("schema") if isinstance(record, dict) else None
        if schema != SCHEMA_VERSION:
            problems.append(f"{filename}: unsupported schema {schema!r}")
            skipped.append(filename)
            continue
        if record.get("id") != filename[:-len(".json")]:
            problems.append(f"{filename}: id does not match the file name")
            skipped.append(filename)
            continue
        records[record["id"]] = record
    return records


def load_library(root=None):
    """Load every sheet. Never raises for a bad file.

    Skipped files are listed in ``problems`` and logged once as a warning; the
    Sheets tab shows them in its banner. A readable but invalid sheet is
    loaded; model.validate_sheet reports it. ``skipped`` names those files;
    delete_skipped_files removes them.
    """
    problems = []
    skipped = []
    sheets = _load_sheets(root or library_root(), problems, skipped)
    if problems:
        nw_logger.warn(f"Sheet library: skipped {len(problems)} file(s): {'; '.join(problems)}")
    return Library(sheets=sheets, problems=problems, skipped=tuple(skipped))


def save_sheet(sheet, root=None):
    """Write *sheet* atomically; returns the stored copy. OSError propagates."""
    root = root or library_root()
    os.makedirs(root, exist_ok=True)
    now = _now()
    record = dict(sheet, schema=SCHEMA_VERSION, modified=now)
    record.setdefault("created", now)
    path = os.path.join(root, f"{record['id']}.json")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)  # a crash mid-write never leaves a truncated record
    return record


def delete_sheet(sheet_id, root=None):
    """Remove a sheet's file; a missing file is not an error. Other OSErrors propagate."""
    try:
        os.remove(os.path.join(root or library_root(), f"{sheet_id}.json"))
    except FileNotFoundError as e:
        nw_logger.debug(f"Sheet library: file not found on delete: {e}")


def delete_sheet_detaching(library, sheet_id, root=None):
    """Delete *sheet_id*, first saving each direct variant detached from it
    (model.detach_variants), so no variant is left pointing at a missing sheet.

    Variants are written first and the sheet's file last: a failure part-way leaves
    the sheet in place, and repeating the delete finishes the job. Returns the
    detached variant ids. OSError propagates.
    """
    detached = model.detach_variants(library, sheet_id)
    for record in detached:
        save_sheet(record, root=root)
    delete_sheet(sheet_id, root=root)
    return [record["id"] for record in detached]


def delete_skipped_files(filenames, root=None):
    """Delete sheet files that load_library skipped (Library.skipped).

    Each name must be a bare ``*.json`` file name, as _load_sheets records it;
    anything else (a path, ``..``, another extension) raises ValueError before any
    file is touched. A file that is already gone is not an error. Returns
    ``[(filename, error_text), ...]`` for the files that could not be removed.
    """
    for name in filenames:
        if os.path.basename(name) != name or not name.endswith(".json") or name in (".json", ".."):
            raise ValueError(f"not a sheet file name: {name!r}")
    root = root or library_root()
    failed = []
    for name in filenames:
        try:
            os.remove(os.path.join(root, name))
        except FileNotFoundError as e:
            nw_logger.debug(f"Sheet library: skipped file already gone: {e}")
        except OSError as e:
            nw_logger.warn(f"Sheet library: could not delete {name}: {e}")
            failed.append((name, str(e)))
    return failed


MATERIALS_SCHEMA_VERSION = 1


def materials_path():
    """The per-user materials list file. Not created until the first save."""
    import FreeCAD
    return os.path.join(FreeCAD.getUserAppDataDir(), "NestingWorkbench", "materials.json")


def load_materials(path=None):
    """(names sorted case-insensitively, problem or None). Never raises; never writes.

    No file yet means the seed list, DEFAULT_MATERIALS. A file that can't be used
    gives [] and a problem string (logged once), so the caller can refuse to
    overwrite it.
    """
    path = path or materials_path()
    if not os.path.exists(path):
        return sorted(DEFAULT_MATERIALS, key=str.casefold), None
    problem = None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        problem = f"materials.json: unreadable ({e})"
    else:
        schema = data.get("schema") if isinstance(data, dict) else None
        names = data.get("materials") if isinstance(data, dict) else None
        if schema != MATERIALS_SCHEMA_VERSION:
            problem = f"materials.json: unsupported schema {schema!r}"
        elif not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
            problem = "materials.json: every entry must be a non-empty name"
        else:
            seen = {}
            for n in names:
                if n.strip().casefold() in seen:
                    problem = f"materials.json: '{n.strip()}' is listed twice"
                    break
                seen[n.strip().casefold()] = n.strip()
            else:
                return sorted(seen.values(), key=str.casefold), None
    nw_logger.warn(f"Sheet library: {problem}")
    return [], problem


def save_materials(names, path=None):
    """Write the materials list atomically, sorted case-insensitively. OSError propagates."""
    path = path or materials_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"schema": MATERIALS_SCHEMA_VERSION,
                   "materials": sorted(names, key=str.casefold)}, f, indent=2)
    os.replace(tmp_path, path)


def replace_material(library, old, new, materials, root=None, path=None):
    """Rename material *old* to *new* on every sheet and in the list, or delete it when *new* is "".

    Sheets are rewritten first and the list last: a failure part-way leaves *old*
    in the list, and repeating the same action finishes the job. Only `material`
    changes on a sheet. Returns the rewritten sheet ids. OSError propagates.
    """
    changed = []
    for sheet in model.material_users(library, old):
        save_sheet(dict(sheet, material=new), root=root)
        changed.append(sheet["id"])
    kept = [m for m in materials if m.casefold() != old.casefold()]
    save_materials(kept + [new] if new else kept, path=path)
    return changed
