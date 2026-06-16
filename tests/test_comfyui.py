"""test_comfyui.py - Tests for ComfyUI integration."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video.image_gen.comfyui_client import ComfyUIClient, ComfyUIError
from video.image_gen.comfyui_runtime import ComfyUIRuntime
from video.image_gen.comfyui_workflow import (
    WorkflowPatcher,
    create_default_workflow,
)


class TestComfyUIRuntime:
    def test_is_running_returns_true_when_server_responds(self):
        runtime = ComfyUIRuntime({"comfyui": {"host": "127.0.0.1", "port": 8188}})

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            assert runtime.is_running(timeout=1.0) is True

    def test_is_running_returns_false_on_timeout(self):
        runtime = ComfyUIRuntime({"comfyui": {"host": "127.0.0.1", "port": 8188}})

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError()

            assert runtime.is_running(timeout=1.0) is False

    def test_is_running_returns_false_on_connection_error(self):
        runtime = ComfyUIRuntime({"comfyui": {"host": "127.0.0.1", "port": 8188}})

        import urllib.error
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            assert runtime.is_running(timeout=1.0) is False

    def test_ensure_running_returns_true_when_already_running(self):
        runtime = ComfyUIRuntime({"comfyui": {"auto_start": False}})

        with patch.object(runtime, "is_running", return_value=True):
            assert runtime.ensure_running(timeout=5.0) is True

    def test_ensure_running_returns_false_when_not_running_and_no_auto_start(self):
        runtime = ComfyUIRuntime({"comfyui": {"auto_start": False}})

        with patch.object(runtime, "is_running", return_value=False):
            assert runtime.ensure_running(timeout=5.0) is False

    def test_base_url_constructs_correctly(self):
        runtime = ComfyUIRuntime({"comfyui": {"host": "192.168.1.100", "port": 8189}})
        assert runtime.base_url == "http://192.168.1.100:8189"


class TestComfyUIClient:
    def test_get_system_stats(self):
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps({"devices": []}).encode()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = client.get_system_stats()
            assert "devices" in result

    def test_get_history(self):
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps({"prompt_123": {}}).encode()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = client.get_history("prompt_123")
            assert "prompt_123" in result

    def test_queue_prompt(self):
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps({"prompt_id": "test_123"}).encode()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            prompt = {"1": {"inputs": {"text": "test"}, "class_type": "CLIPTextEncode"}}
            result = client.queue_prompt(prompt)
            assert result["prompt_id"] == "test_123"

    def test_wait_for_completion_handles_dict_status(self):
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")

        with patch.object(client, "get_prompt_status") as mock_status:
            mock_status.return_value = {
                "status": {"completed": True, "status_str": "success"},
                "outputs": {},
            }

            result = client.wait_for_completion("prompt_123", poll_interval=0.1, timeout=5.0)
            assert result["status"]["completed"] is True

    def test_wait_for_completion_handles_string_status(self):
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")

        with patch.object(client, "get_prompt_status") as mock_status:
            mock_status.return_value = {
                "status": "completed",
                "outputs": {},
            }

            result = client.wait_for_completion("prompt_123", poll_interval=0.1, timeout=5.0)
            assert result["status"] == "completed"

    def test_wait_for_completion_raises_on_failure(self):
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")

        with patch.object(client, "get_prompt_status") as mock_status:
            mock_status.return_value = {
                "status": {"failed": True, "status_str": "RuntimeError"},
            }

            with pytest.raises(ComfyUIError, match="Prompt failed"):
                client.wait_for_completion("prompt_123", poll_interval=0.1, timeout=5.0)

    def test_wait_for_completion_raises_on_timeout(self):
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")

        with patch.object(client, "get_prompt_status") as mock_status:
            mock_status.return_value = None

            with pytest.raises(ComfyUIError, match="Timeout"):
                client.wait_for_completion("prompt_123", poll_interval=0.1, timeout=0.5)

    def test_wait_for_completion_parses_execution_error_details(self):
        client = ComfyUIClient(base_url="http://127.0.0.1:8188")

        with patch.object(client, "get_prompt_status") as mock_status:
            mock_status.return_value = {
                "status": {
                    "completed": False,
                    "status_str": "error",
                    "messages": [
                        [
                            "ExecutionError",
                            {
                                "node_id": "12",
                                "node_type": "KSampler",
                                "exception_message": "CUDA out of memory",
                            },
                        ]
                    ],
                }
            }

            with pytest.raises(ComfyUIError, match="Node 12 \\(KSampler\\): CUDA out of memory"):
                client.wait_for_completion("prompt_123", poll_interval=0.1, timeout=5.0)


class TestWorkflowPatcher:
    def test_load_workflow(self, tmp_path):
        workflow_file = tmp_path / "test.json"
        workflow_file.write_text('{"1": {"class_type": "CLIPTextEncode"}}')

        patcher = WorkflowPatcher(workflow_file)
        assert patcher.workflow is not None
        assert "1" in patcher.workflow

    def test_patch_positive_prompt(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "original"}}
        }

        patcher.patch_positive_prompt("new prompt")
        assert patcher.workflow["1"]["inputs"]["text"] == "new prompt"

    def test_patch_seed(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "KSampler", "inputs": {"seed": 0}}
        }

        patcher.patch_seed(12345)
        assert patcher.workflow["1"]["inputs"]["seed"] == 12345

    def test_patch_width_height(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}}
        }

        patcher.patch_width_height(1024, 768)
        assert patcher.workflow["1"]["inputs"]["width"] == 1024
        assert patcher.workflow["1"]["inputs"]["height"] == 768

    def test_patch_steps(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "KSampler", "inputs": {"steps": 20}}
        }

        patcher.patch_steps(30)
        assert patcher.workflow["1"]["inputs"]["steps"] == 30

    def test_patch_cfg(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "KSampler", "inputs": {"cfg": 1.0}}
        }

        patcher.patch_cfg(7.5)
        assert patcher.workflow["1"]["inputs"]["cfg"] == 7.5

    def test_patch_sampler(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "KSampler", "inputs": {"sampler_name": "euler"}}
        }

        patcher.patch_sampler("dpm_2m")
        assert patcher.workflow["1"]["inputs"]["sampler_name"] == "dpm_2m"

    def test_patch_scheduler(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "KSampler", "inputs": {"scheduler": "normal"}}
        }

        patcher.patch_scheduler("karras")
        assert patcher.workflow["1"]["inputs"]["scheduler"] == "karras"

    def test_patch_checkpoint(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ""}}
        }

        patcher.patch_checkpoint("model.safetensors")
        assert patcher.workflow["1"]["inputs"]["ckpt_name"] == "model.safetensors"

    def test_patch_filename_prefix(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}}
        }

        patcher.patch_filename_prefix("scene_01")
        assert patcher.workflow["1"]["inputs"]["filename_prefix"] == "scene_01"

    def test_patch_all(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
            "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ""}},
            "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
            "5": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal"}},
            "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "old"}},
        }

        patcher.patch_all(
            prompt="test prompt",
            negative_prompt="negative",
            seed=42,
            width=1024,
            height=768,
            steps=25,
            cfg=8.0,
            sampler_name="dpm_sde",
            scheduler="exponential",
            checkpoint="model.safetensors",
            filename_prefix="scene_01",
        )

        assert patcher.workflow["1"]["inputs"]["text"] == "test prompt"
        assert patcher.workflow["3"]["inputs"]["ckpt_name"] == "model.safetensors"
        assert patcher.workflow["4"]["inputs"]["width"] == 1024
        assert patcher.workflow["5"]["inputs"]["seed"] == 42
        assert patcher.workflow["5"]["inputs"]["steps"] == 25
        assert patcher.workflow["5"]["inputs"]["cfg"] == 8.0
        assert patcher.workflow["7"]["inputs"]["filename_prefix"] == "scene_01"

    def test_resolve_prompt_nodes_direct(self):
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "positive": ["1", 0],
                    "negative": ["2", 0]
                }
            },
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}
        }
        patcher._build_node_cache()
        pos, neg = patcher._resolve_prompt_nodes()
        assert pos == {"1"}
        assert neg == {"2"}

    def test_resolve_prompt_nodes_intermediate_nodes(self):
        # Even if declaration order is misleading (negative CLIPTextEncode first),
        # it should resolve correctly by traversing the intermediate nodes.
        patcher = WorkflowPatcher()
        patcher.workflow = {
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},  # First in declaration, negative
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},  # Second in declaration, positive
            "5": {
                "class_type": "ConditioningConcat",
                "inputs": {
                    "conditioning_to": ["1", 0],
                    "conditioning_from": ["6", 0]
                }
            },
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},  # Also positive
            "7": {
                "class_type": "ConditioningSetArea",
                "inputs": {
                    "conditioning": ["2", 0]
                }
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "positive": ["5", 0],
                    "negative": ["7", 0]
                }
            }
        }
        patcher._build_node_cache()
        pos, neg = patcher._resolve_prompt_nodes()
        # "1" and "6" should be positive, "2" should be negative
        assert pos == {"1", "6"}
        assert neg == {"2"}

    def test_resolve_prompt_nodes_fallback_minimal(self):
        patcher = WorkflowPatcher()
        # No KSampler, just CLIPTextEncode nodes
        patcher.workflow = {
            "10": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}, "_meta": {"title": "negative text"}},
            "11": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}, "_meta": {"title": "positive text"}},
            "12": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}} # fallback by order
        }
        patcher._build_node_cache()
        pos, neg = patcher._resolve_prompt_nodes()
        # "11" resolved as positive via title
        # "10" resolved as negative via title
        # "12" is unclassified and not matched by title
        assert "11" in pos
        assert "10" in neg
        
        # Order fallback only triggers if BOTH positive and negative are completely empty
        # If title matches resolved one of them, the order fallback doesn't run.
        # Let's test order fallback when no titles match and no KSampler exists:
        patcher2 = WorkflowPatcher()
        patcher2.workflow = {
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}}
        }
        patcher2._build_node_cache()
        pos2, neg2 = patcher2._resolve_prompt_nodes()
        assert pos2 == {"3"}
        assert neg2 == {"4"}


class TestCreateDefaultWorkflow:
    def test_creates_valid_workflow(self):
        workflow = create_default_workflow(
            prompt="a beautiful landscape",
            negative_prompt="ugly, blurry",
            seed=12345,
            width=512,
            height=512,
            steps=10,
            cfg=5.0,
            sampler_name="euler",
            scheduler="normal",
            checkpoint="test_model.safetensors",
            filename_prefix="test_img",
        )

        assert "1" in workflow
        assert workflow["1"]["class_type"] == "CLIPTextEncode"
        assert workflow["1"]["inputs"]["text"] == "a beautiful landscape"

        assert "3" in workflow
        assert workflow["3"]["inputs"]["ckpt_name"] == "test_model.safetensors"

        assert "4" in workflow
        assert workflow["4"]["inputs"]["width"] == 512
        assert workflow["4"]["inputs"]["height"] == 512

        assert "5" in workflow
        assert workflow["5"]["inputs"]["seed"] == 12345
        assert workflow["5"]["inputs"]["steps"] == 10
        assert workflow["5"]["inputs"]["cfg"] == 5.0

        assert "7" in workflow
        assert workflow["7"]["inputs"]["filename_prefix"] == "test_img"

    def test_uses_default_values(self):
        workflow = create_default_workflow(prompt="test")

        assert workflow["3"]["inputs"]["ckpt_name"] == "DreamShaper_8.safetensors"
        assert workflow["4"]["inputs"]["width"] == 1024
        assert workflow["5"]["inputs"]["steps"] == 20
        assert workflow["7"]["inputs"]["filename_prefix"] == "ComfyUI"


class TestBackendRouting:
    def test_generate_images_routes_to_comfyui(self):
        import video.image_gen.image_gen as image_gen_module

        with patch.object(image_gen_module, "_comfyui") as mock_comfyui:
            mock_comfyui.return_value = [Path("test.png")]

            config = {"image_gen": {"backend": "comfyui", "comfyui": {}, "fallback_backend": "bonsai"}}
            result = image_gen_module.generate_images("test prompt", Path("/tmp"), config)

            mock_comfyui.assert_called_once()
            assert len(result) == 1

    def test_generate_images_routes_to_bonsai(self):
        import video.image_gen.image_gen as image_gen_module

        with patch.object(image_gen_module, "_bonsai") as mock_bonsai:
            mock_bonsai.return_value = [Path("test.png")]

            config = {"image_gen": {"backend": "bonsai"}}
            result = image_gen_module.generate_images("test prompt", Path("/tmp"), config)

            mock_bonsai.assert_called_once()
            assert len(result) == 1

    def test_generate_images_falls_back_to_bonsai_on_comfyui_failure(self):
        import video.image_gen.image_gen as image_gen_module

        with (
            patch.object(image_gen_module, "_comfyui") as mock_comfyui,
            patch.object(image_gen_module, "_bonsai") as mock_bonsai,
        ):

            mock_comfyui.side_effect = Exception("ComfyUI failed")
            mock_bonsai.return_value = [Path("fallback.png")]

            config = {
                "image_gen": {
                    "backend": "comfyui",
                    "comfyui": {},
                    "fallback_backend": "bonsai",
                }
            }
            result = image_gen_module.generate_images("test prompt", Path("/tmp"), config)

            mock_comfyui.assert_called_once()
            mock_bonsai.assert_called_once()
            assert result[0].name == "fallback.png"

    def test_generate_images_raises_when_fallback_disabled(self):
        import video.image_gen.image_gen as image_gen_module

        with patch.object(image_gen_module, "_comfyui") as mock_comfyui:
            mock_comfyui.side_effect = RuntimeError("ComfyUI not running")

            config = {
                "image_gen": {
                    "backend": "comfyui",
                    "comfyui": {},
                    "fallback_backend": "none",
                }
            }

            with pytest.raises(RuntimeError):
                image_gen_module.generate_images("test prompt", Path("/tmp"), config)
