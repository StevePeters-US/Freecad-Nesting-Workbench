# Nesting/nesting/utils.py

"""
This file contains utility functions, such as the convex hull algorithm,
which are used for geometry processing.
"""

def _orientation(p, q, r):
    """ Helper function for the convex hull algorithm. """
    val = (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)
    if val == 0: return 0
    return 1 if val > 0 else 2

def get_convex_hull(points):
    """
    Computes the convex hull of a set of points using the Gift Wrapping Algorithm.
    """
    n = len(points)
    if n < 3: return points
    l = 0
    for i in range(1, n):
        if points[i].x < points[l].x:
            l = i
    hull = []
    p = l
    q = 0
    while True:
        hull.append(points[p])
        q = (p + 1) % n
        for i in range(n):
            if _orientation(points[p], points[i], points[q]) == 2:
                q = i
        p = q
        if p == l: break
    return hull
