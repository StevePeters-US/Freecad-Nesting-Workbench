# Nesting/nesting/ui_nesting.py

"""
This module contains the NestingPanel class, which defines the user interface
for the main nesting task panel.
"""

from PySide import QtGui, QtCore
import FreeCAD
import FreeCADGui
import os

class NestingPanel(QtGui.QWidget):
    """
    Defines the user interface for the main nesting task panel, including
    all input fields, buttons, and the table of shapes.
    """
    def __init__(self, parent=None):
        super(NestingPanel, self).__init__(parent)
        FreeCAD.Console.PrintMessage("NestingPanel initialized.\n")
        self.setWindowTitle("Nesting Tool")
        self.selected_shapes_to_process = []
        self.hidden_originals = []
        self.current_layout = None
        self.selected_font_path = ""
        self.initUI()
        self.load_selection()
        self.set_default_font()
    
    def accept(self):
        """Called when the user clicks Standard Button OK / Apply."""
        if hasattr(self, 'controller'):
            self.controller.finalize_job()
        return True

    def reject(self):
        """Called when the user clicks Standard Button Cancel / Close."""
        if hasattr(self, 'controller'):
            self.controller.cancel_job()
            
        # Also ensure visibility is restored if controller didn't fully run
        for obj in self.hidden_originals:
             if hasattr(obj, "ViewObject"):
                 obj.ViewObject.Visibility = True
                 
        return True

    def initUI(self):
        main_layout = QtGui.QVBoxLayout()
        form_layout = QtGui.QFormLayout()
        font_layout = QtGui.QHBoxLayout()
        table_button_layout = QtGui.QHBoxLayout()
        action_button_layout = QtGui.QHBoxLayout()

        self.sheet_width_input = QtGui.QDoubleSpinBox(); self.sheet_width_input.setRange(1, 10000); self.sheet_width_input.setValue(600)
        self.sheet_height_input = QtGui.QDoubleSpinBox(); self.sheet_height_input.setRange(1, 10000); self.sheet_height_input.setValue(600)
        self.part_spacing_input = QtGui.QDoubleSpinBox(); self.part_spacing_input.setRange(0, 1000); self.part_spacing_input.setValue(12.5)
        
        # --- Advanced Boundary Settings ---
        self.deflection_input = QtGui.QDoubleSpinBox(); self.deflection_input.setRange(0.001, 1.0); self.deflection_input.setValue(0.05); self.deflection_input.setSingleStep(0.01); self.deflection_input.setDecimals(3)
        self.deflection_input.setToolTip("Maximum deviation from original curve (mm). Lower = Smoother used for boundary creation.")
        
        self.simplification_input = QtGui.QDoubleSpinBox(); self.simplification_input.setRange(0.001, 1.0); self.simplification_input.setValue(0.1); self.simplification_input.setSingleStep(0.01); self.simplification_input.setDecimals(3)
        self.simplification_input.setToolTip("Tolerance for removing redundant points (mm). Higher = Fewer points used for optimization.")

        self.info_button = QtGui.QPushButton("Make default (20)")
        self.info_button.setToolTip("Show info about Deflection and Simplification")
        self.info_button.clicked.connect(self.show_boundary_info)

        # Load persisted settings immediately
        self.load_persisted_settings()


        self.shape_table = QtGui.QTableWidget()
        self.shape_table.setColumnCount(6)
        self.shape_table.setHorizontalHeaderLabels(["Shape", "Quantity", "Rotations", "Override", "Up Dir", "Fill"])

        # --- Global Rotation Slider ---
        self.rotation_steps_slider = QtGui.QSlider(QtCore.Qt.Horizontal)
        self.rotation_steps_slider.setRange(1, 360) # Minimum 1 rotation step
        self.rotation_steps_slider.setValue(1)     # Default to 1 rotation step
        self.rotation_steps_spinbox = QtGui.QSpinBox()
        self.rotation_steps_spinbox.setRange(1, 360) # Minimum 1 rotation step
        self.rotation_steps_spinbox.setValue(1)     # Default to 1 rotation step
        self.rotation_steps_spinbox.valueChanged.connect(self.rotation_steps_slider.setValue)
        self.rotation_steps_slider.valueChanged.connect(self.rotation_steps_spinbox.setValue)


        # --- Minkowski Packer Settings ---
        self.minkowski_settings_group = QtGui.QGroupBox("Minkowski Nester Settings")
        minkowski_form_layout = QtGui.QFormLayout()

        # Direction Dial for Minkowski
        self.minkowski_direction_dial = QtGui.QDial()
        self.minkowski_direction_dial.setRange(0, 359)
        self.minkowski_direction_dial.setValue(0) # Default to Down
        self.minkowski_direction_dial.setWrapping(True)
        self.minkowski_direction_dial.setNotchesVisible(True)
        self.minkowski_direction_label = QtGui.QLabel("Down")
        self.minkowski_direction_label.setAlignment(QtCore.Qt.AlignCenter)
        
        def update_minkowski_dial_label(value):
            direction_map = {0: "Down", 90: "Left", 180: "Up", 270: "Right"}
            direction_text = direction_map.get(value, "")
            self.minkowski_direction_label.setText(direction_text if direction_text else f"{value}°")
        self.minkowski_direction_dial.valueChanged.connect(update_minkowski_dial_label)

        minkowski_dial_layout = QtGui.QVBoxLayout()
        minkowski_dial_layout.addWidget(self.minkowski_direction_dial)
        minkowski_dial_layout.addWidget(self.minkowski_direction_label)

        # Random Direction Checkbox for Minkowski
        self.minkowski_random_checkbox = QtGui.QCheckBox("Use Random Strategy")
        self.minkowski_random_checkbox.setToolTip("If checked, each part will use a randomized placement weighting.")
        self.minkowski_random_checkbox.stateChanged.connect(lambda state: self.minkowski_direction_dial.setDisabled(state))

        minkowski_form_layout.addRow("Packing Direction:", minkowski_dial_layout)
        minkowski_form_layout.addRow(self.minkowski_random_checkbox)
        
        self.clear_cache_checkbox = QtGui.QCheckBox("Clear NFP Cache")
        self.clear_cache_checkbox.setChecked(False)
        self.clear_cache_checkbox.setToolTip("Forces recalculation of No-Fit Polygons. Slower, but resolves potential caching issues.")
        minkowski_form_layout.addRow(self.clear_cache_checkbox)
        
        # Genetic options for Minkowski
        self.minkowski_population_size_input = QtGui.QSpinBox()
        self.minkowski_population_size_input.setRange(1, 500)
        self.minkowski_population_size_input.setValue(1)
        self.minkowski_population_size_input.setToolTip("Set to 1 for a single pass. Increase with generations for Genetic Algorithm.")
        
        self.minkowski_generations_input = QtGui.QSpinBox()
        self.minkowski_generations_input.setRange(1, 1000)
        self.minkowski_generations_input.setValue(1) # Default to 1 (No Genetic Loop)
        self.minkowski_generations_input.setToolTip("Set to 1 for a single pass. Increase to optimize using Genetic Algorithm.")

        minkowski_form_layout.addRow(QtGui.QLabel("")) # Spacer
        minkowski_form_layout.addRow(QtGui.QLabel("--- Optimization ---"))
        minkowski_form_layout.addRow("Generations:", self.minkowski_generations_input)
        minkowski_form_layout.addRow("Population Size:", self.minkowski_population_size_input)
        
        self.minkowski_settings_group.setLayout(minkowski_form_layout)





        self.show_bounds_checkbox = QtGui.QCheckBox("Show Bounds"); self.show_bounds_checkbox.setChecked(True)
        self.add_labels_checkbox = QtGui.QCheckBox("Add Identifier Labels"); self.add_labels_checkbox.setChecked(True)
        self.label_height_input = QtGui.QDoubleSpinBox(); self.label_height_input.setRange(0, 1000); self.label_height_input.setValue(25.0)
        self.label_height_input.setToolTip("The height (Z-offset) for the identifier labels.")
        self.simulate_nesting_checkbox = QtGui.QCheckBox("Simulate Nesting (slower)"); self.simulate_nesting_checkbox.setChecked(True)
        self.sound_checkbox = QtGui.QCheckBox("Play sound on completion"); self.sound_checkbox.setChecked(True)
        
        self.nest_button = QtGui.QPushButton("Run Nesting")

        # --- Add/Remove buttons for the shape table ---
        self.add_parts_button = QtGui.QPushButton("Add Selected")
        self.remove_parts_button = QtGui.QPushButton("Remove Selected")
        
        # --- Font Selection UI Elements ---
        self.font_select_button = QtGui.QPushButton("Select Font")
        self.font_label = QtGui.QLabel("No Font Selected")
        self.font_label.setWordWrap(True)
        font_layout.addWidget(self.font_select_button)
        font_layout.addWidget(self.font_label)
        
        self.status_label = QtGui.QLabel("Select master shapes to nest.")
        self.status_label.setWordWrap(True)

        # --- Layout Assembly ---
        label_options_layout = QtGui.QHBoxLayout()
        label_options_layout.addWidget(self.add_labels_checkbox)
        label_options_layout.addWidget(QtGui.QLabel("Height (Z):"))
        label_options_layout.addWidget(self.label_height_input)
        label_options_layout.addStretch()


        form_layout.addRow("Sheet Width:", self.sheet_width_input)
        form_layout.addRow("Sheet Height:", self.sheet_height_input)
        form_layout.addRow("Part Spacing:", self.part_spacing_input)
        
        # Advanced Curve Settings
        curve_settings_layout = QtGui.QHBoxLayout()
        curve_settings_layout.addWidget(QtGui.QLabel("Deflect:"))
        curve_settings_layout.addWidget(self.deflection_input)
        curve_settings_layout.addWidget(QtGui.QLabel("Simplify:"))
        curve_settings_layout.addWidget(self.simplification_input)
        curve_settings_layout.addWidget(self.info_button)
        
        form_layout.addRow("Curve Quality:", curve_settings_layout)


        form_layout.addRow(self.minkowski_settings_group)

        form_layout.addRow("Identifier Font:", font_layout)
        form_layout.addRow(label_options_layout)
        
        rotation_layout = QtGui.QHBoxLayout()
        rotation_layout.addWidget(self.rotation_steps_slider)
        rotation_layout.addWidget(self.rotation_steps_spinbox)
        form_layout.addRow("Global Rotation Steps:", rotation_layout)

        form_layout.addRow(self.simulate_nesting_checkbox)
        form_layout.addRow(self.show_bounds_checkbox) # Keep this on its own line
        form_layout.addRow(self.sound_checkbox)
        
        table_button_layout.addWidget(self.add_parts_button)
        table_button_layout.addWidget(self.remove_parts_button)

        action_button_layout.addWidget(self.nest_button)

        main_layout.addLayout(form_layout)
        main_layout.addWidget(self.shape_table)
        main_layout.addLayout(table_button_layout)
        main_layout.addLayout(action_button_layout)
        main_layout.addWidget(self.status_label)
        main_layout.addStretch()
        
        self.setLayout(main_layout)

        # Connect signals


        # Link label height input to the add labels checkbox
        self.add_labels_checkbox.stateChanged.connect(self.label_height_input.setEnabled)
        self.label_height_input.setEnabled(self.add_labels_checkbox.isChecked())

        # Connect the nesting controller
        from .nesting_controller import NestingController
        self.controller = NestingController(self)
        self.nest_button.clicked.connect(self.controller.execute_nesting)
        self.font_select_button.clicked.connect(self.select_font_file)
        self.show_bounds_checkbox.stateChanged.connect(self.controller.toggle_bounds_visibility)
        self.add_parts_button.clicked.connect(self.add_selected_shapes)
        self.remove_parts_button.clicked.connect(self.remove_selected_shapes)




    def load_selection(self):
        FreeCAD.Console.PrintMessage("Loading selection into Nesting Panel...\n")
        selection = FreeCADGui.Selection.getSelection()
        self.shape_table.setRowCount(0)

        if not selection:
            FreeCAD.Console.PrintMessage("  -> No selection found.\n")
            self.status_label.setText("Warning: No shapes selected.")
            self.nest_button.setEnabled(False)
            return

        # Check if a layout group is selected
        first_selected = selection[0]
        if first_selected.isDerivedFrom("App::DocumentObjectGroup") and first_selected.Label.startswith("Layout_"):
            FreeCAD.Console.PrintMessage(f"  -> Detected layout selection: {first_selected.Label}\n")
            self.load_layout(first_selected)
        else:
            FreeCAD.Console.PrintMessage(f"  -> Detected {len(selection)} shapes.\n")
            self.load_shapes(selection)

    def load_layout(self, layout_group):
        """Loads the parameters and shapes from a layout group."""
        FreeCAD.Console.PrintMessage(f"\n=== DEBUG: load_layout({layout_group.Label}) ===\n")
        self.current_layout = layout_group
        self.nest_button.setEnabled(True)
        self.selected_shapes_to_process = []
        self.hidden_originals = []

        # Read parameters directly from the layout group's properties
        if hasattr(layout_group, 'SheetWidth'):
            self.sheet_width_input.setValue(layout_group.SheetWidth)
        if hasattr(layout_group, 'SheetHeight'):
            self.sheet_height_input.setValue(layout_group.SheetHeight)
        if hasattr(layout_group, 'PartSpacing'):
            self.part_spacing_input.setValue(layout_group.PartSpacing)
        if hasattr(layout_group, 'Deflection'):
            self.deflection_input.setValue(layout_group.Deflection)
        if hasattr(layout_group, 'Simplification'):
            self.simplification_input.setValue(layout_group.Simplification)
        if hasattr(layout_group, 'FontFile') and os.path.exists(layout_group.FontFile):
            self.selected_font_path = layout_group.FontFile
            self.font_label.setText(os.path.basename(layout_group.FontFile))
        if hasattr(layout_group, 'Generations'):
            self.minkowski_generations_input.setValue(layout_group.Generations)
        if hasattr(layout_group, 'PopulationSize'):
            self.minkowski_population_size_input.setValue(layout_group.PopulationSize)

        # Get the shapes from the layout
        master_shapes_group = None
        FreeCAD.Console.PrintMessage(f"  Layout children: {[c.Label for c in layout_group.Group]}\n")
        for child in layout_group.Group:
            if child.Label.startswith("MasterShapes"):
                master_shapes_group = child
                break
        
        if master_shapes_group:
            FreeCAD.Console.PrintMessage(f"  Found MasterShapes: {master_shapes_group.Label}\n")
            FreeCAD.Console.PrintMessage(f"  MasterShapes children: {[c.Label for c in master_shapes_group.Group]}\n")
            # The master shapes are now copies inside this group.
            # We need to find the actual ShapeObject inside each 'master_' container,
            # as that is what the processing logic expects.
            shapes_to_load = []
            quantities = {}
            rotation_overrides = {}
            rotation_steps_map = {}
            up_directions = {}
            fill_sheet_map = {}
            
            for master_container in master_shapes_group.Group:
                FreeCAD.Console.PrintMessage(f"    Checking container: {master_container.Label}\n")
                # Use a relaxed check for the container to ensure robust loading.
                # While we enforce "master_" naming on write, reading should be tolerant
                # to handle potential legacy or manually modified files.
                if hasattr(master_container, "Group"):
                    container_children = [c.Label for c in master_container.Group]
                    FreeCAD.Console.PrintMessage(f"      Container children: {container_children}\n")
                    # The object to load is the 'master_shape_...' object inside the container.
                    shape_obj = next((child for child in master_container.Group if child.Label.startswith("master_shape_")), None)
                    # Note: We no longer require Proxy, as it may be lost during copyObject.
                    # The shape geometry is what we need.
                    if shape_obj and hasattr(shape_obj, "Shape"):
                        FreeCAD.Console.PrintMessage(f"      Found shape: {shape_obj.Label}\n")
                        shapes_to_load.append(shape_obj)
                        
                        # Recover properties from container
                        quantities[shape_obj.Label] = getattr(master_container, "Quantity", 1)
                        
                        if hasattr(master_container, "PartRotationOverride"):
                            rotation_overrides[shape_obj.Label] = master_container.PartRotationOverride
                        if hasattr(master_container, "PartRotationSteps"):
                            rotation_steps_map[shape_obj.Label] = master_container.PartRotationSteps
                        if hasattr(master_container, "UpDirection"):
                            up_directions[shape_obj.Label] = master_container.UpDirection
                        if hasattr(master_container, "FillSheet"):
                            fill_sheet_map[shape_obj.Label] = master_container.FillSheet
                    else:
                        FreeCAD.Console.PrintMessage(f"      No master_shape_ found in container!\n")
                else:
                    FreeCAD.Console.PrintMessage(f"      Container has no Group attribute\n")
            
            FreeCAD.Console.PrintMessage(f"  Total shapes to load: {len(shapes_to_load)}\n")
            
            self.load_shapes(
                shapes_to_load, 
                is_reloading_layout=True, 
                initial_quantities=quantities,
                initial_overrides=rotation_overrides,
                initial_rotation_steps=rotation_steps_map,
                initial_up_directions=up_directions,
                initial_fill_sheet=fill_sheet_map
            )
            
            # Load Global Rotation Steps if present
            if hasattr(layout_group, "GlobalRotationSteps"):
                self.rotation_steps_spinbox.setValue(layout_group.GlobalRotationSteps)
        else:
            FreeCAD.Console.PrintMessage(f"  WARNING: No MasterShapes group found!\n")
            self.status_label.setText("Warning: Could not find 'MasterShapes' group in the selected layout.")

    def _extract_parts_from_selection(self, selection):
        """
        Extracts parts from Assembly containers only.
        Regular Part objects are used directly without extracting children.
        """
        parts = []
        
        def is_assembly(obj):
            """Check if object is an Assembly container (not a regular Part/Body)."""
            # Check for common assembly types
            type_id = obj.TypeId if hasattr(obj, 'TypeId') else ''
            if 'Assembly' in type_id:
                return True
            # Also check for App::Part that's used as an assembly (has Link children)
            if type_id == 'App::Part' and hasattr(obj, 'Group'):
                # If it contains links or other assembly-like parts, treat as assembly
                for child in obj.Group:
                    child_type = child.TypeId if hasattr(child, 'TypeId') else ''
                    if 'Link' in child_type or 'Assembly' in child_type:
                        return True
            return False
        
        def extract_from_assembly(obj):
            """Recursively extract nestable parts from an assembly."""
            if hasattr(obj, 'Group'):
                for child in obj.Group:
                    # Skip constraints, origins, etc.
                    child_type = child.TypeId if hasattr(child, 'TypeId') else ''
                    if 'Constraint' in child_type or 'Origin' in child_type:
                        continue
                    # If child is a link, get the linked object
                    if hasattr(child, 'LinkedObject') and child.LinkedObject:
                        linked = child.LinkedObject
                        if hasattr(linked, 'Shape') and linked.Shape and not linked.Shape.isNull():
                            parts.append(linked)
                    elif hasattr(child, 'Shape') and child.Shape and not child.Shape.isNull():
                        # Check if child is also an assembly
                        if is_assembly(child):
                            extract_from_assembly(child)
                        else:
                            parts.append(child)
        
        for obj in selection:
            if is_assembly(obj):
                # Extract parts from assembly
                extract_from_assembly(obj)
            else:
                # Use regular part directly
                parts.append(obj)
        
        return parts

    def load_shapes(self, selection, is_reloading_layout=False, initial_quantities=None, 
                     initial_overrides=None, initial_rotation_steps=None,
                     initial_up_directions=None, initial_fill_sheet=None):
        """Loads a selection of shapes into the UI."""
        self.nest_button.setEnabled(True)
        
        # Extract individual parts from assemblies/groups
        if not is_reloading_layout:
            extracted = self._extract_parts_from_selection(selection)
            if extracted:
                selection = extracted
                FreeCAD.Console.PrintMessage(f"  -> Extracted {len(selection)} parts from selection.\n")
        
        self.selected_shapes_to_process = list(dict.fromkeys(selection)) # Keep unique, preserve order
        if not is_reloading_layout:
            self.current_layout = None
            self.hidden_originals = list(self.selected_shapes_to_process)
        
        self.shape_table.setRowCount(len(self.selected_shapes_to_process))
        for i, obj in enumerate(self.selected_shapes_to_process):
            # Clean up label if it's a master shape
            display_label = obj.Label
            if display_label.startswith("master_shape_"):
                display_label = display_label.replace("master_shape_", "")
            
            qty = 1
            if initial_quantities and obj.Label in initial_quantities:
                qty = initial_quantities[obj.Label]
                
            steps = 4
            override = False
            if initial_rotation_steps and obj.Label in initial_rotation_steps:
                steps = initial_rotation_steps[obj.Label]
            if initial_overrides and obj.Label in initial_overrides:
                override = initial_overrides[obj.Label]
            
            up_dir = "Z+"
            if initial_up_directions and obj.Label in initial_up_directions:
                up_dir = initial_up_directions[obj.Label]
                
            fill = False
            if initial_fill_sheet and obj.Label in initial_fill_sheet:
                fill = initial_fill_sheet[obj.Label]

            self._add_part_row(i, display_label, quantity=qty, rotation_steps=steps, 
                              override_rotation=override, up_direction=up_dir, fill_sheet=fill)
        
        self.shape_table.resizeColumnsToContents()
        self.status_label.setText(f"{len(selection)} unique object(s) selected. Specify quantities and nest.")

    def add_selected_shapes(self):
        """Adds the currently selected FreeCAD objects to the shape table if they aren't already present."""
        selection = FreeCADGui.Selection.getSelection()
        if not selection:
            self.status_label.setText("Select shapes in the 3D view or tree to add them.")
            return

        existing_labels = [self.shape_table.item(row, 0).text() for row in range(self.shape_table.rowCount())]
        
        added_count = 0
        for obj in selection:
            if obj.Label not in existing_labels:
                row_position = self.shape_table.rowCount()
                self.shape_table.insertRow(row_position)
                self._add_part_row(row_position, obj.Label)
                self.selected_shapes_to_process.append(obj)
                added_count += 1
        
        self.shape_table.resizeColumnsToContents()
        self.status_label.setText(f"Added {added_count} new shape(s).")

    def _add_part_row(self, row_index, label, quantity=1, rotation_steps=4, override_rotation=False, 
                       up_direction="Z+", fill_sheet=False):
        """Helper function to create and populate a single row in the parts table."""
        label_item = QtGui.QTableWidgetItem(label)
        label_item.setFlags(label_item.flags() & ~QtCore.Qt.ItemIsEditable)

        quantity_spinbox = QtGui.QSpinBox()
        quantity_spinbox.setRange(1, 500)
        quantity_spinbox.setValue(quantity)

        # --- Rotation Override Widget ---
        rotation_widget = QtGui.QWidget()
        rotation_layout = QtGui.QHBoxLayout(rotation_widget)
        rotation_layout.setContentsMargins(0, 0, 0, 0)
        
        rotation_slider = QtGui.QSlider(QtCore.Qt.Horizontal)
        rotation_slider.setRange(0, 360) # Allow 0 for no rotation
        rotation_slider.setValue(rotation_steps)
        
        rotation_spinbox = QtGui.QSpinBox()
        rotation_spinbox.setRange(0, 360) # Allow 0 for no rotation
        rotation_spinbox.setValue(rotation_steps)
        rotation_spinbox.setToolTip("Override global rotation steps for this part. 0 or 1 means no rotation.")

        rotation_slider.valueChanged.connect(rotation_spinbox.setValue)
        rotation_spinbox.valueChanged.connect(rotation_slider.setValue)
        
        rotation_layout.addWidget(rotation_slider)
        rotation_layout.addWidget(rotation_spinbox)

        override_checkbox = QtGui.QCheckBox()
        override_checkbox.setChecked(override_rotation)
        override_checkbox.stateChanged.connect(rotation_widget.setEnabled)
        rotation_widget.setEnabled(override_rotation) # Disabled by default unless overridden

        # --- Up Direction Combo ---
        up_dir_combo = QtGui.QComboBox()
        up_dir_combo.addItems(["Z+", "Z-", "Y+", "Y-", "X+", "X-"])
        up_dir_combo.setCurrentText(up_direction)
        up_dir_combo.setToolTip("Define which direction is 'up' for this part when projecting to 2D.")

        # --- Fill Sheet Checkbox ---
        fill_checkbox = QtGui.QCheckBox()
        fill_checkbox.setChecked(fill_sheet)
        fill_checkbox.setToolTip("If checked, this part will be used to fill remaining space after all other parts are placed.")

        self.shape_table.setItem(row_index, 0, label_item)
        self.shape_table.setCellWidget(row_index, 1, quantity_spinbox)
        self.shape_table.setCellWidget(row_index, 2, rotation_widget)
        self.shape_table.setCellWidget(row_index, 3, override_checkbox)
        self.shape_table.setCellWidget(row_index, 4, up_dir_combo)
        self.shape_table.setCellWidget(row_index, 5, fill_checkbox)

    def remove_selected_shapes(self):
        """Removes the selected rows from the shape table."""
        # Get all selected items and find their unique rows. This is more robust
        # than selectedRows() as it works even if only a single cell is selected.
        selected_items = self.shape_table.selectedItems()
        selected_rows = sorted(list(set(item.row() for item in selected_items)), reverse=True)
        for row in selected_rows:
            label_to_remove = self.shape_table.item(row, 0).text()
            self.selected_shapes_to_process = [obj for obj in self.selected_shapes_to_process if obj.Label != label_to_remove]
            self.shape_table.removeRow(row)
        self.status_label.setText(f"Removed {len(selected_rows)} shape(s).")

    def select_font_file(self):
        """Opens a file dialog to let the user select a font file."""
        # Correctly find the workbench's root directory and the 'fonts' subfolder
        try:
            # __file__ is the path to this file (ui_nesting.py)
            # os.path.dirname gives the directory it's in (.../nesting)
            # We need to go up three levels from .../Tools/Nesting/ to get to the workbench root.
            current_dir = os.path.dirname(os.path.abspath(__file__))
            workbench_root = os.path.abspath(os.path.join(current_dir, '..', '..', '..'))
            default_font_dir = os.path.join(workbench_root, "fonts")
            if not os.path.isdir(default_font_dir):
                default_font_dir = "" # Fallback if fonts dir doesn't exist
        except:
            default_font_dir = "" # Fallback on any error

        file_dialog_result = QtGui.QFileDialog.getOpenFileName(
            self, 
            "Select Font File", 
            default_font_dir, # Set the default directory
            "Font Files (*.ttf *.otf)"
        )
        font_path = file_dialog_result[0]

        if font_path:
            self.selected_font_path = font_path
            # Display just the filename for a cleaner UI
            self.font_label.setText(os.path.basename(font_path))

    def set_default_font(self):
        """Checks for and sets a default font on initialization."""
        try:
            workbench_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            fonts_dir = os.path.join(workbench_root, "fonts")
            default_font_file = "PoiretOne-Regular.ttf"
            default_font_path = os.path.join(fonts_dir, default_font_file)

            if os.path.exists(default_font_path):
                self.selected_font_path = default_font_path
                self.font_label.setText(default_font_file)
        except Exception:
            # Silently fail, no default will be set.
            pass

    def log_message(self, message, level="message"):
        """Displays a message in the status label and logs to the console."""
        try:
            self.status_label.setText(message)
        except RuntimeError:
            # The widget C++ object has been deleted (panel closed), but Python object persists.
            # We can just log to console and ignore the UI update.
            pass

        if level == "warning":
            FreeCAD.Console.PrintWarning(message + "\n")
        else:
            FreeCAD.Console.PrintMessage(message + "\n")
        
        # Process UI events to make sure the label updates immediately
        # We wrap this too, just in case
        try:
            QtGui.QApplication.processEvents()
        except RuntimeError:
            pass

    def load_persisted_settings(self):
        """Loads settings from FreeCAD preferences."""
        prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/NestingWorkbench")
        self.sheet_width_input.setValue(prefs.GetFloat("SheetWidth", 600.0))
        self.sheet_height_input.setValue(prefs.GetFloat("SheetHeight", 600.0))
        self.part_spacing_input.setValue(prefs.GetFloat("PartSpacing", 12.5))
        self.deflection_input.setValue(prefs.GetFloat("Deflection", 0.05))
        self.simplification_input.setValue(prefs.GetFloat("Simplification", 0.1))
        
    def show_boundary_info(self):
        msg = (
            "<b>Deflection (Curve Quality):</b><br>"
            "Controls how smoothly curves are approximated. It is the maximum distance (in mm) "
            "between the true curve and the straight line segment approximating it.<br>"
            "<i>Smaller value (e.g. 0.01) = Smoother curves, more points.</i><br>"
            "<i>Larger value (e.g. 0.1) = Coarser curves, fewer points.</i><br><br>"
            "<b>Simplification (Optimization):</b><br>"
            "Controls how aggressively redundant points are removed after initial creation.<br>"
            "<i>Should specificially correspond to your machine's tolerance.</i>"
        )
        QtGui.QMessageBox.information(self, "Boundary Settings Info", msg)