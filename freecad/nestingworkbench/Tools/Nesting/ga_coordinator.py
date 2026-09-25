# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Coordinates the Genetic Algorithm nesting loop.
Extracted from NestingController._execute_ga_nesting() to follow SRP.
"""
import FreeCAD
import FreeCADGui
import math
import random
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, wait
from ...datatypes.shape import Shape
from .layout_manager import LayoutManager
from .algorithms import genetic_utils
from .ga_snapshot import UNPLACED_PENALTY_FACTOR

ELITE_FRACTION_DIVISOR = 5      # top 1/5 of the population survives unchanged
MIN_ELITE_COUNT = 2             # always keep a breeding pair
DEFAULT_MUTATION_RATE = 0.1
DEFAULT_IMMIGRANT_RATIO = 0.15  # fraction of each new generation seeded at random
POOL_POLL_SECONDS = 0.25        # how often a wait on workers re-checks for cancel
POOL_STARTUP_TIMEOUT = 60.0     # a worker that has not answered by now never will

def enumerate_nfp_jobs(parts):
    """Enumerates every NFP cache key the placement loop can request for
    these parts, as {cache_key: (rep_A, rep_B, relative_angle)}.

    Part type identity is source_freecad_object.Label — the same field the
    cache key uses (never parse part.id). The placed part A is always keyed
    at angle 0 with the rotation folded into relative_angle, matching
    get_incremental_candidates / _calculate_and_cache_nfp.
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
                       rel, b.spacing, b.deflection, b.simplification)
                jobs.setdefault(key, (a, b, rel))
    return jobs

