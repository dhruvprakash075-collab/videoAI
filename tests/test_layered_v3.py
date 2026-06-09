"""test_layered_v3.py - Tests for video/image_gen/layered_v3.py.

Covers: _resolve_dominant_char, preflight_layered_v3, generate_layered_images dispatch,
workflow path validation, custom node checks, IPAdapter model checks, fallback behavior.
"""

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest


class TestResolveDominantChar:
    """_resolve_dominant_char(char_presence, threshold) -> (key, weight) or (None, 0.0)."""

    def test_above_threshold_returns_char(self):
        from video.image_gen.layered_v3 import _resolve_dominant_char

        cp = {"marcus": 0.6, "elena": 0.2}
        key, weight = _resolve_dominant_char(cp, threshold=0.3)
        assert key == "marcus"
        assert weight == 0.6

    def test_below_threshold_returns_none(self):
        from video.image_gen.layered_v3 import _resolve_dominant_char

        cp = {"marcus": 0.2, "elena": 0.1}
        key, weight = _resolve_dominant_char(cp, threshold=0.3)
        assert key is None
        assert weight == 0.0

    def test_empty_dict_returns_none(self):
        from video.image_gen.layered_v3 import _resolve_dominant_char

        assert _resolve_dominant_char({}, threshold=0.3) == (None, 0.0)
        assert _resolve_dominant_char(None, threshold=0.3) == (None, 0.0)
        assert _resolve_dominant_char({}, threshold=0.3) == (None, 0.0)

    def test_picks_max_weight_above_threshold(self):
        from video.image_gen.layered_v3 import _resolve_dominant_char

        cp = {"marcus": 0.4, "elena": 0.5}
        key, weight = _resolve_dominant_char(cp, threshold=0.3)
        assert key == "elena"
        assert weight == 0.5

    def test_equal_weights_picks_arbitrarily(self):
        from video.image_gen.layered_v3 import _resolve_dominant_char

        cp = {"marcus": 0.5, "elena": 0.5}
        key, weight = _resolve_dominant_char(cp, threshold=0.3)
        assert key in ("marcus", "elena")
        assert weight == 0.5

    def test_custom_threshold(self):
        from video.image_gen.layered_v3 import _resolve_dominant_char

        cp = {"marcus": 0.4, "elena": 0.2}
        assert _resolve_dominant_char(cp, threshold=0.5) == (None, 0.0)
        assert _resolve_dominant_char(cp, threshold=0.3) == ("marcus", 0.4)


