# Nesting Workbench Translations

This directory contains translation files (`.ts` and `.qm`) for the FreeCAD Nesting Workbench.

## Translation Workflow

### 1. Marking Strings in Code
All translatable user-facing strings (labels, tooltips, buttons, command descriptions) are wrapped using `QT_TRANSLATE_NOOP(context, text)` imported from `freecad.nestingworkbench.ui_helpers`.

Example:
```python
from freecad.nestingworkbench.ui_helpers import QT_TRANSLATE_NOOP

label = QtWidgets.QLabel(QT_TRANSLATE_NOOP("NestingPanel", "Sheet Width:"))
```

### 2. Updating Translation Files
Run the update script to parse Python source code and update the master `.ts` template:
```bash
python3 Resources/translations/update_translations.py
```
This updates `NestingWorkbench.ts` and synchronizes any existing locale files (e.g., `NestingWorkbench_de.ts`, `NestingWorkbench_fr.ts`), preserving existing translations.

### 3. Compiling Binary `.qm` Catalogs
When Qt Linguist tools (`lrelease`) are installed, compile `.ts` files to binary `.qm` catalogs:
```bash
python3 Resources/translations/update_translations.py --compile
```
Or directly using `lrelease`:
```bash
lrelease Resources/translations/NestingWorkbench_de.ts -qm Resources/translations/NestingWorkbench_de.qm
```

### 4. Runtime Loading
On workbench activation (`NestingWorkbench.Initialize()`), `FreeCADGui.addLanguagePath(TRANSLATIONS_DIR)` is called so FreeCAD automatically loads the appropriate `.qm` file matching the user's active FreeCAD language preferences.
