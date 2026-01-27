# nestingworkbench/Tools/Silhouette/silhouette_creator.py

"""
Creates 2D silhouette faces from 3D objects.

Supports two methods:
1. Cross-section: Cuts the part with a plane at a specified Z height (simpler, more reliable)
2. Projection: Projects the full 3D shape onto the XY plane (for complex shapes)
"""

import FreeCAD
import Part
from ..Nesting.algorithms.shape_processor import get_2d_profile_from_obj


def create_cross_section(obj, cut_height=None):
    """
    Creates a 2D silhouette by cutting the object with a horizontal plane.
    
    This is simpler and more reliable than full projection for most parts.
    The resulting wires are converted to a filled face.
    
    Args:
        obj: FreeCAD object with a Shape property
        cut_height: Z height at which to cut. If None, uses midpoint of bounding box.
        
    Returns:
        Part.Shape: A 2D Face (or Compound of Faces) representing the cross-section, or None on failure
    """
    try:
        shape = obj.Shape
        
        if shape.isNull():
            FreeCAD.Console.PrintWarning(f"[CrossSection] Shape is null for '{obj.Label}'\n")
            return None
        
        # Get bounding box to determine cut height
        bbox = shape.BoundBox
        
        if cut_height is None:
            # Default to midpoint of the part's height
            cut_height = (bbox.ZMin + bbox.ZMax) / 2.0
        
        # Create a cutting plane and slice
        cutting_direction = FreeCAD.Vector(0, 0, 1)  # Normal to XY plane
        wires = shape.slice(cutting_direction, cut_height)
        
        if not wires:
            FreeCAD.Console.PrintWarning(f"[Silhouette] No cross-section at Z={cut_height:.2f} for '{obj.Label}'\n")
            return None
        
        # Convert wires to faces
        faces = []
        for wire in wires:
            if wire.isClosed():
                try:
                    face = Part.Face(wire)
                    faces.append(face)
                except Exception as e:
                    FreeCAD.Console.PrintWarning(f"[CrossSection] Could not make face from wire: {e}\n")
        
        if not faces:
            FreeCAD.Console.PrintWarning(f"[CrossSection] No closed wires found for '{obj.Label}'\n")
            return None
        
        # Combine faces if multiple
        if len(faces) == 1:
            result = faces[0]
        else:
            result = Part.Compound(faces)
        
        # Move the result to Z=0
        result.translate(FreeCAD.Vector(0, 0, -cut_height))
        
        return result
        
    except Exception as e:
        FreeCAD.Console.PrintError(f"[CrossSection] Error for '{obj.Label}': {e}\n")
        return None


def is_valid_shape_object(obj):
    """
    Check if an object is a valid geometric shape (not a group or container).
    
    Args:
        obj: FreeCAD object to check
        
    Returns:
        tuple: (is_valid, reason) - reason explains why it's not valid if applicable
    """
    # Check if object exists
    if obj is None:
        return False, "Object is None"
    
    # Check for Shape attribute
    if not hasattr(obj, "Shape"):
        return False, f"No Shape attribute (type: {obj.TypeId})"
    
    # Check if Shape is null/empty
    if obj.Shape.isNull():
        return False, "Shape is null"
    
    # Check for groups/containers that may have a Shape but aren't geometric objects
    group_types = [
        "App::DocumentObjectGroup",
        "App::Part",
        "App::Origin",
        "App::Line",
        "App::Plane"
    ]
    if obj.TypeId in group_types:
        return False, f"Object is a container/group ({obj.TypeId})"
    
    # Check if shape has actual geometry (faces, edges, or solids)
    shape = obj.Shape
    if not (shape.Faces or shape.Edges or shape.Solids):
        return False, "Shape has no geometry (no faces, edges, or solids)"
    
    return True, "Valid"


