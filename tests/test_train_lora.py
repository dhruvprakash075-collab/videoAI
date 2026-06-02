"""Tests for train_lora.py — verifies P2-8: unified diffusers-compatible key format.

Both the real and mock training paths must emit the same safetensors key schema:
  - Use dot notation for to_out.0 (not to_out_0)
  - No .alpha keys (real path doesn't write them)
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))



def test_mock_lora_uses_dot_notation_for_to_out(tmp_path):
    """P2-8: mock path must use 'to_out.0' (dot notation), not 'to_out_0'."""
    from train_lora import train_protagonist_lora

    result = train_protagonist_lora(
        image_paths=[Path("nonexistent.png")],  # mock ignores image_paths
        char_name="test_char",
        output_dir=tmp_path,
        mock=True,
    )
    assert result is not None
    assert result.exists()

    from safetensors import safe_open
    keys = []
    with safe_open(str(result), framework="pt", device="cpu") as f:
        keys = list(f.keys())

    # All to_out keys must use dot notation
    to_out_keys = [k for k in keys if "to_out" in k]
    assert len(to_out_keys) > 0, "Expected to_out keys in mock LoRA"
    for k in to_out_keys:
        assert "to_out.0" in k, f"Key uses underscore notation instead of dot: {k}"
        assert "to_out_0" not in k, f"Key uses underscore notation: {k}"


def test_mock_lora_has_no_alpha_keys(tmp_path):
    """P2-8: mock path must not write .alpha keys (real path doesn't write them)."""
    from train_lora import train_protagonist_lora

    result = train_protagonist_lora(
        image_paths=[Path("nonexistent.png")],
        char_name="test_char",
        output_dir=tmp_path,
        mock=True,
    )
    assert result is not None

    from safetensors import safe_open
    keys = []
    with safe_open(str(result), framework="pt", device="cpu") as f:
        keys = list(f.keys())

    alpha_keys = [k for k in keys if k.endswith(".alpha")]
    assert alpha_keys == [], f"Mock LoRA must not write .alpha keys, found: {alpha_keys}"


def test_mock_lora_has_lora_down_and_up_weights(tmp_path):
    """Mock LoRA must have lora_down.weight and lora_up.weight for each projection."""
    from train_lora import train_protagonist_lora

    result = train_protagonist_lora(
        image_paths=[Path("nonexistent.png")],
        char_name="test_char",
        output_dir=tmp_path,
        mock=True,
    )
    assert result is not None

    from safetensors import safe_open
    keys = []
    with safe_open(str(result), framework="pt", device="cpu") as f:
        keys = list(f.keys())

    down_keys = [k for k in keys if k.endswith("lora_down.weight")]
    up_keys = [k for k in keys if k.endswith("lora_up.weight")]
    assert len(down_keys) > 0, "Expected lora_down.weight keys"
    assert len(up_keys) > 0, "Expected lora_up.weight keys"
    assert len(down_keys) == len(up_keys), "lora_down and lora_up counts must match"


def test_mock_lora_key_format_matches_diffusers_convention(tmp_path):
    """P2-8: all keys must follow diffusers lora_unet_* naming convention."""
    from train_lora import train_protagonist_lora

    result = train_protagonist_lora(
        image_paths=[Path("nonexistent.png")],
        char_name="test_char",
        output_dir=tmp_path,
        mock=True,
    )
    assert result is not None

    from safetensors import safe_open
    keys = []
    with safe_open(str(result), framework="pt", device="cpu") as f:
        keys = list(f.keys())

    for k in keys:
        assert k.startswith("lora_unet_"), f"Key does not start with lora_unet_: {k}"
        # Must end with lora_down.weight or lora_up.weight
        assert k.endswith(".weight"), f"Key does not end with .weight: {k}"