class TestPreflightLayeredV3:
    """preflight_layered_v3(config) returns list of error strings (empty = pass)."""

    def _make_cfg(self, **overrides):
        base = {
            "image_gen": {
                "composition_mode": "layered_v3",
                "comfyui": {
                    "host": "127.0.0.1",
                    "port": 8188,
                    "root": "C:\\Video.AI\\external\\ComfyUI",
                },
                "layered_v3": {
                    "character_threshold": 0.3,
                    "closeup_threshold": 0.8,
                    "max_characters": 2,
                    "approval_mode": "hybrid",
                    "fallback_mode": "one_pass",
                    "workflows": {
                        "character_sheet": "C:\\Workflows\\char_sheet.json",
                        "background": "C:\\Workflows\\bg.json",
                        "character_pose": "C:\\Workflows\\pose.json",
                        "composite_refine": "C:\\Workflows\\composite.json",
                    },
                },
            }
        }
        img = base["image_gen"]
        for k, v in overrides.items():
            parts = k.split("__")
            if len(parts) == 2:
                section, key = parts
                img[section][key] = v
            elif len(parts) == 3:
                section, subsection, key = parts
                img[section][subsection][key] = v
        return base

    def test_not_layered_v3_returns_empty(self):
        from video.image_gen.layered_v3 import preflight_layered_v3

        cfg = {"image_gen": {"composition_mode": "one_pass"}}
        assert preflight_layered_v3(cfg) == []

    def test_missing_workflow_files(self):
        from video.image_gen.layered_v3 import preflight_layered_v3

        cfg = self._make_cfg(
            layered_v3__workflows__character_sheet=r"C:\Missing\char_sheet.json",
            layered_v3__workflows__background=r"C:\Missing\bg.json",
        )
        errors = preflight_layered_v3(cfg)
        assert len(errors) >= 2
        assert any("char_sheet" in e and "not found" in e for e in errors)
        assert any("bg.json" in e and "not found" in e for e in errors)

    def test_missing_workflow_path_not_set(self):
        from video.image_gen.layered_v3 import preflight_layered_v3

        cfg = self._make_cfg(
            layered_v3__workflows__character_sheet="",
            layered_v3__workflows__background="",
        )
        errors = preflight_layered_v3(cfg)
        assert len(errors) >= 2
        assert any("not set" in e and "character_sheet" in e for e in errors)

    def test_missing_custom_nodes(self):
        from video.image_gen.layered_v3 import preflight_layered_v3

        cfg = self._make_cfg(
            comfyui__root=r"C:\NonExistent\ComfyUI",
        )
        errors = preflight_layered_v3(cfg)
        assert len(errors) >= 3
        node_names = ["IPAdapter Plus", "Impact Pack", "ControlNet Aux"]
        for name in node_names:
            assert any(name in e for e in errors)

    def test_missing_ipadapter_models(self):
        from video.image_gen.layered_v3 import preflight_layered_v3

        cfg = self._make_cfg(
            comfyui__root=r"C:\NonExistent\ComfyUI",
        )
        errors = preflight_layered_v3(cfg)
        model_errors = [e for e in errors if "IPAdapter model" in e]
        assert len(model_errors) >= 2

    def test_comfyui_unreachable(self):
        from video.image_gen.layered_v3 import preflight_layered_v3

        cfg = self._make_cfg(
            comfyui__host="192.0.2.1",
            comfyui__port=19999,
        )
        errors = preflight_layered_v3(cfg)
        assert len(errors) >= 1
        assert any("not reachable" in e for e in errors)

    def test_all_good_returns_empty(self, tmp_path: Path, monkeypatch):
        from video.image_gen.layered_v3 import preflight_layered_v3

        workflows = tmp_path / "workflows"
        workflows.mkdir()
        for name in ["char_sheet.json", "bg.json", "pose.json", "composite.json"]:
            (workflows / name).write_text("{}", encoding="utf-8")

        comfy_root = tmp_path / "comfyui"
        (comfy_root / "custom_nodes" / "ComfyUI_IPAdapter_plus").mkdir(parents=True)
        (comfy_root / "custom_nodes" / "ComfyUI-Impact-Pack").mkdir(parents=True)
        (comfy_root / "custom_nodes" / "comfyui_controlnet_aux").mkdir(parents=True)
        ipadapter_dir = comfy_root / "models" / "ipadapter"
        ipadapter_dir.mkdir(parents=True)
        for model in ["ip-adapter-plus_sd15.bin", "ip-adapter-plus-fullface_sd15.bin"]:
            (ipadapter_dir / model).write_text("fake", encoding="utf-8")

        cfg = self._make_cfg(
            comfyui__root=str(comfy_root),
            layered_v3__workflows__character_sheet=str(workflows / "char_sheet.json"),
            layered_v3__workflows__background=str(workflows / "bg.json"),
            layered_v3__workflows__character_pose=str(workflows / "pose.json"),
            layered_v3__workflows__composite_refine=str(workflows / "composite.json"),
        )

        mock_response = MagicMock()
        type(mock_response).status = PropertyMock(return_value=200)
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_response)
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_cm):
            errors = preflight_layered_v3(cfg)
        assert errors == []


