"""Tests for dashboard config API (ComfyUI settings load/save)."""

from unittest.mock import MagicMock, patch

import pytest


class TestConfigAPI:
    @pytest.fixture
    def mock_config(self):
        return {
            "tts": {"engine": "omnivoice"},
            "subtitles": {"format": "classic"},
            "script": {"uncapped_scaling": False, "default_images_per_segment": 6},
            "image_gen": {
                "backend": "comfyui",
                "comfyui": {
                    "host": "127.0.0.1",
                    "port": 8188,
                    "root": "C:\\Video.AI\\external\\ComfyUI",
                    "auto_start": False,
                    "workflow_path": "config/comfyui/workflows/manga_identity_pose_api.json",
                    "checkpoint": "meinamix_meinaV11.safetensors",
                    "width": 768,
                    "height": 512,
                    "steps": 20,
                    "cfg": 7.0,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "timeout_seconds": 300,
                    "poll_seconds": 1.0,
                },
            },
        }

    def test_get_config_returns_comfyui_settings(self, mock_config):
        with patch("utils.local_ui.load_config", return_value=mock_config):
            from fastapi.testclient import TestClient

            from utils.local_ui import app

            client = TestClient(app)
            response = client.get("/api/config")

            assert response.status_code == 200
            data = response.json()

            assert data["imageBackend"] == "comfyui"
            assert data["comfyUiAdvanced"]["autoStart"] is False
            assert data["comfyUiAdvanced"]["host"] == "127.0.0.1"
            assert data["comfyUiAdvanced"]["port"] == 8188
            assert data["comfyUiAdvanced"]["checkpoint"] == "meinamix_meinaV11.safetensors"

    @patch("utils.local_ui.load_config")
    @patch("builtins.open", MagicMock())
    @patch("os.replace", MagicMock())
    @patch("yaml.safe_dump")
    def test_save_config_updates_image_backend_to_comfyui(self, mock_yaml_dump, mock_load_config, mock_config):
        mock_load_config.return_value = mock_config.copy()

        from fastapi.testclient import TestClient

        from utils.local_ui import app

        client = TestClient(app)
        response = client.post(
            "/api/config",
            data={
                "voice_engine": "omnivoice",
                "dynamic_subtitles": "false",
                "uncapped_scaling": "false",
                "max_images_per_segment": 6,
                "image_backend": "comfyui",
            },
        )

        assert response.status_code == 200
        saved_config = mock_yaml_dump.call_args[0][0]
        assert saved_config["image_gen"]["backend"] == "comfyui"

    @patch("utils.local_ui.load_config")
    @patch("builtins.open", MagicMock())
    @patch("os.replace", MagicMock())
    @patch("yaml.safe_dump")
    def test_save_config_updates_comfyui_settings(self, mock_yaml_dump, mock_load_config, mock_config):
        mock_load_config.return_value = mock_config.copy()

        from fastapi.testclient import TestClient

        from utils.local_ui import app

        client = TestClient(app)
        response = client.post(
            "/api/config",
            data={
                "voice_engine": "omnivoice",
                "dynamic_subtitles": "false",
                "uncapped_scaling": "false",
                "max_images_per_segment": 6,
                "image_backend": "comfyui",
                "comfyui_auto_start": "true",
                "comfyui_host": "0.0.0.0",
                "comfyui_port": 8199,
                "comfyui_checkpoint": "model_v2.safetensors",
                "comfyui_width": 512,
                "comfyui_height": 512,
                "comfyui_steps": 10,
                "comfyui_cfg": 5.0,
                "comfyui_sampler_name": "dpm_2m",
                "comfyui_scheduler": "karras",
            },
        )

        assert response.status_code == 200
        saved_config = mock_yaml_dump.call_args[0][0]
        comfy = saved_config["image_gen"]["comfyui"]
        assert comfy["auto_start"] is True
        assert comfy["host"] == "0.0.0.0"
        assert comfy["port"] == 8199
        assert comfy["checkpoint"] == "model_v2.safetensors"
        assert comfy["width"] == 512
        assert comfy["height"] == 512
        assert comfy["steps"] == 10
        assert comfy["cfg"] == 5.0
        assert comfy["sampler_name"] == "dpm_2m"
        assert comfy["scheduler"] == "karras"

    def test_save_config_validates_image_backend(self, mock_config):
        with patch("utils.local_ui.load_config", return_value=mock_config):
            from fastapi.testclient import TestClient

            from utils.local_ui import app

            client = TestClient(app)
            response = client.post(
                "/api/config",
                data={
                    "voice_engine": "omnivoice",
                    "dynamic_subtitles": "false",
                    "uncapped_scaling": "false",
                    "max_images_per_segment": 6,
                    "image_backend": "invalid_backend",
                },
            )

            assert response.status_code == 400
            data = response.json()
            assert "comfyui" in data.get("message", "").lower()

    def test_get_config_with_comfyui_backend(self):
        config = {
            "tts": {"engine": "omnivoice"},
            "subtitles": {"format": "classic"},
            "script": {"uncapped_scaling": False},
            "image_gen": {"backend": "comfyui"},
        }

        with patch("utils.local_ui.load_config", return_value=config):
            from fastapi.testclient import TestClient

            from utils.local_ui import app

            client = TestClient(app)
            response = client.get("/api/config")

            assert response.status_code == 200
            data = response.json()
            assert data["imageBackend"] == "comfyui"
