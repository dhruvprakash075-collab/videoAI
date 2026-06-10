# Skylos Security & Code Quality Workflow

## Overview
This document defines a **repeatable workflow** for running Skylos scans on the Video.AI codebase to identify security vulnerabilities, dead code, and quality issues while minimizing false positives.

---

## Scan Commands

### 1. Full Scan (All Checks)
Run this **weekly** or after major changes to analyze the entire codebase, including dependencies and optional checks.

```powershell
uvx skylos . -a \
  --format json \
  --output-file diagnostics/skylos_full_scan.json
```

**Description**:
- Enables **all checks** (security, dead code, quality, dependencies, secrets).
- Outputs results to `diagnostics/skylos_full_scan.json`.

---

### 2. Focused Scan (First-Party Code)
Run this **daily** or during development to focus on **actionable first-party issues** (runtime code, excluding noise).

```powershell
uvx skylos . -a \
  --exclude "**/venv*" --exclude "**/indicf5*" --exclude "**/__pycache__" --exclude "**/*cache*" --exclude "**/node_modules*" --exclude "**/dist*" --exclude "**/build*" --exclude "**/external*" --exclude "**/studio_outputs*" --exclude "**/hf_cache*" --exclude "**/tests*" \
  --format concise \
  --output-file diagnostics/skylos_focused_scan.txt
```

**Description**:
- Excludes **virtual environments, caches, generated outputs, and third-party code**.
- Outputs concise, human-readable results to `diagnostics/skylos_focused_scan.txt`.

---

### 3. Incremental Scan (Changed Files Only)
Run this **on every commit** to analyze only modified files since the last scan.

```powershell
uvx skylos . --incremental \
  --exclude "**/venv* **/indicf5* **/__pycache__ **/*cache* **/external* **/studio_outputs*" \
  --format json \
  --output-file diagnostics/skylos_incremental_scan.json
```

**Description**:
- Scans **only modified files** to save time.
- Excludes **noise directories** to reduce false positives.

---

## Exclusions
To reduce false positives, exclude the following patterns from **all scans**:

| Directory Pattern       | Reason for Exclusion                          |
|-------------------------|-----------------------------------------------|
| `**/venv*`             | Python virtual environments (dev dependencies) |
| `**/indicf5*`          | Third-party dependencies (Indic TTS)         |
| `**/__pycache__`        | Compiled Python bytecode                     |
| `**/*cache*`           | Cache folders (vision, OSV, etc.)            |
| `**/node_modules*`     | Node.js dependencies                         |
| `**/dist*`             | Build outputs (Python wheels)                |
| `**/build*`            | Build directories                            |
| `**/external*`         | Third-party libraries/submodules             |
| `**/studio_outputs*`   | Generated output files (videos, images)      |
| `**/hf_cache*`         | Hugging Face model cache                     |
| `**/tests*`            | Test files (excluding `tests/` may be optional) |

---

## Output Handling
- **Default Output Directory**: `diagnostics/` (gitignored).
- **File Naming**:
  - Full scan: `skylos_full_scan_YYYYMMDD.json`.
  - Focused scan: `skylos_focused_scan.txt`.
  - Incremental scan: `skylos_incremental_scan.json`.

**Note**: The `diagnostics/` folder is intentionally ignored by Git to avoid committing scan artifacts.

---

## Skylos Configuration File
Use the `.skylos.yml` file to **persist settings** across scans:

```yaml
version: "4.24.1"

# Exclude directories and files
exclude:
  - "**/venv*"
  - "**/indicf5*"
  - "**/__pycache__"
  - "**/*cache*"
  - "**/node_modules*"
  - "**/dist*"
  - "**/build*"
  - "**/external*"
  - "**/studio_outputs*"
  - "**/hf_cache*"

# Scan settings
checks:
  dead_code: true
  security: true
  secrets: true
  quality: true
  dependencies: true

# Output settings
output:
  format: json
  file: diagnostics/skylos_scan_results.json

# Performance
max_files: 1000
parallelism: 4
```

Save this file as `.skylos.yml` in the **repo root** (`C:\Video.AI`).

---

## Processing Scan Results
Use the provided PowerShell script to generate a **prioritized action list** from JSON scan results:

```powershell
# Process Skylos JSON output and create a prioritized markdown report
scripts/parse_skylos.ps1 -InputFile diagnostics/skylos_full_scan.json -OutputFile diagnostics/SKYLOS_ACTIONABLE_FIXES.md
```

**Output**: A markdown report (`SKYLOS_ACTIONABLE_FIXES.md`) listing **top-priority fixes** sorted by severity.

---

## CI/CD Integration
Add this **focused scan** to your CI pipeline (e.g., GitHub Actions) before merging PRs:

```yaml
- name: Run Skylos Scan
  run: |
    uvx skylos . -a \
      --exclude "**/venv* **/indicf5* **/__pycache__ **/*cache* **/external* **/studio_outputs*" \
      --format concise \
      --output-file diagnostics/skylos_ci_scan.txt
  shell: pwsh
```

Fail the build if **critical/high-severity issues** are found.

---

## Audit Notes
Track **accepted false positives** or exceptions in `diagnostics/SKYLOS_AUDIT_NOTES.md`:

```markdown
# Skylos Audit Notes

## Accepted False Positives
| File | Issue | Rationale |
|------|-------|-----------|
| `utils/local_ui.py:1107` | SSRF risk | False positive; validated by manual review |
| `tests/temp_paths.py` | Path traversal | Test-only; acceptable risk |

## Exceptions
- Unused functions in `core/segment_runner.py:1001-1122` (dynamic callbacks) are intentionally retained.
- `.pt` files in `audio/f5_worker.py` are validated via other mechanisms.
```