def create_silhouette(obj, up_direction="Z+"):
    """
    Creates a 2D silhouette face from a 3D object.
    
    The silhouette is created by projecting the 3D shape onto the XY plane
    and converting the resulting polygon to a filled FreeCAD face.
    
    Args:
        obj: FreeCAD object with a Shape property
        up_direction: Which direction to project from ("Z+", "Z-", "Y+", "Y-", "X+", "X-")
        
    Returns:
        Part.Shape: A 2D Face representing the silhouette, or None on failure
    """
    try:
        # Use existing projection function to get Shapely polygon
        shapely_polygon = get_2d_profile_from_obj(obj, up_direction)
        
        if shapely_polygon is None or shapely_polygon.is_empty:
            FreeCAD.Console.PrintError(f"Failed to create silhouette for '{obj.Label}': Empty projection\n")
            return None
        
        # Convert Shapely polygon to FreeCAD face
        face = shapely_to_fc_face(shapely_polygon)
        return face
        
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to create silhouette for '{obj.Label}': {e}\n")
        return None


def shapely_to_fc_face(shapely_polygon):
    """
    Converts a Shapely polygon to a FreeCAD Face.
    
    Handles both simple polygons and polygons with holes.
    
    Args:
        shapely_polygon: A shapely.geometry.Polygon
        
    Returns:
        Part.Face: The FreeCAD face
    """
    from shapely.geometry import Polygon
    
    if not isinstance(shapely_polygon, Polygon):
        raise ValueError(f"Expected Polygon, got {type(shapely_polygon)}")
    
    # Create outer wire from exterior coordinates
    exterior_coords = list(shapely_polygon.exterior.coords)
    outer_points = [FreeCAD.Vector(x, y, 0) for x, y in exterior_coords]
    
    if len(outer_points) < 3:
        raise ValueError("Not enough points to create face")
    
    # Close the wire if needed
    if outer_points[0] != outer_points[-1]:
        outer_points.append(outer_points[0])
    
    # Create outer wire
    outer_wire = Part.makePolygon(outer_points)
    
    # Create wires for holes (interior rings)
    hole_wires = []
    for interior in shapely_polygon.interiors:
        hole_coords = list(interior.coords)
        hole_points = [FreeCAD.Vector(x, y, 0) for x, y in hole_coords]
        if len(hole_points) >= 3:
            if hole_points[0] != hole_points[-1]:
                hole_points.append(hole_points[0])
            hole_wires.append(Part.makePolygon(hole_points))
    
    # Create face from outer wire
    face = Part.Face(outer_wire)
    
    # Cut out holes if any
    if hole_wires:
        for hole_wire in hole_wires:
            try:
                hole_face = Part.Face(hole_wire)
                face = face.cut(hole_face)
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Could not cut hole: {e}\n")
    
    return face


def create_silhouette_container(doc, source_obj, up_direction="Z+"):
    """
    Creates an App::Part container with the source object and its silhouette.
    
    Args:
        doc: The FreeCAD document
        source_obj: The source FreeCAD object to create silhouette from
        up_direction: Projection direction
        
    Returns:
        tuple: (container, silhouette_obj) or (None, None) on failure
    """
    FreeCAD.Console.PrintMessage(f"[Silhouette] Processing '{source_obj.Label}' (type: {source_obj.TypeId})\n")
    
    # Validate the source object
    is_valid, reason = is_valid_shape_object(source_obj)
    if not is_valid:
        FreeCAD.Console.PrintWarning(f"[Silhouette] Skipping '{source_obj.Label}': {reason}\n")
        return None, None
    
    FreeCAD.Console.PrintMessage(f"[Silhouette] Object validated. Creating silhouette...\n")
    
    # Create the silhouette face
    silhouette_face = create_silhouette(source_obj, up_direction)
    
    if silhouette_face is None:
        FreeCAD.Console.PrintError(f"[Silhouette] Failed to create silhouette face for '{source_obj.Label}'\n")
        return None, None
    
    FreeCAD.Console.PrintMessage(f"[Silhouette] Silhouette face created. Building container...\n")
    
    # Create container
    container_name = f"Silhouette_{source_obj.Label}"
    container = doc.addObject("App::Part", container_name)
    
    # Create a link to the source object (or copy if linking not desired)
    # Using a simple Part::Feature copy for now - links can be complex
    source_copy = doc.addObject("Part::Feature", f"Source_{source_obj.Label}")
    source_copy.Shape = source_obj.Shape.copy()
    
    # Copy placement if object has one, otherwise use identity
    if hasattr(source_obj, "Placement"):
        source_copy.Placement = source_obj.Placement
    else:
        source_copy.Placement = FreeCAD.Placement()
    
    container.addObject(source_copy)
    
    # Create silhouette object
    silhouette_obj = doc.addObject("Part::Feature", f"Outline_{source_obj.Label}")
    silhouette_obj.Shape = silhouette_face
    
    # Position silhouette at Z=0, centered like the source
    # The projection is already centered at origin from get_2d_profile_from_obj
    silhouette_obj.Placement = FreeCAD.Placement(FreeCAD.Vector(0, 0, 0), FreeCAD.Rotation())
    
    # Style the silhouette
    if hasattr(silhouette_obj, "ViewObject"):
        silhouette_obj.ViewObject.ShapeColor = (0.2, 0.6, 1.0)  # Light blue
        silhouette_obj.ViewObject.Transparency = 50
        silhouette_obj.ViewObject.LineWidth = 2.0
    
    container.addObject(silhouette_obj)
    
    FreeCAD.Console.PrintMessage(f"Created silhouette container '{container_name}'\n")
    
    return container, silhouette_obj


