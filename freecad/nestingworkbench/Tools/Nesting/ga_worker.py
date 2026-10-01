# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Worker process entry points for GA population member nesting.
Executes in worker processes without FreeCAD dependencies.
"""
import dataclasses

from freecad.nestingworkbench import nw_logger
from ...datatypes.shape import Shape
from .ga_snapshot import MemberTask, MemberResult, nest_from_snapshot


_sim_queue = None  # set by init_worker when Simulate Nesting runs on the pool


def init_worker(cache_path, sim_queue=None):
    """
    Initializer for ProcessPoolExecutor workers.
    Loads the precomputed NFP cache that start_worker_pool pickled to
    cache_path into Shape.nfp_cache, and keeps the simulation queue that
    worker_nest publishes placements to.
    """
    global _sim_queue
    _sim_queue = sim_queue
    if cache_path is not None:
        import pickle
        with open(cache_path, "rb") as f:
            cache_payload = pickle.load(f)
        with Shape.nfp_cache_lock:
            Shape.nfp_cache.clear()
            Shape.nfp_cache.update(cache_payload)


def worker_ping():
    """Start-up probe: answering at all proves the worker process runs."""
    return True


def worker_nest(task: MemberTask) -> MemberResult:
    """
    Nests a single population member task in pure geometry, carrying back any
    warnings the worker logged. If nesting raises, the buffer survives and rides
    on this worker's next result. Streams placements when task.stream is set.
    """
    sink = _sim_queue.put_nowait if (task.stream and _sim_queue is not None) else None
    result = nest_from_snapshot(task, placement_sink=sink)
    return dataclasses.replace(result, diagnostics=nw_logger.drain_worker_messages())
