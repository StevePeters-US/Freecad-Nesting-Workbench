import FreeCAD

def calculate_label_placement(shapestring_center, container_rotation, label_z_offset=0.1):
    """
    Calculates the local placement for a label centered on a part.
    
    Args:
        shapestring_center (FreeCAD.Vector): The center of the label's bound box.
        container_rotation (FreeCAD.Rotation): The rotation of the parent container.
        label_z_offset (float): Vertical offset above the part.
        
    Returns:
        FreeCAD.Placement: The local placement for the label.
    """
    inverse_rotation = container_rotation.inverted()
    target_label_center = FreeCAD.Vector(0, 0, label_z_offset)
    shapestring_center_rotated = inverse_rotation.multVec(shapestring_center)
    label_placement_base = target_label_center - shapestring_center_rotated
    return FreeCAD.Placement(label_placement_base, inverse_rotation)

def calculate_container_centroid(polygon, sheet_origin):
    """
    Calculates the target world position for a container based on the polygon's centroid.
    
    Args:
        polygon (shapely.geometry.Polygon): The nesting boundary polygon.
        sheet_origin (FreeCAD.Vector): The origin of the sheet.
        
    Returns:
        FreeCAD.Vector: The target world position.
    """
    nested_centroid_shapely = polygon.centroid
    nested_centroid = FreeCAD.Vector(nested_centroid_shapely.x, nested_centroid_shapely.y, 0)
    return sheet_origin + nested_centroid
