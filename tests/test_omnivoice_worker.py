import sys
from unittest.mock import MagicMock

from audio import omnivoice_worker


def test_set_seed_noop():
    omnivoice_worker._set_seed(None)
    omnivoice_worker._set_seed(-1)
    assert True


def test_set_seed_deterministic(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", MagicMock(manual_seed=MagicMock()))
    monkeypatch.setattr("numpy.random.seed", MagicMock())
    omnivoice_worker._set_seed(42)
    assert sys.modules["torch"].manual_seed.called


def test_split_text_chunks():
    text = "Hello world. " * 100
    chunks = omnivoice_worker._split_text_chunks(text, max_chars=50)
    assert all(len(c) <= 50 for c in chunks)
