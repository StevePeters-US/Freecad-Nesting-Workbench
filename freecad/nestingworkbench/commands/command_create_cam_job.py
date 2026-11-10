# SPDX-License-Identifier: LGPL-2.1-or-later
import FreeCAD
import FreeCADGui
import os
from PySide import QtWidgets, QtCore
from freecad.nestingworkbench import nw_logger
from freecad.nestingworkbench.Tools.Cam import cam_manager
from freecad.nestingworkbench.constants import PREFS_PATH
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP, make_checkbox, rich_tooltip
from freecad.nestingworkbench.freecad_helpers import is_layout_group

_TITLE = QT_TRANSLATE_NOOP("CAMOptionsDialog", "CAM Job Options")
_SELECT_LABEL = QT_TRANSLATE_NOOP("CAMOptionsDialog", "Select objects to include in CAM job:")
_PARTS_LABEL = QT_TRANSLATE_NOOP("CAMOptionsDialog", "Parts (full cuts)")
_LABELS_LABEL = QT_TRANSLATE_NOOP("CAMOptionsDialog", "Labels (engraving)")
_SILHOUETTES_LABEL = QT_TRANSLATE_NOOP("CAMOptionsDialog", "Silhouettes (outlines)")
_TEMPLATE_LABEL = QT_TRANSLATE_NOOP("CAMOptionsDialog", "CAM Template (Optional):")
_NO_TEMPLATE_TEXT = QT_TRANSLATE_NOOP("CAMOptionsDialog", "None")
_BROWSE_TEXT = QT_TRANSLATE_NOOP("CAMOptionsDialog", "Browse...")
_POST_LABEL = QT_TRANSLATE_NOOP("CAMOptionsDialog", "Post Processor:")
_SELECT_TEMPLATE_TITLE = QT_TRANSLATE_NOOP("CAMOptionsDialog", "Select CAM Job Template")
_TEMPLATE_FILTER = QT_TRANSLATE_NOOP("CAMOptionsDialog", "JSON Files (*.json);;All Files (*)")


