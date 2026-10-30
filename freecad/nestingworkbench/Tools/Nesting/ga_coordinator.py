# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Coordinates the Genetic Algorithm nesting loop.
Extracted from NestingController._execute_ga_nesting() to follow SRP.
"""
import FreeCADGui
import random
import time
from concurrent.futures import FIRST_COMPLETED, wait
from ...datatypes.shape import Shape
from ...constants import ALGO_BEAM, ALGO_GREEDY
from freecad.nestingworkbench import nw_logger, nw_settings
from .layout_manager import LayoutManager
from .algorithms import search_genome
from .algorithms.fitness import weights_from_params
from .algorithms import sheet_sequence
from .algorithms.minkowski_engine import DEFAULT_CANDIDATE_SPACING
from .worker_sizing import ga_worker_count, SOURCE_LOGICAL_FALLBACK, SOURCE_SETTING
from ...sheet_library import model as sheet_model

DEFAULT_MUTATION_RATE = 0.1     # fallback when ui_params has no 'mutation_rate'
DEFAULT_WINNERS = 2             # fallback when ui_params has no 'ga_winners'
POOL_POLL_SECONDS = 0.25        # how often a wait on workers re-checks for cancel
POOL_STARTUP_TIMEOUT = 60.0     # a worker that has not answered by now never will
TERMINATE_JOIN_SECONDS = 2.0   # per worker; terminate() is immediate, so this is a cap, not a wait
CACHE_REMOVE_ATTEMPTS = 5
CACHE_REMOVE_RETRY_SECONDS = 0.1

# numpy's BLAS reads these at import and otherwise starts os.cpu_count() - 1
# threads in every worker. Nesting never makes a BLAS call big enough to use them.
BLAS_THREAD_VARS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")

# id(pool) -> the NFP cache file its workers load; removed by terminate_pool
_pool_cache_files = {}

def enumerate_nfp_jobs(parts, candidate_spacing=DEFAULT_CANDIDATE_SPACING):
    """Enumerates every NFP cache key the placement loop can request for
    these parts, as {cache_key: (rep_A, rep_B, relative_angle)}.

    Part type identity is Shape.type_label (the label plus an outline hash, set by
    ShapePreparer) — the same field the cache key uses (never parse part.id). The placed part A is always keyed
    at angle 0 with the rotation folded into relative_angle, matching
    get_incremental_candidates / _calculate_and_cache_nfp:
    (placed_label, part_label, relative_angle, spacing, deflection, simplification, candidate_spacing).
    """
    reps = {}
    for p in parts:
        reps.setdefault(p.type_label, p)

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
                key = (a.type_label,
                       b.type_label,
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
    """Stops a worker pool without waiting on its work.

    shutdown(wait=False) only stops handing out work: a worker busy on a
    member, or one that never started, stays alive and so does the run
    waiting on it. Workers hold nothing the GA needs, so terminate them.
    Terminated workers are joined (at most TERMINATE_JOIN_SECONDS each) so
    Windows releases the cache file before it is deleted.
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
    for process in processes:
        try:
            process.join(TERMINATE_JOIN_SECONDS)
        except Exception as e:
            nw_logger.debug(f"[GACoordinator] Worker process join ignored: {e}")
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
    """Deletes the NFP cache file, retrying briefly: on Windows a just-terminated worker or a
    virus scanner can hold it for a moment. Warns if it is still there."""
    import os
    if path is None:
        return
    error = None
    for _ in range(CACHE_REMOVE_ATTEMPTS):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except OSError as e:
            error = e
            time.sleep(CACHE_REMOVE_RETRY_SECONDS)
    nw_logger.warn(f"[GACoordinator] NFP cache file not removed: {path}: {error}")

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