class TestGenerateLayeredImages:
    """generate_layered_images() dispatcher routing and fallback behavior."""

    def test_routes_to_layered_when_composition_mode_layered_v3(self, tmp_path: Path):
        from video.image_gen.layered_v3 import generate_layered_images

        cfg = {
            "image_gen": {
                "composition_mode": "layered_v3",
                "comfyui": {
                    "host": "127.0.0.1",
                    "port": 8188,
                    "root": "C:\\Video.AI\\external\\ComfyUI",
                    "timeout_seconds": 10,
                },
                "layered_v3": {
                    "approval_mode": "hybrid",
                    "character_threshold": 0.3,
                    "closeup_threshold": 0.8,
                    "max_characters": 2,
                    "fallback_mode": "one_pass",
                    "workflows": {
                        "character_sheet": "C:\\Workflows\\char_sheet.json",
                        "background": "C:\\Workflows\\bg.json",
                        "character_pose": "C:\\Workflows\\pose.json",
                        "composite_refine": "C:\\Workflows\\composite.json",
                    },
                },
            }
        }

        with patch("video.image_gen.layered_v3.preflight_layered_v3", return_value=[]):
            with patch("video.image_gen.layered_v3._run_workflow", return_value=[]) as mock_run_wf:
                generate_layered_images(["test prompt"], tmp_path, cfg)
                assert mock_run_wf.called

    def test_routes_to_one_pass_fallback_when_preflight_fails(self, tmp_path: Path):
        from video.image_gen.layered_v3 import generate_layered_images

        cfg = {
            "image_gen": {
                "composition_mode": "layered_v3",
                "comfyui": {
                    "host": "127.0.0.1",
                    "port": 8188,
                    "timeout_seconds": 10,
                },
                "layered_v3": {
                    "approval_mode": "hybrid",
                    "character_threshold": 0.3,
                    "closeup_threshold": 0.8,
                    "max_characters": 2,
                    "fallback_mode": "one_pass",
                    "workflows": {
                        "character_sheet": "",
                        "background": "",
                        "character_pose": "",
                        "composite_refine": "",
                    },
                },
            }
        }

        with patch(
            "video.image_gen.layered_v3.preflight_layered_v3",
            return_value=["workflow file not found [character_sheet]: C:\\Workflows\\char_sheet.json"],
        ):
            with patch("video.image_gen.comfyui_runtime.get_comfyui_runtime") as mock_get_runtime:
                mock_runtime_instance = MagicMock()
                mock_runtime_instance.base_url = "http://127.0.0.1:8188"
                mock_get_runtime.return_value = mock_runtime_instance

                with patch("video.image_gen.comfyui_client.ComfyUIClient") as mock_client_cls:
                    mock_client = MagicMock()
                    mock_client.generate_image.return_value = []
                    mock_client_cls.return_value = mock_client

                    generate_layered_images(["test prompt"], tmp_path, cfg)

                    assert mock_client.generate_image.called

    def test_raises_when_fallback_mode_error_and_preflight_fails(self, tmp_path: Path):
        from video.image_gen.layered_v3 import generate_layered_images

        cfg = {
            "image_gen": {
                "composition_mode": "layered_v3",
                "comfyui": {
                    "host": "127.0.0.1",
                    "port": 8188,
                    "timeout_seconds": 10,
                },
                "layered_v3": {
                    "approval_mode": "hybrid",
                    "character_threshold": 0.3,
                    "closeup_threshold": 0.8,
                    "max_characters": 2,
                    "fallback_mode": "error",
                    "workflows": {
                        "character_sheet": "",
                        "background": "",
                        "character_pose": "",
                        "composite_refine": "",
                    },
                },
            }
        }

        with patch(
            "video.image_gen.layered_v3.preflight_layered_v3",
            return_value=["workflow file not found [character_sheet]: C:\\Workflows\\char_sheet.json"],
        ):
            with pytest.raises(RuntimeError, match="Preflight failed"):
                generate_layered_images(["test prompt"], tmp_path, cfg)

    def test_string_prompts_split(self, tmp_path: Path):
        from video.image_gen.layered_v3 import generate_layered_images

        cfg = {
            "image_gen": {
                "composition_mode": "layered_v3",
                "comfyui": {
                    "host": "127.0.0.1",
                    "port": 8188,
                    "timeout_seconds": 10,
                },
                "layered_v3": {
                    "approval_mode": "hybrid",
                    "character_threshold": 0.3,
                    "closeup_threshold": 0.8,
                    "max_characters": 2,
                    "fallback_mode": "one_pass",
                    "workflows": {
                        "character_sheet": "C:\\Workflows\\char_sheet.json",
                        "background": "C:\\Workflows\\bg.json",
                        "character_pose": "C:\\Workflows\\pose.json",
                        "composite_refine": "C:\\Workflows\\composite.json",
                    },
                },
            }
        }

        with patch("video.image_gen.layered_v3.preflight_layered_v3", return_value=[]):
            with patch("video.image_gen.layered_v3._run_workflow", return_value=[]) as mock_run_wf:
                generate_layered_images("a; b; c", tmp_path, cfg)

                assert mock_run_wf.call_count >= 1


class TestComputeIdentityHash:
    """_compute_identity_hash produces stable hash from approved assets."""

    def test_same_files_same_hash(self, tmp_path: Path):
        from video.image_gen.layered_v3 import _compute_identity_hash

        f1 = tmp_path / "char.png"
        f1.write_bytes(b"pixel data")

        assets = {"character_sheet_path": str(f1)}
        h1 = _compute_identity_hash("marcus", "myproject", assets)
        h2 = _compute_identity_hash("marcus", "myproject", assets)
        assert h1 == h2
        assert len(h1) == 16

    def test_different_files_different_hash(self, tmp_path: Path):
        from video.image_gen.layered_v3 import _compute_identity_hash

        f1 = tmp_path / "char1.png"
        f1.write_bytes(b"pixel data 1")
        f2 = tmp_path / "char2.png"
        f2.write_bytes(b"pixel data 2")

        h1 = _compute_identity_hash("marcus", "myproject", {"character_sheet_path": str(f1)})
        h2 = _compute_identity_hash("marcus", "myproject", {"character_sheet_path": str(f2)})
        assert h1 != h2

    def test_missing_file_skipped(self, tmp_path: Path):
        from video.image_gen.layered_v3 import _compute_identity_hash

        assets = {"character_sheet_path": str(tmp_path / "nonexistent.png")}
        h = _compute_identity_hash("marcus", "myproject", assets)
        assert len(h) == 16
        assert h == "d41d8cd98f00b204"  # md5 of empty bytes, truncated to 16 chars

    def test_empty_assets_returns_empty_hash(self, tmp_path: Path):
        from video.image_gen.layered_v3 import _compute_identity_hash

        h = _compute_identity_hash("marcus", "myproject", {})
        assert len(h) == 16
