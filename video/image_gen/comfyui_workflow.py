"""ComfyUI workflow loader and patcher - fills prompt, seed, parameters into workflow JSON."""

import json
import logging
import random
from pathlib import Path

log = logging.getLogger(__name__)


class WorkflowPatcher:
    def __init__(self, workflow_path: Path | None = None):
        self.workflow_path = workflow_path
        self.workflow: dict | None = None
        self._node_cache: dict[str, dict] = {}

        if workflow_path and workflow_path.exists():
            self.load(workflow_path)

    def load(self, workflow_path: Path) -> dict:
        """Load a workflow JSON file."""
        with open(workflow_path, encoding="utf-8") as f:
            self.workflow = json.load(f)
        self.workflow_path = workflow_path
        self._build_node_cache()
        log.info(f"[ComfyUI] Loaded workflow from {workflow_path}")
        return self.workflow

    def _build_node_cache(self) -> None:
        """Build a cache of nodes by class_type for quick lookup."""
        self._node_cache.clear()
        if not self.workflow:
            return

        for node_id, node_data in self.workflow.items():
            if not isinstance(node_data, dict):
                continue
            class_type = node_data.get("class_type", "")
            if class_type:
                if class_type not in self._node_cache:
                    self._node_cache[class_type] = {}
                self._node_cache[class_type][node_id] = node_data

    def find_nodes(self, class_type: str) -> dict[str, dict]:
        """Find all nodes of a given class_type."""
        return self._node_cache.get(class_type, {})

    def find_node(self, class_type: str) -> tuple[str, dict] | None:
        """Find the first node of a given class_type."""
        nodes = self.find_nodes(class_type)
        if nodes:
            node_id = next(iter(nodes))
            return node_id, nodes[node_id]
        return None

    def patch_positive_prompt(self, prompt: str) -> "WorkflowPatcher":
        """Patch the positive prompt into CLIPTextEncode nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "CLIPTextEncode":
                inputs = node.get("inputs", {})
                if "text" in inputs:
                    inputs["text"] = prompt
                    log.debug(f"[ComfyUI] Patched positive prompt into node {node_id}")

        return self

    def patch_negative_prompt(self, prompt: str) -> "WorkflowPatcher":
        """Patch the negative prompt into CLIPTextEncode nodes.

        Typically the second CLIPTextEncode node or one with specific naming.
        """
        if not self.workflow:
            raise ValueError("No workflow loaded")

        encode_nodes = self.find_nodes("CLIPTextEncode")
        if len(encode_nodes) >= 2:
            keys = list(encode_nodes.keys())
            second_node_id = keys[1]
            inputs = encode_nodes[second_node_id].get("inputs", {})
            if "text" in inputs:
                inputs["text"] = prompt
                log.debug(f"[ComfyUI] Patched negative prompt into node {second_node_id}")
        else:
            for node_id, node in encode_nodes.items():
                inputs = node.get("inputs", {})
                if "text" in inputs and inputs.get("text") in ("", "negative prompt"):
                    inputs["text"] = prompt
                    log.debug(f"[ComfyUI] Patched negative prompt into node {node_id}")
                    break

        return self

    def patch_seed(self, seed: int) -> "WorkflowPatcher":
        """Patch seed into KSampler nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "KSampler":
                inputs = node.get("inputs", {})
                if "seed" in inputs:
                    inputs["seed"] = seed
                    log.debug(f"[ComfyUI] Patched seed {seed} into node {node_id}")

        return self

    def patch_width_height(self, width: int, height: int) -> "WorkflowPatcher":
        """Patch width and height into EmptyLatentImage nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "EmptyLatentImage":
                inputs = node.get("inputs", {})
                if "width" in inputs:
                    inputs["width"] = width
                if "height" in inputs:
                    inputs["height"] = height
                log.debug(f"[ComfyUI] Patched {width}x{height} into node {node_id}")

        return self

    def patch_steps(self, steps: int) -> "WorkflowPatcher":
        """Patch steps into KSampler nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "KSampler":
                inputs = node.get("inputs", {})
                if "steps" in inputs:
                    inputs["steps"] = steps
                    log.debug(f"[ComfyUI] Patched steps {steps} into node {node_id}")

        return self

    def patch_cfg(self, cfg: float) -> "WorkflowPatcher":
        """Patch CFG (cfg_scale) into KSampler nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "KSampler":
                inputs = node.get("inputs", {})
                if "cfg" in inputs:
                    inputs["cfg"] = cfg
                    log.debug(f"[ComfyUI] Patched cfg {cfg} into node {node_id}")

        return self

    def patch_sampler(self, sampler_name: str) -> "WorkflowPatcher":
        """Patch sampler name into KSampler nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "KSampler":
                inputs = node.get("inputs", {})
                if "sampler_name" in inputs:
                    inputs["sampler_name"] = sampler_name
                    log.debug(f"[ComfyUI] Patched sampler {sampler_name} into node {node_id}")

        return self

    def patch_scheduler(self, scheduler: str) -> "WorkflowPatcher":
        """Patch scheduler into KSampler nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "KSampler":
                inputs = node.get("inputs", {})
                if "scheduler" in inputs:
                    inputs["scheduler"] = scheduler
                    log.debug(f"[ComfyUI] Patched scheduler {scheduler} into node {node_id}")

        return self

    def patch_checkpoint(self, checkpoint_path: str) -> "WorkflowPatcher":
        """Patch checkpoint model path into CheckpointLoaderSimple nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "CheckpointLoaderSimple":
                inputs = node.get("inputs", {})
                if "ckpt_name" in inputs:
                    inputs["ckpt_name"] = checkpoint_path
                    log.debug(f"[ComfyUI] Patched checkpoint {checkpoint_path} into node {node_id}")

        return self

    def patch_vae(self, vae_name: str) -> "WorkflowPatcher":
        """Patch VAE name into VAELoader nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "VAELoader":
                inputs = node.get("inputs", {})
                if "vae_name" in inputs:
                    inputs["vae_name"] = vae_name
                    log.debug(f"[ComfyUI] Patched VAE {vae_name} into node {node_id}")

        return self

    def patch_filename_prefix(self, prefix: str) -> "WorkflowPatcher":
        """Patch filename_prefix into SaveImage nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "SaveImage":
                inputs = node.get("inputs", {})
                if "filename_prefix" in inputs:
                    inputs["filename_prefix"] = prefix
                    log.debug(f"[ComfyUI] Patched filename_prefix '{prefix}' into node {node_id}")

        return self

    def patch_denoise(self, denoise: float) -> "WorkflowPatcher":
        """Patch denoise into KSampler nodes."""
        if not self.workflow:
            raise ValueError("No workflow loaded")

        for node_id, node in self.workflow.items():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "KSampler":
                inputs = node.get("inputs", {})
                if "denoise" in inputs:
                    inputs["denoise"] = denoise
                    log.debug(f"[ComfyUI] Patched denoise {denoise} into node {node_id}")

        return self

    def patch_all(
        self,
        prompt: str,
        negative_prompt: str = "",
        seed: int | None = None,
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        cfg: float = 7.0,
        sampler_name: str = "euler",
        scheduler: str = "normal",
        checkpoint: str = "",
        filename_prefix: str = "",
    ) -> "WorkflowPatcher":
        """Patch all parameters at once."""
        if seed is None:
            seed = random.randint(0, 2**31 - 1)

        (self
            .patch_positive_prompt(prompt)
            .patch_negative_prompt(negative_prompt)
            .patch_seed(seed)
            .patch_width_height(width, height)
            .patch_steps(steps)
            .patch_cfg(cfg)
            .patch_denoise(1.0)
            .patch_sampler(sampler_name)
            .patch_scheduler(scheduler))

        if checkpoint:
            self.patch_checkpoint(checkpoint)

        if filename_prefix:
            self.patch_filename_prefix(filename_prefix)

        log.info(f"[ComfyUI] Patched workflow: {width}x{height}, {steps} steps, cfg {cfg}, seed {seed}")
        return self

    def get_workflow(self) -> dict:
        """Get the patched workflow dict."""
        if not self.workflow:
            raise ValueError("No workflow loaded")
        return self.workflow