def is_layout_group(obj):
    """
    Check if an object is a Layout group.
    
    Args:
        obj: FreeCAD object to check
        
    Returns:
        bool: True if it's a Layout group
    """
    return (obj.isDerivedFrom("App::DocumentObjectGroup") and 
            obj.Label.startswith("Layout_"))


def get_parts_from_layout_by_sheet(layout_group):
    """
    Extracts all placed part objects from a Layout group, organized by sheet.
    
    Traverses the Layout structure:
    Layout_XXX → Sheet_X groups → Shapes_X subgroups → App::Part containers → Part::Feature objects
    
    Args:
        layout_group: A FreeCAD DocumentObjectGroup that is a Layout
        
    Returns:
        dict: {sheet_group: [(part_object, container_placement, container_label), ...]}
    """
    sheets_data = {}
    
    FreeCAD.Console.PrintMessage(f"[Silhouette] Scanning Layout '{layout_group.Label}'...\n")
    
    for sheet_group in layout_group.Group:
        # Sheet groups are named Sheet_0, Sheet_1, etc.
        if not sheet_group.isDerivedFrom("App::DocumentObjectGroup"):
            continue
            
        if not sheet_group.Label.startswith("Sheet_"):
            continue
            
        FreeCAD.Console.PrintMessage(f"[Silhouette]   Found sheet: {sheet_group.Label}\n")
        
        sheet_parts = []
        
        for sub_group in sheet_group.Group:
            # Look for Shapes_X groups containing the placed parts
            if not sub_group.isDerivedFrom("App::DocumentObjectGroup"):
                continue
                
            if sub_group.Label.startswith("Shapes_"):
                FreeCAD.Console.PrintMessage(f"[Silhouette]     Found shapes group: {sub_group.Label}\n")
                
                for container in sub_group.Group:
                    # Parts are inside App::Part containers (like nested_Side_1)
                    if container.TypeId == "App::Part":
                        FreeCAD.Console.PrintMessage(f"[Silhouette]       Found container: {container.Label}\n")
                        
                        # Get the container's placement - this is where the part is positioned
                        container_placement = container.Placement if hasattr(container, "Placement") else FreeCAD.Placement()
                        
                        # Look inside the container for the actual part object
                        # We only want "part_*" objects, not boundary or label objects
                        if hasattr(container, "Group"):
                            for child in container.Group:
                                # Only process objects that start with "part_"
                                if child.Label.startswith("part_"):
                                    is_valid, reason = is_valid_shape_object(child)
                                    if is_valid:
                                        # Store both the part and its container's placement
                                        sheet_parts.append((child, container_placement, container.Label))
                                        FreeCAD.Console.PrintMessage(f"[Silhouette]         Found part: {child.Label}\n")
                    else:
                        # Try as direct Part::Feature (fallback)
                        is_valid, reason = is_valid_shape_object(container)
                        if is_valid:
                            container_placement = container.Placement if hasattr(container, "Placement") else FreeCAD.Placement()
                            sheet_parts.append((container, container_placement, container.Label))
                            FreeCAD.Console.PrintMessage(f"[Silhouette]       Found part: {container.Label}\n")
                        else:
                            FreeCAD.Console.PrintMessage(f"[Silhouette]       Skipping: {container.Label} ({reason})\n")
        
        if sheet_parts:
            sheets_data[sheet_group] = sheet_parts
            FreeCAD.Console.PrintMessage(f"[Silhouette]   Sheet '{sheet_group.Label}': {len(sheet_parts)} parts\n")
    
    total_parts = sum(len(parts) for parts in sheets_data.values())
    FreeCAD.Console.PrintMessage(f"[Silhouette] Found {total_parts} parts across {len(sheets_data)} sheets\n")
    
    return sheets_data


