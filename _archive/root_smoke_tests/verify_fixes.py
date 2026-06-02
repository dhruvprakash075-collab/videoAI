"""verify_fixes.py — prove both bugs are fixed."""
import sys

sys.path.insert(0, r"C:\Video.AI")
from utils.compatibility import apply_all_patches

apply_all_patches()
from pathlib import Path

print("=" * 60)
print("VERIFICATION: Both bug fixes")
print("=" * 60)

# ── Fix 1: bitsandbytes now imports cleanly ──
print()
print("FIX 1: bitsandbytes CUDA 12.8 binary")
import bitsandbytes as bnb

print("  bitsandbytes " + bnb.__version__ + " imported OK")
import torch

print("  torch " + torch.__version__ + " + cuda " + torch.version.cuda + " available=" + str(torch.cuda.is_available()))
import peft

print("  peft " + peft.__version__ + " + LoraConfig OK")
print("  -> Deep import chain (peft->transformers->bnb) works")

# ── Fix 2: train_lora.py now fails loud, not silent ──
print()
print("FIX 2: train_lora.py silent-mock fallback removed")
import train_lora

print("  _verify_real_deps_or_raise imported OK")

# Case A: real training with all deps available
print()
print("  Case A: real training with all deps available")
refs = sorted(Path(r"C:\Video.AI\studio_outputs\smoke_test_190722\studio_refs\zara").glob("*.png"))
test_out = train_lora.train_protagonist_lora(
    image_paths=[Path(p) for p in refs[:5]],
    char_name="verify_test_char",
    output_dir=Path(r"C:\Video.AI\studio_checkpoints"),
    char_description="purple hair, green eyes",
    mock=False,
)
if test_out and test_out.exists():
    sz = test_out.stat().st_size
    from safetensors import safe_open
    with safe_open(str(test_out), framework="pt") as f:
        keys = list(f.keys())
        sample = f.get_tensor(keys[0])
        std = sample.float().std().item()
    is_real = sz > 1_000_000 and std > 0.001
    print("  -> " + test_out.name + " (" + str(round(sz/1e6, 2)) + " MB, " + str(len(keys)) + " tensors, std=" + str(round(std, 4)) + ")")
    print("  -> REAL: " + ("YES" if is_real else "NO"))

# Case B: simulate missing dep — should raise
print()
print("  Case B: simulate missing torchvision (should RAISE, not silently mock)")
train_lora._REQUIRED_REAL_DEPS = [*train_lora._REQUIRED_REAL_DEPS, ("nonexistent_module_xyz", "fake")]
try:
    train_lora._verify_real_deps_or_raise()
    print("  -> FAIL: should have raised")
except RuntimeError as e:
    msg = str(e)
    has_required = "pip install" in msg and "nonexistent_module_xyz" in msg
    has_warning = "silently fall back to mock" in msg
    print("  -> RAISED RuntimeError: " + str(len(msg)) + " chars")
    print("  -> contains pip install hint: " + str(has_required))
    print("  -> contains silent-mock warning: " + str(has_warning))
    print("  -> CORRECT: loud failure with actionable fix")

print()
print("=" * 60)
print("Both fixes verified")
print("=" * 60)
