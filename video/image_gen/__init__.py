"""video.image_gen package — Stable Diffusion image generation via ComfyUI.

Submodules:
    comfyui_client: Low-level HTTP client (prompts, upload, queue, history)
    comfyui_runtime: ComfyUI process lifecycle (start/stop/health/prewarm)
    comfyui_workflow: JSON workflow construction (KSampler, LoRA, IP-Adapter, etc.)
    image_gen: High-level generate_images() orchestrating the above
    ip_adapter: IP-Adapter face/identity conditioning helpers
    panel_compositor: Manga panel layout + speech bubble compositing (optional)

Config (under config["image_gen"]["comfyui"]):
    host: "127.0.0.1"
    port: 8188
    checkpoint: "sd_xl_base_1.0.safetensors"
    steps: 20
    cfg: 7.0
    sampler: "euler_ancestral"
    scheduler: "karras"
    width: 1024
    height: 576
    negative_prompt: "low quality, bad anatomy, ..."

Public API:
    from video.image_gen import generate_images
"""
