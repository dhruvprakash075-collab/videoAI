# Phase 6 Qwen Hardware Acceptance Fix Plan

## Goal

Finish every known Phase 6 issue and produce one genuine, uncached Qwen character-insertion edit through ComfyUI. The run passes only if it uses no model download, stays below 5000 MiB observed GPU memory, leaves the host stable, produces a technically valid and visually acceptable image, and starts with no relevant ComfyUI import or validation error.

## Non-negotiable rules

- Never use the character portrait as both background and reference.
- Never count `cached`, `skipped`, or fallback output as hardware acceptance; status must be `edited`.
- Never overwrite the source portrait or background.
- No model or LoRA download is permitted. Enforce this with process-level outbound blocking, offline environment variables, and before/after model-directory manifests.
- A package-only download is allowed solely for the exact matching Nunchaku wheel in Phase 3. Network access is blocked before ComfyUI starts.
- Abort and mark the run failed if GPU memory reaches 5000 MiB, system committed memory reaches 90%, available RAM falls below 1 GiB, the output contains NaN/corruption, or any model file changes.
- All temporary firewall rules, processes, monitors, and staged inputs are removed in a `finally` cleanup path, including failed runs.

## Phase 0: Freeze and inventory the environment

Status: in_progress

- Record Git status without touching unrelated user changes.
- Record Python, Torch/CUDA, ComfyUI, ComfyUI-nunchaku, Nunchaku, NVIDIA driver, GPU, total VRAM, total/free RAM, commit limit/headroom, pagefile, and free disk.
- Record SHA-256, size, and safetensors-header readability for the diffusion model, text encoder, and VAE.
- Record the PID of the existing ComfyUI instance; stop only that instance before repairs.
- Create a dedicated acceptance evidence directory outside model directories for logs, manifests, metrics, and output.

Exit gate: all three model assets exist, pass safetensors metadata reads, and have stable hashes; the output volume has at least 10 GiB free.

## Phase 1: Make the workflow schema and VRAM profile valid

Status: in_progress

- Add `resolution_steps: 1` to node `3` (`ImageScaleToTotalPixels`) in `config/comfyui/workflows/qwen_image_edit_api.json`.
- Reduce node `6` `num_blocks_on_gpu` from `20` to `1`; retain CPU offload and disabled pinned memory.
- Extend `tests/test_qwen_repose.py` to assert the required scale input and hardware-safe loader settings in the committed template.
- Add a schema check that compares every workflow node's required inputs against live `/object_info` before a prompt is queued.
- Run `external/ComfyUI/.venv/Scripts/python.exe -m pytest tests/test_qwen_repose.py -q`.

Exit gate: focused tests pass and the offline schema check reports zero missing classes, inputs, models, encoders, or VAE files. No validation-only request may accidentally queue inference.

## Phase 2: Preserve actionable ComfyUI errors

Status: complete

- Fix `video/image_gen/comfyui_client.py::_request` so a parsed `ComfyUIError` is raised outside the JSON-decoding `try` and is not replaced by `Bad Request`.
- Include both ComfyUI `error` and `node_errors` fields in the message when present.
- Add one focused mocked-HTTP-400 test that proves node/input details survive.
- Run the focused ComfyUI client tests plus `tests/test_qwen_repose.py`.

Exit gate: a deliberately invalid in-memory response reports its exact node and missing input; all focused tests pass.

## Phase 3: Repair the Nunchaku package mismatch

Status: in_progress

- Verify the environment is CPython 3.11, Torch 2.9, and CUDA 12.8 before selecting a wheel.
- Use the official Nunchaku GitHub release asset `nunchaku-1.2.1+cu12.8torch2.9-cp311-cp311-win_amd64.whl`, matching `ComfyUI-nunchaku 1.2.1`.
- Download only that wheel to the evidence directory, verify its GitHub API digest when supplied, and install it with `--no-deps` so no dependency or model download can cascade.
- Restart ComfyUI once and verify both `NunchakuQwenImageDiTLoader` and `NunchakuZImageDiTLoader` register without `convert_fp16` errors.
- If the exact platform tags do not match or the official digest cannot be verified, abort rather than substituting another build.

