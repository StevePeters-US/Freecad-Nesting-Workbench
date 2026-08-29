# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Physics engine for the manual nesting tool.
Handles repulsion and falloff computation for parts near a dragged part.
This module is designed to be standalone and doesn't import FreeCAD.
"""

class PhysicsEngine:
    def __init__(self, radius=200.0, curve_exponent=2.0, strength=1.0):
        """
        Args:
            radius: max influence distance (mm) from dragged part center
            curve_exponent: falloff curve power (1=linear, 2=quadratic, 3=cubic)
            strength: global multiplier on displacement
        """
        self.radius = radius
        self.curve_exponent = curve_exponent
        self.strength = strength

    def compute_falloff(self, distance):
        """Returns falloff factor in [0, 1]. 0 = no influence, 1 = full influence."""
        if self.radius <= 0:
            return 0.0
        if distance >= self.radius:
            return 0.0
        if distance <= 0:
            return 1.0
        return max(0.0, 1.0 - (distance / self.radius) ** self.curve_exponent)

    def compute_displacements(self, dragged_center, drag_delta, parts_info):
        """
        Compute displacement vectors for all parts based on center-to-center distance.
        Parts are pushed AWAY from the dragged part center (repulsion).

        Uses NumPy for vectorised distance/falloff computation when available,
        falling back to a pure-Python loop otherwise.

        Args:
            dragged_center: FreeCAD.Vector — current center of the dragged part
            drag_delta: FreeCAD.Vector — how much the dragged part moved this frame
            parts_info: list of (obj, center, width, height) — other parts

        Returns:
            list of (obj, FreeCAD.Vector) — each part and its displacement vector
        """
        if not parts_info:
            return []

        Vec = type(drag_delta)
        drag_len = drag_delta.Length

        if drag_len < 0.001:
            return [(obj, Vec(0, 0, 0)) for obj, *_ in parts_info]

        try:
            import numpy as np
            return self._compute_numpy(dragged_center, drag_len, parts_info, Vec)
        except ImportError:
            return self._compute_python(dragged_center, drag_len, parts_info, Vec)

    def _compute_numpy(self, dragged_center, drag_len, parts_info, Vec):
        import numpy as np
        n = len(parts_info)
        centers = np.empty((n, 2))
        for i, (_, c, _, _) in enumerate(parts_info):
            centers[i, 0] = c.x
            centers[i, 1] = c.y

        dc = np.array([dragged_center.x, dragged_center.y])
        diffs = centers - dc
        dists = np.sqrt((diffs * diffs).sum(axis=1))

        factors = np.zeros(n)
        r = self.radius
        if r > 0:
            mask = (dists >= 0.001) & (dists < r)
            if mask.any():
                factors[mask] = (
                    np.maximum(0.0, 1.0 - (dists[mask] / r) ** self.curve_exponent)
                    * self.strength
                )

        result = []
        for i, (obj, _, _, _) in enumerate(parts_info):
            f = factors[i]
            if f < 0.001:
                result.append((obj, Vec(0, 0, 0)))
            else:
                push = drag_len * f
                d = dists[i]
                result.append((obj, Vec(diffs[i, 0] / d * push, diffs[i, 1] / d * push, 0)))
        return result

    def compute_raw(self, dragged_xy, drag_len, parts_keys_and_centers):
        """Like compute_displacements but takes plain tuples — no FreeCAD.Vector needed.

        Args:
            dragged_xy: (cx, cy) float tuple — center of dragged part
            drag_len: float — length of drag delta this frame
            parts_keys_and_centers: list of (key, (cx, cy)) tuples

        Returns:
            list of (key, (dx, dy)) displacement tuples
        """
        if not parts_keys_and_centers or drag_len < 0.001:
            return [(k, (0.0, 0.0)) for k, _ in parts_keys_and_centers]

        dcx, dcy = dragged_xy
        try:
            import numpy as np
            n = len(parts_keys_and_centers)
            centers = np.empty((n, 2))
            for i, (_, c) in enumerate(parts_keys_and_centers):
                centers[i, 0] = c[0]
                centers[i, 1] = c[1]
            dc = np.array([dcx, dcy])
            diffs = centers - dc
            dists = np.sqrt((diffs * diffs).sum(axis=1))
            factors = np.zeros(n)
            r = self.radius
            if r > 0:
                mask = (dists >= 0.001) & (dists < r)
                if mask.any():
                    factors[mask] = (
                        np.maximum(0.0, 1.0 - (dists[mask] / r) ** self.curve_exponent)
                        * self.strength
                    )
            result = []
            for i, (key, _) in enumerate(parts_keys_and_centers):
                f = factors[i]
                if f < 0.001:
                    result.append((key, (0.0, 0.0)))
                else:
                    push = drag_len * f
                    d = dists[i]
                    result.append((key, (diffs[i, 0] / d * push, diffs[i, 1] / d * push)))
            return result
        except ImportError:
            result = []
            for key, c in parts_keys_and_centers:
                dx = c[0] - dcx
                dy = c[1] - dcy
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < 0.001:
                    result.append((key, (0.0, 0.0)))
                    continue
                factor = self.compute_falloff(dist) * self.strength
                if factor < 0.001:
                    result.append((key, (0.0, 0.0)))
                    continue
                push = drag_len * factor
                result.append((key, (dx / dist * push, dy / dist * push)))
            return result

    def _compute_python(self, dragged_center, drag_len, parts_info, Vec):
        result = []
        for obj, center, _, _ in parts_info:
            dx = center.x - dragged_center.x
            dy = center.y - dragged_center.y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 0.001:
                result.append((obj, Vec(0, 0, 0)))
                continue
            factor = self.compute_falloff(dist) * self.strength
            if factor < 0.001:
                result.append((obj, Vec(0, 0, 0)))
                continue
            push = drag_len * factor
            result.append((obj, Vec(dx / dist * push, dy / dist * push, 0)))
        return result
