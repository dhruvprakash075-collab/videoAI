"""ComfyUI HTTP API client for image generation."""

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from utils.circuit_breaker import BreakerOpen, CircuitBreakerRegistry
from utils.errors import ComfyUIError

log = logging.getLogger(__name__)


class ComfyUIClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session_id: str | None = None

    def _request(self, endpoint: str, method: str = "GET", data: dict | None = None) -> dict:
        """Make a request to the ComfyUI API."""
        cb = CircuitBreakerRegistry.get("comfyui", fails=3, cooldown=30.0)
        if not cb.allow_request():
            log.warning("[ComfyUIClient] Circuit breaker OPEN for ComfyUI — failing fast")
            raise BreakerOpen("comfyui", cb.cooldown_remaining_s())

        url = f"{self.base_url}{endpoint}"
        headers = {"Content-Type": "application/json"}

        req = urllib.request.Request(
            url,
            headers=headers,
            method=method,
        )

        if data is not None:
            body = json.dumps(data).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers=headers,
                method=method,
            )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                res = json.loads(response.read().decode("utf-8"))
                cb.record_success()
                return res
        except urllib.error.HTTPError as e:
            cb.record_failure()
            try:
                error_body = json.loads(e.read().decode("utf-8"))
                raise ComfyUIError(f"HTTP {e.code}: {error_body.get('error', e.reason)}") from e
            except Exception:
                raise ComfyUIError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            cb.record_failure()
            raise ComfyUIError(f"Connection failed: {e.reason}") from e
        except Exception as e:
            cb.record_failure()
            raise ComfyUIError(f"Request failed: {e}") from e

    def get_system_stats(self) -> dict:
        """Get ComfyUI system stats (memory, device info)."""
        return self._request("/system_stats")

    def get_history(self, prompt_id: str) -> dict:
        """Get execution history for a prompt."""
        return self._request(f"/history/{prompt_id}")

    def get_queue(self) -> dict:
        """Get current queue status."""
        return self._request("/queue")

    def upload_image(self, image_path: Path, name: str | None = None) -> dict:
        """Upload an image to ComfyUI."""
        if name is None:
            name = image_path.name

        import multipart  # noqa: F401

        raise NotImplementedError("upload_image requires multipart form encoding")

    def get_view(self, filename: str, subfolder: str = "", image_type: str = "output") -> bytes:
        """Get an image from ComfyUI."""
        cb = CircuitBreakerRegistry.get("comfyui", fails=3, cooldown=30.0)
        if not cb.allow_request():
            log.warning("[ComfyUIClient] Circuit breaker OPEN for ComfyUI — failing fast")
            raise BreakerOpen("comfyui", cb.cooldown_remaining_s())

        url = f"{self.base_url}/view"
        params = f"?filename={filename}&type={image_type}"
        if subfolder:
            params += f"&subfolder={subfolder}"

        req = urllib.request.Request(url + params)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                content = response.read()
                cb.record_success()
                return content
        except urllib.error.HTTPError as e:
            cb.record_failure()
            try:
                error_body = json.loads(e.read().decode("utf-8"))
                raise ComfyUIError(f"HTTP {e.code}: {error_body.get('error', e.reason)}") from e
            except Exception:
                raise ComfyUIError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            cb.record_failure()
            raise ComfyUIError(f"Connection failed: {e.reason}") from e
        except Exception as e:
            cb.record_failure()
            raise ComfyUIError(f"Failed to fetch image view: {e}") from e

    def free_memory(self) -> dict:
        """Trigger garbage collection to free GPU memory."""
        return self._request("/free", method="POST")

    def queue_prompt(self, prompt: dict, prompt_id: str | None = None) -> dict:
        """Queue a prompt for execution."""
        if prompt_id is None:
            prompt_id = f"prompt_{int(time.time() * 1000)}"

        data = {
            "prompt": prompt,
            "prompt_id": prompt_id,
            "extra_data": {},
        }

        return self._request("/prompt", method="POST", data=data)

    def get_prompt_status(self, prompt_id: str) -> dict | None:
        """Get status of a prompt from history."""
        try:
            history = self.get_history(prompt_id)
            return history.get(prompt_id)
        except ComfyUIError:
            return None

    def wait_for_completion(
        self,
        prompt_id: str,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
    ) -> dict:
        """Wait for a prompt to complete, return the result."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            status = self.get_prompt_status(prompt_id)

            if status is None:
                time.sleep(poll_interval)
                continue

            status_obj = status.get("status", {})
            completed = False
            error_details = []

            if isinstance(status_obj, dict):
                if status_obj.get("completed") is True:
                    completed = True
                else:
                    # Inspect execution messages for error details
                    messages = status_obj.get("messages", [])
                    for msg in messages:
                        if isinstance(msg, list) and len(msg) >= 2:
                            msg_type, msg_val = msg[0], msg[1]
                            if msg_type == "ExecutionError" and isinstance(msg_val, dict):
                                node_id = msg_val.get("node_id", "unknown")
                                node_type = msg_val.get("node_type", "unknown")
                                exc_msg = msg_val.get("exception_message", "Unknown error")
                                error_details.append(f"Node {node_id} ({node_type}): {exc_msg}")
                    if not error_details:
                        error_details.append(status_obj.get("status_str", "Unknown error"))
            elif isinstance(status_obj, str):
                if status_obj == "completed":
                    completed = True
                else:
                    error_details.append(status.get("status_str", "Unknown error"))
            else:
                error_details.append("Unknown status format")

            if completed:
                return status
            else:
                err_msg = "; ".join(error_details)
                raise ComfyUIError(f"Prompt failed: {err_msg}")

            time.sleep(poll_interval)

        raise ComfyUIError(f"Timeout waiting for prompt {prompt_id} after {timeout}s")

    def generate_image(
        self,
        prompt: dict,
        output_dir: Path,
        filename_prefix: str = "comfy",
        poll_interval: float = 1.0,
        timeout: float = 300.0,
    ) -> list[Path]:
        """Generate an image and save to output directory.

        Returns list of saved PNG paths.
        """
        result = self.queue_prompt(prompt)
        prompt_id = result.get("prompt_id")

        if not prompt_id:
            raise ComfyUIError("No prompt_id returned from queue_prompt")

        log.info(f"[ComfyUI] Queued prompt {prompt_id}")

        status = self.wait_for_completion(prompt_id, poll_interval, timeout)

        output_images = []
        outputs = status.get("outputs", {})

        for node_output in outputs.values():
            images = node_output.get("images", [])
            for img_info in images:
                img_filename = img_info.get("filename")
                img_subfolder = img_info.get("subfolder", "")
                img_type = img_info.get("type", "output")

                if img_filename:
                    img_data = self.get_view(img_filename, img_subfolder, img_type)
                    output_path = output_dir / img_filename
                    output_path.write_bytes(img_data)
                    output_images.append(output_path)
                    log.info(f"[ComfyUI] Saved {output_path}")

        if not output_images:
            log.warning(f"[ComfyUI] No images returned for prompt {prompt_id}")

        return output_images

    def interrupt(self) -> dict:
        """Interrupt current execution."""
        return self._request("/interrupt", method="POST")
