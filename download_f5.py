"""download_f5.py - Reliable F5-TTS Hindi model download for Windows.

Bypasses the xet backend (which stalls at 0 bytes on Windows) and uses the
classic HTTP download path via huggingface_hub.snapshot_download.
Resumes automatically if interrupted.
"""

import os
import sys

# Force the classic HTTP download path (xet hangs on Windows)
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"

ROOT = r"C:\Video.AI"
os.environ.setdefault("HF_HOME", os.path.join(ROOT, "hf_cache"))

CACHE_DIR = os.path.join(ROOT, "hf_cache", "hub")
REPO_ID = "SPRINGLab/F5-Hindi-24KHz"


def main():
    from huggingface_hub import snapshot_download

    print(f"Downloading {REPO_ID} -> {CACHE_DIR}")
    print("(classic HTTP download, resumes automatically)\n")

    path = snapshot_download(
        repo_id=REPO_ID,
        cache_dir=CACHE_DIR,
        resume_download=True,
        max_workers=4,
        # Only fetch the files we actually need (skip sample wavs / extras)
        allow_patterns=["*.safetensors", "*.pt", "*.txt", "*.json", "*.yaml", "*.md"],
    )
    print(f"\nDone. Model at: {path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
