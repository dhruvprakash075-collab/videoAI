import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from utils.circuit_breaker import BreakerOpen, CircuitBreakerRegistry
from utils.errors import ComfyUIError
from video.image_gen.comfyui_client import ComfyUIClient
from video.image_gen.comfyui_workflow import WorkflowPatcher, load_workflow


@pytest.fixture(autouse=True)
def reset_breakers():
    CircuitBreakerRegistry.reset_all()


def _response(payload: dict | bytes) -> MagicMock:
    res = MagicMock()
    res.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    res.__enter__ = MagicMock(return_value=res)
    res.__exit__ = MagicMock(return_value=False)
    return res


def test_request_errors_preserve_http_reason_when_body_is_not_json():
    client = ComfyUIClient(base_url="http://127.0.0.1:8188")
    err = urllib.error.HTTPError(
        "http://127.0.0.1:8188/system_stats",
        500,
        "Server Error",
        None,
        BytesIO(b"not json"),
    )

    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(ComfyUIError, match="HTTP 500: Server Error"):
            client.get_system_stats()


def test_request_wraps_connection_and_unexpected_errors():
    client = ComfyUIClient(base_url="http://127.0.0.1:8188")

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("nope")):
        with pytest.raises(ComfyUIError, match="Connection failed: nope"):
            client.get_queue()

    CircuitBreakerRegistry.reset_all()
    with patch("urllib.request.urlopen", side_effect=RuntimeError("boom")):
        with pytest.raises(ComfyUIError, match="Request failed: boom"):
            client.free_memory()


def test_upload_errors_are_wrapped(tmp_path):
    image = tmp_path / "frame.unknown"
    image.write_bytes(b"img")
    client = ComfyUIClient(base_url="http://127.0.0.1:8188")

    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 400, "Bad", None, None)):
        with pytest.raises(ComfyUIError, match="HTTP 400: Bad"):
            client.upload_image(image)

    CircuitBreakerRegistry.reset_all()
    with patch("urllib.request.urlopen", side_effect=RuntimeError("disk")):
        with pytest.raises(ComfyUIError, match="Upload failed: disk"):
            client.upload_image(image)


def test_get_view_http_body_and_unexpected_error_paths():
    client = ComfyUIClient(base_url="http://127.0.0.1:8188")
    err = urllib.error.HTTPError("u", 404, "Missing", None, BytesIO(b'{"error":"gone"}'))

    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(ComfyUIError, match="HTTP 404: Missing"):
            client.get_view("x.png")

    CircuitBreakerRegistry.reset_all()
    with patch("urllib.request.urlopen", side_effect=RuntimeError("socket")):
        with pytest.raises(ComfyUIError, match="Failed to fetch image view: socket"):
            client.get_view("x.png")


def test_wait_for_completion_reports_string_and_unknown_status_failures():
    client = ComfyUIClient(base_url="http://127.0.0.1:8188")

    with patch.object(client, "get_prompt_status", return_value={"status": "failed", "status_str": "bad"}):
        with pytest.raises(ComfyUIError, match="Prompt failed: bad"):
            client.wait_for_completion("p", poll_interval=0, timeout=1)

    with patch.object(client, "get_prompt_status", return_value={"status": object()}):
        with pytest.raises(ComfyUIError, match="Unknown status format"):
            client.wait_for_completion("p", poll_interval=0, timeout=1)


def test_generate_image_handles_missing_prompt_id_and_empty_outputs(tmp_path):
    client = ComfyUIClient(base_url="http://127.0.0.1:8188")

    with patch.object(client, "queue_prompt", return_value={}):
        with pytest.raises(ComfyUIError, match="No prompt_id"):
            client.generate_image({}, tmp_path)

    with (
        patch.object(client, "queue_prompt", return_value={"prompt_id": "p"}),
        patch.object(client, "wait_for_completion", return_value={"outputs": {"1": {"images": []}}}),
    ):
        assert client.generate_image({}, tmp_path) == []


def test_generate_image_saves_each_returned_image(tmp_path):
    client = ComfyUIClient(base_url="http://127.0.0.1:8188")

    with (
        patch.object(client, "queue_prompt", return_value={"prompt_id": "p"}),
        patch.object(
            client,
            "wait_for_completion",
            return_value={
                "outputs": {
                    "1": {"images": [{"filename": "a.png", "subfolder": "s", "type": "output"}]},
                    "2": {"images": [{"subfolder": "ignored"}]},
                }
            },
        ),
        patch.object(client, "get_view", return_value=b"png") as get_view,
    ):
        assert client.generate_image({}, tmp_path) == [tmp_path / "a.png"]

    get_view.assert_called_once_with("a.png", "s", "output")
    assert (tmp_path / "a.png").read_bytes() == b"png"


def test_client_fast_fails_when_breaker_is_open(tmp_path):
    client = ComfyUIClient(base_url="http://127.0.0.1:8188")
    image = tmp_path / "frame.png"
    image.write_bytes(b"img")
    breaker = CircuitBreakerRegistry.get("comfyui")
    for _ in range(3):
        breaker.record_failure()

    with pytest.raises(BreakerOpen):
        client.get_system_stats()
    with pytest.raises(BreakerOpen):
        client.upload_image(image)
    with pytest.raises(BreakerOpen):
        client.get_view("x.png")