def load_workflow(workflow_path: Path) -> WorkflowPatcher:
    """Factory function to load a workflow."""
    return WorkflowPatcher(workflow_path)


def create_default_workflow(
    prompt: str,
    negative_prompt: str = "",
    seed: int | None = None,
    width: int = 1024,
    height: int = 1024,
    steps: int = 20,
    cfg: float = 7.0,
    sampler_name: str = "euler",
    scheduler: str = "normal",
    checkpoint: str = "DreamShaper_8.safetensors",
    filename_prefix: str = "ComfyUI",
) -> dict:
    """Create a minimal default workflow JSON for ComfyUI.

    This creates a simple txt2img workflow that can be used if no custom
    workflow is provided.
    """
    if seed is None:
        seed = random.randint(0, 2**31 - 1)

    workflow = {
        "1": {
            "inputs": {"text": prompt, "clip": ["3", 1]},
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Positive Prompt"}
        },
        "2": {
            "inputs": {"text": negative_prompt, "clip": ["3", 1]},
            "class_type": "CLIPTextEncode",
            "_meta": {"title": "Negative Prompt"}
        },
        "3": {
            "inputs": {"ckpt_name": checkpoint},
            "class_type": "CheckpointLoaderSimple",
            "_meta": {"title": "Load Checkpoint"}
        },
        "4": {
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1
            },
            "class_type": "EmptyLatentImage",
            "_meta": {"title": "Empty Latent Image"}
        },
        "5": {
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": 1.0,
                "positive": ["1", 0],
                "negative": ["2", 0],
                "latent_image": ["4", 0],
                "model": ["3", 0]
            },
            "class_type": "KSampler",
            "_meta": {"title": "KSampler"}
        },
        "6": {
            "inputs": {"samples": ["5", 0], "vae": ["3", 2]},
            "class_type": "VAEDecode",
            "_meta": {"title": "VAE Decode"}
        },
        "7": {
            "inputs": {"filename_prefix": filename_prefix, "images": ["6", 0]},
            "class_type": "SaveImage",
            "_meta": {"title": "Save Image"}
        }
    }

    return workflow
