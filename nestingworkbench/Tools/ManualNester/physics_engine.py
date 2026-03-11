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
        if distance >= self.radius:
            return 0.0
        if distance <= 0:
            return 1.0
        return max(0.0, 1.0 - (distance / self.radius) ** self.curve_exponent)

    def compute_displacements(self, dragged_center, drag_delta, parts_with_centers):
        """
        Compute displacement vectors for all parts based on proximity to dragged part.

        Args:
            dragged_center: FreeCAD.Vector — current center of the dragged part
            drag_delta: FreeCAD.Vector — how much the dragged part moved this frame
            parts_with_centers: list of (obj, FreeCAD.Vector) — other parts and their centers

        Returns:
            list of (obj, FreeCAD.Vector) — each part and its displacement vector
        """
        displacements = []
        for obj, center in parts_with_centers:
            # Calculate distance between centers (XY plane only)
            dx = center.x - dragged_center.x
            dy = center.y - dragged_center.y
            distance = (dx**2 + dy**2)**0.5

            factor = self.compute_falloff(distance) * self.strength
            
            # Displacement is drag_delta scaled by falloff factor
            # We assume FreeCAD.Vector supports multiplication by scalar and addition
            # But since this file is standalone, we'll return a way to compute it or a vector-like object
            # The tool will handle the actual FreeCAD.Vector addition.
            # To keep it standalone, we'll assume drag_delta has .x, .y, .z and return same.
            
            displacement_vec = type(drag_delta)(
                drag_delta.x * factor,
                drag_delta.y * factor,
                drag_delta.z * factor
            )
            displacements.append((obj, displacement_vec))
            
        return displacements
