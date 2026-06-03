"""test_model_eval.py - model_eval harness with mocked image_gen + audio_proxy."""

from pathlib import Path
from unittest.mock import patch


def test_run_image_eval_returns_paths(tmp_path: Path):
    from utils.model_eval import run_image_eval

    fake_paths = [tmp_path / "img1.png", tmp_path / "img2.png"]
    with patch("video.image_gen.image_gen.generate_images", return_value=fake_paths):
        out = run_image_eval(tmp_path, {"image_gen": {}})
    assert out == [str(p) for p in fake_paths]


def test_run_image_eval_returns_empty_when_no_image_gen(tmp_path: Path):
    from utils.model_eval import run_image_eval

    with patch.dict("sys.modules", {"video.image_gen.image_gen": None}):
        out = run_image_eval(tmp_path, {})
    assert out == []


def test_run_image_eval_handles_exception(tmp_path: Path):
    from utils.model_eval import run_image_eval

    with patch("video.image_gen.image_gen.generate_images", side_effect=RuntimeError("VRAM")):
        out = run_image_eval(tmp_path, {})
    assert out == []


def test_run_tts_eval_returns_path(tmp_path: Path):
    from utils.model_eval import run_tts_eval

    wav = tmp_path / "test.wav"
    wav.write_bytes(b"RIFF")
    with patch("audio.audio_proxy.tts_generate", return_value={"wav_path": wav}):
        out = run_tts_eval(tmp_path, {"tts": {"lang": "hi"}})
    assert out == str(wav)


def test_run_tts_eval_returns_empty_when_no_audio_proxy(tmp_path: Path):
    from utils.model_eval import run_tts_eval

    with patch.dict("sys.modules", {"audio.audio_proxy": None}):
        out = run_tts_eval(tmp_path, {})
    assert out == ""


def test_run_tts_eval_handles_exception(tmp_path: Path):
    from utils.model_eval import run_tts_eval

    with patch("audio.audio_proxy.tts_generate", side_effect=RuntimeError("TTS fail")):
        out = run_tts_eval(tmp_path, {"tts": {"lang": "hi"}})
    assert out == ""


def test_run_tts_eval_uses_english_when_lang_not_hindi(tmp_path: Path):
    from utils.model_eval import run_tts_eval

    wav = tmp_path / "en.wav"
    wav.write_bytes(b"RIFF")
    captured = {}

    def fake_tts(text, **kw):
        captured["text"] = text
        captured["lang"] = kw.get("lang")
        return {"wav_path": wav}

    with patch("audio.audio_proxy.tts_generate", side_effect=fake_tts):
        run_tts_eval(tmp_path, {"tts": {"lang": "en"}})
    assert captured["lang"] == "en"
    assert "test" in captured["text"].lower() or "lighthouse" in captured["text"].lower()


def test_run_eval_writes_summary_json(tmp_path: Path):
    from utils.model_eval import run_eval

    out_dir = tmp_path / "eval"
    with (
        patch("utils.model_eval.run_image_eval", return_value=[]),
        patch("utils.model_eval.run_tts_eval", return_value=""),
        patch(
            "config.load_config",
            return_value={
                "image_gen": {"sd_model_path": "my/model"},
                "tts": {"engine": "omnivoice"},
            },
        ),
    ):
        summary = run_eval(out_dir=out_dir, image=True, tts=True)
    assert (out_dir / "eval_summary.json").exists()
    assert summary["image_model"] == "my/model"
    assert summary["tts_engine"] == "omnivoice"
    assert summary["acceleration"] == "none"
    assert summary["upscaler"] == "none"


def test_run_eval_skip_image(tmp_path: Path):
    from utils.model_eval import run_eval

    out_dir = tmp_path / "eval"
    with (
        patch("utils.model_eval.run_image_eval") as img_mock,
        patch("utils.model_eval.run_tts_eval", return_value=""),
        patch("config.load_config", return_value={}),
    ):
        run_eval(out_dir=out_dir, image=False, tts=True)
    assert not img_mock.called


def test_run_eval_skip_tts(tmp_path: Path):
    from utils.model_eval import run_eval

    out_dir = tmp_path / "eval"
    with (
        patch("utils.model_eval.run_image_eval", return_value=["p"]),
        patch("utils.model_eval.run_tts_eval") as tts_mock,
        patch("config.load_config", return_value={}),
    ):
        summary = run_eval(out_dir=out_dir, image=True, tts=False)
    assert not tts_mock.called
    assert summary["images"] == ["p"]


def test_run_eval_default_out_dir(tmp_path: Path, monkeypatch):
    from utils import model_eval

    monkeypatch.chdir(tmp_path)
    with (
        patch("utils.model_eval.run_image_eval", return_value=[]),
        patch("utils.model_eval.run_tts_eval", return_value=""),
        patch("config.load_config", return_value={}),
    ):
        summary = model_eval.run_eval(image=False, tts=False)
    # The output dir is a relative Path; verify it resolves under tmp_path
    out_dir = Path(summary["output_dir"])
    assert (tmp_path / "model_eval").resolve() in out_dir.resolve().parents or str(
        out_dir
    ).startswith("model_eval")


def test_run_eval_captures_acceleration_type(tmp_path: Path):
    from utils.model_eval import run_eval

    out_dir = tmp_path / "eval"
    with (
        patch("utils.model_eval.run_image_eval", return_value=[]),
        patch("utils.model_eval.run_tts_eval", return_value=""),
        patch(
            "config.load_config", return_value={"image_gen": {"acceleration": {"type": "xformers"}}}
        ),
    ):
        summary = run_eval(out_dir=out_dir, image=False, tts=False)
    assert summary["acceleration"] == "xformers"
