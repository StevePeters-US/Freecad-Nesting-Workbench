# Nesting/nesting/ui_panel.py

"""
This module contains the PackerUI class, which defines the user interface
for the main nesting task panel.
"""

from PySide import QtGui, QtCore
import FreeCAD
import FreeCADGui
import os

class PackerUI(QtGui.QWidget):
    """
    Defines the user interface for the main nesting task panel, including
    all input fields, buttons, and the table of shapes.
    """
    def __init__(self, parent=None):
        super(PackerUI, self).__init__(parent)
        print("PackerUI initialized")
        self.setWindowTitle("Shape Packer")
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
        action_button_layout = QtGui.QHBoxLayout()

        self.sheet_width_input = QtGui.QDoubleSpinBox(); self.sheet_width_input.setRange(1, 10000); self.sheet_width_input.setValue(600)
        self.sheet_height_input = QtGui.QDoubleSpinBox(); self.sheet_height_input.setRange(1, 10000); self.sheet_height_input.setValue(600)
        self.part_spacing_input = QtGui.QDoubleSpinBox(); self.part_spacing_input.setRange(0, 1000); self.part_spacing_input.setValue(12.5)
        
        self.shape_table = QtGui.QTableWidget()
        self.shape_table.setColumnCount(2)
        self.shape_table.setHorizontalHeaderLabels(["Shape", "Quantity"])

        self.rotation_dropdown = QtGui.QComboBox()
        self.rotation_dropdown.addItems(["No Rotation", "Force 90 Degree Right", "Free Rotation"])
        self.rotation_dropdown.setCurrentIndex(0)

        self.rotation_steps_input = QtGui.QSpinBox()
        self.rotation_steps_input.setRange(1, 360)
        self.rotation_steps_input.setValue(4)

        self.algorithm_dropdown = QtGui.QComboBox()
        self.algorithm_dropdown.addItems(["Grid Fill", "Greedy", "Gravity", "Genetic", "Minkowski"])
        self.algorithm_dropdown.setCurrentIndex(0)

        # --- Gravity Packer Settings ---
        self.gravity_settings_group = QtGui.QGroupBox("Gravity Packer Settings")
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
        self.gravity_shake_count_input = QtGui.QSpinBox(); self.gravity_shake_count_input.setRange(0, 100); self.gravity_shake_count_input.setValue(25)
        gravity_form_layout.addRow("Gravity Direction:", dial_layout)
        gravity_form_layout.addRow(self.gravity_random_checkbox)
        gravity_form_layout.addRow("Step Size:", self.gravity_step_size_input)
        gravity_form_layout.addRow("Max Spawn Tries:", self.gravity_max_spawn_input)
        gravity_form_layout.addRow("Shake Count on Collide:", self.gravity_shake_count_input)
        self.gravity_settings_group.setLayout(gravity_form_layout)


        self.show_bounds_checkbox = QtGui.QCheckBox("Show Bounds"); self.show_bounds_checkbox.setChecked(False)
        self.add_labels_checkbox = QtGui.QCheckBox("Add Identifier Labels"); self.add_labels_checkbox.setChecked(True)
        self.animate_packing_checkbox = QtGui.QCheckBox("Animate packing process (slower)"); self.animate_packing_checkbox.setChecked(False)
        self.sound_checkbox = QtGui.QCheckBox("Play sound on completion"); self.sound_checkbox.setChecked(True)
        
        self.pack_button = QtGui.QPushButton("Pack Shapes")
        
        # --- Font Selection UI Elements ---
        self.font_select_button = QtGui.QPushButton("Select Font")
        self.font_label = QtGui.QLabel("No Font Selected")
        self.font_label.setWordWrap(True)
        font_layout.addWidget(self.font_select_button)
        font_layout.addWidget(self.font_label)
        
        self.status_label = QtGui.QLabel("Select master shapes to pack.")
        self.status_label.setWordWrap(True)

        form_layout.addRow("Sheet Width:", self.sheet_width_input)
        form_layout.addRow("Sheet Height:", self.sheet_height_input)
        form_layout.addRow("Part Spacing:", self.part_spacing_input)
        form_layout.addRow("Algorithm:", self.algorithm_dropdown)
        form_layout.addRow(self.gravity_settings_group)
        form_layout.addRow("Rotation:", self.rotation_dropdown)
        form_layout.addRow("Rotation Steps:", self.rotation_steps_input)
        form_layout.addRow("Identifier Font:", font_layout)
        form_layout.addRow(self.show_bounds_checkbox)
        form_layout.addRow(self.add_labels_checkbox)
        form_layout.addRow(self.animate_packing_checkbox)
        form_layout.addRow(self.sound_checkbox)
        
        action_button_layout.addWidget(self.pack_button)

        main_layout.addLayout(form_layout)
        main_layout.addWidget(self.shape_table)
        main_layout.addLayout(action_button_layout)
        main_layout.addWidget(self.status_label)
        main_layout.addStretch()
        
        self.setLayout(main_layout)

        # Connect signals
        self.algorithm_dropdown.currentIndexChanged.connect(self._on_algorithm_changed)
        self._on_algorithm_changed(0) # Set initial visibility

        # Connect the packer controller
        from .packer_controller import PackerController
        self.controller = PackerController(self)
        self.pack_button.clicked.connect(self.controller.execute_packing)
        self.font_select_button.clicked.connect(self.select_font_file)

    def _on_algorithm_changed(self, index):
        """Shows or hides algorithm-specific settings."""
        algo_name = self.algorithm_dropdown.itemText(index)
        self.gravity_settings_group.setVisible(algo_name == "Gravity")

    def load_selection(self):
        selection = FreeCADGui.Selection.getSelection()
        self.shape_table.setRowCount(0)

        if not selection:
            self.status_label.setText("Warning: No shapes selected.")
            self.pack_button.setEnabled(False)
            return

        # Check if a layout group is selected
        first_selected = selection[0]
        if first_selected.isDerivedFrom("App::DocumentObjectGroup") and first_selected.Label.startswith("Layout_"):
            self.load_layout(first_selected)
        else:
            self.load_shapes(selection)

    def load_layout(self, layout_group):
        """Loads the parameters and shapes from a layout group."""
        self.pack_button.setEnabled(True)
        self.selected_shapes_to_process = []
        self.hidden_originals = []

        # Parse the spreadsheet
        spreadsheet = layout_group.getObject("LayoutParameters")
        if spreadsheet:
            self.sheet_width_input.setValue(spreadsheet.get('B2'))
            self.sheet_height_input.setValue(spreadsheet.get('B3'))
            self.part_spacing_input.setValue(spreadsheet.get('B4'))
            font_path = spreadsheet.get('B5')
            if font_path and os.path.exists(font_path):
                self.selected_font_path = font_path
                self.font_label.setText(os.path.basename(font_path))

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
        self.pack_button.setEnabled(True)
        self.selected_shapes_to_process = selection
        self.hidden_originals = list(selection)
        
        self.shape_table.setRowCount(len(selection))
        for i, obj in enumerate(selection):
            label_item = QtGui.QTableWidgetItem(obj.Label)
            label_item.setFlags(label_item.flags() & ~QtCore.Qt.ItemIsEditable)
            
            quantity_spinbox = QtGui.QSpinBox()
            quantity_spinbox.setRange(0, 500)
            quantity_spinbox.setValue(1)
            
            self.shape_table.setItem(i, 0, label_item)
            self.shape_table.setCellWidget(i, 1, quantity_spinbox)
            
        self.shape_table.resizeColumnsToContents()
        self.status_label.setText(f"{len(selection)} unique object(s) selected. Specify quantities and pack.")

    def select_font_file(self):
        """Opens a file dialog to let the user select a font file."""
        # Correctly find the workbench's root directory and the 'fonts' subfolder
        try:
            # __file__ is the path to this file (ui_panel.py)
            # os.path.dirname gives the directory it's in (.../nesting)
            # os.path.dirname again gives the parent directory (.../Nesting)
            workbench_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
            
    def cleanup_preview(self, name_to_clean=None):
        """Hides existing finalized layouts and robustly removes the temp preview group."""
        doc = FreeCAD.ActiveDocument
        if not doc: return
        
        # Hide existing finalized layouts
        self.hidden_layouts.clear()
        for obj in doc.Objects:
            if obj.Label.startswith("Layout_"):
                if hasattr(obj, "ViewObject"):
                    obj.ViewObject.Visibility = False
                self.hidden_layouts.append(obj)

        # Now, clean up any leftover temporary preview groups from previous operations.
        groups_to_delete = []
        for obj in doc.Objects:
            if hasattr(obj, "Label") and obj.Label.startswith(self.preview_group_name):
                groups_to_delete.append(obj)
        
        for group in groups_to_delete:
            self._recursive_delete(doc, group)
        
        if groups_to_delete:
            doc.recompute()

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
        """Handles the cancellation of the packing operation."""
        self.accepted = False
        doc = FreeCAD.ActiveDocument
        if not doc: return

        # Restore visibility of any previously existing layouts that were hidden
        for obj in self.hidden_layouts:
            try:
                if obj in doc.Objects:
                    obj.ViewObject.Visibility = True
            except ReferenceError:
                pass
        self.hidden_layouts.clear()
