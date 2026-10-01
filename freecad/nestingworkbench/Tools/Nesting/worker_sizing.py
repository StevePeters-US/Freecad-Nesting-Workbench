# SPDX-License-Identifier: LGPL-2.1-or-later
"""Sizes the GA worker pool from the machine's physical core count.

FreeCAD ships no psutil, and os.cpu_count() counts logical processors. The GA's
placement work gains nothing from SMT siblings (STEP_SIZE_AND_THREADING.md §2).
"""
import math
import os
import sys


def _windows_physical_cores():
    """Counts RelationProcessorCore records across all processor groups."""
    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.GetLogicalProcessorInformationEx
    query.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(wt.DWORD))
    query.restype = wt.BOOL
    relation_processor_core = 0
    size = wt.DWORD(0)
    query(relation_processor_core, None, ctypes.byref(size))
    buf = ctypes.create_string_buffer(size.value)
    if not query(relation_processor_core, buf, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    count, offset = 0, 0
    while offset < size.value:
        # SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX starts DWORD Relationship, DWORD Size
        count += 1
        offset += int.from_bytes(buf.raw[offset + 4:offset + 8], "little")
    return count


def _linux_physical_cores(cpuinfo_text):
    """Counts distinct (physical id, core id) pairs in /proc/cpuinfo text.

    Returns None when the fields are absent (some ARM kernels and VMs).
    """
    cores, physical_id = set(), None
    for line in cpuinfo_text.splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "physical id":
            physical_id = value.strip()
        elif key == "core id" and physical_id is not None:
            cores.add((physical_id, value.strip()))
    return len(cores) or None


def _macos_physical_cores():
    import subprocess
    out = subprocess.run(["sysctl", "-n", "hw.physicalcpu"],
                         capture_output=True, text=True, timeout=5, check=True)
    return int(out.stdout.strip())


def physical_core_count():
    """Physical cores available to this process, or None if unknown.

    Capped by the CPU affinity mask where the platform exposes one, so a
    restricted process is never given more workers than it may run.
    """
    try:
        if sys.platform == "win32":
            count = _windows_physical_cores()
        elif sys.platform.startswith("linux"):
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                count = _linux_physical_cores(f.read())
        elif sys.platform == "darwin":
            count = _macos_physical_cores()
        else:
            count = None
    except Exception:
        # Not silent: ga_worker_count() reports SOURCE_LOGICAL_FALLBACK and the
        # coordinator warns in the Report view.
        return None
    if not count or count < 1:
        return None
    if hasattr(os, "sched_getaffinity"):
        count = min(count, len(os.sched_getaffinity(0)))
    return count


SOURCE_POPULATION = "population of 1"
SOURCE_SETTING = "Advanced setting"
SOURCE_PHYSICAL = "physical cores"
SOURCE_LOGICAL_FALLBACK = "logical processors (physical core count unavailable)"


def auto_core_count():
    """Returns (cores, source) that Auto sizes from.

    Physical cores, or logical processors when the physical count is unknown.
    """
    cores = physical_core_count()
    if cores is None:
        return os.cpu_count() or 1, SOURCE_LOGICAL_FALLBACK
    return cores, SOURCE_PHYSICAL


def balanced_worker_count(population_size, cores):
    """Auto's worker count: one per core, at most one per member, shrunk so that
    every round of ceil(population / workers) is full. At P=10 and 8 cores, 5
    workers finish in the same two rounds as 8 and start 3 fewer processes.
    No fixed upper bound: the owner removed the 8-worker cap on 2026-09-30.
    """
    if population_size <= 1:
        return 1
    cap = max(1, min(cores, population_size))
    rounds = math.ceil(population_size / cap)
    return math.ceil(population_size / rounds)


def ga_worker_count(population_size, override=0):
    """Returns (workers, source) for the GA process pool.

    override > 0 wins, clamped to the population. Otherwise Auto:
    balanced_worker_count() over auto_core_count().
    """
    if population_size <= 1:
        return 1, SOURCE_POPULATION
    if override > 0:
        return min(override, population_size), SOURCE_SETTING
    cores, source = auto_core_count()
    return balanced_worker_count(population_size, cores), source



WORKER_PREF = "GAWorkerProcesses"


def get_worker_override():
    """GAWorkerProcesses preference; 0 means Auto."""
    import FreeCAD
    from freecad.nestingworkbench.constants import PREFS_PATH
    return max(0, FreeCAD.ParamGet(PREFS_PATH).GetInt(WORKER_PREF, 0))


def set_worker_override(value):
    """Persist GAWorkerProcesses; negative values are stored as 0 (Auto)."""
    import FreeCAD
    from freecad.nestingworkbench.constants import PREFS_PATH
    FreeCAD.ParamGet(PREFS_PATH).SetInt(WORKER_PREF, max(0, int(value)))
