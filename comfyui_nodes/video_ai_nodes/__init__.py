"""Video.AI ComfyUI custom node suite."""

try:
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, comfy_entrypoint
except ModuleNotFoundError as exc:
    if (exc.name or "").split(".")[0] == "comfy_api":
        NODE_CLASS_MAPPINGS = {}
        NODE_DISPLAY_NAME_MAPPINGS = {}
        comfy_entrypoint = None
    else:
        raise

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "comfy_entrypoint"]
