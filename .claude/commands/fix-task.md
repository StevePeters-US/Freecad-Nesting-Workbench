Fix a single task from TASKS.md by its ID (e.g. `/fix-task T-007`).

## Instructions

1. Read `TASKS.md` and locate the task matching the ID: $ARGUMENTS
2. Read the exact file(s) listed in the task at the specified line numbers.
3. Apply only the change described in the task — nothing more, nothing less.
4. After making the change, confirm the task is done by briefly describing what was changed.
5. Do NOT mark the checkbox in `TASKS.md` — the user will do that.

## Rules
- Only change what the task specifies. Do not refactor surrounding code.
- If the change would break other code, report the conflict rather than making an unrelated fix.
- If the task references a function or line that does not exist, report it.
- Follow the project style guide in `STYLE_GUIDE.md`.
