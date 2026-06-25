from pathlib import Path

import yaml

from scripts.qwen_edit_spike_check import (
    FOCUSED_PYTEST_COMMAND,
    TARGETED_RUFF_COMMAND,
    VRAM_MONITOR_COMMAND,
    analyze_config,
    build_issue_template,
    load_config,
    print_command_plan,
    write_issue_template,
)


def _base_config() -> dict:
    return {
        "image_gen": {
            "composition_mode": "qwen_edit",
            "comfyui": {"root": "external/ComfyUI"},
            "qwen_edit": {
                "enabled": True,
                "workflow_path": "config/comfyui/workflows/qwen_image_edit_api.json",
                "model_path": "",
                "required_custom_nodes": ["ComfyUI-nunchaku"],
            },
        }
    }


def test_analyze_config_accepts_resource_gated_qwen():
    checks = analyze_config(_base_config())
    by_name = {check.name: check for check in checks}

    assert by_name["resource-gated composition mode"].ok is True
    assert by_name["Qwen enabled"].ok is True
    assert by_name["local Qwen model configured"].ok is False


def test_analyze_config_flags_disabled_config():
    config = _base_config()
    config["image_gen"]["composition_mode"] = "one_pass"
    config["image_gen"]["qwen_edit"]["enabled"] = False

    checks = analyze_config(config)
    by_name = {check.name: check for check in checks}

    assert by_name["resource-gated composition mode"].ok is False
    assert by_name["Qwen enabled"].ok is False


def test_issue_template_contains_required_result_fields():
    template = build_issue_template()

    assert "Qwen local GPU spike results" in template
    assert "Peak VRAM" in template
    assert "Seconds/image" in template
    assert "Ready to keep as optional experimental feature" in template


def test_write_issue_template(tmp_path: Path):
    output = tmp_path / "qwen_results.md"

    write_issue_template(output)

    assert output.exists()
    assert "Three-frame identity test" in output.read_text(encoding="utf-8")


def test_load_config_requires_mapping(tmp_path: Path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    try:
        load_config(config_path)
    except TypeError as exc:
        assert "Config must be a mapping" in str(exc)
    else:
        raise AssertionError("Expected TypeError for non-mapping YAML")


def test_load_config_reads_yaml(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(_base_config()), encoding="utf-8")

    loaded = load_config(config_path)

    assert loaded["image_gen"]["composition_mode"] == "qwen_edit"


def test_command_plan_lists_all_focused_qwen_checks(capsys):
    print_command_plan()

    captured = capsys.readouterr().out
    assert FOCUSED_PYTEST_COMMAND in captured
    assert TARGETED_RUFF_COMMAND in captured
    assert VRAM_MONITOR_COMMAND in captured
    assert "tests/test_qwen_repose.py" in TARGETED_RUFF_COMMAND
    assert "tests/test_image_gen.py" in TARGETED_RUFF_COMMAND
    assert "tests/test_config_schemas.py" in TARGETED_RUFF_COMMAND
    assert "tests/test_preflight.py" in TARGETED_RUFF_COMMAND
    assert "tests/test_qwen_spike_check.py" in TARGETED_RUFF_COMMAND
