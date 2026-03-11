Run the physics engine tests and report results.

## Instructions

1. Check if `tests/test_physics_engine.py` exists. If not, report that M-001 tests haven't been created yet.

2. Run the physics tests:
   ```
   python -m pytest tests/test_physics_engine.py -v
   ```

3. If tests fail, read the test file and the source file (`nestingworkbench/Tools/ManualNester/physics_engine.py`) to diagnose the issue.

4. Report:
   - Number of tests passed/failed
   - For each failure: the test name, expected vs actual, and a suggested fix
   - Any missing test coverage (refer to M-001 in `todo_manual.md` for required tests)

## Rules
- Do NOT modify any source files. Only report findings.
- If pytest is not available, try `python -m unittest discover tests/` as a fallback.
