# Skylos Dependency Vulnerability Exceptions

This document records known dependency vulnerabilities flagged by Skylos
that are either (a) in vendored/external code not owned by this repo, or
(b) in repo-owned code where the installed version already addresses the issue.

## Format

| File | Package | Version | Vulnerability ID | Disposition | Reason |
|------|---------|---------|-----------------|-------------|--------|

## Repo-Owned Dependencies (installed version safe)

| File | Package | Version | Vulnerability ID | Disposition | Reason |
|------|---------|---------|-----------------|-------------|--------|
| `requirements.txt` | pytest | >=8.0.0 (installed: 9.0.3) | GHSA-6w46-j5rx-g56g | No action needed | Installed version is 9.0.3 which is not affected. Lower bound `>=8.0.0` is a minimum version spec, not a pin to a vulnerable version. |
| `dashboard/package.json` | vite | ^8.0.16 (installed: 8.0.16) | GHSA-fx2h-pf6j-xcff | No action needed | Installed 8.0.16 not affected. `^8.0.16` is a semver range; npm installs 8.0.16. |
| `dashboard/package.json` | vite | ^8.0.16 (installed: 8.0.16) | GHSA-v6wh-96g9-6wx3 | No action needed | Same as above. |

## Vendored External Dependencies (not owned by this repo)

| File | Package | Version | Vulnerability ID | Disposition | Reason |
|------|---------|---------|-----------------|-------------|--------|
| `external/ComfyUI/requirements.txt` | torch | 2.11.0+cu130 | Multiple CVEs | Exception documented | Vendored ComfyUI dependency. Managed by ComfyUI upstream. torch is installed via separate CUDA index; ComfyUI pins a specific version for compatibility. |
| `external/ComfyUI/requirements.txt` | aiohttp | 3.11.8 | Multiple CVEs | Exception documented | Vendored ComfyUI dependency. Installed version is 3.13.5 (safe). ComfyUI pins a minimum version. |
| `external/ComfyUI/requirements.txt` | simpleeval | 1.0.0 | — | Exception documented | Vendored ComfyUI dependency. Managed by ComfyUI upstream. |
| `external/ComfyUI/requirements.txt` | pydantic | 2.0 | — | Exception documented | Vendored ComfyUI dependency. Managed by ComfyUI upstream. |
| `external/ComfyUI/tests-unit/requirements.txt` | pytest | 7.8.0 | — | Exception documented | Vendored ComfyUI test dependency. Managed by ComfyUI project. |
| `external/supertonic_embed/requirements.txt` | torch | 2.11.0+cu128 | Multiple CVEs | Exception documented | Vendored Supertonic embedding dependency. Managed by Supertonic project. |

## Notes

- When Skylos is run, vendored dependencies under `external/` should be excluded
  in the scanner configuration.
- The above entries are documented here for audit trail purposes only.
- Do not modify vendored dependency files unless this repo owns the dependency.
- If a vendored dependency's upstream releases a fix, the vendored copy should
  be updated as part of a routine external dependency refresh.

## CI / Test Dependencies

On CI, `torch` and other heavy packages are never installed. They are
stubbed in `tests/conftest.py:_install_optional_dependency_stubs()`.
See `docs/testing_and_linting.md`.
