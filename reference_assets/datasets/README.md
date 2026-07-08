# Manga Image-Gen Datasets

These folders are local-only. Git keeps this README and the small generated
layout/prompt configs, but not the downloaded images or model files.

## Current Setup

- Panel structure comes from `config/panel_layouts.roboflow.json`.
- Fallback panel templates live in `config/panel_layouts.json`.
- Face style inspiration comes from `config/anime_face_inspiration.json`.
- Direct face reference images, if present locally, live in
  `reference_assets/face_reference_pools/anime_face/`.
- ComfyUI uses `config/comfyui/workflows/manga_ipadapter_style_api.json`.

No LoRA training is run by this pipeline.

## Manga Panels, Roboflow Universe

Dataset: https://universe.roboflow.com/mdataset/manga-panels

Export as YOLOv8 segmentation or YOLO format, unzip to:

`reference_assets/datasets/manga-panels/`

Then run:

```powershell
python tools/import_manga_panel_dataset.py reference_assets/datasets/manga-panels --output config/panel_layouts.roboflow.json
```

`config/config.yaml` already points `image_gen.panel_composite.layout_file` to
`config/panel_layouts.roboflow.json`.

## Anime Face Dataset, Kaggle

Download/unzip the Kaggle Anime Face Dataset to:

`reference_assets/datasets/anime-face/`

Then run:

```powershell
python tools/import_anime_face_dataset.py reference_assets/datasets/anime-face --output reference_assets/face_reference_pools/anime_face --limit 200
```

`config/config.yaml` already points `image_gen.comfyui.reference_image_dir` to
`reference_assets/face_reference_pools/anime_face`.

## Required IPAdapter Model

The workflow expects this local ComfyUI model:

```text
external/ComfyUI/models/ipadapter/ip-adapter_sd15.safetensors
```

Keep it local. Model files are intentionally ignored by git.
