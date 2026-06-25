# Testing and Linting Guide

This document details how to verify codebase health, run the test suite, analyze coverage, and check formatting.

---

## 1. Running Unit Tests

All tests run locally using mock-heavy patterns — no GPU, Ollama server, or external connections required.

* **Execute All Tests**:
  ```powershell
  venv\Scripts\python.exe -m pytest tests/ -q
  ```
  **Current status**: **1,888 passing tests**, 5 skipped, 0 failing (verified 2026-06-25).
  Runtime: ~3 minutes on warm cache. **Clean exit** — no access violation, no PermissionError traceback.

  > **Windows note**: The old `PermissionError: [WinError 5]` about `pytest-current`
  > cleanup in `%TEMP%` has been suppressed via a monkeypatch on
  > `_pytest.pathlib.cleanup_numbered_dir` in `tests/conftest.py`. See P8-8.

* **Execute Specific Test Module**:
  ```powershell
  venv\Scripts\python.exe -m pytest tests/test_retry_manager.py -q
  ```

### Test Suite Inventory
The `tests/` directory contains **81 `test_*.py` modules**.
Key test modules include:
- `test_ollama_client.py` — B1 breaker state machine
- `test_crewai_breaker.py` — guarded_crewai_kickoff and BreakerOpen contract
- `test_source_loader.py` (57 tests) — v6 Phase 1 source ingestion
- `test_critic.py` (51 tests) — 5-dim rubric + rewrite loop
- `test_segment_runner_helpers.py` — per-segment loop logic
- `test_2026_06_fixes.py` (25 tests) — regression coverage for P5-1..4, P4-8, P4-23
- Supertonic 3 TTS (CPU ONNX) is tested via `test_audio_proxy_extended.py`.
- **ComfyUI image gen** is covered by `test_image_gen.py`, `test_comfyui.py`,
  `test_qwen_repose.py`, `test_qwen_spike_check.py`.

### Important Notes
- **16 warnings** appear (torch jit deprecation, pydub audioop deprecation, CUDA expandable_segments) — all harmless.
- `tests/conftest.py` has an **autouse fixture that resets `UIState`** between tests. If you add a new `UIState` class attribute, you **must** add it to `conftest.py` too (otherwise state bleeds between tests).
- `tests/conftest.py` also contains a **pyarrow stub** (prevents native DLL loading on Windows, which causes atexit access violations) and a `cleanup_numbered_dir` **monkeypatch** (suppresses PermissionError on temp dir cleanup). These are **required** for clean test suite exit on Windows. See P8-8.

---

## 2. Measuring Code Coverage

Coverage tracks which source lines are exercised by the test suite.

* **Collect and Print Coverage**:
  ```powershell
  venv\Scripts\python.exe -m coverage run -m pytest
  venv\Scripts\python.exe -m coverage report
  ```

* **Current baseline** (recorded in `coverage_baseline.txt` at project root):
  - **22.4% first-party coverage** — this is the broad measurement across all modules including those excluded from active test measurement (e.g. `utils/local_ui.py`, `utils/diagnose.py`).
  - Measured modules only (those actively collected by `pyproject.toml [tool.coverage]`): **95.1%** of actively measured statements covered.

* **Coverage config** lives in `pyproject.toml` under `[tool.coverage.run]` — which specifies which files to include/omit from measurement.

---

## 3. Formatting and Linting

The project uses `ruff 0.15.15` to enforce styling, import sorting, and bug-pattern checks.

* **Run Linter Checks**:
  ```powershell
  venv\Scripts\ruff check .
  ```

* **Auto-Fix Style Issues**:
  ```powershell
  venv\Scripts\ruff check . --fix
  ```

* **Format Files**:
  ```powershell
  venv\Scripts\ruff format .
  ```

See `LINTING.md` (if present) or `pyproject.toml` `[tool.ruff]` for the full rule configuration. Run `ruff check .` before any commit — all checks must pass with 0 errors.

---

## 4. Frontend (Dashboard) Tests — Vitest

The React dashboard at `dashboard/` uses **Vitest** + **React Testing Library** + **jsdom** for unit tests. The Python tooling above does not cover the dashboard.

* **Test runner config**: `dashboard/vite.config.js` (`test` block) + `dashboard/src/test/setup.js`.
* **Coverage tool**: `@vitest/coverage-v8` (V8-native, no instrumentation step).

### Run Tests

```powershell
cd dashboard
npm test          # watch mode
npm run test:run  # single-shot, CI mode (Vitest 3.2.6)
npm run test:coverage  # with v8 coverage report
```

> **2026-06-08:** Vitest upgraded from `2.1.9` → `3.2.6` with
> `@vitest/coverage-v8`. Build scripts use `cross-env NODE_OPTIONS=--no-deprecation`
> to suppress Node.js deprecation warnings. The `esbuild: { jsx: 'automatic' }`
> config is now conditional (`command !== 'build'`) to silence the Vite 8
> "Both esbuild and oxc options were set" warning in production builds.