class CAMOptionsDialog(QtWidgets.QDialog):
    """Dialog for selecting which object types to include in CAM job."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        tr = QtWidgets.QApplication.translate
        self.setWindowTitle(tr("CAMOptionsDialog", _TITLE))
        self.setMinimumWidth(250)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # Title label
        title = QtWidgets.QLabel(tr("CAMOptionsDialog", _SELECT_LABEL))
        layout.addWidget(title)
        
        # Checkboxes
        self.parts_checkbox = make_checkbox(
            tr("CAMOptionsDialog", _PARTS_LABEL),
            checked=True,
            tooltip=rich_tooltip("CAMOptionsDialog", QT_TRANSLATE_NOOP("CAMOptionsDialog", "Cuts each part's full outline through the sheet."))
        )
        layout.addWidget(self.parts_checkbox)

        self.labels_checkbox = make_checkbox(
            tr("CAMOptionsDialog", _LABELS_LABEL),
            checked=True,
            tooltip=rich_tooltip("CAMOptionsDialog", QT_TRANSLATE_NOOP("CAMOptionsDialog", "Engraves each part's identifier label."))
        )
        layout.addWidget(self.labels_checkbox)

        self.silhouettes_checkbox = make_checkbox(
            tr("CAMOptionsDialog", _SILHOUETTES_LABEL),
            checked=False,
            tooltip=rich_tooltip("CAMOptionsDialog", QT_TRANSLATE_NOOP("CAMOptionsDialog", "Adds each part's silhouette outline to the job's model."))
        )
        layout.addWidget(self.silhouettes_checkbox)
        
        # Separator
        layout.addSpacing(10)

        # Template Selection
        layout.addWidget(QtWidgets.QLabel(tr("CAMOptionsDialog", _TEMPLATE_LABEL)))
        
        template_layout = QtWidgets.QHBoxLayout()
        
        self.template_combo = QtWidgets.QComboBox()
        self.template_combo.addItem(tr("CAMOptionsDialog", _NO_TEMPLATE_TEXT), None)
        self.template_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.template_combo.setToolTip(rich_tooltip("CAMOptionsDialog", QT_TRANSLATE_NOOP(
            "CAMOptionsDialog", "Optional CAM job template to take tool controllers and setup-sheet defaults from.")))
        self._populate_templates()
        
        self.browse_button = QtWidgets.QPushButton(tr("CAMOptionsDialog", _BROWSE_TEXT))
        self.browse_button.setToolTip(rich_tooltip("CAMOptionsDialog", QT_TRANSLATE_NOOP(
            "CAMOptionsDialog", "Browses for a CAM job template JSON file on disk.")))
        self.browse_button.clicked.connect(self.browse_template)
        
        template_layout.addWidget(self.template_combo)
        template_layout.addWidget(self.browse_button)
        
        layout.addLayout(template_layout)

        # Post Processor Selection
        layout.addWidget(QtWidgets.QLabel(tr("CAMOptionsDialog", _POST_LABEL)))
        self.post_processor_combo = QtWidgets.QComboBox()
        self.post_processor_combo.setToolTip(rich_tooltip("CAMOptionsDialog", QT_TRANSLATE_NOOP(
            "CAMOptionsDialog", "Post processor that turns the job into G-code.")))
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
        
        paths = [
            os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "CAM", "Templates"),
            os.path.join(FreeCAD.getHomePath(), "Mod", "CAM", "Templates"),
            os.path.join(FreeCAD.getHomePath(), "data", "Mod", "CAM", "Templates"),
        ]
        try:
            import Path.Preferences
            # CAM's own template dir under its (versioned) asset path, and the
            # directory of the user's default job template, if one is set.
            paths.insert(0, str(Path.Preferences.getTemplateDirectory()))
            default_template = Path.Preferences.defaultJobTemplate()
            if default_template:
                paths.insert(0, os.path.dirname(default_template))
        except Exception as e:
            nw_logger.debug(f"[CAMOptionsDialog] CAM template path lookup failed: {e}")
                
        found_templates = set()
        
        for p in paths:
            if os.path.exists(p) and os.path.isdir(p):
                try:
                    files = [f for f in os.listdir(p) if f.lower().endswith(".json")]
                    for f in files:
                        full_path = os.path.normpath(os.path.join(p, f))
                        # Avoid duplicates
                        if full_path not in found_templates:
                            self.template_combo.addItem(f, full_path)
                            found_templates.add(full_path)
                except Exception as e:
                    nw_logger.debug(f"[CAMOptionsDialog] template dir access failed for '{p}': {e}")
    
    def browse_template(self):
        """Opens a file dialog to select a template."""
        tr = QtWidgets.QApplication.translate
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            tr("CAMOptionsDialog", _SELECT_TEMPLATE_TITLE),
            FreeCAD.getUserAppDataDir(),
            tr("CAMOptionsDialog", _TEMPLATE_FILTER)
        )
        
        if filename:
            filename = os.path.normpath(filename)
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
            # A Job's PostProcessor accepts legacy posts only; machine posts raise.
            processors = sorted(Path.Preferences.allEnabledLegacyPostProcessors())
        except Exception as e:
            nw_logger.debug(f"[CAMOptionsDialog] Path.Preferences access failed: {e}")
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
            normalized_last = os.path.normpath(last_template)
            # Check if it's already in the list
            index = self.template_combo.findData(normalized_last)
            if index == -1:
                 # Add it if not found in standard paths
                 self.template_combo.addItem(os.path.basename(normalized_last), normalized_last)
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
        tr = QtWidgets.QApplication.translate
        return {
            'Pixmap': 'Nesting_CNC_Icon.svg',
            'MenuText': tr('CreateCAMJobCommand', 'Create CAM Job'),
            'ToolTip': rich_tooltip(
                'CreateCAMJobCommand',
                QT_TRANSLATE_NOOP('CreateCAMJobCommand', 'Creates a CAM job from the selected layout.\nSelect a Layout group first.'),
            ),
        }

    def Activated(self):
        """This method is executed when the command is activated."""
        selection = FreeCADGui.Selection.getSelection()
        layout_group = None
        if selection:
            selected = selection[0]
            if is_layout_group(selected):
                layout_group = selected

        if not layout_group:
            nw_logger.info("Please select a layout group to create a CAM job from.")
            return
        
        # Show options dialog
        dialog = CAMOptionsDialog(FreeCADGui.getMainWindow())
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            options = dialog.get_options()
            
            # Check that at least one object type is selected
            if not (options['include_parts'] or options['include_labels'] or options['include_outlines']):
                nw_logger.warn("No object types selected. CAM job not created.")
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
        return is_layout_group(selected)

if FreeCAD.GuiUp:
    FreeCADGui.addCommand('Nesting_CreateCAMJob', CreateCAMJobCommand())