def create_silhouettes_for_layout(doc, layout_group, cut_height=None, method="cross_section"):
    """
    Creates silhouettes for all placed parts in a Layout group.
    
    Silhouettes are placed INSIDE each nested container (App::Part) alongside the part.
    
    Args:
        doc: The FreeCAD document
        layout_group: The Layout DocumentObjectGroup
        cut_height: For cross-section method, Z height to cut at. None = midpoint of each part.
        method: "cross_section" (default) or "projection"
        
    Returns:
        list: All created silhouette objects
    """
    all_silhouettes = []
    sheets_processed = set()
    
    FreeCAD.Console.PrintMessage(f"[Silhouette] Processing Layout '{layout_group.Label}'...\n")
    
    # Traverse layout → sheets → shapes groups → containers
    for sheet_group in layout_group.Group:
        if not sheet_group.isDerivedFrom("App::DocumentObjectGroup"):
            continue
        if not sheet_group.Label.startswith("Sheet_"):
            continue
        
        sheets_processed.add(sheet_group.Label)
        
        for sub_group in sheet_group.Group:
            if not sub_group.isDerivedFrom("App::DocumentObjectGroup"):
                continue
            if not sub_group.Label.startswith("Shapes_"):
                continue
            
            for container in sub_group.Group:
                # Only process App::Part containers (like nested_Side_1)
                if container.TypeId != "App::Part":
                    continue
                if not container.Label.startswith("nested_"):
                    continue
                
                # Find the part_* object inside the container
                part_obj = None
                if hasattr(container, "Group"):
                    for child in container.Group:
                        if child.Label.startswith("part_"):
                            is_valid, reason = is_valid_shape_object(child)
                            if is_valid:
                                part_obj = child
                                break
                
                if part_obj is None:
                    continue
                
                try:
                    # Create silhouette
                    if method == "cross_section":
                        silhouette_face = create_cross_section(part_obj, cut_height)
                    else:
                        silhouette_face = create_silhouette(part_obj)
                    
                    if silhouette_face is None:
                        FreeCAD.Console.PrintWarning(f"[Silhouette] Could not create silhouette for '{container.Label}'\n")
                        continue
                    
                    # Create silhouette object INSIDE the container
                    silhouette_obj = doc.addObject("Part::Feature", f"outline_{container.Label}")
                    silhouette_obj.Shape = silhouette_face
                    
                    # Position at Z=0 with no offset (cross-section already translated)
                    silhouette_obj.Placement = FreeCAD.Placement()
                    
                    # Style the silhouette
                    if hasattr(silhouette_obj, "ViewObject"):
                        silhouette_obj.ViewObject.ShapeColor = (0.2, 0.6, 1.0)  # Light blue
                        silhouette_obj.ViewObject.Transparency = 50
                        silhouette_obj.ViewObject.LineWidth = 2.0
                    
                    # Add to the container (alongside the part)
                    container.addObject(silhouette_obj)
                    all_silhouettes.append(silhouette_obj)
                    
                except Exception as e:
                    FreeCAD.Console.PrintError(f"[Silhouette] Error for '{container.Label}': {e}\n")
                    continue
    
    FreeCAD.Console.PrintMessage(f"[Silhouette] Created {len(all_silhouettes)} silhouettes across {len(sheets_processed)} sheets\n")
    
    return all_silhouettes


def is_nested_container(obj):
    """
    Check if an object is a nested part container (App::Part like nested_Side_1).
    
    Args:
        obj: FreeCAD object to check
        
    Returns:
        bool: True if it's a nested container
    """
    if obj.TypeId != "App::Part":
        return False
    # Nested containers from nesting start with "nested_"
    return obj.Label.startswith("nested_")


