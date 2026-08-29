# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
import os
from PySide import QtWidgets, QtCore
from freecad.nestingworkbench.Tools.Cam import cam_manager
from freecad.nestingworkbench.constants import PREFS_PATH


class CAMOptionsDialog(QtWidgets.QDialog):
    """Dialog for selecting which object types to include in CAM job."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CAM Job Options")
        self.setMinimumWidth(250)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # Title label
        title = QtWidgets.QLabel("Select objects to include in CAM job:")
        layout.addWidget(title)
        
        # Checkboxes
        self.parts_checkbox = QtWidgets.QCheckBox("Parts (full cuts)")
        self.parts_checkbox.setChecked(True)
        layout.addWidget(self.parts_checkbox)
        
        self.labels_checkbox = QtWidgets.QCheckBox("Labels (engraving)")
        self.labels_checkbox.setChecked(True)
        layout.addWidget(self.labels_checkbox)
        
        self.silhouettes_checkbox = QtWidgets.QCheckBox("Silhouettes (outlines)")
        self.silhouettes_checkbox.setChecked(False)
        layout.addWidget(self.silhouettes_checkbox)
        
        # Separator
        layout.addSpacing(10)

        # Template Selection
        layout.addWidget(QtWidgets.QLabel("CAM Template (Optional):"))
        
        template_layout = QtWidgets.QHBoxLayout()
        
        self.template_combo = QtWidgets.QComboBox()
        self.template_combo.addItem("None", None)
        self.template_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._populate_templates()
        
        self.browse_button = QtWidgets.QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_template)
        
        template_layout.addWidget(self.template_combo)
        template_layout.addWidget(self.browse_button)
        
        layout.addLayout(template_layout)

        # Post Processor Selection
        layout.addWidget(QtWidgets.QLabel("Post Processor:"))
        self.post_processor_combo = QtWidgets.QComboBox()
        self._populate_post_processors()
        layout.addWidget(self.post_processor_combo)
        
        # Load last used template from preferences
        self._load_last_template()
        
        # Separator
        layout.addSpacing(10)
        
        # Buttons
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def _populate_templates(self):
        """Finds and populates CAM templates."""
        
        # Standard paths for CAM templates
        # Check both User AppData and Installation Data directories
        paths = [
            os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "CAM", "Templates"),
            os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "Path", "Templates"),
            os.path.join(FreeCAD.getHomePath(), "Mod", "CAM", "Templates"),
            os.path.join(FreeCAD.getHomePath(), "Mod", "Path", "Templates"),
            os.path.join(FreeCAD.getHomePath(), "data", "Mod", "CAM", "Templates"),
            os.path.join(FreeCAD.getHomePath(), "data", "Mod", "Path", "Templates"),
        ]
        
        # Add custom template path if configured in preferences
        custom_path = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Path/Job").GetString("Template", "")
        if custom_path and os.path.isdir(custom_path):
             paths.insert(0, custom_path)

        # FreeCAD 1.1 CamAssets locations: directly under the user app data
        # dir on Linux, under a versioned subdir on Windows.
        for candidate in (
            os.path.join(FreeCAD.getUserAppDataDir(), "CamAssets", "Templates"),
            os.path.join(FreeCAD.getUserAppDataDir(), "v1-1", "CamAssets", "Templates"),
        ):
            if os.path.isdir(candidate):
                paths.insert(0, candidate)
                
        found_templates = set()
        
        for p in paths:
            if os.path.exists(p) and os.path.isdir(p):
                try:
                    files = [f for f in os.listdir(p) if f.lower().endswith(".json")]
                    for f in files:
                        full_path = os.path.join(p, f)
                        # Avoid duplicates
                        if full_path not in found_templates:
                            self.template_combo.addItem(f, full_path)
                            found_templates.add(full_path)
                except Exception:
                    pass  # Skip candidate template directories that do not exist or are inaccessible
    
    def browse_template(self):
        """Opens a file dialog to select a template."""
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select CAM Job Template",
            FreeCAD.getUserAppDataDir(),
            "JSON Files (*.json);;All Files (*)"
        )
        
        if filename:
            # Check if already in combo
            index = self.template_combo.findData(filename)
            if index == -1:
                # Add it
                self.template_combo.addItem(os.path.basename(filename), filename)
                index = self.template_combo.count() - 1
                
            self.template_combo.setCurrentIndex(index)

    def _populate_post_processors(self):
        """Populates the post processor combo from the CAM workbench."""
        processors = []
        try:
            import Path.Preferences
            processors = sorted(Path.Preferences.allEnabledPostProcessors())
        except Exception:
            pass  # Path/CAM preferences module unavailable or Path workbench not loaded
        for p in processors:
            self.post_processor_combo.addItem(p)

        prefs = FreeCAD.ParamGet(PREFS_PATH)
        last = prefs.GetString("LastCAMPostProcessor", "grbl")
        index = self.post_processor_combo.findText(last)
        if index != -1:
            self.post_processor_combo.setCurrentIndex(index)
        elif not processors:
            # CAM modules unavailable at dialog time — keep the stored choice
            self.post_processor_combo.addItem(last)

    def _load_last_template(self):
        """Loads the last used template path from preferences."""
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        last_template = prefs.GetString("LastCAMTemplate", "")
        
        if last_template and os.path.exists(last_template):
            # Check if it's already in the list
            index = self.template_combo.findData(last_template)
            if index == -1:
                 # Add it if not found in standard paths
                 self.template_combo.addItem(os.path.basename(last_template), last_template)
                 index = self.template_combo.count() - 1
            
            self.template_combo.setCurrentIndex(index)

    def _save_last_template(self):
        """Saves the currently selected template to preferences."""
        selected_path = self.template_combo.itemData(self.template_combo.currentIndex())
        if selected_path:
             prefs = FreeCAD.ParamGet(PREFS_PATH)
             prefs.SetString("LastCAMTemplate", selected_path)

    def accept(self):
        """Overridden accept to save state."""
        self._save_last_template()
        prefs = FreeCAD.ParamGet(PREFS_PATH)
        prefs.SetString("LastCAMPostProcessor", self.post_processor_combo.currentText())
        super().accept()

    def get_options(self):
        """Returns the selected options."""
        return {
            'include_parts': self.parts_checkbox.isChecked(),
            'include_labels': self.labels_checkbox.isChecked(),
            'include_outlines': self.silhouettes_checkbox.isChecked(),
            'selected_template': self.template_combo.itemData(self.template_combo.currentIndex()),
            'post_processor': self.post_processor_combo.currentText()
        }


class CreateCAMJobCommand:
    """The command to create a CAM job from a layout."""
    def GetResources(self):
        return {
            'Pixmap': 'Nesting_CNC_Icon.svg',
            'MenuText': 'Create CAM Job',
            'ToolTip': 'Creates a CAM job from the selected layout.'
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        selection = FreeCADGui.Selection.getSelection()
        layout_group = None
        if selection:
            selected = selection[0]
            if selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_"):
                layout_group = selected

        if not layout_group:
            FreeCAD.Console.PrintMessage("Please select a layout group to create a CAM job from.\n")
            return
        
        # Show options dialog
        dialog = CAMOptionsDialog(FreeCADGui.getMainWindow())
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            options = dialog.get_options()
            
            # Check that at least one object type is selected
            if not (options['include_parts'] or options['include_labels'] or options['include_outlines']):
                FreeCAD.Console.PrintWarning("No object types selected. CAM job not created.\n")
                return
            
            cam_manager_instance = cam_manager.CAMManager(layout_group=layout_group)
            cam_manager_instance.create_cam_job(
                include_parts=options['include_parts'],
                include_labels=options['include_labels'],
                include_outlines=options['include_outlines'],
                template_path=options.get('selected_template'),
                post_processor=options.get('post_processor') or "grbl"
            )

    def IsActive(self):
        """Active only if a document is open and a layout group is selected."""
        if not FreeCAD.ActiveDocument: return False
        selection = FreeCADGui.Selection.getSelection()
        if not selection: return False
        selected = selection[0]
        return selected.isDerivedFrom("App::DocumentObjectGroup") and selected.Label.startswith("Layout_")

if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_CreateCAMJob', CreateCAMJobCommand())
