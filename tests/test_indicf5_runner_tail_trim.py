"""Tail-trim regression check for external/IndicF5/run_indic.py.

run_indic.py imports torch/f5_tts at module level (not installed on CI), so
the module is loaded via importlib with lightweight stubs; only the pure
trim logic is exercised. numpy is stubbed too — the trim works on any
sequence supporting len()/slicing.

external/IndicF5/ is gitignored (local-only runtime deps), so this test
skips on CI where run_indic.py does not exist.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_run_indic() -> types.ModuleType:
    path = Path(__file__).resolve().parents[1] / "external" / "IndicF5" / "run_indic.py"
    if not path.exists():
        pytest.skip(f"run_indic.py not found (gitignored external dep): {path}")
    for name in (
        "numpy",
        "soundfile",
        "f5_tts",
        "f5_tts.model",
        "f5_tts.model.utils",
        "f5_tts.infer",
        "f5_tts.infer.utils_infer",
    ):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
    # run_indic annotations (-> np.ndarray) are evaluated at def time; only
    # patch when numpy is our bare stub (real numpy already has ndarray).
    numpy_mod = sys.modules["numpy"]
    if not hasattr(numpy_mod, "ndarray"):
        numpy_mod.ndarray = list
    if "torchaudio" not in sys.modules:
        torchaudio_stub = types.ModuleType("torchaudio")
        torchaudio_stub.load = lambda *a, **k: None  # module reads _ta.load at import
        sys.modules["torchaudio"] = torchaudio_stub
    spec = importlib.util.spec_from_file_location("run_indic_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tail_trim_cuts_surplus_frames():
    mod = _load_run_indic()
    sr = 24000
    long = list(range(5000))
    cut = mod._tail_trim(long, 0.02, sr)  # 0.02s * 0.90 * 24000 = 432 frames
    assert len(cut) == 432
    assert cut == long[:432]


def test_tail_trim_keeps_short_audio_untouched():
    mod = _load_run_indic()
    short = list(range(100))
    assert mod._tail_trim(short, 1.0, 24000) is short