def worker_mp_context():
    """Returns the multiprocessing context for the GA worker pool.

    Spawned workers are started by re-running the parent's interpreter, which
    inside FreeCAD is freecad.exe, not Python. FreeCAD then parses Python's
    command-line flags as its own (-E is its --macro-path) and pops an
    "Initialization of FreeCAD failed" dialog for every worker. Point spawn at
    the Python interpreter FreeCAD ships beside its executable instead.

    Fork contexts need no interpreter and are returned unchanged.

    Raises:
        RuntimeError: No Python interpreter found next to the executable.
    """
    import multiprocessing
    import multiprocessing.spawn
    import os
    import sys

    ctx = multiprocessing.get_context()
    if ctx.get_start_method() == "fork":
        return ctx

    current = multiprocessing.spawn.get_executable()
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
    """
    if pool is None:
        return
    processes = list((getattr(pool, "_processes", None) or {}).values())
    pool.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        try:
            process.terminate()
        except Exception:
            pass  # Already exited

def wait_for_any(pending, cancel_callback, deadline=None):
    """Blocks until at least one future in pending finishes.

    Polls instead of blocking outright, so Cancel is seen within
    POOL_POLL_SECONDS even when no worker ever answers.

    Returns:
        The set of finished futures, or None if cancel was requested.

    Raises:
        TimeoutError: deadline (a time.monotonic() value) passed first.
    """
    while True:
        done, _ = wait(pending, timeout=POOL_POLL_SECONDS, return_when=FIRST_COMPLETED)
        if done:
            return done
        if cancel_callback():
            return None
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError

def start_worker_pool(max_workers, cache_payload, cancel_callback):
    """Creates the GA worker pool and waits for a worker to answer.

    Workers start lazily, so a pool that cannot run - a worker that dies on
    start-up, or one stuck before it ever reads a task - only shows itself
    once work is waiting on it. Probe it here, while falling back to serial
    is still possible.

    Returns:
        A working pool, or None if cancel was requested during start-up.

    Raises:
        RuntimeError: No worker answered within POOL_STARTUP_TIMEOUT.
        Exception: Whatever the worker raised while starting.
    """
    from concurrent.futures import ProcessPoolExecutor
    from .ga_worker import init_worker, worker_ping

    pool = ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=worker_mp_context(),
        initializer=init_worker,
        initargs=(cache_payload,)
    )
    try:
        probe = pool.submit(worker_ping)
        deadline = time.monotonic() + POOL_STARTUP_TIMEOUT
        if wait_for_any({probe}, cancel_callback, deadline) is None:
            terminate_pool(pool)
            return None
        probe.result()
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

    def _set_status(self, msg):
        if self.worker:
            self.worker.status_changed.emit(msg)
            return
        callback = self.ui_callbacks.get('set_status')
        if callback:
            try:
                callback(msg)
            except RuntimeError:
                pass  # UI widget deleted (panel closed)

    def _update_progress(self, current, total, msg=None):
        if self.worker:
            self.worker.progress_updated.emit(current, total, msg or "")
            return
        callback = self.ui_callbacks.get('update_progress')
        if callback:
            try:
                callback(current, total, msg)
            except RuntimeError:
                pass  # UI widget deleted (panel closed)

    def _play_sound(self):
        callback = self.ui_callbacks.get('play_sound')
        if callback:
            try:
                callback()
            except RuntimeError:
                pass  # Sound callback failed or widget deleted

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
        FreeCAD.Console.PrintMessage(f"GA random seed: {seed}\n")
        
        if algo_kwargs.pop('clear_nfp_cache', False):
            Shape.clear_nfp_cache()
            FreeCAD.Console.PrintMessage("NFP cache cleared (user request).\n")
        
        if verbose:
            FreeCAD.Console.PrintMessage(f"GA Mode: {generations} generations, {population_size} population\n")
        
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
            self._precompute_all_nfps(layouts[0].parts, cancel_callback)

        best_layout = None
        best_efficiency = 0
        best_fitness_at_last_reset = None
        generations_without_improvement = 0
        total_nesting_time = 0

        # Pool lifecycle: single pool across the whole GA run.
        # Measured 2026-09-03, 27 parts, P=20, G=10 on 24-core host (3-run median,
        # 191 placements in every run): 1w 10.38s wall / 9.34s nest,
        # 8w 3.96s wall / 2.72s nest (2.62x wall / 3.44x placement).
        # Re-measured after the diversity fix, which roughly doubled both columns:
        # a population that no longer collapses to clones keeps improving, so the
        # run no longer early-stops and actually evaluates all 10 generations.
        # Worker serialization and process overhead diminish returns past 8 workers.
        # Sized to min(os.cpu_count() or 1, population_size, 8).
        import os

        pool = None
        self._serial_fallback = False
        max_workers = min(os.cpu_count() or 1, population_size, 8)
        if max_workers > 1 and not is_simulating:
            self._set_status("Starting worker processes...")
            try:
                with Shape.nfp_cache_lock:
                    cache_payload = dict(Shape.nfp_cache)
                pool = start_worker_pool(max_workers, cache_payload, cancel_callback)
            except Exception as e:
                FreeCAD.Console.PrintWarning(
                    f"[GACoordinator] Worker processes failed to start ({e}); "
                    f"evaluating the population one member at a time instead.\n"
                )
                self._set_status("Worker processes failed to start - running serially (see Report view)")
                self._serial_fallback = True
                pool = None

        try:
            for gen in range(generations):
                if cancel_callback():
                    terminate_pool(pool)
                    FreeCAD.Console.PrintMessage("Nesting cancelled by user.\n")
                    break

                if verbose:
                    FreeCAD.Console.PrintMessage(f"\n=== Generation {gen+1}/{generations} ===\n")
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
                        FreeCAD.Console.PrintMessage(f"\n>>> New Best: {best_efficiency:.1f}% efficiency <<<\n")

                if best_fitness_at_last_reset is None or (
                        best_fitness_at_last_reset - current_best.fitness
                        > max(1e-4, stagnation_epsilon * abs(best_fitness_at_last_reset))):
                    best_fitness_at_last_reset = current_best.fitness
                    generations_without_improvement = 0
                else:
                    generations_without_improvement += 1
                    if verbose:
                        FreeCAD.Console.PrintMessage(f"\nNo improvement ({generations_without_improvement}/{early_stop_threshold})\n")
                
                # Early Stopping / Stagnation Check:
                # An early stopping threshold of 5 generations is selected because nesting jobs typically
                # operate with relatively small chromosome/population sizes where genetic diversity 
                # converges quickly. If no elite child (improved ordering/rotation layout) is found 
                # after 5 generations, the population has converged to a local optimum, and continuing
                # to run more generations would waste CPU/GPU resources without realistic chance of improvement.
                if generations_without_improvement >= early_stop_threshold:
                    FreeCAD.Console.PrintMessage(f"Early stopping: no improvement for {early_stop_threshold} generations\n")
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
                            lambda msg, level=None: FreeCAD.Console.PrintMessage(f"{msg}\n"))
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
            import traceback
            FreeCAD.Console.PrintError(f"GA Nesting Error: {e}\n{traceback.format_exc()}\n")
            self._set_status(f"Error: {e}")
            if 'layouts' in locals():
                for layout in layouts:
                    self.layout_manager.delete_layout(layout)
            self._dispatch_recompute()
            return None
        finally:
            terminate_pool(pool)

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

    def request_spawn(self, spawn_fn):
        """Runs spawn_fn() on the main thread (it creates FreeCAD doc objects)
        and returns the new part. Used by the nester to mint fill-part
        instances on demand."""
        if getattr(self, '_in_parallel_generation', False):
            FreeCAD.Console.PrintError(
                "[GACoordinator] request_spawn called during parallel generation! Fill must be winner-only.\n"
            )
            raise AssertionError(
                "request_spawn called during parallel generation! Fill must be deferred to the winner."
            )
        return self._dispatch_to_main_thread('spawn_fill_part', spawn_fn, spawn_fn=spawn_fn)

    def _precompute_all_nfps(self, parts, cancel_callback):
        """Fills Shape.nfp_cache with every NFP the run can request, before
        the generation loop starts. Runs on the GA worker thread; workers
        touch only Shapely geometry, so no main-thread marshaling is needed.
        Progress goes through the worker signals so the UI stays live."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import os
        from .algorithms.minkowski_engine import compute_and_cache_nfp

        jobs = enumerate_nfp_jobs(parts)
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
            futures = [pool.submit(compute_and_cache_nfp, a, 0.0, b, rel, key)
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

        if pool is not None and not is_simulating:
            from .ga_snapshot import snapshot_member, apply_result
            from .ga_worker import worker_nest

            self._in_parallel_generation = True
            try:
                tasks = []
                pending_layouts = []

                for idx, layout in enumerate(layouts):
                    if cancel_callback(): return total_time, True

                    if verbose:
                        FreeCAD.Console.PrintMessage(f"  [Gen {gen+1}] Layout {idx+1}/{len(layouts)}: {layout.name}\n")

                    if layout.sheets: continue
                    if not layout.parts:
                        layout.fitness, layout.efficiency = float('inf'), 0
                        continue

                    member_idx = layout.member_idx
                    task = snapshot_member(layout, ui_params, gen, member_idx, getattr(self, 'seed', 0),
                                           search_direction=algo_kwargs.get('search_direction', (0, -1)))
                    tasks.append(task)
                    pending_layouts.append((member_idx, layout))

                if tasks:
                    # Cancel is seen within POOL_POLL_SECONDS; the caller then
                    # terminates the workers, so a long member does not hold
                    # the run open until it finishes.
                    pending = {pool.submit(worker_nest, t) for t in tasks}
                    results = []
                    while pending:
                        done = wait_for_any(pending, cancel_callback)
                        if done is None:
                            return total_time, True
                        pending -= done
                        results.extend(fut.result() for fut in done)
                    results.sort(key=lambda r: r.member_idx)
                    layout_map = {m_idx: l for m_idx, l in pending_layouts}
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
                FreeCAD.Console.PrintMessage(f"  [Gen {gen+1}] Layout {idx+1}/{len(layouts)}: {layout.name}\n")

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
                    lambda msg, level=None: FreeCAD.Console.PrintMessage(f"{msg}\n"))
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
        if not best_layout: return None
            
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
        FreeCAD.Console.PrintMessage(f"{msg}\n")
        if unplaced_count:
            FreeCAD.Console.PrintWarning(f"WARNING: {unplaced_count} part(s) could not be placed: {[p.id for p in best_layout.unplaced]}\n")
        FreeCAD.Console.PrintMessage(f"--- NESTING DONE ---\n")
        self._play_sound()
        return job
