# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Coordinates the Genetic Algorithm nesting loop.
Extracted from NestingController._execute_ga_nesting() to follow SRP.
"""
import FreeCADGui
import math
import random
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, wait
from ...datatypes.shape import Shape
from freecad.nestingworkbench import nw_logger
from .layout_manager import LayoutManager
from .algorithms import genetic_utils
from .algorithms.minkowski_engine import DEFAULT_CANDIDATE_SPACING
from .ga_snapshot import UNPLACED_PENALTY_FACTOR
from .worker_sizing import ga_worker_count, SOURCE_LOGICAL_FALLBACK, SOURCE_SETTING

ELITE_FRACTION_DIVISOR = 5      # top 1/5 of the population survives unchanged
MIN_ELITE_COUNT = 2             # always keep a breeding pair
DEFAULT_MUTATION_RATE = 0.1
DEFAULT_IMMIGRANT_RATIO = 0.15  # fraction of each new generation seeded at random
POOL_POLL_SECONDS = 0.25        # how often a wait on workers re-checks for cancel
POOL_STARTUP_TIMEOUT = 60.0     # a worker that has not answered by now never will

# numpy's BLAS reads these at import and otherwise starts os.cpu_count() - 1
# threads in every worker. Nesting never makes a BLAS call big enough to use them.
BLAS_THREAD_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")

# id(pool) -> the NFP cache file its workers load; removed by terminate_pool
_pool_cache_files = {}

def enumerate_nfp_jobs(parts, candidate_spacing=DEFAULT_CANDIDATE_SPACING):
    """Enumerates every NFP cache key the placement loop can request for
    these parts, as {cache_key: (rep_A, rep_B, relative_angle)}.

    Part type identity is source_freecad_object.Label — the same field the
    cache key uses (never parse part.id). The placed part A is always keyed
    at angle 0 with the rotation folded into relative_angle, matching
    get_incremental_candidates / _calculate_and_cache_nfp:
    (placed_label, part_label, relative_angle, spacing, deflection, simplification, candidate_spacing).
    """
    reps = {}
    for p in parts:
        reps.setdefault(p.source_freecad_object.Label, p)

    def angle_grid(part):
        steps = max(1, getattr(part, 'rotation_steps', 1) or 1)
        return [i * (360.0 / steps) for i in range(steps)]

    jobs = {}
    for a in reps.values():
        for b in reps.values():
            rel_angles = set()
            for ang_a in angle_grid(a):
                for ang_b in angle_grid(b):
                    rel = (ang_b - ang_a) % 360.0
                    if abs(rel - 360.0) < 1e-5:
                        rel = 0.0
                    rel_angles.add(round(rel, 4))
            for rel in rel_angles:
                key = (a.source_freecad_object.Label,
                       b.source_freecad_object.Label,
                       rel, b.spacing, b.deflection, b.simplification,
                       float(candidate_spacing))
                jobs.setdefault(key, (a, b, rel))
    return jobs

def worker_mp_context():
    """Returns the multiprocessing context for the GA worker pool.

    Always spawn, on every platform. A forked worker inherits FreeCAD's
    sys.stdin, a PythonStdin with no close(), and multiprocessing's child
    bootstrap calls sys.stdin.close() catching only OSError/ValueError, so
    every forked worker dies with exit code 1 before running a task. Forking
    a process running Qt and OCC threads is unsafe anyway.

    Spawned workers are started by re-running the parent's interpreter, which
    inside FreeCAD is freecad.exe, not Python. FreeCAD then parses Python's
    command-line flags as its own (-E is its --macro-path) and pops an
    "Initialization of FreeCAD failed" dialog for every worker. Point spawn at
    the Python interpreter FreeCAD ships beside its executable instead.

    Raises:
        RuntimeError: No Python interpreter found next to the executable.
    """
    import multiprocessing
    import multiprocessing.spawn
    import os
    import sys

    ctx = multiprocessing.get_context("spawn")

    # bytes on POSIX: set_executable() stores os.fsencode(path) there
    current = os.fsdecode(multiprocessing.spawn.get_executable())
    if os.path.basename(current).lower().startswith("python"):
        return ctx

    bin_dir = os.path.dirname(sys.executable)
    names = ("python.exe",) if sys.platform == "win32" else ("python3", "python")
    for name in names:
        candidate = os.path.join(bin_dir, name)
        if os.path.isfile(candidate):
            # Global to the spawn module; only replaces a non-Python executable
            ctx.set_executable(candidate)
            return ctx
    raise RuntimeError(f"no Python interpreter found in {bin_dir} to start workers")

def terminate_pool(pool):
    """Stops a worker pool without waiting on its workers.

    shutdown(wait=False) only stops handing out work: a worker busy on a
    member, or one that never started, stays alive and so does the run
    waiting on it. Workers hold nothing the GA needs, so terminate them.
    Also deletes the pool's NFP cache file.
    """
    if pool is None:
        return
    processes = list((getattr(pool, "_processes", None) or {}).values())
    pool.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        try:
            process.terminate()
        except Exception as e:
            nw_logger.debug(f"[GACoordinator] Worker process terminate ignored (already exited): {e}")
    _remove_cache_file(_pool_cache_files.pop(id(pool), None))

def _write_cache_file(cache_payload):
    """Pickles the NFP cache to a private temp file and returns its path."""
    import os
    import pickle
    import tempfile
    fd, path = tempfile.mkstemp(prefix="nw_nfp_cache_", suffix=".pkl")
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(cache_payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    except BaseException:
        _remove_cache_file(path)
        raise
    return path

def _remove_cache_file(path):
    import os
    if path is None:
        return
    try:
        os.remove(path)
    except OSError as e:
        nw_logger.debug(f"[GACoordinator] NFP cache file not removed: {e}")

def wait_for_any(pending, cancel_callback, deadline=None, on_poll=None):
    """Blocks until at least one future in pending finishes.

    Polls instead of blocking outright, so Cancel is seen within
    POOL_POLL_SECONDS even when no worker ever answers. on_poll, if given,
    runs once per poll, before the done check, so a simulation drain keeps
    pace with the workers.

    Returns:
        The set of finished futures, or None if cancel was requested.

    Raises:
        TimeoutError: deadline (a time.monotonic() value) passed first.
    """
    while True:
        done, _ = wait(pending, timeout=POOL_POLL_SECONDS, return_when=FIRST_COMPLETED)
        if on_poll is not None:
            on_poll()
        if done:
            return done
        if cancel_callback():
            return None
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError


SIM_ROW_GAP_FRACTION = 0.15  # gap between preview rows, as a fraction of sheet height


def build_preview_row(placements, parts_by_id, row, sheet_w, sheet_h, spacing):
    """World-space outlines for one simulation preview row.

    placements: [(sheet_index, part_id, x, y, angle)] in placement order,
    sheet-local centroids as PlacedPart records them. Row 0 sits on the real
    sheet row; row r is lifted by r * sheet_h * (1 + SIM_ROW_GAP_FRACTION).
    Sheets in a row step along x exactly as Sheet.get_origin() does.

    Returns (sheet_rects, part_outlines): two lists of Shapely Polygons.
    """
    from shapely.geometry import box
    from shapely.affinity import rotate, translate
    y0 = row * sheet_h * (1.0 + SIM_ROW_GAP_FRACTION)
    used = sorted({s for s, *_ in placements})
    rects = [box(s * (sheet_w + spacing), y0, s * (sheet_w + spacing) + sheet_w, y0 + sheet_h)
             for s in used]
    outlines = []
    for s, part_id, x, y, angle in placements:
        part = parts_by_id.get(part_id)
        if part is None or part.original_polygon is None:
            continue
        poly = rotate(part.original_polygon, angle, origin="centroid")
        c = poly.centroid
        outlines.append(translate(poly, x - c.x + s * (sheet_w + spacing), y - c.y + y0))
    return rects, outlines


def start_worker_pool(max_workers, cache_payload, cancel_callback, sim_queue=None):
    """Creates the GA worker pool and waits for every worker to answer.

    Starts all max_workers up front, one ping each. Spawned workers otherwise
    start lazily inside submit(), and the pool only spawns while no worker is
    idle, so a generation began on 3 of 8 workers and the pool grew by about
    one per generation. A worker that cannot run - one that dies on start-up,
    or is stuck before it ever reads a task - also shows itself here, while
    falling back to serial is still possible. Pins each worker's BLAS thread
    pool to one thread (see BLAS_THREAD_VARS).

    The NFP cache goes to the workers as a file path, not as initargs. Spawn
    writes initargs down a 64 KB pipe that the child drains only after
    importing the initializer's module (FreeCAD, Part and PySide6 via the
    freecad package, ~0.9 s), so a 1 MB cache blocked submit() for that long
    per worker and serialized every worker's start-up.

    Returns:
        A working pool, or None if cancel was requested during start-up.

    Raises:
        RuntimeError: Not every worker answered within POOL_STARTUP_TIMEOUT.
        Exception: Whatever a worker raised while starting.
    """
    import os
    from concurrent.futures import ProcessPoolExecutor
    from .ga_worker import init_worker, worker_ping

    # Not restored afterwards: workers spawn lazily, on submit, long after this
    # returns. FreeCAD's own numpy is already loaded, so this process is unaffected;
    # setdefault leaves a value the user set themselves alone.
    for var in BLAS_THREAD_VARS:
        os.environ.setdefault(var, "1")

    cache_path = _write_cache_file(cache_payload)
    try:
        pool = ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=worker_mp_context(),
            initializer=init_worker,
            initargs=(cache_path, sim_queue)
        )
    except BaseException:
        _remove_cache_file(cache_path)
        raise
    _pool_cache_files[id(pool)] = cache_path
    try:
        pending = {pool.submit(worker_ping) for _ in range(max_workers)}
        deadline = time.monotonic() + POOL_STARTUP_TIMEOUT
        while pending:
            done = wait_for_any(pending, cancel_callback, deadline)
            if done is None:
                terminate_pool(pool)
                return None
            for probe in done:
                probe.result()
            pending -= done
        return pool
    except TimeoutError:
        terminate_pool(pool)
        raise RuntimeError(f"no worker process started within {POOL_STARTUP_TIMEOUT:.0f}s")
    except BaseException:
        terminate_pool(pool)
        raise

class GACoordinator:
    """Runs the GA optimization loop and returns the best Layout."""

    def __init__(self, doc, shape_preparer, ui_callbacks=None, draw_callback=None, worker=None):
        """
        Args:
            doc: FreeCAD.ActiveDocument
            shape_preparer: ShapePreparer instance (for processed_shape_cache)
            ui_callbacks: dict with optional keys:
                'set_status': callable(str) — update status label
                'update_progress': callable(current, total, msg) — update progress bar
                'reset_progress': callable() — reset progress bar
                'play_sound': callable() — beep on completion
            draw_callback: callable(payload_dict) — marshal to main thread
            worker: NestingWorker instance — for signal emission
        """
        self.doc = doc
        self.shape_preparer = shape_preparer
        self.ui_callbacks = ui_callbacks or {}
        self.draw_callback = draw_callback
        self.worker = worker
        self.layout_manager = None
        self._pending_layouts = None
        self.seed = 0
        self.rng = random.Random(self.seed)
        self._in_parallel_generation = False
        self._sim_queue = None
        self._sim_rows_shown = set()

    def _set_status(self, msg):
        if self.worker:
            self.worker.status_changed.emit(msg)
            return
        callback = self.ui_callbacks.get('set_status')
        if callback:
            try:
                callback(msg)
            except RuntimeError as e:
                nw_logger.debug(f"[GACoordinator] UI widget deleted (set_status): {e}")

    def _update_progress(self, current, total, msg=None):
        if self.worker:
            self.worker.progress_updated.emit(current, total, msg or "")
            return
        callback = self.ui_callbacks.get('update_progress')
        if callback:
            try:
                callback(current, total, msg)
            except RuntimeError as e:
                nw_logger.debug(f"[GACoordinator] UI widget deleted (update_progress): {e}")

    def _play_sound(self):
        callback = self.ui_callbacks.get('play_sound')
        if callback:
            try:
                callback()
            except RuntimeError as e:
                nw_logger.debug(f"[GACoordinator] Sound callback failed or widget deleted: {e}")

    def run(self, target_layout, ui_params, quantities, master_map,
            rotation_params, algo_kwargs, is_simulating, viz_manager=None):
        """
        Execute the GA optimization and return a NestingJob for the winner.

        Returns:
            NestingJob — ready to commit or cancel
        """
        generations = algo_kwargs.get('generations', 1)
        population_size = algo_kwargs.get('population_size', 1)
        rotation_steps = ui_params.get('rotation_steps', 1)
        elite_count = min(population_size, max(MIN_ELITE_COUNT, population_size // ELITE_FRACTION_DIVISOR)) if population_size > 1 else population_size
        mutation_rate = DEFAULT_MUTATION_RATE
        immigrant_ratio = DEFAULT_IMMIGRANT_RATIO
        early_stop_threshold = algo_kwargs.get('early_stop_threshold', 5)
        stagnation_epsilon = algo_kwargs.get('stagnation_epsilon', 1e-4)
        verbose = algo_kwargs.get('verbose', False)
        cancel_callback = algo_kwargs.get('cancel_callback', lambda: False)
        
        seed = algo_kwargs.get('random_seed')
        if seed is None:
            seed = random.randrange(2**32)
        self.seed = seed
        self.rng = random.Random(seed)
        self.is_simulating = is_simulating
        nw_logger.info(f"GA random seed: {seed}")
        
        if algo_kwargs.pop('clear_nfp_cache', False):
            Shape.clear_nfp_cache()
            nw_logger.info("NFP cache cleared (user request).")
        
        if verbose:
            nw_logger.info(f"GA Mode: {generations} generations, {population_size} population")
        
        self.layout_manager = LayoutManager(self.doc, self.shape_preparer.processed_shape_cache, rng=self.rng)
        
        self._set_status(f"Creating {population_size} layouts...")
        if self.draw_callback:
            self.draw_callback({'updateGui_only': True})
        else:
            FreeCADGui.updateGui()
        
        if self.draw_callback:
            # Marshal to main thread
            create_payload = {
                'create_population': True,
                'master_map': master_map,
                'quantities': quantities, 
                'ui_params': ui_params,
                'population_size': population_size,
                'rotation_steps': rotation_steps,
                'verbose': verbose,
            }
            self.draw_callback(create_payload)
            layouts = self._pending_layouts
        else:
            layouts = self.layout_manager.create_ga_population(
                master_map, quantities, ui_params, population_size, rotation_steps, verbose=verbose
            )
        
        if layouts and layouts[0].parts:
            self._precompute_all_nfps(
                layouts[0].parts, cancel_callback,
                algo_kwargs.get('candidate_spacing', DEFAULT_CANDIDATE_SPACING))

        best_layout = None
        best_efficiency = 0
        best_fitness_at_last_reset = None
        generations_without_improvement = 0
        total_nesting_time = 0

        # Pool lifecycle: single pool across the whole GA run. Sizing lives in
        # worker_sizing.ga_worker_count (physical cores, capped, batch-balanced).
        pool = None
        self._sim_queue = None
        self._sim_rows_shown = set()
        self._serial_fallback = False
        max_workers, worker_source = ga_worker_count(
            population_size, algo_kwargs.get('worker_processes', 0))
        if worker_source == SOURCE_LOGICAL_FALLBACK:
            nw_logger.warn(
                f"[GACoordinator] Could not read the physical core count; sizing the "
                f"worker pool from logical processors ({max_workers} workers).")
        if max_workers > 1:
            nw_logger.info(f"GA worker processes: {max_workers} ({worker_source})")
            self._set_status("Starting worker processes...")
            if is_simulating:
                self._sim_queue = worker_mp_context().Queue()
            else:
                self._sim_queue = None
            try:
                with Shape.nfp_cache_lock:
                    cache_payload = dict(Shape.nfp_cache)
                pool = start_worker_pool(max_workers, cache_payload, cancel_callback, sim_queue=self._sim_queue)
            except Exception as e:
                nw_logger.warn(
                    f"[GACoordinator] Worker processes failed to start ({e}); "
                    f"evaluating the population one member at a time instead."
                )
                self._set_status("Worker processes failed to start - running serially (see Report view)")
                self._serial_fallback = True
                if self._sim_queue is not None:
                    self._sim_queue.close()
                    self._sim_queue.cancel_join_thread()
                    self._sim_queue = None
                pool = None
        else:
            reason = ("population size is 1" if population_size <= 1
                      else "GA worker processes set to 1 in Nesting Settings > Advanced"
                      if worker_source == SOURCE_SETTING
                      else "only one CPU core")
            nw_logger.info(f"GA evaluating serially: {reason}.")

        try:
            for gen in range(generations):
                if cancel_callback():
                    terminate_pool(pool)
                    nw_logger.info("Nesting cancelled by user.")
                    break

                if verbose:
                    nw_logger.info(f"\n=== Generation {gen+1}/{generations} ===")
                self._set_status(f"Generation {gen+1}/{generations}...")
                if self.draw_callback:
                    self.draw_callback({'updateGui_only': True})
                else:
                    FreeCADGui.updateGui()

                gen_time, interrupted = self._run_generation(
                    layouts, gen, generations, ui_params, rotation_steps,
                    algo_kwargs, is_simulating, cancel_callback, verbose, viz_manager,
                    pool=pool
                )
                total_nesting_time += gen_time
                if interrupted:
                    terminate_pool(pool)
                    break

                # Evaluate progress
                layouts.sort(key=lambda l: l.fitness)
                current_best = layouts[0]

                # Strict best: the winner must always be the best layout we
                # actually found. The epsilon below only governs *stagnation*,
                # because a converged population still jitters the compactness
                # blend by ~1e-9 and a strict `<` would read that as progress.
                if best_layout is None or current_best.fitness < best_layout.fitness:
                    best_layout = current_best
                    best_efficiency = current_best.efficiency
                    if verbose:
                        nw_logger.info(f"\n>>> New Best: {best_efficiency:.1f}% efficiency <<<")

                if best_fitness_at_last_reset is None or (
                        best_fitness_at_last_reset - current_best.fitness
                        > max(1e-4, stagnation_epsilon * abs(best_fitness_at_last_reset))):
                    best_fitness_at_last_reset = current_best.fitness
                    generations_without_improvement = 0
                else:
                    generations_without_improvement += 1
                    if verbose:
                        nw_logger.info(f"\nNo improvement ({generations_without_improvement}/{early_stop_threshold})")
                
                # Early Stopping / Stagnation Check:
                # An early stopping threshold of 5 generations is selected because nesting jobs typically
                # operate with relatively small chromosome/population sizes where genetic diversity 
                # converges quickly. If no elite child (improved ordering/rotation layout) is found 
                # after 5 generations, the population has converged to a local optimum, and continuing
                # to run more generations would waste CPU/GPU resources without realistic chance of improvement.
                if generations_without_improvement >= early_stop_threshold:
                    nw_logger.info(f"Early stopping: no improvement for {early_stop_threshold} generations")
                    break
                
                # STEP 2 & 3: Build next generation
                if gen < generations - 1:
                    actual_elite = min(elite_count, len(layouts))
                    elites = layouts[:actual_elite]
                    
                    if self.draw_callback:
                        next_gen_payload = {
                            'build_next_generation': True,
                            'gen': gen,
                            'layouts': layouts,
                            'elites': elites,
                            'master_map': master_map,
                            'quantities': quantities,
                            'ui_params': ui_params,
                            'rotation_steps': rotation_steps,
                            'mutation_rate': mutation_rate,
                            'immigrant_ratio': immigrant_ratio,
                            'verbose': verbose
                        }
                        self.draw_callback(next_gen_payload)
                        layouts = self._pending_layouts
                    else:
                        layouts = self._build_next_generation(
                            gen, layouts, elites, master_map, quantities, ui_params, 
                            rotation_steps, mutation_rate, immigrant_ratio, verbose
                        )
                else:
                    # Final cleanup
                    if self.draw_callback:
                         self.draw_callback({
                             'cleanup_layouts': True,
                             'layouts': layouts,
                             'best_layout': best_layout,
                             'verbose': verbose
                         })
                    else:
                        for layout in layouts:
                            if layout != best_layout:
                                self.layout_manager.delete_layout(layout, verbose=verbose)
                    layouts = [best_layout]

            if is_simulating and self.draw_callback and pool is not None:
                self.draw_callback({'clear_sim_member_rows': True})

            # Fill phase: generations nested regular parts only — fill the
            # winning layout exactly once (spawns still marshal to the main
            # thread via request_spawn).
            if best_layout is not None and not cancel_callback():
                _, fill_parts = self._split_fill_parts(best_layout.parts)
                if fill_parts:
                    from .nesting_logic import fill_existing_sheets
                    self._set_status("Filling winning layout...")
                    fill_kwargs = algo_kwargs.copy()
                    fill_kwargs.pop('clear_nfp_cache', None)
                    fill_kwargs['rng'] = self.rng
                    fill_kwargs['spawn_more_callback'] = self.request_spawn
                    fill_kwargs['cancel_callback'] = cancel_callback
                    if self.draw_callback:
                        fill_kwargs.pop('progress_callback', None)
                        fill_kwargs['log_callback'] = (
                            lambda msg, level=None: nw_logger.info(msg))
                    if best_layout.sheets is None:
                        best_layout.sheets = []
                    pre_counts = [len(s.parts) for s in best_layout.sheets]
                    _, fill_time = fill_existing_sheets(
                        best_layout.sheets, fill_parts,
                        ui_params['sheet_width'], ui_params['sheet_height'],
                        rotation_steps, simulate=is_simulating,
                        viz_manager=viz_manager, **fill_kwargs)
                    total_nesting_time += fill_time
                    # Sync doc placement for all fill parts placed here
                    for sheet, n_before in zip(best_layout.sheets, pre_counts):
                        for placed in sheet.parts[n_before:]:
                            placed.shape.placement = placed.shape.get_final_placement(sheet.get_origin())
                    for sheet in best_layout.sheets[len(pre_counts):]:
                        for placed in sheet.parts:
                            placed.shape.placement = placed.shape.get_final_placement(sheet.get_origin())
                    self.layout_manager.calculate_efficiency(
                        best_layout, ui_params['sheet_width'], ui_params['sheet_height'],
                        ui_params.get('compactness_weight', 0.0))
                    best_efficiency = best_layout.efficiency
            
            # STEP 4: Finalize result — dispatch to main thread (ViewObject + recompute)
            job = self._dispatch_finalize(best_layout, best_efficiency, total_nesting_time, target_layout, ui_params)
            return job

        except Exception as e:
            nw_logger.exception(f"GA Nesting Error: {e}")
            self._set_status(f"Error: {e}")
            if 'layouts' in locals():
                for layout in layouts:
                    self.layout_manager.delete_layout(layout)
            self._dispatch_discard_template()
            self._dispatch_recompute()
            return None
        finally:
            if is_simulating and self.draw_callback and pool is not None:
                self.draw_callback({'clear_sim_member_rows': True})
            terminate_pool(pool)
            if self._sim_queue is not None:
                self._sim_queue.close()
                self._sim_queue.cancel_join_thread()
                self._sim_queue = None

    @staticmethod
    def _split_fill_parts(parts):
        """Splits parts into (regular_parts, fill_parts)."""
        regular = []
        fill = []
        for p in parts:
            if getattr(p, 'fill_sheet', False) is True:
                fill.append(p)
            else:
                regular.append(p)
        return regular, fill

    def _dispatch_to_main_thread(self, payload_key, fallback_fn, **kwargs):
        """Runs fallback_fn() synchronously if no draw_callback; otherwise dispatches
        payload_key with kwargs and result_holder via draw_callback to the main thread."""
        if not self.draw_callback:
            return fallback_fn()
        result_holder = [None]
        payload = {payload_key: True, 'result_holder': result_holder}
        payload.update(kwargs)
        self.draw_callback(payload)
        return result_holder[0]

    def _dispatch_finalize(self, best_layout, best_efficiency, total_time, target_layout, ui_params):
        """Runs _finalize() and doc.recompute() on the main thread if using a worker."""
        def fallback():
            job = self._finalize(best_layout, best_efficiency, total_time, target_layout, ui_params)
            self.doc.recompute()
            return job

        return self._dispatch_to_main_thread(
            'ga_finalize', fallback,
            best_layout=best_layout,
            best_efficiency=best_efficiency,
            total_time=total_time,
            target_layout=target_layout,
            ui_params=ui_params,
        )

    def _dispatch_recompute(self):
        """Runs doc.recompute() on the main thread if using a worker."""
        if self.draw_callback:
            self.draw_callback({'doc_recompute_only': True})
        else:
            self.doc.recompute()

    def _dispatch_discard_template(self):
        """Deletes the run's shared document objects on the main thread."""
        layout_manager = getattr(self, 'layout_manager', None)
        if layout_manager is None:
            return
        if self.draw_callback:
            self.draw_callback({'discard_template': True})
        else:
            layout_manager.discard_template()

    def request_spawn(self, spawn_fn):
        """Runs spawn_fn() on the main thread (it creates FreeCAD doc objects)
        and returns the new part. Used by the nester to mint fill-part
        instances on demand."""
        if getattr(self, '_in_parallel_generation', False):
            nw_logger.error(
                "[GACoordinator] request_spawn called during parallel generation! Fill must be winner-only."
            )
            raise AssertionError(
                "request_spawn called during parallel generation! Fill must be deferred to the winner."
            )
        return self._dispatch_to_main_thread('spawn_fill_part', spawn_fn, spawn_fn=spawn_fn)

    def _precompute_all_nfps(self, parts, cancel_callback, candidate_spacing):
        """Fills Shape.nfp_cache with every NFP the run can request, before
        the generation loop starts. Runs on the GA worker thread; workers
        touch only Shapely geometry, so no main-thread marshaling is needed.
        Progress goes through the worker signals so the UI stays live."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import os
        from .algorithms.minkowski_engine import compute_and_cache_nfp

        jobs = enumerate_nfp_jobs(parts, candidate_spacing)
        with Shape.nfp_cache_lock:
            missing = {k: v for k, v in jobs.items() if k not in Shape.nfp_cache}
        total = len(missing)
        if not total:
            return

        self._set_status(f"Precomputing {total} NFPs...")
        done = 0
        # Measured 2026-09-02 post-NPERF-001..003, 324 jobs on 24 cores:
        # 1w 1.611s, 2w 1.341s (1.20x), 4w 1.411s, 8w 1.505s, 16w 1.925s,
        # 24w 2.257s. Shapely/GEOS releases the GIL only briefly here, so
        # scaling peaks at 2 and decays from there. Do not raise this without
        # re-running the thread-scaling benchmark.
        with ThreadPoolExecutor(max_workers=min(2, os.cpu_count() or 1)) as pool:
            futures = [pool.submit(compute_and_cache_nfp, a, 0.0, b, rel, key, None, candidate_spacing)
                       for key, (a, b, rel) in missing.items()]
            for future in as_completed(futures):
                if cancel_callback():
                    pool.shutdown(wait=False, cancel_futures=True)
                    return
                done += 1
                self._update_progress(done, total, f"Precomputing NFPs {done}/{total}")

    def _run_generation(self, layouts, gen, generations, ui_params, rotation_steps, algo_kwargs,
                        is_simulating, cancel_callback, verbose, viz_manager=None, pool=None):
        """Nests each layout in the population and calculates fitness/efficiency."""
        total_time = 0

        if pool is not None:
            from .ga_snapshot import snapshot_member, apply_result
            from .ga_worker import worker_nest

            self._in_parallel_generation = True
            try:
                tasks = []
                carried = []
                pending_layouts = []

                for idx, layout in enumerate(layouts):
                    if cancel_callback(): return total_time, True

                    if verbose:
                        nw_logger.info(f"  [Gen {gen+1}] Layout {idx+1}/{len(layouts)}: {layout.name}")

                    if layout.sheets:
                        # The champion carries its layout; it is not nested again.
                        carried.append((layout.member_idx, layout))
                        continue
                    if not layout.parts:
                        layout.fitness, layout.efficiency = float('inf'), 0
                        continue

                    member_idx = layout.member_idx
                    task = snapshot_member(layout, ui_params, gen, member_idx, getattr(self, 'seed', 0),
                                           search_direction=algo_kwargs.get('search_direction', (0, -1)),
                                           candidate_spacing=algo_kwargs.get('candidate_spacing', DEFAULT_CANDIDATE_SPACING),
                                           stream=self._sim_queue is not None)
                    tasks.append(task)
                    pending_layouts.append((member_idx, layout))

                if tasks:
                    rows_acc = {}
                    show_all = algo_kwargs.get('sim_show_all_members', True)
                    streamed = sorted(t.member_idx for t in tasks)
                    # Every member gets a row, including carried ones, so member i
                    # stays in row i from one generation to the next.
                    row_members = sorted(streamed + [m for m, _ in carried])
                    sheet_w = float(ui_params.get('sheet_width', 300.0))
                    sheet_h = float(ui_params.get('sheet_height', 300.0))
                    spacing = float(ui_params.get('spacing', 0.0))
                    layout_map = {m_idx: l for m_idx, l in pending_layouts}
                    parts_by_id_map = {m_idx: {p.id: p for p in l.parts} for m_idx, l in layout_map.items()}

                    def on_poll():
                        if self._sim_queue is None:
                            return
                        touched_members = set()
                        import queue
                        while True:
                            try:
                                msg = self._sim_queue.get_nowait()
                            except (queue.Empty, AttributeError):
                                break
                            # msg: (generation, member_idx, sheet_index, part_id, x, y, angle)
                            msg_gen, m_idx, s_idx, p_id, x, y, angle = msg
                            if msg_gen != gen:
                                continue
                            if not show_all and m_idx != streamed[0]:
                                continue
                            if m_idx not in rows_acc:
                                rows_acc[m_idx] = []
                            rows_acc[m_idx].append((s_idx, p_id, x, y, angle))
                            touched_members.add(m_idx)

                        if touched_members and self.draw_callback:
                            rows = {}
                            for m in touched_members:
                                row_idx = row_members.index(m) if show_all else 0
                                parts_by_id = parts_by_id_map.get(m, {})
                                rects, outlines = build_preview_row(
                                    rows_acc[m], parts_by_id, row_idx, sheet_w, sheet_h, spacing
                                )
                                rows[row_idx] = (rects, outlines)
                            self.draw_callback({'sim_member_rows': True, 'rows': rows})
                            self._sim_rows_shown.update(rows)

                    # Rows keep their last shape between draws. Empty every shown row,
                    # so a row never shows a member from an earlier generation, and
                    # draw carried members from their stored sheets: they are not
                    # nested again, so they stream nothing.
                    if self._sim_queue is not None and self.draw_callback:
                        start_rows = {r: ([], []) for r in self._sim_rows_shown}
                        if show_all:
                            for m_idx, layout in carried:
                                # PlacedPart x/y are sheet-local centroids, the same
                                # frame the workers stream.
                                placements = [(sheet.id, pp.shape.id, pp.x, pp.y, pp.angle)
                                              for sheet in layout.sheets for pp in sheet.parts]
                                row_idx = row_members.index(m_idx)
                                start_rows[row_idx] = build_preview_row(
                                    placements, {p.id: p for p in layout.parts},
                                    row_idx, sheet_w, sheet_h, spacing)
                        if start_rows:
                            self.draw_callback({'sim_member_rows': True, 'rows': start_rows})
                            self._sim_rows_shown.update(start_rows)

                    # Cancel is seen within POOL_POLL_SECONDS; the caller then
                    # terminates the workers, so a long member does not hold
                    # the run open until it finishes.
                    pending = {pool.submit(worker_nest, t) for t in tasks}
                    results = []
                    while pending:
                        done = wait_for_any(pending, cancel_callback, on_poll=on_poll if self._sim_queue is not None else None)
                        if done is None:
                            return total_time, True
                        pending -= done
                        results.extend(fut.result() for fut in done)
                    if self._sim_queue is not None:
                        on_poll()

                    results.sort(key=lambda r: r.member_idx)
                    nw_logger.replay_worker_messages(
                        [m for res in results for m in res.diagnostics])
                    for res in results:
                        layout = layout_map[res.member_idx]
                        apply_result(layout, res, ui_params)
                        total_time += res.elapsed

                return total_time, False
            finally:
                self._in_parallel_generation = False

        from .nesting_logic import nest
        
        for idx, layout in enumerate(layouts):
            if cancel_callback(): return total_time, True

            if verbose:
                nw_logger.info(f"  [Gen {gen+1}] Layout {idx+1}/{len(layouts)}: {layout.name}")

            if layout.sheets: continue
            if not layout.parts:
                layout.fitness, layout.efficiency = float('inf'), 0
                continue
            
            # Run nesting
            current_kwargs = algo_kwargs.copy()
            # Per-member stream: a member's draws must not depend on how many
            # draws earlier members made, or the run stops being reproducible
            # the moment members are reordered or run concurrently.
            member_idx = layout.member_idx
            current_kwargs['rng'] = random.Random(
                zlib.crc32(f"{self.seed}:{gen}:{member_idx}".encode()))
            current_kwargs['spawn_more_callback'] = self.request_spawn
            if layout.direction is not None:
                current_kwargs['search_direction'] = layout.direction
            if self.draw_callback:
                # Route through thread-safe signal instead of direct Qt widget calls
                if 'progress_callback' in current_kwargs:
                    current_kwargs['progress_callback'] = self._update_progress
                # The Qt log widget isn't thread-safe from the GA worker, but
                # dropping logs entirely hides the per-part [TIMING] lines —
                # route them to the FreeCAD console instead.
                current_kwargs['log_callback'] = (
                    lambda msg, level=None: nw_logger.info(msg))
            if len(layouts) > 1 or generations > 1:
                 current_kwargs['quiet'] = True
                 if 'progress_callback' in current_kwargs: del current_kwargs['progress_callback']
            if layout.genes: current_kwargs['sort'] = False
            
            # Fill placement is deferred to the winner (see run(), Phase B) —
            # generations score regular parts only, so the compactness term
            # measures the true free area.
            regular_parts, _ = self._split_fill_parts(layout.parts)
            consumption_order = []
            sheets, unplaced, _, elapsed = nest(
                regular_parts, ui_params['sheet_width'], ui_params['sheet_height'],
                rotation_steps, is_simulating, algorithm=ui_params.get('algorithm', 'Minkowski'),
                viz_manager=viz_manager, order_out=consumption_order, **current_kwargs
            )

            original_parts_map = {p.id: p for p in layout.parts}
            for s in sheets:
                for i, placed_part in enumerate(s.parts):
                    # Parts spawned mid-nest (fill top-ups) aren't in
                    # layout.parts, but they were created fresh on the main
                    # thread (not deep-copied) so they already carry a live
                    # fc_object — use them as-is.
                    original_part = original_parts_map.get(placed_part.shape.id, placed_part.shape)
                    original_part.placement = placed_part.shape.get_final_placement(s.get_origin())
                    if original_part is not placed_part.shape:
                        # The seed's polygon still sits at the master
                        # location; fitness reads bounding_box() from it
                        # (calculate_efficiency), so sync the placed
                        # geometry across.
                        original_part.polygon = placed_part.shape.polygon
                        original_part._angle = placed_part.shape._angle
                    s.parts[i].shape = original_part
            total_time += elapsed
            layout.sheets, layout.unplaced = sheets, unplaced

            # Capture genes (fill parts are not part of the genotype).
            # The order is the nester's CONSUMPTION order, not layout.parts
            # order — see the same note in ga_snapshot.nest_from_snapshot.
            # A chromosome that does not reproduce its own layout poisons
            # every child bred from it.
            gene_parts, _ = self._split_fill_parts(layout.parts)
            gene_map = {placed_part.shape.id: getattr(placed_part.shape, '_angle', 0)
                        for s in layout.sheets for placed_part in s.parts}
            gene_ids = {p.id for p in gene_parts}
            ordered = [pid for pid in consumption_order if pid in gene_ids]
            seen_ids = set(ordered)
            ordered += [p.id for p in gene_parts if p.id not in seen_ids]
            angles = {p.id: getattr(p, '_angle', 0) for p in gene_parts}
            layout.genes = [(pid, gene_map.get(pid, angles.get(pid, 0)))
                            for pid in ordered] if gene_parts else []
            
            # Efficiency/Fitness
            self.layout_manager.calculate_efficiency(
                layout, ui_params['sheet_width'], ui_params['sheet_height'],
                ui_params.get('compactness_weight', 0.0))
            unplaced_regular, _ = self._split_fill_parts(unplaced)
            if unplaced_regular:
                layout.fitness += len(unplaced_regular) * ui_params['sheet_width'] * ui_params['sheet_height'] * UNPLACED_PENALTY_FACTOR
            
        return total_time, False

    def _diversify(self, genes, seen, mutation_rate, rotation_steps, attempts=4):
        """
        Returns a chromosome that is not already in `seen` (a set of gene
        tuples), and records it there.

        Re-mutates at an escalating rate; if that still collides — a tiny part
        count, or a search space this population has saturated — falls back to
        a fresh random ordering with random angles.
        """
        from .algorithms import genetic_utils
        genes = list(genes)
        if not genes:
            return genes
        for attempt in range(attempts):
            if tuple(genes) not in seen:
                seen.add(tuple(genes))
                return genes
            genes = genetic_utils.mutate_genes(
                genes, min(1.0, mutation_rate * (attempt + 2)), rotation_steps, rng=self.rng)
        part_ids = [g[0] for g in genes]
        self.rng.shuffle(part_ids)
        step = 360.0 / rotation_steps if rotation_steps > 1 else 0.0
        genes = [(pid, self.rng.randrange(rotation_steps) * step if rotation_steps > 1 else 0.0)
                 for pid in part_ids]
        seen.add(tuple(genes))
        return genes

    def _build_next_generation(self, gen, layouts, elites, master_map, quantities, ui_params, 
                               rotation_steps, mutation_rate, immigrant_ratio, verbose):
        """Handles selection, crossover, mutation, and immigrants."""
        from .algorithms import genetic_utils
        
        use_random_direction = ui_params.get('use_random_direction', False)
        # Breed from the WHOLE evaluated population, not just the elites.
        # elite_count is max(2, pop // 5), so an elite-only pool holds exactly
        # two members at pop <= 10; rng.sample() then returns the entire pool
        # and tournament_selection becomes deterministic, handing back the same
        # parent twice. Crossover of a chromosome with itself is the identity,
        # so the population collapses to clones after generation 0.
        ranked_pool = [(l.fitness, (l.genes, l.direction))
                       for l in layouts if l.genes and l.fitness != float('inf')]
        if not ranked_pool:
            ranked_pool = [(e.fitness, (e.genes, e.direction)) for e in elites if e.genes]
        new_layouts = [elites[0]] # Champion carries forward
        elites[0].member_idx = 0

        for e in elites[1:]: self.layout_manager.delete_layout(e, verbose=verbose)
        for layout in layouts:
            if layout not in elites: self.layout_manager.delete_layout(layout, verbose=verbose)

        population_size = len(layouts)
        if population_size <= 1:
            n_immigrants = 0
            n_offspring = 0
        else:
            n_immigrants = min(population_size - 1, max(1, int((population_size - 1) * immigrant_ratio)))
            n_offspring = max(0, (population_size - 1) - n_immigrants)

        next_member_idx = 1
        # A chromosome already present in the next generation nests to a result
        # we have measured; re-evaluating it costs a full nest() for nothing.
        seen = {tuple(elites[0].genes or ())}
        for i in range(n_offspring):
            # k must leave at least one pool member out, otherwise sample()
            # draws the whole pool and the "tournament" always returns its
            # fittest member.
            k = min(3, max(1, len(ranked_pool) - 1))
            if len(ranked_pool) >= 2:
                winner1 = genetic_utils.tournament_selection(ranked_pool, k=k, rng=self.rng)
                winner2 = genetic_utils.tournament_selection(ranked_pool, k=k, rng=self.rng)
                p1_genes, p1_dir = winner1
                p2_genes, p2_dir = winner2
                child_genes = genetic_utils.crossover_genes(p1_genes, p2_genes, rng=self.rng)
                parent_direction = p1_dir
            else:
                p1_genes, p1_dir = ranked_pool[0][1] if ranked_pool else ([], None)
                child_genes = list(p1_genes)
                parent_direction = p1_dir
            child_genes = genetic_utils.mutate_genes(child_genes, mutation_rate, rotation_steps, rng=self.rng)
            child_genes = self._diversify(child_genes, seen, mutation_rate, rotation_steps)
            child_layout = self.layout_manager.create_layout(
                f"Layout_GA_{gen+2}_c{i+1}", master_map, quantities, ui_params,
                chromosome_ordering=child_genes, member_idx=next_member_idx
            )
            next_member_idx += 1
            # Offspring: inherit parent 1's direction, mutated by a small random rotation:
            if use_random_direction:
                if parent_direction is not None and self.rng.random() < mutation_rate:
                    jitter = self.rng.uniform(-math.pi / 4, math.pi / 4)
                    cur = math.atan2(parent_direction[1], parent_direction[0])
                    parent_direction = (math.cos(cur + jitter), math.sin(cur + jitter))
                child_layout.direction = parent_direction
            else:
                child_layout.direction = None
            new_layouts.append(child_layout)

        for i in range(n_immigrants):
            imm = self.layout_manager.create_layout(
                f"Layout_GA_{gen+2}_i{i+1}", master_map, quantities, ui_params,
                member_idx=next_member_idx
            )
            next_member_idx += 1
            if imm.parts:
                regular, fill = self._split_fill_parts(imm.parts)

                # Shuffle the regular parts order; fill parts stay at the tail
                self.rng.shuffle(regular)
                imm.parts = regular + fill

                # Random rotations for regular parts only — fill parts keep their
                # full rotation sweep and never get a gene_angle pin
                if rotation_steps > 1:
                    for part in regular:
                        angle = self.rng.randrange(rotation_steps) * (360.0 / rotation_steps)
                        part.set_rotation(angle)
                        part.gene_angle = angle
                else:
                    for part in regular:
                        part.gene_angle = 0.0
                imm.genes = [(p.id, getattr(p, '_angle', 0)) for p in regular]
                diversified = self._diversify(imm.genes, seen, mutation_rate, rotation_steps)
                if diversified != imm.genes:
                    # The shuffle collided with a chromosome already queued for
                    # this generation; _diversify reshuffled it, so re-sync the
                    # parts list nest() actually consumes. Fill parts stay at
                    # the tail — they are never part of the genotype.
                    by_id = {p.id: p for p in regular}
                    regular = []
                    for part_id, angle in diversified:
                        part = by_id.get(part_id)
                        if part is None:
                            continue
                        part.set_rotation(angle)
                        part.gene_angle = angle
                        regular.append(part)
                    imm.parts = regular + fill
                    imm.genes = diversified

                # Immigrants get a fresh random direction if enabled
                if use_random_direction:
                    angle_rad = self.rng.uniform(0, 2 * math.pi)
                    imm.direction = (math.cos(angle_rad), math.sin(angle_rad))
                else:
                    imm.direction = None
            new_layouts.append(imm)
        return new_layouts

    def _finalize(self, best_layout, best_efficiency, total_time, target_layout, ui_params):
        """Prepares the final NestingJob from the best layout."""
        from .nesting_job import NestingJob
        layout_manager = getattr(self, 'layout_manager', None)
        if not best_layout:
            if layout_manager:
                layout_manager.discard_template()
            return None
        if layout_manager:
            layout_manager.adopt_template(best_layout)
            
        if best_layout.layout_group and hasattr(best_layout.layout_group, "ViewObject"):
            best_layout.layout_group.ViewObject.Visibility = True
        
        for sheet in best_layout.sheets:
            sheet.draw(self.doc, ui_params, best_layout.layout_group,
                       parts_to_place_group=best_layout.parts_group)

        if best_layout.layout_group and hasattr(best_layout.layout_group, "Group"):
            for child in best_layout.layout_group.Group:
                if child.Label.startswith("MasterShapes") and hasattr(child, "ViewObject"):
                    child.ViewObject.Visibility = False
        
        best_layout.layout_group.Label = "Layout_temp"
        job = NestingJob.from_ga_result(
            doc=self.doc, target_layout=target_layout, params=ui_params, preparer=self.shape_preparer,
            layout_group=best_layout.layout_group, parts_group=best_layout.parts_group, sheets=best_layout.sheets
        )
        
        unplaced_count = len(getattr(best_layout, 'unplaced', []) or [])
        placed_count = sum(len(s) for s in best_layout.sheets)
        msg = f"GA Complete: {best_efficiency:.1f}% efficiency, {len(best_layout.sheets)} sheets, {placed_count} placed"
        if unplaced_count: msg += f", {unplaced_count} UNPLACED"
        msg += f", Time: {total_time:.2f}s"
        if getattr(self, '_serial_fallback', False):
            msg += " (workers failed to start - ran serially)"
        
        self._set_status(msg)
        nw_logger.info(msg)
        if unplaced_count:
            nw_logger.warn(f"WARNING: {unplaced_count} part(s) could not be placed: {[p.id for p in best_layout.unplaced]}")
        nw_logger.info("--- NESTING DONE ---")
        self._play_sound()
        return job
