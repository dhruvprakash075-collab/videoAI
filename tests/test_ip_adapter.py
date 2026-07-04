from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from video.image_gen import ip_adapter
from video.image_gen.ip_adapter import IPAdapterManager, _sha256_file


def test_sha256_file_success_and_failure(tmp_path: Path):
    path = tmp_path / "portrait.bin"
    path.write_bytes(b"abc")
    assert _sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert _sha256_file(tmp_path / "missing.bin") == ""


def test_attach_idempotent_and_detach_clears_cache():
    pipe = MagicMock()
    mgr = IPAdapterManager(repo="repo", weight_name="weight", subfolder="sub")

    mgr.attach(pipe)
    mgr.attach(pipe)

    pipe.load_ip_adapter.assert_called_once_with(
        repo_id="repo", weight_name="weight", subfolder="sub"
    )
    mgr._embeddings_cache["char"] = object()
    mgr._image_cache["char"] = object()

    mgr.detach()

    pipe.unload_ip_adapter.assert_called_once()
    assert mgr._pipe is None
    assert mgr._attached_pipe_id is None
    assert mgr._embeddings_cache == {}
    assert mgr._image_cache == {}


def test_attach_none_and_attach_failure():
    mgr = IPAdapterManager()
    with pytest.raises(ValueError):
        mgr.attach(None)

    pipe = MagicMock()
    pipe.load_ip_adapter.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        mgr.attach(pipe)


def test_detach_ignores_missing_and_unload_errors():
    mgr = IPAdapterManager()
    mgr.detach()

    pipe = MagicMock()
    pipe.unload_ip_adapter.side_effect = RuntimeError("ignored")
    mgr._pipe = pipe
    mgr._attached_pipe_id = id(pipe)
    mgr.detach()
    assert mgr._pipe is None


def test_unload_collects_and_clears_cuda_when_available():
    mgr = IPAdapterManager()
    with (
        patch.object(mgr, "detach") as detach,
        patch("video.image_gen.ip_adapter.gc.collect") as collect,
        patch.dict("sys.modules", {"torch": MagicMock()}),
    ):
        import torch

        torch.cuda.is_available.return_value = True
        mgr.unload()

    detach.assert_called_once()
    collect.assert_called_once()
    torch.cuda.empty_cache.assert_called_once()


def test_unload_cuda_unavailable_and_module_unload_without_manager():
    mgr = IPAdapterManager()
    with (
        patch.object(mgr, "detach"),
        patch("video.image_gen.ip_adapter.gc.collect"),
        patch.dict("sys.modules", {"torch": MagicMock()}),
    ):
        import torch

        torch.cuda.is_available.return_value = False
        mgr.unload()
    torch.cuda.empty_cache.assert_not_called()

    ip_adapter._manager = None
    ip_adapter.unload_ip_adapter()


def test_set_scale_no_pipe_success_and_failure():
    mgr = IPAdapterManager()
    mgr.set_scale(0.5)

    pipe = MagicMock()
    mgr._pipe = pipe
    mgr.set_scale("0.75")
    pipe.set_ip_adapter_scale.assert_called_once_with(0.75)

    pipe.set_ip_adapter_scale.side_effect = RuntimeError("ignored")
    mgr.set_scale(1.0)


def test_pre_encode_missing_bad_image_fallback_and_cache(tmp_path: Path):
    mgr = IPAdapterManager()
    assert mgr.pre_encode("missing", tmp_path / "missing.png") is None

    bad = tmp_path / "bad.png"
    bad.write_text("not image", encoding="utf-8")
    assert mgr.pre_encode("bad", bad) is None

    img = tmp_path / "ok.png"
    img.write_bytes(b"x")
    with patch("PIL.Image.open") as open_image:
        raw = MagicMock()
        rgb = object()
        raw.convert.return_value = rgb
        open_image.return_value = raw
        assert mgr.pre_encode("char", img) is None
        assert mgr.get_image("char") is rgb


def test_pre_encode_uses_pipe_encode_and_embedding_cache(tmp_path: Path):
    img = tmp_path / "ok.png"
    img.write_bytes(b"x")
    pipe = MagicMock()
    pipe.encode_image.return_value = "embedding"
    mgr = IPAdapterManager()
    mgr._pipe = pipe

    with patch("PIL.Image.open") as open_image:
        raw = MagicMock()
        raw.convert.return_value = "rgb"
        open_image.return_value = raw
        assert mgr.pre_encode("char", img) == "embedding"
        assert mgr.pre_encode("char", img) == "embedding"

    pipe.encode_image.assert_called_once_with("rgb", num_images_per_prompt=1)


def test_pre_encode_encode_failure_clear_cache_and_singleton(tmp_path: Path):
    img = tmp_path / "ok.png"
    img.write_bytes(b"x")
    pipe = MagicMock()
    pipe.encode_image.side_effect = RuntimeError("nope")
    mgr = IPAdapterManager()
    mgr._pipe = pipe

    with patch("PIL.Image.open") as open_image:
        raw = MagicMock()
        raw.convert.return_value = "rgb"
        open_image.return_value = raw
        assert mgr.pre_encode("char", img) is None

    mgr.clear_cache()
    assert mgr._embeddings_cache == {}
    assert mgr._image_cache == {}

    ip_adapter._manager = None
    first = ip_adapter.get_ip_adapter()
    assert ip_adapter.get_ip_adapter() is first
    with patch.object(first, "unload") as unload:
        ip_adapter.unload_ip_adapter()
    unload.assert_called_once()


def test_detach_without_unload_method_and_unload_torch_failure():
    mgr = IPAdapterManager()
    mgr._pipe = object()
    mgr._attached_pipe_id = 1
    mgr.detach()
    assert mgr._pipe is None

    with (
        patch.object(mgr, "detach"),
        patch("video.image_gen.ip_adapter.gc.collect"),
        patch.dict("sys.modules", {"torch": None}),
    ):
        mgr.unload()
