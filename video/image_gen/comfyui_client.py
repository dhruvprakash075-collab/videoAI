"""ComfyUI HTTP API client for image generation."""

import json
import logging
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from utils.circuit_breaker import BreakerOpen, CircuitBreakerRegistry
from utils.errors import ComfyUIError

log = logging.getLogger(__name__)


class ComfyUIClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", timeout: int = 300):
        # SSRF: validate local service URL at init time
        from utils.url_security import validate_local_service_base_url

        self.base_url = validate_local_service_base_url(base_url).rstrip("/")
        self.timeout = timeout
        self._session_id: str | None = None

    def _request(self, endpoint: str, method: str = "GET", data: dict | None = None) -> dict:
        """Make a request to the ComfyUI API — local service URL."""
        cb = CircuitBreakerRegistry.get("comfyui", fails=3, cooldown=30.0)
        if not cb.allow_request():
            log.warning("[ComfyUIClient] Circuit breaker OPEN for ComfyUI — failing fast")
            raise BreakerOpen("comfyui", cb.cooldown_remaining_s())

        from utils.url_security import build_validated_url

        url = build_validated_url(self.base_url, endpoint)
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

    def upload_image(
        self,
        image_path: Path,
        name: str | None = None,
        overwrite: bool = False,
    ) -> dict:
        """Upload a local image to ComfyUI's ``/upload/image`` endpoint.

        Builds a ``multipart/form-data`` request using only the standard
        library and returns the validated JSON response expected by a
        ``LoadImage`` workflow node. The returned dict always contains a
        non-empty ``name`` and preserves ``subfolder``/``type`` when present.

        Args:
            image_path: Path to the local image file to upload.
            name: Optional filename to present to ComfyUI. Only the basename is
                used so host paths never leak into the input store.
            overwrite: When True, instruct ComfyUI to overwrite an existing
                input file with the same name.
        """
        image_path = Path(image_path)
        # Reject a missing file before any network activity.
        if not image_path.is_file():
            raise FileNotFoundError(f"Image to upload does not exist: {image_path}")

        safe_name = Path(name).name if name else image_path.name
        if not safe_name:
            safe_name = image_path.name

        cb = CircuitBreakerRegistry.get("comfyui", fails=3, cooldown=30.0)
        if not cb.allow_request():
            log.warning("[ComfyUIClient] Circuit breaker OPEN for ComfyUI — failing fast")
            raise BreakerOpen("comfyui", cb.cooldown_remaining_s())

        from utils.url_security import build_validated_url

        url = build_validated_url(self.base_url, "/upload/image")

        image_bytes = image_path.read_bytes()
        content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        boundary = "----comfyui" + uuid.uuid4().hex
        body = self._encode_multipart(boundary, safe_name, content_type, image_bytes, overwrite)

        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
            cb.record_success()
        except urllib.error.HTTPError as e:
            cb.record_failure()
            raise ComfyUIError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            cb.record_failure()
            raise ComfyUIError(f"Connection failed: {e.reason}") from e
        except Exception as e:
            cb.record_failure()
            raise ComfyUIError(f"Upload failed: {e}") from e

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ComfyUIError(f"Malformed upload response from ComfyUI: {e}") from e

        if not isinstance(result, dict) or not result.get("name"):
            raise ComfyUIError("ComfyUI upload response missing required 'name' field")

        return {
            "name": result["name"],
            "subfolder": result.get("subfolder", ""),
            "type": result.get("type", "input"),
        }

    @staticmethod
    def _encode_multipart(
        boundary: str,
        filename: str,
        content_type: str,
        image_bytes: bytes,
        overwrite: bool,
    ) -> bytes:
        """Encode a multipart/form-data body for the upload request.

        Never logs the body or the image bytes.
        """
        crlf = b"\r\n"
        bbound = boundary.encode("ascii")
        parts: list[bytes] = []

        # Binary image field.
        parts.append(b"--" + bbound + crlf)
        parts.append(
            f'Content-Disposition: form-data; name="image"; filename="{filename}"'.encode()
            + crlf
        )
        parts.append(f"Content-Type: {content_type}".encode() + crlf + crlf)
        parts.append(image_bytes + crlf)

        # type=input field.
        parts.append(b"--" + bbound + crlf)
        parts.append(b'Content-Disposition: form-data; name="type"' + crlf + crlf)
        parts.append(b"input" + crlf)

        # overwrite field (only when explicitly requested).
        if overwrite:
            parts.append(b"--" + bbound + crlf)
            parts.append(b'Content-Disposition: form-data; name="overwrite"' + crlf + crlf)
            parts.append(b"true" + crlf)

        parts.append(b"--" + bbound + b"--" + crlf)
        return b"".join(parts)

    def get_view(self, filename: str, subfolder: str = "", image_type: str = "output") -> bytes:
        """Get an image from ComfyUI — local service URL."""
        cb = CircuitBreakerRegistry.get("comfyui", fails=3, cooldown=30.0)
        if not cb.allow_request():
            log.warning("[ComfyUIClient] Circuit breaker OPEN for ComfyUI — failing fast")
            raise BreakerOpen("comfyui", cb.cooldown_remaining_s())

        from urllib.parse import urlencode

        from utils.url_security import build_validated_url

        params = urlencode({"filename": filename, "type": image_type, **({"subfolder": subfolder} if subfolder else {})})
        url = build_validated_url(self.base_url, f"/view?{params}")
        try:
            req = urllib.request.Request(url)
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
