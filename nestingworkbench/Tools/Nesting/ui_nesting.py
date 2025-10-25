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
        self.setWindowTitle("Nesting Tool")
        self.selected_shapes_to_process = []
        self.hidden_originals = []
        self.preview_group_name = "__temp_Layout"
        self.accepted = False
        self.selected_font_path = ""
        self.hidden_layouts = []
        self.initUI()
        self.load_selection()
        self.set_default_font()

    def initUI(self):
        main_layout = QtGui.QVBoxLayout()
        form_layout = QtGui.QFormLayout()
        font_layout = QtGui.QHBoxLayout()
        table_button_layout = QtGui.QHBoxLayout()
        action_button_layout = QtGui.QHBoxLayout()

        self.sheet_width_input = QtGui.QDoubleSpinBox(); self.sheet_width_input.setRange(1, 10000); self.sheet_width_input.setValue(600)
        self.sheet_height_input = QtGui.QDoubleSpinBox(); self.sheet_height_input.setRange(1, 10000); self.sheet_height_input.setValue(600)
        self.part_spacing_input = QtGui.QDoubleSpinBox(); self.part_spacing_input.setRange(0, 1000); self.part_spacing_input.setValue(12.5)
        self.boundary_resolution_input = QtGui.QSpinBox(); self.boundary_resolution_input.setRange(10, 500); self.boundary_resolution_input.setValue(75)
        self.boundary_resolution_input.setToolTip("Number of points per curve for boundary creation. Higher values are more accurate but slower.")
        
        self.shape_table = QtGui.QTableWidget()
        self.shape_table.setColumnCount(4)
        self.shape_table.setHorizontalHeaderLabels(["Shape", "Quantity", "Rotations", "Enable Override"])

        # --- Global Rotation Slider ---
        self.rotation_steps_slider = QtGui.QSlider(QtCore.Qt.Horizontal)
        self.rotation_steps_slider.setRange(0, 360)
        self.rotation_steps_slider.setValue(0)
        self.rotation_steps_spinbox = QtGui.QSpinBox()
        self.rotation_steps_spinbox.setRange(0, 360)
        self.rotation_steps_spinbox.setValue(0)
        self.rotation_steps_slider.valueChanged.connect(self.rotation_steps_spinbox.setValue)
        self.rotation_steps_spinbox.valueChanged.connect(self.rotation_steps_slider.setValue)

        self.algorithm_dropdown = QtGui.QComboBox()
        self.algorithm_dropdown.addItems(["Gravity", "Genetic", "Minkowski", "SAT"])
        self.algorithm_dropdown.setCurrentIndex(0)

        # --- Genetic Packer Settings ---
        self.genetic_settings_group = QtGui.QGroupBox("Genetic Packer Settings")
        genetic_form_layout = QtGui.QFormLayout()
        self.genetic_population_size_input = QtGui.QSpinBox()
        self.genetic_population_size_input.setRange(10, 500)
        self.genetic_population_size_input.setValue(20)
        self.genetic_generations_input = QtGui.QSpinBox()
        self.genetic_generations_input.setRange(1, 1000)
        self.genetic_generations_input.setValue(50)
        genetic_form_layout.addRow("Population Size:", self.genetic_population_size_input)
        genetic_form_layout.addRow("Generations:", self.genetic_generations_input)
        self.genetic_settings_group.setLayout(genetic_form_layout)


        # --- Gravity Packer Settings ---
        self.gravity_settings_group = QtGui.QGroupBox("Gravity Nester Settings")
        gravity_form_layout = QtGui.QFormLayout()

        # Direction Dial
        self.gravity_direction_dial = QtGui.QDial()
        self.gravity_direction_dial.setRange(0, 359)
        self.gravity_direction_dial.setValue(0) # Default to Down
        self.gravity_direction_dial.setWrapping(True)
        self.gravity_direction_dial.setNotchesVisible(True)
        self.gravity_direction_label = QtGui.QLabel("Down")
        self.gravity_direction_label.setAlignment(QtCore.Qt.AlignCenter)
        
        def update_dial_label(value):
            direction_map = {0: "Down", 90: "Left", 180: "Up", 270: "Right"}
            direction_text = direction_map.get(value, "")
            self.gravity_direction_label.setText(direction_text if direction_text else f"{value}°")
        self.gravity_direction_dial.valueChanged.connect(update_dial_label)

        dial_layout = QtGui.QVBoxLayout()
        dial_layout.addWidget(self.gravity_direction_dial)
        dial_layout.addWidget(self.gravity_direction_label)

        # Random Direction Checkbox
        self.gravity_random_checkbox = QtGui.QCheckBox("Use Random Direction")
        self.gravity_random_checkbox.stateChanged.connect(lambda state: self.gravity_direction_dial.setDisabled(state))

        self.gravity_step_size_input = QtGui.QDoubleSpinBox(); self.gravity_step_size_input.setRange(0.1, 100); self.gravity_step_size_input.setValue(5.0)
        self.gravity_max_spawn_input = QtGui.QSpinBox(); self.gravity_max_spawn_input.setRange(1, 1000); self.gravity_max_spawn_input.setValue(100)
        self.gravity_anneal_steps_input = QtGui.QSpinBox(); self.gravity_anneal_steps_input.setRange(0, 1000); self.gravity_anneal_steps_input.setValue(100)
        self.anneal_rotate_checkbox = QtGui.QCheckBox("Anneal Rotation"); self.anneal_rotate_checkbox.setChecked(True)
        self.anneal_translate_checkbox = QtGui.QCheckBox("Anneal Position"); self.anneal_translate_checkbox.setChecked(True)
        self.anneal_random_shake_checkbox = QtGui.QCheckBox("Random Anneal Direction"); self.anneal_random_shake_checkbox.setChecked(False)
        self.gravity_max_nesting_steps_input = QtGui.QSpinBox(); self.gravity_max_nesting_steps_input.setRange(1, 5000); self.gravity_max_nesting_steps_input.setValue(500)
        gravity_form_layout.addRow("Gravity Direction:", dial_layout)
        gravity_form_layout.addRow(self.gravity_random_checkbox)
        gravity_form_layout.addRow("Step Size:", self.gravity_step_size_input)
        gravity_form_layout.addRow("Max Spawn Tries:", self.gravity_max_spawn_input)
        gravity_form_layout.addRow("Anneal Steps on Collide:", self.gravity_anneal_steps_input)
        gravity_form_layout.addRow("Max Nesting Steps:", self.gravity_max_nesting_steps_input)
        gravity_form_layout.addRow(self.anneal_rotate_checkbox)
        gravity_form_layout.addRow(self.anneal_translate_checkbox)
        gravity_form_layout.addRow(self.anneal_random_shake_checkbox)
        self.gravity_settings_group.setLayout(gravity_form_layout)


        self.show_bounds_checkbox = QtGui.QCheckBox("Show Bounds"); self.show_bounds_checkbox.setChecked(False)
        self.add_labels_checkbox = QtGui.QCheckBox("Add Identifier Labels"); self.add_labels_checkbox.setChecked(True)
        self.label_height_input = QtGui.QDoubleSpinBox(); self.label_height_input.setRange(0, 1000); self.label_height_input.setValue(25.0)
        self.label_height_input.setToolTip("The height (Z-offset) for the identifier labels.")
        self.animate_nesting_checkbox = QtGui.QCheckBox("Animate nesting process (slower)"); self.animate_nesting_checkbox.setChecked(False)
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
        form_layout.addRow("Boundary Resolution:", self.boundary_resolution_input)
        form_layout.addRow("Algorithm:", self.algorithm_dropdown)
        form_layout.addRow(self.genetic_settings_group)
        form_layout.addRow(self.gravity_settings_group)
        form_layout.addRow("Identifier Font:", font_layout)
        form_layout.addRow(label_options_layout)
        
        rotation_layout = QtGui.QHBoxLayout()
        rotation_layout.addWidget(self.rotation_steps_slider)
        rotation_layout.addWidget(self.rotation_steps_spinbox)
        form_layout.addRow("Global Rotation Steps:", rotation_layout)

        animation_layout = QtGui.QHBoxLayout()
        animation_layout.addWidget(self.animate_nesting_checkbox)
        animation_layout.addStretch()
        form_layout.addRow(self.show_bounds_checkbox) # Keep this on its own line
        form_layout.addRow(animation_layout)
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
        self.algorithm_dropdown.currentIndexChanged.connect(self._on_algorithm_changed)
        self._on_algorithm_changed(0) # Set initial visibility

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

    def _on_algorithm_changed(self, index):
        """Shows or hides algorithm-specific settings."""
        algo_name = self.algorithm_dropdown.itemText(index)
        self.genetic_settings_group.setVisible(algo_name == "Genetic")
        self.gravity_settings_group.setVisible(algo_name == "Gravity")

    def load_selection(self):
        selection = FreeCADGui.Selection.getSelection()
        self.shape_table.setRowCount(0)

        if not selection:
            self.status_label.setText("Warning: No shapes selected.")
            self.nest_button.setEnabled(False)
            return

        # Check if a layout group is selected
        first_selected = selection[0]
        if first_selected.isDerivedFrom("App::DocumentObjectGroup") and first_selected.Label.startswith("Layout_"):
            self.load_layout(first_selected)
        else:
            self.load_shapes(selection)

    def load_layout(self, layout_group):
        """Loads the parameters and shapes from a layout group."""
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
        if hasattr(layout_group, 'FontFile') and os.path.exists(layout_group.FontFile):
            self.selected_font_path = layout_group.FontFile
            self.font_label.setText(os.path.basename(layout_group.FontFile))

        # Get the shapes from the layout
        shapes = []
        for sheet_group in layout_group.Group:
            if sheet_group.isDerivedFrom("App::DocumentObjectGroup") and sheet_group.Label.startswith("Sheet_"):
                objects_group = sheet_group.getObject(f"Objects_{sheet_group.Label.split('_')[1]}")
                if objects_group:
                    for obj in objects_group.Group:
                        if obj.isDerivedFrom("Part::Feature") and obj.Label.startswith("packed_"):
                            original_label = obj.Label.split("_")[1]
                            original_obj = self.doc.getObject(original_label)
                            if original_obj:
                                shapes.append(original_obj)
        
        self.load_shapes(shapes)

    def load_shapes(self, selection):
        """Loads a selection of shapes into the UI."""
        self.nest_button.setEnabled(True)
        self.selected_shapes_to_process = list(dict.fromkeys(selection)) # Keep unique, preserve order
        self.hidden_originals = list(self.selected_shapes_to_process)
        
        self.shape_table.setRowCount(len(self.selected_shapes_to_process))
        for i, obj in enumerate(self.selected_shapes_to_process):
            label_item = QtGui.QTableWidgetItem(obj.Label)
            label_item.setFlags(label_item.flags() & ~QtCore.Qt.ItemIsEditable)
            
            self._add_part_row(i, obj.Label)

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

    def _add_part_row(self, row_index, label):
        """Helper function to create and populate a single row in the parts table."""
        label_item = QtGui.QTableWidgetItem(label)
        label_item.setFlags(label_item.flags() & ~QtCore.Qt.ItemIsEditable)

        quantity_spinbox = QtGui.QSpinBox()
        quantity_spinbox.setRange(1, 500)
        quantity_spinbox.setValue(1)

        # --- Rotation Override Widget ---
        rotation_widget = QtGui.QWidget()
        rotation_layout = QtGui.QHBoxLayout(rotation_widget)
        rotation_layout.setContentsMargins(0, 0, 0, 0)
        
        rotation_slider = QtGui.QSlider(QtCore.Qt.Horizontal)
        rotation_slider.setRange(0, 360) # Allow 0 for no rotation
        rotation_slider.setValue(4)
        
        rotation_spinbox = QtGui.QSpinBox()
        rotation_spinbox.setRange(0, 360) # Allow 0 for no rotation
        rotation_spinbox.setValue(4)
        rotation_spinbox.setToolTip("Override global rotation steps for this part. 0 or 1 means no rotation.")

        rotation_slider.valueChanged.connect(rotation_spinbox.setValue)
        rotation_spinbox.valueChanged.connect(rotation_slider.setValue)
        
        rotation_layout.addWidget(rotation_slider)
        rotation_layout.addWidget(rotation_spinbox)

        override_checkbox = QtGui.QCheckBox()
        override_checkbox.setChecked(False)
        override_checkbox.stateChanged.connect(rotation_widget.setEnabled)
        rotation_widget.setEnabled(False) # Disabled by default

        self.shape_table.setItem(row_index, 0, label_item)
        self.shape_table.setCellWidget(row_index, 1, quantity_spinbox)
        self.shape_table.setCellWidget(row_index, 2, rotation_widget)
        self.shape_table.setCellWidget(row_index, 3, override_checkbox)

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

    def _recursive_delete(self, doc, obj_to_delete):
        """Helper to recursively delete all objects inside a group."""
        # Create a static list of children to iterate over
        children = list(obj_to_delete.Group)
        for child in children:
            if child.isDerivedFrom("App::DocumentObjectGroup"):
                self._recursive_delete(doc, child)
            else:
                try:
                    doc.removeObject(child.Name)
                except Exception:
                    pass
        # Finally, delete the (now empty) group itself
        try:
            doc.removeObject(obj_to_delete.Name)
        except Exception:
            pass
            
    def _delete_current_temporary_preview(self):
        """Robustly removes the current temporary preview group."""
        doc = FreeCAD.ActiveDocument
        if not doc: return
        
        # Now, clean up any leftover temporary preview groups from previous operations.
        groups_to_delete = []
        for obj in doc.Objects:
            if hasattr(obj, "Label") and obj.Label.startswith(self.preview_group_name):
                groups_to_delete.append(obj)
        
        for group in groups_to_delete:
            self._recursive_delete(doc, group)
        
        if groups_to_delete:
            doc.recompute()

    def cleanup_preview(self):
        """
        Hides existing finalized layouts and deletes any old temporary preview groups.
        This is typically called at the start of a new nesting operation.
        """
        doc = FreeCAD.ActiveDocument
        if not doc: return
        
        # Hide existing finalized layouts
        self.hidden_layouts.clear()
        for obj in doc.Objects:
            if obj.Label.startswith("Layout_"):
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.Visibility = False
                self.hidden_layouts.append(obj)
        self._delete_current_temporary_preview()

    def handle_accept(self):
        """Finalizes the preview layout by renaming its Label to a permanent, unique name."""
        self.accepted = True
        doc = FreeCAD.ActiveDocument
        if not doc: return

        # First, restore visibility of any previously existing layouts
        for obj in self.hidden_layouts:
            try:
                # Check if the object still exists in the document before accessing it
                if obj in doc.Objects:
                    obj.ViewObject.Visibility = True
            except ReferenceError:
                # This can happen if the user deleted a layout manually.
                # It's safe to just ignore it.
                pass
        self.hidden_layouts.clear()

        # Find the current preview group by looking for a label that STARTS with the temp name.
        # This correctly finds the group even if FreeCAD auto-renamed it (e.g., to __temp_Layout001)
        preview_group = None
        for obj in doc.Objects:
            if hasattr(obj, "Label") and obj.Label.startswith(self.preview_group_name):
                preview_group = obj
                break

        if preview_group:
            base_name, i = "Layout", 0
            # Find the next available unique name for the final layout's LABEL.
            # We must check against all object labels to ensure uniqueness.
            existing_labels = [o.Label for o in doc.Objects]
            while f"{base_name}_{i:03d}" in existing_labels:
                i += 1
            final_name = f"{base_name}_{i:03d}"
            
            # Simply rename the Label. This makes it "permanent" as far as our logic is concerned,
            # because the cleanup function only deletes objects with the original temporary label.
            preview_group.Label = final_name
            doc.recompute()
            
            # Ensure the newly finalized group is visible
            if hasattr(preview_group, "ViewObject"):
                preview_group.ViewObject.Visibility = True
        
    def handle_cancel(self):
        """Handles the cancellation of the nesting operation."""
        self.accepted = False
        doc = FreeCAD.ActiveDocument
        if not doc: return

        # Restore visibility of any previously existing layouts that were hidden
        # First, ensure the temporary preview group is deleted.
        self._delete_current_temporary_preview()

        # Restore visibility of original shapes that were hidden
        for obj in self.hidden_originals:
            try:
                if obj in doc.Objects:
                    obj.ViewObject.Visibility = True
            except ReferenceError:
                pass
        self.hidden_originals.clear()

        # Restore visibility of other finalized layouts that were hidden by the initial cleanup_preview call.
        for obj in self.hidden_layouts:
            try:
                if obj in doc.Objects:
                    obj.ViewObject.Visibility = True
            except ReferenceError:
                pass
        self.hidden_layouts.clear() # Clear the list after restoring visibility