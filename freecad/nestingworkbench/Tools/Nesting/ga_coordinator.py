# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Coordinates the Genetic Algorithm nesting loop.
Extracted from NestingController._execute_ga_nesting() to follow SRP.
"""
import FreeCAD
import FreeCADGui
import math
import random
from ...datatypes.shape import Shape
from .layout_manager import LayoutManager
from .algorithms import genetic_utils
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
        elite_count = max(2, population_size // 5)
        mutation_rate = 0.1
        immigrant_ratio = 0.15
        early_stop_threshold = 5
        verbose = algo_kwargs.get('verbose', False)
        cancel_callback = algo_kwargs.get('cancel_callback', lambda: False)
        
        seed = algo_kwargs.get('random_seed')
        if seed is None:
            seed = random.randrange(2**32)
        # Threading: self.rng is accessed sequentially (either worker or main thread via blocking callbacks), never concurrently.
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
        generations_without_improvement = 0
        total_nesting_time = 0
        
        try:
            for gen in range(generations):
                if cancel_callback():
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
                    algo_kwargs, is_simulating, cancel_callback, verbose, viz_manager
                )
                total_nesting_time += gen_time
                if interrupted: break

                # Evaluate progress
                layouts.sort(key=lambda l: l.fitness)
                current_best = layouts[0]
                
                if best_layout is None or current_best.fitness < best_layout.fitness:
                    best_layout = current_best
                    best_efficiency = current_best.efficiency
                    generations_without_improvement = 0
                    if verbose:
                        FreeCAD.Console.PrintMessage(f"\n>>> New Best: {best_efficiency:.1f}% efficiency <<<\n")
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
                fill_parts = [p for p in best_layout.parts
                              if getattr(p, 'fill_sheet', False) is True]
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
                    if not is_simulating:
                        # Fill parts placed here never went through the
                        # non-sim rebind loop, so sync their doc placement
                        # (mirrors _run_generation's rebind).
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

    def _dispatch_finalize(self, best_layout, best_efficiency, total_time, target_layout, ui_params):
        """Runs _finalize() and doc.recompute() on the main thread if using a worker."""
        if self.draw_callback:
            result_holder = [None]
            self.draw_callback({
                'ga_finalize': True,
                'best_layout': best_layout,
                'best_efficiency': best_efficiency,
                'total_time': total_time,
                'target_layout': target_layout,
                'ui_params': ui_params,
                'result_holder': result_holder,
            })
            return result_holder[0]
        else:
            job = self._finalize(best_layout, best_efficiency, total_time, target_layout, ui_params)
            self.doc.recompute()
            return job

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
        if not self.draw_callback:
            return spawn_fn()
        result_holder = [None]
        self.draw_callback({'spawn_fill_part': True, 'spawn_fn': spawn_fn,
                            'result_holder': result_holder})
        return result_holder[0]

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
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
            futures = [pool.submit(compute_and_cache_nfp, a, 0.0, b, rel, key)
                       for key, (a, b, rel) in missing.items()]
            for future in as_completed(futures):
                if cancel_callback():
                    pool.shutdown(wait=False, cancel_futures=True)
                    return
                done += 1
                self._update_progress(done, total, f"Precomputing NFPs {done}/{total}")

    def _run_generation(self, layouts, gen, generations, ui_params, rotation_steps, algo_kwargs,
                        is_simulating, cancel_callback, verbose, viz_manager=None):
        """Nests each layout in the population and calculates fitness/efficiency."""
        from .nesting_logic import nest
        total_time = 0
        
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
            current_kwargs['rng'] = self.rng  # Seeded fallback for search_direction=None
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
            regular_parts = [p for p in layout.parts
                             if getattr(p, 'fill_sheet', False) is not True]
            sheets, unplaced, _, elapsed = nest(
                regular_parts, ui_params['sheet_width'], ui_params['sheet_height'],
                rotation_steps, is_simulating, algorithm=ui_params.get('algorithm', 'Minkowski'),
                viz_manager=viz_manager, **current_kwargs
            )

            if not is_simulating:
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

            # Capture genes (fill parts are not part of the genotype)
            gene_parts = [p for p in layout.parts if getattr(p, 'fill_sheet', False) is not True]
            if is_simulating:
                layout.genes = [(p.id, getattr(p, '_angle', 0)) for p in gene_parts] if gene_parts else []
            else:
                gene_map = {placed_part.shape.id: getattr(placed_part.shape, '_angle', 0)
                            for s in layout.sheets for placed_part in s.parts}
                layout.genes = [(p.id, gene_map.get(p.id, getattr(p, '_angle', 0)))
                                for p in gene_parts] if gene_parts else []
            
            # Efficiency/Fitness
            self.layout_manager.calculate_efficiency(
                layout, ui_params['sheet_width'], ui_params['sheet_height'],
                ui_params.get('compactness_weight', 0.0))
            unplaced_regular = [p for p in unplaced if getattr(p, 'fill_sheet', False) is not True]
            if unplaced_regular:
                layout.fitness += len(unplaced_regular) * ui_params['sheet_width'] * ui_params['sheet_height'] * 10
            
            # Draw (simulate mode only — non-sim draws just the winner in _finalize)
            if is_simulating:
                draw_payload = {
                    'sheets': sheets,
                    'doc': self.doc,
                    'ui_params': ui_params,
                    'layout_group': layout.layout_group,
                    'parts_group': layout.parts_group,
                    'verbose': verbose,
                    'hide_layout': len(layouts) > 1,
                }

                if self.draw_callback:
                    self.draw_callback(draw_payload)
                else:
                    for sheet in sheets:
                        sheet.draw(self.doc, ui_params, layout.layout_group, 
                                   parts_to_place_group=layout.parts_group, verbose=verbose)
                    
                    if len(layouts) > 1 and layout.layout_group and hasattr(layout.layout_group, "ViewObject"):
                        layout.layout_group.ViewObject.Visibility = False
                    FreeCADGui.updateGui()
            
        return total_time, False

    def _build_next_generation(self, gen, layouts, elites, master_map, quantities, ui_params, 
                               rotation_steps, mutation_rate, immigrant_ratio, verbose):
        """Handles selection, crossover, mutation, and immigrants."""
        from .algorithms import genetic_utils
        
        use_random_direction = ui_params.get('use_random_direction', False)
        ranked_pool = [(e.fitness, (e.genes, e.direction)) for e in elites if e.genes]
        new_layouts = [elites[0]] # Champion carries forward

        for e in elites[1:]: self.layout_manager.delete_layout(e, verbose=verbose)
        for layout in layouts:
            if layout not in elites: self.layout_manager.delete_layout(layout, verbose=verbose)

        population_size = len(layouts)
        n_immigrants = max(1, int((population_size - 1) * immigrant_ratio))
        n_offspring = max(0, (population_size - 1) - n_immigrants)

        for i in range(n_offspring):
            k = min(3, len(ranked_pool))
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
            child_layout = self.layout_manager.create_layout(
                f"Layout_GA_{gen+2}_c{i+1}", master_map, quantities, ui_params, chromosome_ordering=child_genes
            )
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
            imm = self.layout_manager.create_layout(f"Layout_GA_{gen+2}_i{i+1}", master_map, quantities, ui_params)
            if imm.parts:
                regular = [p for p in imm.parts if getattr(p, 'fill_sheet', False) is not True]
                fill = [p for p in imm.parts if getattr(p, 'fill_sheet', False) is True]

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
        
        if not getattr(self, 'is_simulating', False):
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
        
        self._set_status(msg)
        FreeCAD.Console.PrintMessage(f"{msg}\n")
        if unplaced_count:
            FreeCAD.Console.PrintWarning(f"WARNING: {unplaced_count} part(s) could not be placed: {[p.id for p in best_layout.unplaced]}\n")
        FreeCAD.Console.PrintMessage(f"--- NESTING DONE ---\n")
        self._play_sound()
        return job
