Fix a single task from todo_manual.md by its ID (e.g. `/fix-manual-task M-003`).

## Instructions

1. Read `todo_manual.md` and locate the task matching the ID: $ARGUMENTS
2. Read the exact file(s) listed in the task. If the file is marked `(NEW)`, you will create it.
3. Apply only the change described in the task — nothing more, nothing less.
4. Follow the interface/code snippets in the task description as closely as possible.
5. If the task says to add tests, create them in the `tests/` directory.
6. After making the change, confirm the task is done by briefly describing what was changed.
7. Do NOT mark the checkbox in `todo_manual.md` — the user will do that.

## Rules
- Only change what the task specifies. Do not refactor surrounding code.
- If the change would break other code, report the conflict rather than making an unrelated fix.
- If the task references a function or line that does not exist, report it.
- Follow the project style guide in `STYLE_GUIDE.md`.
- New files in `ManualNester/` may import FreeCAD. Files in `algorithms/` must NOT import FreeCAD.
- Keep physics_engine.py and collision_resolver.py as standalone as possible — they should be easy to unit test.

## Key File Locations
- `nestingworkbench/Tools/ManualNester/manual_nester_tool.py` — main drag/drop observer
- `nestingworkbench/Tools/ManualNester/ui_manual_nester.py` — task panel UI
- `nestingworkbench/Tools/ManualNester/manual_nester_panel_manager.py` — panel lifecycle
- `nesting_commands/command_manual_nester.py` — FreeCAD command entry point
- `tests/` — pytest test directory