def build_preview_row(placements, parts_by_id, row, sheet_sizes, spacing):
    """World-space outlines for one simulation preview row.

    placements: [(sheet_index, part_id, x, y, angle)] in placement order,
    sheet-local centroids as PlacedPart records them. Row 0 sits on the real
    sheet row; row r is lifted by r * max_h * (1 + SIM_ROW_GAP_FRACTION).
    Sheets in a row step along x exactly as Sheet.get_origin() does.

    Returns (sheet_rects, part_outlines): two lists of Shapely Polygons.
    """
    from shapely.geometry import box
    from shapely.affinity import rotate, translate
    max_h = max(h for _, h in sheet_sizes)
    y0 = row * max_h * (1.0 + SIM_ROW_GAP_FRACTION)
    used = sorted({s for s, *_ in placements})

    def origin_x_for_sheet(s_idx):
        return sum(sheet_sequence.row_for_index(sheet_sizes, j)[0] + spacing for j in range(s_idx))

    rects = []
    for s in used:
        w_s, h_s = sheet_sequence.row_for_index(sheet_sizes, s)
        ox = origin_x_for_sheet(s)
        rects.append(box(ox, y0, ox + w_s, y0 + h_s))

    outlines = []
    for s, part_id, x, y, angle in placements:
        part = parts_by_id.get(part_id)
        if part is None or part.original_polygon is None:
            continue
        poly = rotate(part.original_polygon, angle, origin="centroid")
        c = poly.centroid
        ox = origin_x_for_sheet(s)
        outlines.append(translate(poly, x - c.x + ox, y - c.y + y0))
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