### Current Dashboard Test Inventory

20 test files, **165 passing tests**, 0 failing (verified 2026-06-08). Coverage: **96.04%** statements, **93.48%** branches, **90.9%** functions across `dashboard/src/**`.

> Dashboard tests produce **zero stderr noise** — expected console errors from
> network-failure tests are suppressed via `vi.spyOn(console, 'error').mockImplementation()`
> in individual test files (P8-15).

| Path | Tests | Notes |
| --- | --- | --- |
| `src/lib/api.test.js` | 7 | `apiGet` / `apiSend` / `API_BASE` |
| `src/lib/voiceFile.test.js` | 12 | `validateVoiceFile` accept/reject matrix |
| `src/hooks/useStatusPolling.test.js` | 7 | Polling lifecycle, abort, error path |
| `src/hooks/useScriptUpload.test.js` | 8 | File pick + FormData upload |
| `src/hooks/useVoices.test.js` | 6 | Voice list fetch + refresh |
| `src/hooks/useVoicePlayer.test.js` | 9 | Audio preview playback |
| `src/hooks/useABJob.test.js` | 12 | A/B job status polling (fake timers) |
| `src/components/ToggleRow.test.jsx` | 8 | Accessible toggle button |
| `src/components/Sidebar.test.jsx` | 7 | Nav + active state |
| `src/components/Header.test.jsx` | 5 | Tab title + pause button |
| `src/components/PreviewCanvas.test.jsx` | 5 | Upload card + video player |
| `src/components/SettingsDrawer.test.jsx` | 6 | Settings panel mount/unmount |
| `src/components/ConsultationModal.test.jsx` | 11 | Director-paused prompt reply |
| `src/components/ControlPanel.test.jsx` | 14 | Config load + save + validation |
| `src/components/VoiceManager.test.jsx` | 6 | Upload + gallery integration |
| `src/components/VoiceCard.test.jsx` | 6 | Voice card + play indicator |
| `src/components/UploadZone.test.jsx` | 9 | Drag-drop, file validation, upload |
| `src/components/ABPlayground.test.jsx` | 9 | Prompt input + run + variant panels |
| `src/components/VariantPanel.test.jsx` | 5 | A/B image grid + commit button |
| `src/App.test.jsx` | 11 | Top-level integration: tabs, modal, drawer, pause |

### Pitfalls (Vitest + jsdom + React 19)

1. **JSX needs `esbuild: { jsx: 'automatic' }` in `vite.config.js`** at the top level. Vite 8 also uses `oxc` for the production build (which ignores the esbuild config with a warning — that's fine). Without the esbuild config, vitest tests fail with `ReferenceError: React is not defined` because jsx gets compiled to `React.createElement(...)` instead of `_jsx(...)`.

2. **jsdom's `AbortSignal` is a separate class from Node's global `AbortSignal`.** When production code calls `fetch(url, { signal: controller.signal })`, Node's fetch rejects the signal with `TypeError: Expected signal to be an instance of AbortSignal`. Two options:
   - **Mock components that use `AbortController`** (e.g. `SettingsDrawer` in `App.test.jsx` is mocked away so its child `ControlPanel` never mounts).
   - **In test files, override `global.fetch` with a `vi.fn()` that returns a resolved `{ json: () => Promise.resolve({}) }`** and assert call args directly.

3. **`vi.spyOn(window, 'alert').mockImplementation(() => {})` must live in `beforeEach`, not at module top level.** The global `vi.restoreAllMocks()` in `setup.js#afterEach` resets spies, so a top-level spy is gone after the first test.

4. **Async hooks need `await act(async () => { await hookCall() })`.** A bare `act(() => hookCall())` will miss `setState` calls inside `await` blocks because the microtask boundary flush happens after `act` returns.

5. **Polling hooks with `setInterval` need `vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })`**, NOT `vi.useFakeTimers()`. The default fake timers also fake `setTimeout` and microtasks, which breaks `waitFor`. Only fake the interval; let `setTimeout` and `setImmediate` flow naturally for assertion flushes.

6. **`fireEvent.change(slider, { target: { value: '11' } })` is required for `<input type="range">` updates** — direct `slider.value = '11'; slider.dispatchEvent(new Event('change'))` is a no-op for React's synthetic event system.

7. **`getByRole('button', { name: /Voice Studio/i })` matches by `aria-label`, not visible text.** The dashboard's `Sidebar` uses `aria-label` on icon-only buttons. `getByText('Voice Studio')` will then fail if the same text appears elsewhere on the page (e.g. the page heading) — use `getAllByText` for that case.

8. **The dashboard ESLint config (`dashboard/eslint.config.js`) sets `globals.node` for `**/*.{test,spec}.{js,jsx}` and `src/test/**`** so test files can use `global`, `globalThis`, `process`, etc. without `no-undef` errors.

9. **Vite 8 build warning**: "Both esbuild and oxc options were set. oxc options will be used." This is **expected and harmless** — the esbuild config is required for vitest, and Vite 8's prod build uses oxc anyway.