def test_client_misc_thin_wrappers_and_status_swallower():
    client = ComfyUIClient(base_url="http://127.0.0.1:8188")

    with patch.object(client, "_request", return_value={"ok": True}) as request:
        assert client.queue_prompt({}, prompt_id="fixed") == {"ok": True}
        assert request.call_args.kwargs["data"]["prompt_id"] == "fixed"
        assert client.interrupt() == {"ok": True}

    with patch.object(client, "get_history", side_effect=ComfyUIError("down")):
        assert client.get_prompt_status("p") is None


def test_workflow_prompt_resolution_falls_back_to_titles_and_nested_links():
    patcher = WorkflowPatcher()
    patcher.workflow = {
        "pos": {"class_type": "CLIPTextEncode", "_meta": {"title": "Positive"}, "inputs": {"text": ""}},
        "neg": {"class_type": "CLIPTextEncode", "_meta": {"title": "Negative"}, "inputs": {"text": ""}},
        "pipe": {"class_type": "ConditioningSetArea", "inputs": {"clip": ["pos", 0]}},
        "ks": {"class_type": "KSampler", "inputs": {"positive": ["pipe", 0], "negative": ["neg", 0]}},
        "junk": "not a node",
    }
    patcher._build_node_cache()

    patcher.patch_positive_prompt("good").patch_negative_prompt("bad")

    assert patcher.workflow["pos"]["inputs"]["text"] == "good"
    assert patcher.workflow["neg"]["inputs"]["text"] == "bad"


def test_workflow_fallbacks_for_prompt_nodes_and_empty_cache():
    patcher = WorkflowPatcher()
    patcher._build_node_cache()
    assert patcher.find_node("Nothing") is None
    assert patcher._resolve_prompt_nodes() == (set(), set())

    patcher.workflow = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    }
    patcher._build_node_cache()
    patcher.patch_positive_prompt("good").patch_negative_prompt("bad")
    assert patcher.workflow["1"]["inputs"]["text"] == "good"
    assert patcher.workflow["2"]["inputs"]["text"] == "bad"

    one = WorkflowPatcher()
    one.workflow = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "negative prompt"}}}
    one._build_node_cache()
    one.patch_negative_prompt("bad")
    assert one.workflow["1"]["inputs"]["text"] == "bad"


def test_workflow_scalar_patchers_ignore_missing_inputs_without_crashing():
    patcher = WorkflowPatcher()
    patcher.workflow = {
        "bad": "skip",
        "latent": {"class_type": "EmptyLatentImage", "inputs": {}},
        "ks": {"class_type": "KSampler", "inputs": {}},
        "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
        "vae": {"class_type": "VAELoader", "inputs": {}},
        "save": {"class_type": "SaveImage", "inputs": {}},
    }
    patcher._build_node_cache()

    patcher.patch_width_height(1, 2)
    patcher.patch_seed(3)
    patcher.patch_steps(4)
    patcher.patch_cfg(5.0)
    patcher.patch_sampler("s")
    patcher.patch_scheduler("normal")
    patcher.patch_checkpoint("c")
    patcher.patch_vae("v")
    patcher.patch_filename_prefix("p")
    patcher.patch_denoise(0.1)

    assert patcher.workflow["ks"]["inputs"] == {}


def test_patch_all_generates_seed_and_skips_optional_checkpoint_prefix():
    patcher = WorkflowPatcher()
    patcher.workflow = {
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "ks": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 0,
                "steps": 1,
                "cfg": 1.0,
                "denoise": 0.0,
                "sampler_name": "",
                "scheduler": "",
            },
        },
        "latent": {"class_type": "EmptyLatentImage", "inputs": {"width": 1, "height": 1}},
        "ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "old"}},
        "save": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
    }
    patcher._build_node_cache()

    with patch("video.image_gen.comfyui_workflow.random.randint", return_value=99):
        patcher.patch_all("prompt")

    assert patcher.workflow["ks"]["inputs"]["seed"] == 99
    assert patcher.workflow["ckpt"]["inputs"]["ckpt_name"] == "old"
    assert patcher.workflow["save"]["inputs"]["filename_prefix"] == "old"


def test_workflow_patchers_raise_without_loaded_workflow_and_load_factory(tmp_path):
    patcher = WorkflowPatcher()

    for method, arg in [
        (patcher.patch_positive_prompt, "x"),
        (patcher.patch_negative_prompt, "x"),
        (patcher.patch_seed, 1),
        (patcher.patch_width_height, (1, 1)),
        (patcher.patch_steps, 1),
        (patcher.patch_cfg, 1.0),
        (patcher.patch_sampler, "euler"),
        (patcher.patch_scheduler, "normal"),
        (patcher.patch_checkpoint, "model"),
        (patcher.patch_vae, "vae"),
        (patcher.patch_filename_prefix, "x"),
        (patcher.patch_denoise, 0.5),
    ]:
        with pytest.raises(ValueError, match="No workflow loaded"):
            method(*arg) if isinstance(arg, tuple) else method(arg)

    with pytest.raises(ValueError, match="No workflow loaded"):
        patcher.get_workflow()

    workflow = tmp_path / "workflow.json"
    workflow.write_text('{"1":{"class_type":"VAELoader","inputs":{"vae_name":"old"}}}', encoding="utf-8")
    loaded = load_workflow(workflow)
    loaded.patch_vae("new")
    assert loaded.get_workflow()["1"]["inputs"]["vae_name"] == "new"
