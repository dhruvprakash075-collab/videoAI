"""Video.AI ComfyUI custom node suite."""

try:
    from .nodes import comfy_entrypoint
except ModuleNotFoundError as exc:
    if (exc.name or "").split(".")[0] == "comfy_api":
        comfy_entrypoint = None
    else:
        raise

__all__ = ["comfy_entrypoint"]
