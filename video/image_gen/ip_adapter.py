"""ip_adapter.py - IP-Adapter FLUX v2 manager for character face consistency.

Loads the XLabs-AI/flux-ip-adapter-v2 weights onto a Bonsai FLUX pipeline and
provides per-frame injection of a pre-encoded master portrait embedding.

Design notes:
- VRAM is sequential (Bonsai loaded only when generating frames). IP-Adapter
  weights stay resident only while attached to the pipeline.
- Pre-encoding is done via the diffusers `encode_image()` helper exposed on
  pipelines that support IP-Adapter; this avoids re-encoding the master
  portrait per frame.
- If the diffusers API does not expose pre-encoding for FLUX, the manager
  transparently falls back to passing the raw PIL image via the
  `ip_adapter_image=` kwarg per call (slower, simpler).

Cache keys for invalidation live in image_gen.py — this module is stateless
about persistence.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Default IP-Adapter repo. Apache 2.0, commercial-safe.
DEFAULT_IP_ADAPTER_REPO = "XLabs-AI/flux-ip-adapter-v2"
DEFAULT_IP_ADAPTER_WEIGHT = "flux-ip-adapter-v2.safetensors"
DEFAULT_IP_ADAPTER_SUBFOLDER: str | None = None  # XLabs ships at root, not in subfolder


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, or '' if it cannot be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        log.warning(f"[IPAdapter] Could not hash {path}: {e}")
        return ""


class IPAdapterManager:
    """Loads, caches, and unloads the FLUX IP-Adapter for one Bonsai pipeline.

    Thread-safety: the manager uses a lock around load/unload because
    `pipe.load_ip_adapter()` mutates the pipeline. The per-frame `set_scale()`
    call also acquires the lock so it can't race with a concurrent
    attach()/detach() that swaps the pipeline out mid-call.
    """

    def __init__(
        self,
        repo: str = DEFAULT_IP_ADAPTER_REPO,
        weight_name: str = DEFAULT_IP_ADAPTER_WEIGHT,
        subfolder: str | None = DEFAULT_IP_ADAPTER_SUBFOLDER,
    ) -> None:
        self._repo = repo
        self._weight_name = weight_name
        self._subfolder = subfolder
        self._pipe = None  # type: ignore[var-annotated]
        self._attached_pipe_id: int | None = None
        self._lock = threading.Lock()
        # Per-character encoding cache: char_key -> PIL image (raw, lightweight)
        # Pre-encoded embeddings are stored on a per-pipeline basis in
        # _embeddings_cache when the diffusers API supports it; otherwise
        # `_pre_encode()` returns None and the call site falls back to
        # `ip_adapter_image=`.
        self._embeddings_cache: dict[str, object] = {}
        self._image_cache: dict[str, object] = {}  # char_key -> PIL.Image

    def attach(self, pipe) -> None:
        """Attach IP-Adapter weights to a Bonsai pipeline (idempotent)."""
        if pipe is None:
            raise ValueError("[IPAdapter] Cannot attach to a None pipeline")
        pipe_id = id(pipe)
        with self._lock:
            if self._attached_pipe_id == pipe_id and self._pipe is pipe:
                return
            try:
                kwargs: dict = {
                    "repo_id": self._repo,
                    "weight_name": self._weight_name,
                }
                if self._subfolder is not None:
                    kwargs["subfolder"] = self._subfolder
                pipe.load_ip_adapter(**kwargs)
                self._pipe = pipe
                self._attached_pipe_id = pipe_id
                self._embeddings_cache.clear()  # embeddings are pipeline-specific
                self._image_cache.clear()
                log.info(
                    f"[IPAdapter] Attached {self._repo}/{self._weight_name} to pipeline"
                )
            except Exception as e:
                log.exception(f"[IPAdapter] Failed to attach to pipeline: {e}")
                raise

    def detach(self) -> None:
        """Detach IP-Adapter from the current pipeline (idempotent)."""
        with self._lock:
            if self._pipe is None:
                return
            try:
                if hasattr(self._pipe, "unload_ip_adapter"):
                    self._pipe.unload_ip_adapter()
            except Exception as e:
                log.warning(f"[IPAdapter] unload_ip_adapter failed: {e}")
            self._pipe = None
            self._attached_pipe_id = None
            self._embeddings_cache.clear()
            self._image_cache.clear()
            log.info("[IPAdapter] Detached from pipeline")

    def unload(self) -> None:
        """Release all references and free VRAM."""
        self.detach()
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def set_scale(self, scale: float) -> None:
        """Set the IP-Adapter scale (0.0–1.0, default 0.8) on the attached pipe.

        Acquires the manager lock so a concurrent attach()/detach() can't swap
        the pipeline out from under set_ip_adapter_scale().
        """
        with self._lock:
            if self._pipe is None:
                return
            try:
                self._pipe.set_ip_adapter_scale(float(scale))
            except Exception as e:
                log.warning(f"[IPAdapter] set_ip_adapter_scale({scale}) failed: {e}")

    def pre_encode(self, char_key: str, master_path: Path | str) -> object | None:
        """Pre-encode a master portrait for a character.

        Returns:
        - The embedding object if the diffusers API supports it (use
          `call(image=..., ip_adapter_image_embeds=embedding)`).
        - None if pre-encoding is not supported (use `ip_adapter_image=` per call).

        The result is cached by char_key for the lifetime of this manager.
        The raw PIL image is also cached so per-frame fallback works.
        """
        master_path = Path(master_path)
        if not master_path.exists():
            log.warning(f"[IPAdapter] pre_encode skipped — missing file: {master_path}")
            return None
        with self._lock:
            if char_key in self._embeddings_cache:
                return self._embeddings_cache[char_key]
            # Load + cache the raw PIL image (fallback path)
            try:
                from PIL import Image
                img = Image.open(master_path).convert("RGB")
            except Exception as e:
                log.warning(f"[IPAdapter] Could not open {master_path}: {e}")
                return None
            self._image_cache[char_key] = img
            # Try the diffusers pre-encode API. If it exists, cache the result.
            if self._pipe is not None and hasattr(self._pipe, "encode_image"):
                try:
                    # `encode_image` is the method exposed by some IP-Adapter
                    # pipelines; signature varies by version. Try the most
                    # common shape; on failure return None to force fallback.
                    embedding = self._pipe.encode_image(img, num_images_per_prompt=1)
                    self._embeddings_cache[char_key] = embedding
                    return embedding
                except Exception as e:
                    log.debug(
                        f"[IPAdapter] pipe.encode_image not usable for {char_key}: {e} — "
                        "falling back to per-frame ip_adapter_image="
                    )
            return None

    def get_image(self, char_key: str):
        """Return the cached PIL master image for a character, or None."""
        with self._lock:
            return self._image_cache.get(char_key)

    def clear_cache(self) -> None:
        """Clear pre-encoding and image caches (does not detach)."""
        with self._lock:
            self._embeddings_cache.clear()
            self._image_cache.clear()


# Module-level singleton (one manager per process; the manager attaches to
# whichever pipeline _bonsai() loads).
_manager: IPAdapterManager | None = None
_manager_lock = threading.Lock()


def get_ip_adapter() -> IPAdapterManager:
    """Return the process-wide IPAdapterManager singleton."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = IPAdapterManager()
        return _manager


def unload_ip_adapter() -> None:
    """Detach the IP-Adapter and free VRAM. Safe to call when not attached."""
    global _manager  # noqa: PLW0602  (may conditionally assign)
    with _manager_lock:
        if _manager is not None:
            _manager.unload()
