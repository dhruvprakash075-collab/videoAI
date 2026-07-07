"""indicf5_worker.py - thin wrapper around the external IndicF5 checkout."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


def generate(
    text: str,
    output: Path,
    root: Path,
    python_exe: str,
    ref_audio: Path,
    ref_text: str,
    timeout: int = 900,
) -> dict:
    run_script = root / "run_indic.py"
    if not run_script.exists():
        raise FileNotFoundError(f"IndicF5 runner not found: {run_script}")
    if not ref_audio.exists():
        raise FileNotFoundError(f"IndicF5 reference audio not found: {ref_audio}")
    if not ref_text.strip():
        raise ValueError("IndicF5 reference text is empty")

    output.parent.mkdir(parents=True, exist_ok=True)
    batch = output.parent / f"indicf5_batch_{uuid.uuid4().hex}.txt"
    try:
        # ponytail: use IndicF5 batch mode for Unicode/long text; one job keeps the wrapper tiny.
        batch.write_text(f"{text.replace('|', ' ')}|{output}\n", encoding="utf-8")
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        result = subprocess.run(
            [python_exe, str(run_script), str(ref_audio), ref_text, str(batch)],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env=env,
        )
    finally:
        with contextlib.suppress(OSError):
            batch.unlink()

    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "IndicF5 failed").strip()[:500]
        return {"status": "error", "message": msg}
    if not output.exists():
        # ponytail: some IndicF5 builds ignore absolute batch targets and emit a
        # WAV beside the batch; take the newest one rather than degrading TTS.
        newest = max(output.parent.glob("*.wav"), key=lambda p: p.stat().st_mtime, default=None)
        if newest is not None:
            shutil.move(str(newest), str(output))
    if not output.exists():
        return {"status": "error", "message": "IndicF5 completed but did not create output WAV"}
    return {"status": "success", "wav_path": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description="IndicF5 one-shot TTS worker")
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--ref-audio", required=True)
    parser.add_argument("--ref-text", required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    try:
        text = Path(args.text_file).read_text(encoding="utf-8")
        resp = generate(
            text=text,
            output=Path(args.output),
            root=Path(args.root),
            python_exe=args.python,
            ref_audio=Path(args.ref_audio),
            ref_text=args.ref_text,
            timeout=args.timeout,
        )
    except Exception as exc:
        resp = {"status": "error", "message": str(exc)[:500]}
    print(json.dumps(resp, ensure_ascii=False))
    return 0 if resp.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
