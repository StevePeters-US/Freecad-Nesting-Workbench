Run a targeted code review of a single module. Usage: `/review-module path/to/module.py`

## Instructions

1. Read the file at the path given in: $ARGUMENTS
2. Review it against the project style guide (`STYLE_GUIDE.md`) and the patterns
   identified in `TASKS.md`.
3. Report findings grouped into:
   - **Bugs / correctness issues** (things that can break)
   - **Exception handling** (bare excepts, silent failures, missing tracebacks)
   - **Magic numbers / hardcoded values**
   - **Dead code / unused imports**
   - **Functions over 50 lines** (list name and line count)
   - **Missing docstrings** (list each public function/class with no docstring)
   - **Style violations** (naming, line length over 120, import order)

4. For each finding, output:
   ```
   Line NNN: [category] short description
   ```

5. At the end, suggest up to 3 new task entries (in TASKS.md format) for issues
   not already covered by existing tasks.

## Rules
- Do not make any changes to files in this review pass.
- Be specific: include line numbers and the actual problematic code snippet.
