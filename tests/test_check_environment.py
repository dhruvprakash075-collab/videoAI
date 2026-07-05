from pathlib import Path
from types import SimpleNamespace

import scripts.check_environment as env


def test_python_check_rejects_wrong_venv(monkeypatch):
    monkeypatch.setattr(env.sys, "prefix", str(Path("C:/other-venv")))
    monkeypatch.setattr(env.sys, "base_prefix", str(Path("C:/Python312")))
    monkeypatch.setattr(env.sys, "version_info", SimpleNamespace(major=3, minor=12, micro=0))

    ok, msg = env.check_python_venv()

    assert not ok
    assert "Wrong virtual environment" in msg


def test_ollama_models_come_from_config(monkeypatch):
    monkeypatch.setattr(
        env,
        "_get_ollama_models_cached",
        lambda _config: (True, "", [{"name": "custom-director:latest"}, {"name": "custom-writer:latest"}]),
    )

    ok, msg = env.check_ollama_models(
        {"models": {"director": "custom-director", "writer": "custom-writer"}}
    )

    assert ok
    assert "custom-director" in msg