def create_silhouette_for_container(doc, container, cut_height=None, method="cross_section"):
    """
    Creates a silhouette for the part inside a nested container (App::Part).
    
    The silhouette is placed INSIDE the container alongside the part.
    
    Args:
        doc: FreeCAD document
        container: App::Part container (like nested_Side_1)
        cut_height: Z height to cut at (None = midpoint)
        method: "cross_section" or "projection"
        
    Returns:
        Part::Feature: The created silhouette object, or None on failure
    """
    if not is_nested_container(container):
        FreeCAD.Console.PrintWarning(f"[Silhouette] '{container.Label}' is not a nested container\n")
        return None
    
    # Find the part_* object inside the container
    part_obj = None
    if hasattr(container, "Group"):
        for child in container.Group:
            if child.Label.startswith("part_"):
                is_valid, reason = is_valid_shape_object(child)
                if is_valid:
                    part_obj = child
                    break
    
    if part_obj is None:
        FreeCAD.Console.PrintWarning(f"[Silhouette] No part object found in '{container.Label}'\n")
        return None
    
    # Create the silhouette
    if method == "cross_section":
        silhouette_face = create_cross_section(part_obj, cut_height)
    else:
        silhouette_face = create_silhouette(part_obj)
    
    if silhouette_face is None:
        FreeCAD.Console.PrintWarning(f"[Silhouette] Could not create silhouette for '{container.Label}'\n")
        return None
    
    # Create silhouette object inside the container
    silhouette_obj = doc.addObject("Part::Feature", f"outline_{container.Label}")
    silhouette_obj.Shape = silhouette_face
    
    # Position at Z=0, no additional offset needed (cross-section is already translated)
    silhouette_obj.Placement = FreeCAD.Placement()
    
    # Style the silhouette
    if hasattr(silhouette_obj, "ViewObject"):
        silhouette_obj.ViewObject.ShapeColor = (0.2, 0.6, 1.0)  # Light blue
        silhouette_obj.ViewObject.Transparency = 50
        silhouette_obj.ViewObject.LineWidth = 2.0
    
    # Add to the container
    container.addObject(silhouette_obj)
    
    return silhouette_obj


def create_silhouette_for_part(doc, part_obj, parent_container=None, cut_height=None, method="cross_section"):
    """
    Creates a silhouette for an individual part.
    
    If parent_container is provided, the silhouette is added there.
    Otherwise it's placed at document root level.
    
    Args:
        doc: FreeCAD document
        part_obj: Part::Feature object
        parent_container: Optional container to add the silhouette to
        cut_height: Z height to cut at (None = midpoint)
        method: "cross_section" or "projection"
        
    Returns:
        Part::Feature: The created silhouette object, or None on failure
    """
    is_valid, reason = is_valid_shape_object(part_obj)
    if not is_valid:
        FreeCAD.Console.PrintWarning(f"[Silhouette] '{part_obj.Label}' is not valid: {reason}\n")
        return None
    
    # Create the silhouette
    if method == "cross_section":
        silhouette_face = create_cross_section(part_obj, cut_height)
    else:
        silhouette_face = create_silhouette(part_obj)
    
    if silhouette_face is None:
        FreeCAD.Console.PrintWarning(f"[Silhouette] Could not create silhouette for '{part_obj.Label}'\n")
        return None
    
    # Create silhouette object
    silhouette_obj = doc.addObject("Part::Feature", f"outline_{part_obj.Label}")
    silhouette_obj.Shape = silhouette_face
    silhouette_obj.Placement = FreeCAD.Placement()
    
    # Style the silhouette
    if hasattr(silhouette_obj, "ViewObject"):
        silhouette_obj.ViewObject.ShapeColor = (0.2, 0.6, 1.0)  # Light blue
        silhouette_obj.ViewObject.Transparency = 50
        silhouette_obj.ViewObject.LineWidth = 2.0
    
    # Add to container if provided
    if parent_container and hasattr(parent_container, "addObject"):
        parent_container.addObject(silhouette_obj)
    
    return silhouette_obj


# Keep backward compatibility - old function name
def get_parts_from_layout(layout_group):
    """Backward compatible wrapper - returns flat list of parts."""
    sheets_data = get_parts_from_layout_by_sheet(layout_group)
    parts = []
    for sheet_parts in sheets_data.values():
        parts.extend(sheet_parts)
    return parts



