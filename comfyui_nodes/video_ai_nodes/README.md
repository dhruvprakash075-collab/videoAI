# Video.AI ComfyUI Node Suite

V3 custom nodes for the Video.AI pipeline using `comfy_api.v0_0_2`.

## Install

Create a junction from this tracked package into ComfyUI:

```powershell
New-Item -ItemType Junction -Path "external\ComfyUI\custom_nodes\video_ai_nodes" -Target "comfyui_nodes\video_ai_nodes"
```

Then install dependencies in the ComfyUI environment:

```powershell
pip install -r comfyui_nodes\video_ai_nodes\requirements.txt
```

## Nodes

- `VideoAI_ProjectConfigLoader`
- `VideoAI_ConfigCheckpointLoader`
- `VideoAI_ConfigKSampler`
- `VideoAI_CharacterPortraitLoader`
- `VideoAI_FreeMemoryBarrier`
- `VideoAI_SmartFaceIDLoraRouter`
- `VideoAI_VideoFrameSaver`

Set `VIDEO_AI_ROOT` or pass `repo_root` if ComfyUI runs outside the repo.
