# Layered v3 Setup Guide

## Overview

Layered v3 is a multi-pass image generation mode that separates character identity from background generation. This produces more consistent character appearance across frames while allowing cinematic backgrounds.

**When to use**: Projects with recurring characters that need consistent visual identity across a long video (e.g., story-driven content with named characters that appear throughout).

**When NOT to use**: One-off videos, environment-only scenes, or projects where characters are disposable/ephemeral.

---

## Architecture

Layered v3 generates images in passes:

1. **Character Sheet** (pre-production): Generate character identity assets (face ref, full body ref, pose variants)
2. **Background Pass**: Generate background image (no character)
3. **Character Pass**: Generate character placed in scene using stored identity reference
4. **Composite Pass**: Composite character into background + refine

---

## Required ComfyUI Custom Nodes

Layered v3 requires three ComfyUI community node packs. These are **not** installed by default.

### 1. IPAdapter Plus (required for character identity referencing)
- **Repo**: [cubiq/ComfyUI_IPAdapter_plus](https://github.com/cubiq/ComfyUI_IPAdapter_plus)
- **Purpose**: Reference character identity images when generating new frames
- **Installation**:
  ```powershell
  cd C:\Video.AI\external\ComfyUI\custom_nodes
  git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus.git
  ```
- **Required models**:
  - `ip-adapter-plus_sd15.bin` (，放在 `ComfyUI/models/ipadapter/`)
  - `ip-adapter-plus-fullface_sd15.bin` (for face-only reference, same directory)

### 2. Impact Pack (required for segmentation/masking)
- **Repo**: [ltdrdata/ComfyUI-Impact-Pack](https://github.com/ltdrdata/ComfyUI-Impact-Pack)
- **Purpose**: Segment/composite workflows - isolate character from generated image for recombination
- **Installation**:
  ```powershell
  cd C:\Video.AI\external\ComfyUI\custom_nodes
  git clone https://github.com/ltdrdata/ComfyUI-Impact-Pack.git
  ```
- **Note**: This pack has many nodes; at minimum the `Segmentation` and `FaceDetailer` sub-packs are needed. Install the full pack for safety.

### 3. ControlNet Aux (required for composition guidance)
- **Repo**: [Fannovel16/comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux)
- **Purpose**: Preprocessors for depth/normal maps used in background-to-character alignment
- **Installation**:
  ```powershell
  cd C:\Video.AI\external\ComfyUI\custom_nodes
  git clone https://github.com/Fannovel16/comfyui_controlnet_aux.git
  ```

---

## Required Checkpoints

Place in `C:\Video.AI\external\ComfyUI\models\checkpoints\`:

| Checkpoint | Purpose | Notes |
|---|---|---|
| `DreamShaper_8.safetensors` | Character + composite refine | Already in use for one-pass mode |
| `BriaAI/BackgroundRemover` (or equivalent) | Background isolation | Optional; used in composite workflow |

---

## Workflow Files

Layered v3 requires four ComfyUI workflow JSON files. These define the node graphs for each pass.

**Default location**: `C:\Video.AI\config\comfyui\workflows\`

| Workflow | Purpose | Input | Output |
|---|---|---|---|
| `layered_character_sheet.json` | Generate character identity assets | Character prompt + seed | `character_sheet.png`, `face_ref.png`, `body_ref.png` |
| `layered_background.json` | Generate background | Scene prompt + seed | `background.png` |
| `layered_character_pose.json` | Generate character in scene | Scene prompt + identity ref + pose ref | `character.png` (isolated) |
| `layered_composite_refine.json` | Composite + refine | Background + character + mask | Final `scene_XX.png` |

### Workflow Contract

Each workflow must:
1. Accept prompts via `prompt` (positive) and `negative_prompt` (negative) inputs
2. Support `seed` integer input
3. Support `width` and `height` inputs
4. Support `checkpoint` override input
5. Output images to the directory specified by `output_dir` / `filename_prefix`
6. Return images via the standard ComfyUI `image_output` node

### Workflow Paths

Set paths in `config.yaml`:

```yaml
image_gen:
  composition_mode: "layered_v3"
  layered_v3:
    workflows:
      character_sheet: "C:\\Video.AI\\config\\comfyui\\workflows\\layered_character_sheet.json"
      background: "C:\\Video.AI\\config\\comfyui\\workflows\\layered_background.json"
      character_pose: "C:\\Video.AI\\config\\comfyui\\workflows\\layered_character_pose.json"
      composite_refine: "C:\\Video.AI\\config\\comfyui\\workflows\\layered_composite_refine.json"
```

If a workflow path is empty or the file does not exist, preflight will report a failure with the exact missing path.

---

## Configuration

In `config.yaml`, enable layered mode:

```yaml
image_gen:
  backend: "comfyui"           # Required: only ComfyUI supports layered mode
  composition_mode: "layered_v3"   # Change from default "one_pass"
  fallback_mode: "one_pass"   # "one_pass" falls back to current ComfyUI workflow if preflight fails

  layered_v3:
    approval_mode: "hybrid"    # "hybrid" | "manual" | "auto"
    character_threshold: 0.3  # Weight below this → bg-only frame
    closeup_threshold: 0.8    # Weight above this → closeup composition
    max_characters: 2         # Max 2 characters per frame (v1)
    fallback_mode: "one_pass"  # Only applies if preflight fails AND this is set

    workflows:
      character_sheet: "C:\\Video.AI\\config\\comfyui\\workflows\\layered_character_sheet.json"
      background: "C:\\Video.AI\\config\\comfyui\\workflows\\layered_background.json"
      character_pose: "C:\\Video.AI\\config\\comfyui\\workflows\\layered_character_pose.json"
      composite_refine: "C:\\Video.AI\\config\\comfyui\\workflows\\layered_composite_refine.json"
```

### Approval Modes

| Mode | Behavior |
|---|---|
| `auto` | Best character sheet candidate auto-approved (no human input) |
| `hybrid` | CLI/job mode: auto-approves; Dashboard mode: shows approve/reject UI |
| `manual` | Always requires human approval before production render |

---

## ComfyUI Startup for Layered Mode

Layered v3 requires all four workflows to be loadable in ComfyUI. After installing the custom nodes, verify by:

1. Start ComfyUI: `cd C:\Video.AI\external\ComfyUI && python main.py`
2. Open the web UI at `http://127.0.0.1:8188`
3. Load each workflow file from `Config → Workflows` and verify no missing node errors
4. If nodes are red/missing: restart ComfyUI after installing custom nodes

---

## Preflight Failures

When preflight detects missing layered v3 dependencies, it reports failures like:

```
[FAIL] layered_v3_nodes    ComfyUI custom nodes missing: IPAdapter Plus, Impact Pack, ControlNet Aux
[FAIL] layered_v3_workflows Character sheet workflow not found: C:\Video.AI\config\comfyui\workflows\layered_character_sheet.json
[FAIL] layered_v3_checkpoints Model ip-adapter-plus_sd15.bin not found in C:\Video.AI\external\ComfyUI\models\ipadapter\
```

**Fix each failure**:
- For missing nodes: Install via git clone as shown above, restart ComfyUI
- For missing workflows: Create or acquire the workflow JSON files
- For missing models: Download and place in the indicated directory

If `fallback_mode: "one_pass"` is configured, preflight failures do NOT block the run — instead the pipeline falls back to current ComfyUI one-pass generation.

---

## Dashboard Settings

Layered v3 settings appear in the dashboard under **Settings → Layered Generation**. Settings include:

- **Composition Mode**: Switch between `one_pass` and `layered_v3`
- **Approval Mode**: `auto` / `hybrid` / `manual`
- **Thresholds**: character threshold, closeup threshold
- **Workflow Paths**: Paths to each workflow JSON
- **Fallback Mode**: What to do if preflight fails

---

## Cache Invalidation

Layered v3 generates an **identity hash** from approved character assets. When this hash changes (character re-approved), all cached frames referencing that character are automatically invalidated and regenerated.

This ensures character appearance stays consistent even if the character sheet is updated mid-project.

---

## Character Asset Storage

Character identity assets are stored at:

```
studio_projects/
  {project}/
    characters/
      {char_key}/
        assets.json         # Full metadata (pose variants, timestamps, extra data)
        character_sheet.png
        face_reference.png
        full_body_reference.png
        pose_variant_01.png
        pose_variant_02.png
        ...
```

Short path references are also stored in `project.json` for fast access during frame generation.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `layered_v3_nodes FAIL` even after git clone | ComfyUI not restarted | Restart ComfyUI after installing custom nodes |
| Character looks different in different frames | Identity hash changed | Re-approve character sheet; check `identity_hash` in project.json |
| Background looks fine but character is wrong | Character identity ref path stale | Check `face_reference_path` in project.json; regenerate if needed |
| Preflight passes but frames look bad | Workflow mismatch | Verify workflow outputs match the contract (see Workflow Contract above) |
| `fallback_mode` ignored | `backend` is not `comfyui` | Layered v3 only works with `backend: comfyui` |