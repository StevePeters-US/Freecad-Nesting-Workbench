Scaffold a test file for an untested module. Usage: `/add-tests path/to/module.py`

## Instructions

1. Read the module at the path given in: $ARGUMENTS
2. Read `tests/conftest.py` to understand available fixtures and FreeCAD mocks.
3. Read `STYLE_GUIDE.md` section 9 (Testing) for conventions.
4. Identify all **public** functions and methods (not prefixed with `_`).
5. Create a new test file at `tests/test_<module_name>.py`.
6. For each public function, scaffold at least:
   - A "happy path" test with valid inputs
   - An edge case test (empty input, zero, None, or boundary value)
7. If the module imports FreeCAD, use the existing mocks from `conftest.py`.
   If the module is in `algorithms/`, it must not need FreeCAD mocks.
8. Use `pytest` style (no `unittest.TestCase`).
9. Use the Arrange / Act / Assert pattern with blank line separators.

## Rules
- Do not test private methods (prefixed with `_`).
- Do not import from `nesting_commands/` in test files.
- Keep each test function under 30 lines.
- If a function is too tightly coupled to FreeCAD to test easily, add a
  `# TODO: requires FreeCAD integration test` comment and skip with `pytest.mark.skip`.