Exit gate: Nunchaku reports `1.2.1`, both loaders register, and the startup log has no Nunchaku import traceback.

## Phase 4: Make configuration, identity, and test input reproducible

Status: in_progress

- Set `image_gen.qwen_edit.model_path` in `config/config.yaml` to `external/ComfyUI/models/diffusion_models/qwen_image_edit_2509_int4.safetensors` while keeping Qwen globally disabled.
- Validate `studio_projects/myproject/characters/hero/master.png` with Pillow, compute its SHA-256, and store the portrait path/hash through `ProjectStore` rather than hand-editing incomplete metadata.
- Abort if the project character cannot be loaded back with the same absolute path and hash; never invoke portrait generation during acceptance.
- Generate a deterministic 768x768 acceptance background with Pillow containing a simple room, floor line, and empty center placement area. This uses no generative model and must have a different SHA-256 from the portrait.
- Use a fixed prompt requiring the saved hero to stand full-body in the empty center while preserving face, outfit, body shape, and background geometry.
- Update configuration documentation with the local model path, offline rule, one-block offload profile, and acceptance command.

Exit gate: portrait and background decode as RGB/RGBA images, differ by hash, project identity round-trips correctly, and `preflight_qwen_edit` returns no issue.

## Phase 5: Prove host resource headroom before inference

Status: pending

- Stop ComfyUI and nonessential workspace GPU helpers, then remeasure resources.
- Require at least 8 GiB available physical RAM before launch and enough Windows commit headroom for the 19.71 GiB of local model assets plus 4 GiB safety margin. If either gate fails, stop and free resources or increase the pagefile before retrying.
- Require at least 10 GiB free disk and a writable output/evidence directory.
- Confirm `nvidia-smi` reports no compute process and less than 256 MiB baseline GPU use.
- Confirm the acceptance cache key does not exist; use a unique acceptance output name and fixed seed. Delete only stale Phase 6 acceptance cache/output, never general project cache.

Exit gate: every numeric resource threshold passes. Current observed 2.34 GiB free RAM does not pass, so inference must not begin until memory is freed.

## Phase 6: Enforce offline and memory-safe ComfyUI startup

Status: pending