class DocumentClosedError(RuntimeError):
    """The nest's document was deleted while the nest ran."""


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
        winners = max(1, min(population_size, int(ui_params.get('ga_winners', DEFAULT_WINNERS))))
        mutation_rate = float(ui_params.get('mutation_rate', DEFAULT_MUTATION_RATE))
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
        
        self.layout_manager = LayoutManager(self.doc, self.shape_preparer.processed_shape_cache)
        
        self._seen_genomes = set()

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
                master_map, quantities, ui_params, population_size,
                rotation_steps, verbose=verbose
            )

        # Genomes need the parts' types and allowed rotation counts, so they are
        # assigned here, once the population exists (member 0 = the panel's values).
        if layouts:
            regular, _ = self._split_fill_parts(layouts[0].parts)
            self._bounds = self._build_bounds(ui_params, algo_kwargs, regular)
            for i, layout in enumerate(layouts):
                layout.genome = (search_genome.seed_genome(self._bounds) if i == 0
                                 else search_genome.random_genome(self._bounds, self.rng))
                self._seen_genomes.add(search_genome.canonical(layout.genome, self._bounds))

        if layouts and layouts[0].parts:
            misfits = sheet_sequence.parts_that_fit_no_sheet(
                layouts[0].parts, sheet_sequence.sizes(ui_params['sheet_sequence']),
                default_rotation_steps=rotation_steps)
            if misfits:
                names = ", ".join(f"{label} ({w:.0f} × {h:.0f} mm)" for label, w, h in misfits)
                msg = (f"Cannot nest: {len(misfits)} part(s) fit on no sheet in the list "
                       f"at any allowed rotation: {names}.")
                self._set_status(msg)
                nw_logger.warn(msg)
                for layout in layouts:
                    self.layout_manager.delete_layout(layout)
                self._dispatch_discard_template()
                self._dispatch_recompute()
                return None

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
                      else "GA worker processes set to 1 in Algorithm > Advanced"
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
                # because a converged population still jitters the offcuts
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
                # operate with relatively small populations where genetic diversity 
                # converges quickly. If no child (a genome of placer parameters) beats the champion 
                # after 5 generations, the population has converged to a local optimum, and continuing
                # to run more generations would waste CPU/GPU resources without realistic chance of improvement.
                if generations_without_improvement >= early_stop_threshold:
                    nw_logger.info(f"Early stopping: no improvement for {early_stop_threshold} generations")
                    break
                
                # STEP 2 & 3: Build next generation
                if gen < generations - 1:
                    parents = layouts[:winners]
                    
                    if self.draw_callback:
                        next_gen_payload = {
                            'build_next_generation': True,
                            'gen': gen,
                            'layouts': layouts,
                            'parents': parents,
                            'master_map': master_map,
                            'quantities': quantities,
                            'ui_params': ui_params,
                            'rotation_steps': rotation_steps,
                            'mutation_rate': mutation_rate,
                            'verbose': verbose
                        }
                        self.draw_callback(next_gen_payload)
                        layouts = self._pending_layouts
                    else:
                        layouts = self._build_next_generation(
                            gen, layouts, parents, master_map, quantities, ui_params,
                            rotation_steps, mutation_rate, verbose
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
                    fill_kwargs['spawn_more_callback'] = self.request_spawn
                    fill_kwargs['cancel_callback'] = cancel_callback
                    # Fill places with the same Scoring weights as the members (ruling 26).
                    fill_kwargs['fitness_weights'] = weights_from_params(ui_params)
                    if self.draw_callback:
                        fill_kwargs.pop('progress_callback', None)
                        fill_kwargs['log_callback'] = (
                            lambda msg, level=None: nw_logger.info(msg))
                    if best_layout.sheets is None:
                        best_layout.sheets = []
                    pre_counts = [len(s.parts) for s in best_layout.sheets]
                    _, fill_time = fill_existing_sheets(
                        best_layout.sheets, fill_parts,
                        sheet_sequence.sizes(ui_params['sheet_sequence']),
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
                        best_layout, weights_from_params(ui_params))
                    best_efficiency = best_layout.efficiency
            
            # An unplaced part is a failed run (owner decision): nothing is
            # drawn or committed. Cancelled runs are left to the existing
            # cancel handling: a cancelled nester stops consuming parts without
            # listing them as unplaced, so this check would be meaningless there.
            if best_layout is not None and not cancel_callback():
                unplaced_regular, _ = self._split_fill_parts(best_layout.unplaced or [])
                if unplaced_regular:
                    names = ", ".join(sorted({getattr(p, 'master_label', None) or p.id
                                              for p in unplaced_regular}))
                    msg = (f"Nesting failed: {len(unplaced_regular)} part(s) could not be "
                           f"placed ({names}). Add a sheet they fit on to the list, or "
                           f"move a larger sheet to the end so it repeats.")
                    self._set_status(msg)
                    nw_logger.warn(msg)
                    doomed = list({id(l): l for l in [*layouts, best_layout]}.values())
                    for layout in doomed:
                        self.layout_manager.delete_layout(layout)
                    self._dispatch_discard_template()
                    self._dispatch_recompute()
                    return None

            # A cancelled run commits nothing: _on_nesting_finished discards
            # whatever job comes back. Don't adopt the template and draw the
            # winner's sheets only to delete them a signal later.
            if cancel_callback():
                self._dispatch_discard_template()
                self._dispatch_recompute()
                return None

            # STEP 4: Finalize result — dispatch to main thread (ViewObject + recompute)
            job = self._dispatch_finalize(best_layout, best_efficiency, total_nesting_time, target_layout, ui_params)
            return job

        except Exception as e:
            if isinstance(e, DocumentClosedError):
                nw_logger.error(f"Nesting Error: {e}")
            else:
                nw_logger.exception(f"GA Nesting Error: {e}")
            self._set_status(f"Error: {e}")
            if not isinstance(e, DocumentClosedError):
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
        # One update per shown percent, plus the last: a signal per NFP (7,688 on
        # 30_Piece_Test) kept the GUI thread repainting and slowed the precompute
        # from 18 s to 73 s (ARS round 2).
        shown_pct = -1
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
                pct = done * 100 // total
                if pct != shown_pct or done == total:
                    shown_pct = pct
                    self._update_progress(done, total, f"Precomputing NFPs {done}/{total}")

    @staticmethod
    def _build_bounds(ui_params, algo_kwargs, regular_parts):
        """The GenomeBounds for this run: what the panel lets the GA vary.

        *regular_parts* are the parts the placer will place (fill parts excluded):
        their count is the number of beam steps and their types the rotation genes.
        """
        default_steps = ui_params.get('rotation_steps', 1)
        counts = {}
        for p in regular_parts:
            n = max(1, int(getattr(p, 'rotation_steps', None) or default_steps))
            if counts.setdefault(p.type_label, n) != n:
                raise ValueError(
                    f"Part type '{p.type_label}' has two different allowed rotation counts "
                    f"({counts[p.type_label]} and {n}); the rotation gene key would be ambiguous.")
        return search_genome.GenomeBounds(
            algorithm=ui_params['algorithm'],
            direction_deg=float(ui_params.get('nesting_direction', 270.0)),
            evolve_direction=bool(ui_params.get('evolve_direction', False)),
            detent_deg=float(ui_params.get('direction_detent', 0.0)),
            beam_width=int(algo_kwargs.get('beam_width', 1)),
            width_decay=float(algo_kwargs.get('width_decay', 1.0)),
            n_steps=len(regular_parts),
            part_types=tuple(sorted(counts.items())),
        )

    @staticmethod
    def _fixed_kwargs(ui_params, algo_kwargs):
        """Placer settings that are not genes, as sorted (key, value) pairs.

        Greedy has none (its direction and sort key are genes). Beam keeps the
        panel's branch limits, iterative flag and direction fixed.
        """
        if ui_params['algorithm'] != ALGO_BEAM:
            return ()
        fixed = {k: algo_kwargs[k] for k in ('max_part_branches', 'max_rot_branches',
                                             'iterative', 'search_direction')
                 if k in algo_kwargs}
        return tuple(sorted(fixed.items()))

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
                        nw_logger.info(f"  [Gen {gen+1}] Layout {idx+1}/{len(layouts)}: {layout.name}"
                                       + (f" ({search_genome.describe(layout.genome)})" if layout.genome is not None else ""))

                    if layout.sheets:
                        # The champion carries its layout; it is not nested again.
                        carried.append((layout.member_idx, layout))
                        continue
                    if not layout.parts:
                        layout.fitness, layout.efficiency = float('inf'), 0
                        continue

                    member_idx = layout.member_idx
                    task = snapshot_member(layout, ui_params, gen, member_idx,
                                           fixed_kwargs=self._fixed_kwargs(ui_params, algo_kwargs),
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
                                    rows_acc[m], parts_by_id, row_idx,
                                    sheet_sequence.sizes(ui_params['sheet_sequence']), spacing
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
                                    row_idx, [(s.width, s.height) for s in layout.sheets], spacing)
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
                nw_logger.info(f"  [Gen {gen+1}] Layout {idx+1}/{len(layouts)}: {layout.name}"
                               + (f" ({search_genome.describe(layout.genome)})" if layout.genome is not None else ""))

            if layout.sheets: continue
            if not layout.parts:
                layout.fitness, layout.efficiency = float('inf'), 0
                continue
            
            # Run nesting
            current_kwargs = algo_kwargs.copy()
            current_kwargs['spawn_more_callback'] = self.request_spawn
            current_kwargs.update(search_genome.placer_kwargs(layout.genome))
            # Beam's pass pick scores with the GA's weights (NLA ruling 6); the
            # worker path reads MemberTask.fitness_weights instead.
            current_kwargs['fitness_weights'] = weights_from_params(ui_params)
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
            
            # Fill placement is deferred to the winner (see run(), Phase B) —
            # generations score regular parts only, so the offcuts term
            # measures the true free area.
            regular_parts, _ = self._split_fill_parts(layout.parts)
            sizes = sheet_sequence.sizes(ui_params['sheet_sequence'])
            sheets, unplaced, _, elapsed = nest(
                regular_parts,
                sizes,
                rotation_steps, is_simulating, algorithm=ui_params['algorithm'],
                viz_manager=viz_manager, **current_kwargs
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

            # Efficiency/Fitness
            unplaced_regular, _ = self._split_fill_parts(unplaced)
            self.layout_manager.calculate_efficiency(
                layout, weights_from_params(ui_params), len(unplaced_regular), sizes)
            
        return total_time, False

    def _diversify(self, genome, mutation_rate, attempts=4):
        """Returns *genome*, re-mutated until it is new to this run, and records it.

        The set of evaluated genomes is run-scoped (``_seen_genomes``). If every
        attempt collides, the duplicate is returned rather than a random genome.
        """
        bounds = self._bounds
        for attempt in range(attempts):
            if search_genome.canonical(genome, bounds) not in self._seen_genomes:
                break
            genome = search_genome.mutate(
                genome, min(1.0, mutation_rate * (attempt + 2)), bounds, self.rng)
        self._seen_genomes.add(search_genome.canonical(genome, bounds))
        return genome

    def _build_next_generation(self, gen, layouts, parents, master_map, quantities, ui_params,
                               rotation_steps, mutation_rate, verbose):
        """Champion carried; children of the winners; then one mutation pass (NLA rulings 10-11).

        *parents* are the top-N members by fitness (N = the panel's Winners). The
        champion, parents[0], survives unchanged and is never mutated. Every other
        member is bred from two distinct winners (a clone when N is 1), gene by
        gene from either parent. Only after all children exist does the mutation
        pass run, so a mutation never leaks into another child's parents.
        """
        bounds = self._bounds
        champion = parents[0]
        champion.member_idx = 0
        for layout in layouts:
            if layout is not champion:
                self.layout_manager.delete_layout(layout, verbose=verbose)

        pool = [p.genome for p in parents if p.genome is not None]
        children = []
        for _ in range(max(0, len(layouts) - 1)):
            if len(pool) >= 2:
                a, b = self.rng.sample(pool, 2)
                children.append(search_genome.crossover(a, b, self.rng))
            else:
                children.append(pool[0] if pool else search_genome.seed_genome(bounds))

        # Mutation pass over the whole new generation (ruling 11), then dedup.
        children = [search_genome.mutate(g, mutation_rate, bounds, self.rng) for g in children]
        if champion.genome is not None:
            self._seen_genomes.add(search_genome.canonical(champion.genome, bounds))
        children = [self._diversify(g, mutation_rate) for g in children]

        new_layouts = [champion]
        for i, genome in enumerate(children, start=1):
            new_layouts.append(self.layout_manager.create_layout(
                f"Layout_GA_{gen+2}_c{i}", master_map, quantities, ui_params,
                genome=genome, member_idx=i))
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
            sheet.stock = dict(sheet_sequence.row_for_index(ui_params['sheet_sequence'], sheet.spec_index))
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
        
        placed_count = sum(len(s) for s in best_layout.sheets)
        algo_label = {ALGO_BEAM: "Beam Search",
                      ALGO_GREEDY: "Greedy"}.get(ui_params.get('algorithm'), "Nesting")
        pop = int(ui_params.get('population_size', 1))
        gens = int(ui_params.get('generations', 1))
        search = "single run" if pop <= 1 and gens <= 1 else f"GA {pop}\u00d7{gens}"
        msg = (f"{algo_label}, {search} complete: {best_efficiency:.1f}% efficiency, "
               f"{len(best_layout.sheets)} sheets, {placed_count} placed")
        msg += f", Time: {total_time:.2f}s"
        total_cost = sum(s.stock['cost'] for s in best_layout.sheets)
        if total_cost > 0:
            msg += f", sheets {sheet_model.format_cost(total_cost, nw_settings.get_currency_symbol())}"
        if getattr(self, '_serial_fallback', False):
            msg += " (workers failed to start - ran serially)"
        
        self._set_status(msg)
        nw_logger.info(msg)
        nw_logger.info("--- NESTING DONE ---")
        self._play_sound()
        return job
