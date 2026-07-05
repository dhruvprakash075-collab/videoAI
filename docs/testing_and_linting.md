# Testing and Linting

This repo is verified with backend tests, Ruff, and BasedPyright. Tests are mock-heavy and do not require a live GPU, Ollama server, ComfyUI server, or external network.

## Backend Tests

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Current local result: `1969 passed, 5 skipped`.

Targeted examples:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_retry_manager.py -q
.\venv\Scripts\python.exe -m pytest tests/test_sentry.py tests/test_bootstrap_sentry_smoke.py -q
```

Important test fixtures:

- `tests/conftest.py` resets `UIState` between tests.
- The pyarrow stub in `tests/conftest.py` avoids Windows native-DLL shutdown crashes.
- The pytest temp cleanup patch suppresses benign Windows `PermissionError` during numbered temp directory cleanup.

## Ruff

```powershell
.\venv\Scripts\python.exe -m ruff check .
.\venv\Scripts\python.exe -m ruff check . --fix
```

Current local result: clean.

## BasedPyright

The repo has `pyrightconfig.json`. The current local checker setup uses the scanner package path plus lightweight checker-only dependency stubs:

```powershell
$env:PYTHONPATH="C:\Video.AI\codex_tmp\scanner_pkgs;C:\Video.AI\codex_tmp\checker_deps"
python -m basedpyright
```

Current local result: `0 errors, 0 warnings, 0 notes`.

## Coverage

```powershell
.\venv\Scripts\python.exe -m coverage run -m pytest
.\venv\Scripts\python.exe -m coverage report
```

Coverage configuration lives in `pyproject.toml`.

## Dashboard Tests

Dashboard tests live under `dashboard/` and use Vitest.

```powershell
cd dashboard
npm run test:run
npm run test:coverage
```

The Python backend checks above do not run dashboard tests.