- Add a temporary Windows outbound firewall block for the exact ComfyUI Python executable, scoped to internet addresses.
- Set `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `DIFFUSERS_OFFLINE=1`, and disable telemetry for the ComfyUI process.
- Prove the same executable cannot reach a public HTTPS URL. Abort if the probe succeeds.
- Start ComfyUI with `--listen 127.0.0.1 --port 8188 --reserve-vram 1.25 --lowvram --cpu-vae --disable-smart-memory --cache-none --disable-pinned-memory --disable-api-nodes --disable-auto-launch`.
- Prove localhost API access still works after outbound blocking.
- Verify required node registration and scan the complete startup log for import errors, missing models, attempted downloads, or CUDA errors.

Exit gate: public network access fails, localhost succeeds, all required Qwen nodes register, model hashes remain unchanged, and idle GPU use remains below 256 MiB.

## Phase 7: Run exactly one monitored, uncached edit

Status: pending

- Start `nvidia-smi` sampling at 100 ms and Windows process/system memory sampling before queueing anything.
- Start a watchdog that terminates the owned ComfyUI process at 5000 MiB GPU memory, 90% committed memory, or less than 1 GiB available RAM. Any watchdog termination is a failed acceptance, not a retry-pass.
- Snapshot all model/LoRA directories before queueing and watch them for creates, writes, or renames.
- Queue exactly one edit using the deterministic room background as base and `hero/master.png` as character reference.
- Require returned status `edited`; reject `cached`, `skipped`, `failed`, fallback paths, or a result pointing to either input.
- Do not retry inference automatically. Diagnose any failure and return to the relevant earlier phase.

Exit gate: the prompt completes once, no watchdog fires, no model path changes, no network attempt succeeds, and peak sampled GPU memory is below 5000 MiB.

## Phase 8: Validate technical and visual output

Status: pending

- Confirm the output exists, is a decodable PNG, has expected non-zero dimensions, contains no all-transparent/all-uniform frame, and differs by SHA-256 from both inputs.
- Use pixel-difference checks to confirm a meaningful changed region exists while background structure outside the placement area remains recognizable.
- Visually inspect the output at original resolution. Require: one visible hero in the requested location; recognizable identity, face, outfit, age, and body shape from the reference; preserved room geometry; no duplicate body, missing major limb, severe facial corruption, text/watermark, or catastrophic frame corruption.
- Record input/output thumbnails, hashes, dimensions, prompt, seed, elapsed time, peak GPU memory, minimum available RAM, maximum committed-memory percentage, and all component versions.

Exit gate: every technical check and every visual criterion passes. A technically valid but visibly bad edit fails acceptance.

## Phase 9: Regression, cleanup, and final closure

Status: pending

- Run focused Qwen workflow and ComfyUI client tests again and report exact pass counts.
- Confirm final logs contain no workflow validation, Nunchaku import, CUDA OOM, NaN, download, watchdog, or fallback event.
- Remove the firewall rule, offline acceptance process, monitors, temporary uploaded inputs, downloaded wheel, failed artifacts, and temporary background after preserving the evidence report and successful output.
- Confirm no owned ComfyUI/GPU process remains and GPU memory returns below 256 MiB.
- Confirm model hashes equal the Phase 0 manifest and Git status contains only intended source/config/test/documentation changes plus the chosen acceptance evidence.
- Mark Phase 6 complete only after every exit gate above passes.

Exit gate: cleanup is verified, tests pass, evidence is complete, and there is no known unresolved Phase 6 or startup issue.

## Assumption-closure matrix

| Former assumption | Closed by |
|---|---|
| Models exist and are intact | SHA-256 manifest plus safetensors metadata reads |
| Correct custom-node package is installed | Exact official 1.2.1 wheel, tag/digest checks, live registration |
| Workflow matches current ComfyUI | Required-input comparison against live `/object_info` |
| Character metadata is usable | `ProjectStore` path/hash round trip |
| Edit uses distinct inputs | Deterministic background and unequal input hashes |
| Hardware result is real | Unique uncached run requiring status `edited` |
| No model is downloaded | Firewall, offline variables, failed public probe, directory manifests |
| GPU stays below limit | One GPU block, VRAM reservation, 100 ms monitor, kill watchdog |
| RAM is sufficient | Preflight RAM/commit gates and runtime watchdog |
| Disk is sufficient | Explicit free-space and writability gates |
| Output is technically valid | Decode, dimensions, transparency/uniformity, and hash checks |
| Output is actually a good edit | Original-resolution visual acceptance criteria |
| Errors remain hidden | HTTP body/node-error preservation test |
| Cleanup happens after failure | Owned-process tracking and mandatory `finally` cleanup |

## Known errors

| Error | Cause | Resolution in plan |
|---|---|---|
| Missing `resolution_steps` | Workflow predates current ComfyUI schema | Phase 1 |
| Generic `HTTP 400` | Client catches its own parsed error | Phase 2 |
| `convert_fp16` import error | Nunchaku 1.0.0 mismatches custom node 1.2.1 | Phase 3 |
| Missing character reference | Project metadata lacked a verified portrait identity | Phase 4 |
| Same image would serve as both inputs | No scene frame exists in the project | Phase 4 deterministic background |
| Only 2.34 GiB RAM currently free | Host does not meet safe launch gate | Phase 5 |
| Prior 73 MiB peak was not inference | Prompt failed validation before model load | Phases 6-8 |
