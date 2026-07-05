# Testing and Linting

This repo is verified with backend tests, Ruff, and mypy (CI) / BasedPyright (local). Tests are mock-heavy and do not require a live GPU, Ollama server, ComfyUI server, or external network.

## Backend Tests

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Current local result: `2053 collected, 0 errors`. Test
files with `pytest.mark.skip` are skipped as expected.

To run individual test files:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_retry_manager.py -q
.\venv\Scripts\python.exe -m pytest tests/test_sentry.py tests/test_bootstrap_sentry_smoke.py -q
```

Important test fixtures:

- `tests/conftest.py` resets `UIState` between tests.
- `tests/conftest.py:_install_optional_dependency_stubs()` injects lightweight
  `types.ModuleType` stubs for heavy packages (`torch`, `pyarrow`, `crewai`,
  `faster_whisper`, `whisper`) so tests using `patch(...)` never load real
  GPU or native DLLs. No 200MB torch download on CI.
- The pytest temp cleanup patch suppresses benign Windows `PermissionError` during numbered temp directory cleanup.

## Ruff

```powershell
.\venv\Scripts\python.exe -m ruff check .
.\venv\Scripts\python.exe -m ruff check . --fix
```

Current local result: clean.

## Type Checking

### CI: mypy

The CI workflow runs `mypy` against selected modules:

```powershell
.\venv\Scripts\python.exe -m mypy --follow-imports=skip --ignore-missing-imports config/config.py config/config_schemas.py utils/errors.py ...
```

Configured in `.github/workflows/ci.yml`.

### Local: BasedPyright

The repo has `pyrightconfig.json` for local type checking with BasedPyright.
The scanner package path plus lightweight checker-only dependency stubs:

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

Coverage configuration lives in `pyproject.toml`. CI enforces 80% coverage minimum.

## Dashboard Tests

Dashboard tests live under `dashboard/` and use Vitest.

```powershell
cd dashboard
npm run test:run
npm run test:coverage
```

The Python backend checks above do not run dashboard tests.
