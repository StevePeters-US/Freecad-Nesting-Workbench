"""
Collision resolver for the manual nesting tool.
Handles clamping parts to sheet boundaries and basic overlap resolution.
"""

class CollisionResolver:
    def clamp_to_sheet(self, obj, sheet_bbox):
        """
        Adjusts obj.Placement.Base so obj's BoundBox stays within sheet_bbox.

        Args:
            obj: FreeCAD object with .Shape.BoundBox and .Placement
            sheet_bbox: FreeCAD.BoundBox of the sheet boundary

        Returns:
            True if position was clamped, False if already within bounds.
        """
        bb = obj.Shape.BoundBox
        # obj.Placement.Base is the local origin. 
        # The BoundBox (bb) is relative to the object's local origin.
        # So absolute min/max of the object is Placement.Base + bb.Min/Max.
        
        current_pos = obj.Placement.Base
        
        obj_min_x = current_pos.x + bb.XMin
        obj_max_x = current_pos.x + bb.XMax
        obj_min_y = current_pos.y + bb.YMin
        obj_max_y = current_pos.y + bb.YMax
        
        new_x = current_pos.x
        new_y = current_pos.y
        clamped = False
        
        # Check X boundaries
        if obj_min_x < sheet_bbox.XMin:
            new_x += (sheet_bbox.XMin - obj_min_x)
            clamped = True
        elif obj_max_x > sheet_bbox.XMax:
            new_x -= (obj_max_x - sheet_bbox.XMax)
            clamped = True
            
        # Check Y boundaries
        if obj_min_y < sheet_bbox.YMin:
            new_y += (sheet_bbox.YMin - obj_min_y)
            clamped = True
        elif obj_max_y > sheet_bbox.YMax:
            new_y -= (obj_max_y - sheet_bbox.YMax)
            clamped = True
            
        if clamped:
            # Recreate vector using the same type as current_pos to avoid FreeCAD dependency here
            obj.Placement.Base = type(current_pos)(new_x, new_y, current_pos.z)
            
        return clamped

    def separate_overlapping(self, moved_obj, other_objs, max_iterations=5):
        """
        Iteratively separates moved_obj from overlapping other_objs using
        BoundBox intersection checks and minimal displacement.

        Args:
            moved_obj: FreeCAD object that was just displaced
            other_objs: list of FreeCAD objects to check against
            max_iterations: retry count for cascading overlaps

        Returns:
            True if all overlaps resolved, False if some remain.
        """
        for _ in range(max_iterations):
            any_overlap = False
            moved_bb = self._get_abs_bbox(moved_obj)
            current_pos = moved_obj.Placement.Base
            
            for other in other_objs:
                if other == moved_obj:
                    continue
                
                other_bb = self._get_abs_bbox(other)
                
                if self._bboxes_intersect(moved_bb, other_bb):
                    any_overlap = True
                    # Calculate separation (XY only)
                    overlap_x = min(moved_bb['max_x'], other_bb['max_x']) - max(moved_bb['min_x'], other_bb['min_x'])
                    overlap_y = min(moved_bb['max_y'], other_bb['max_y']) - max(moved_bb['min_y'], other_bb['min_y'])
                    
                    # Add a tiny epsilon to ensure they actually separate
                    epsilon = 0.001
                    
                    new_x = current_pos.x
                    new_y = current_pos.y
                    
                    if overlap_x < overlap_y:
                        # Push along X
                        if moved_bb['center_x'] < other_bb['center_x']:
                            new_x -= (overlap_x + epsilon)
                        else:
                            new_x += (overlap_x + epsilon)
                    else:
                        # Push along Y
                        if moved_bb['center_y'] < other_bb['center_y']:
                            new_y -= (overlap_y + epsilon)
                        else:
                            new_y += (overlap_y + epsilon)
                    
                    # Update placement so next other_obj check uses new position
                    current_pos = type(current_pos)(new_x, new_y, current_pos.z)
                    moved_obj.Placement.Base = current_pos
                    moved_bb = self._get_abs_bbox(moved_obj)
            
            if not any_overlap:
                return True
                
        return False

    def _get_abs_bbox(self, obj):
        """Helper to get absolute bounding box as a dict."""
        bb = obj.Shape.BoundBox
        pos = obj.Placement.Base
        return {
            'min_x': pos.x + bb.XMin,
            'max_x': pos.x + bb.XMax,
            'min_y': pos.y + bb.YMin,
            'max_y': pos.y + bb.YMax,
            'center_x': pos.x + bb.XMin + bb.XLength / 2,
            'center_y': pos.y + bb.YMin + bb.YLength / 2
        }

    def _bboxes_intersect(self, bb1, bb2):
        """Check if two absolute bboxes (dicts) intersect."""
        return not (bb1['max_x'] < bb2['min_x'] or 
                   bb1['min_x'] > bb2['max_x'] or 
                   bb1['max_y'] < bb2['min_y'] or 
                   bb1['min_y'] > bb2['max_y'])
