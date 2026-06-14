"""audio_proxy.py - TTS audio generation proxy.

This module provides TTS generation using edge-tts and OmniVoice engines.

Used by: pipeline_long.py
"""

import contextlib
import html
import json
import logging
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from config import load_config
from utils import get_audio_duration as _get_audio_duration_utils

# Re-export for backward compatibility - modules importing audio_proxy.get_audio_duration still work
# via the local wrapper below.

log = logging.getLogger(__name__)

# OPT-03: module-level config cache — load_config() only hits disk once per process
_config_cache: dict = {}
_config_loader_id: int | None = None


def _get_config() -> dict:
    """Return cached config, loading from disk only on first call."""
    global _config_cache, _config_loader_id
    loader_id = id(load_config)
    if not _config_cache or _config_loader_id != loader_id:
        try:
            _config_cache = load_config()
            _config_loader_id = loader_id
        except Exception as e:
            log.warning(f"Could not load config: {e}")
            _config_cache = {}
            _config_loader_id = loader_id
    return _config_cache


# P1-7 fix: TTS engine normalization whitelist.
# Vision docs and user responses can contain arbitrary strings (e.g.
# "Calm, measured, storytelling voice"). Map everything to the five
# engine ids that tts_generate actually dispatches: "supertonic", "f5", "omnivoice", "edge", or "indicf5".
_OMNIVOICE_ALIASES = frozenset({"omnivoice", "omni", "voice_clone", "clone"})
_EDGE_ALIASES = frozenset({"edge", "edge-tts", "edge_tts", "microsoft"})
_F5_ALIASES = frozenset({"f5", "f5-tts", "f5tts", "f5_tts"})
_INDICF5_ALIASES = frozenset({"indicf5", "indic-f5", "ai4bharat-indicf5", "hindi-f5"})
_SUPERTONIC_ALIASES = frozenset({"supertonic", "supertone", "supertonic3", "supertonic-3"})


def normalize_tts_engine(engine: str) -> str:
    """Normalize an arbitrary TTS engine string to a known engine id.

    Known supertonic aliases: "supertonic", "supertone", "supertonic3", "supertonic-3"
    Known f5 aliases:         "f5", "f5-tts", "f5tts", "f5_tts"
    Known omnivoice aliases:  "omnivoice", "omni", "voice_clone", "clone"
    Known edge aliases:       "edge", "edge-tts", "edge_tts", "microsoft"
    Known indicf5 aliases:    "indicf5", "indic-f5", "ai4bharat-indicf5", "hindi-f5"
    Everything else (including free-text descriptions) → "supertonic" (default).

    Args:
        engine: Raw engine string from vision doc, config overlay, or user input.

    Returns:
        "supertonic", "f5", "omnivoice", "edge", or "indicf5".
    """
    if not isinstance(engine, str):
        log.warning(
            f"[TTS] normalize_tts_engine: non-string engine value {engine!r} — defaulting to 'supertonic'"
        )
        return "supertonic"

    normalized = engine.strip().lower()
    if normalized in _SUPERTONIC_ALIASES:
        return "supertonic"
    if normalized in _F5_ALIASES:
        return "f5"
    if normalized in _OMNIVOICE_ALIASES:
        return "omnivoice"
    if normalized in _EDGE_ALIASES:
        return "edge"
    if normalized in _INDICF5_ALIASES:
        return "indicf5"

    log.warning(
        f"[TTS] Unknown TTS engine string {engine!r} — defaulting to 'supertonic'. "
        "Add an alias to normalize_tts_engine() if this is a valid engine."
    )
    return "supertonic"


def _call_edge_direct(
    text: str,
    lang: str = "hi",
    output_dir: Path | None = None,
    voice_profile: dict[str, Any] | None = None,
    speed: float | None = None,
) -> dict[str, Any]:
    """Call edge-tts directly from venv.

    Uses edge-tts package for fast cloud TTS as a fallback.

    Args:
        text: Text to synthesize.
        lang: Language code.
        output_dir: Directory to write the output MP3.
        voice_profile: Dict with edge_voice / edge_rate / edge_volume keys.
        speed: Optional float speed multiplier from get_mood_rate (e.g. 0.85 = −15%,
               1.1 = +10%).  When provided it overrides the voice_profile edge_rate so
               that mood-based pacing is honoured on the edge path (P1-8 fix).
    """
    import asyncio

    if output_dir is None:
        output_dir = Path("tts_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_mp3 = output_dir / f"output_{uuid.uuid4().hex[:8]}.mp3"

    vp = voice_profile or {}
    voice = vp.get("edge_voice", "hi-IN-MadhurNeural")
    rate = vp.get("edge_rate", "+5%")
    volume = vp.get("edge_volume", "+0%")

    # P1-8 fix: convert the OmniVoice-style float speed multiplier to the
    # edge-tts rate string format ("+X%" / "-X%") and override the profile default.
    # OmniVoice speed 0.85 → −15% → rate="-15%"
    # OmniVoice speed 1.10 → +10% → rate="+10%"
    if speed is not None:
        try:
            rate_pct = int((float(speed) - 1.0) * 100)
            rate = f"+{rate_pct}%" if rate_pct >= 0 else f"{rate_pct}%"
            log.debug(f"[edge-tts] mood-based speed {speed:.2f} → rate={rate!r}")
        except (TypeError, ValueError) as exc:
            log.warning(
                f"[edge-tts] Could not convert speed {speed!r} to rate string: {exc}; using profile default {rate!r}"
            )

    try:
        from edge_tts import Communicate

        async def _gen():
            communicate = Communicate(text=text, voice=voice, rate=rate, volume=volume)
            await communicate.save(str(output_mp3))

        _coro = _gen()
        try:
            try:
                asyncio.run(_coro)
            except RuntimeError:
                try:
                    import nest_asyncio

                    nest_asyncio.apply()
                    asyncio.get_event_loop().run_until_complete(_coro)
                except (ImportError, ModuleNotFoundError):
                    loop = asyncio.new_event_loop()
                    loop.run_until_complete(_coro)
                    loop.close()
        finally:
            _coro.close()

        # Get duration
        try:
            from pydub import AudioSegment

            audio = AudioSegment.from_file(str(output_mp3))
            duration = len(audio) / 1000.0
        except Exception:
            duration = len(text) / 150.0

        log.info(f"edge-tts direct complete: {output_mp3} ({duration:.1f}s)")
        return {"status": "success", "wav_path": str(output_mp3), "duration": duration}

    except Exception as e:
        log.exception(f"edge-tts direct failed: {e}")
        return {"status": "error", "message": str(e)[:200]}


# ── Supertonic 3 persistent worker ────────────────────────────────────────────


def _enqueue_stdout(proc, q):
    try:
        for line in iter(proc.stdout.readline, ""):
            q.put(line)
    except Exception:
        pass
    finally:
        q.put("")  # EOF sentinel


class _SupertonicWorker:
    """Persistent Supertonic 3 TTS worker subprocess (CPU ONNX, zero VRAM).

    Mirrors _OmniVoiceWorker design. Spawns supertonic_worker.py --serve once,
    keeps the model loaded, and pipes line-delimited JSON requests across many
    segments. Falls back gracefully if supertonic is not installed.
    """

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._failed = False
        self._stdout_q = None
        self._reader_t = None

    def _start(self) -> bool:
        if self._failed:
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True
        python_exe = Path(sys.executable)
        worker_script = Path(__file__).parent / "supertonic_worker.py"
        if not worker_script.exists():
            log.warning("[Supertonic] worker script not found — disabling persistent mode")
            self._failed = True
            return False
        try:
            _super_env = dict(os.environ)
            _super_env.update(
                {
                    "WANDB_MODE": "disabled",
                    "WANDB_DISABLED": "true",
                    "PYTHONIOENCODING": "utf-8",
                }
            )
            self._proc = subprocess.Popen(
                [str(python_exe), str(worker_script), "--serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=_super_env,
            )
            import queue

            self._stdout_q = queue.Queue()
            self._reader_t = threading.Thread(
                target=_enqueue_stdout, args=(self._proc, self._stdout_q), daemon=True
            )
            self._reader_t.start()

            import time as _t

            deadline = _t.time() + 120
            while _t.time() < deadline:
                try:
                    rem = max(0.1, deadline - _t.time())
                    line = self._stdout_q.get(timeout=rem)
                except queue.Empty:
                    raise RuntimeError("Supertonic worker readiness timeout")

                if not line:
                    raise RuntimeError("Supertonic worker exited during startup")

                line = line.strip()
                if not (line.startswith("{") and line.endswith("}")):
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("status") == "ready":
                    log.info("[Supertonic] Persistent worker ready (CPU ONNX model loaded)")
                    return True
                if msg.get("status") == "error":
                    raise RuntimeError(msg.get("message", "supertonic worker init error"))
            raise RuntimeError("Supertonic worker readiness timeout")
        except Exception as e:
            log.warning(
                f"[Supertonic] Persistent worker unavailable ({e}) — using one-shot fallback"
            )
            self._failed = True
            self._cleanup_proc()
            return False

    def _cleanup_proc(self):
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.kill()
            self._proc = None
        self._stdout_q = None
        self._reader_t = None

    def generate(self, req: dict[str, Any], timeout: float = 300) -> dict[str, Any] | None:
        with self._lock:
            if not self._start():
                return None
            if self._stdout_q is None and self._proc is not None:
                import queue

                self._stdout_q = queue.Queue()
                self._reader_t = threading.Thread(
                    target=_enqueue_stdout, args=(self._proc, self._stdout_q), daemon=True
                )
                self._reader_t.start()
            try:
                self._proc.stdin.write(json.dumps(req) + "\n")
                self._proc.stdin.flush()
                import time as _t
                import queue

                deadline = _t.time() + timeout
                while _t.time() < deadline:
                    try:
                        rem = max(0.1, deadline - _t.time())
                        line = self._stdout_q.get(timeout=rem)
                    except queue.Empty:
                        raise RuntimeError("Supertonic worker response timeout")

                    if not line:
                        raise RuntimeError("Supertonic worker died mid-request")

                    line = line.strip()
                    if not (line.startswith("{") and line.endswith("}")):
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    status = msg.get("status")
                    if status == "progress":
                        deadline = _t.time() + timeout
                        continue
                    return msg
                raise RuntimeError("Supertonic worker response timeout")
            except Exception as e:
                log.warning(
                    f"[Supertonic] Persistent worker request failed ({e}) — disabling persistent mode"
                )
                self._failed = True
                self._cleanup_proc()
                return None

    def shutdown(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=10)
                except Exception:
                    pass
            self._cleanup_proc()


_supertonic_worker = _SupertonicWorker()


def shutdown_supertonic_worker():
    """Stop the persistent Supertonic worker (call at pipeline end)."""
    _supertonic_worker.shutdown()


def _call_supertonic_worker(
    text: str,
    lang: str = "hi",
    output_dir: Path | None = None,
    speed_override: float | None = None,
) -> dict[str, Any]:
    """Generate TTS audio using Supertonic 3 (CPU ONNX).

    Tries the persistent worker first, falls back to one-shot subprocess.
    """
    super_cfg = {}
    with contextlib.suppress(Exception):
        super_cfg = load_config().get("tts", {}).get("supertonic", {})

    voice = super_cfg.get("voice", "M1")
    steps = int(super_cfg.get("steps", 16))
    speed = (
        float(speed_override) if speed_override is not None else float(super_cfg.get("speed", 1.0))
    )
    silence_duration = float(super_cfg.get("silence_duration", 0.1))
    # Default max_chunk_length=100 chars to stay under ONNX 1000-token
    # attention limit. Without chunking, texts >1000 chars trigger a Mul_13
    # broadcast error in the ONNX runtime.
    max_chunk_length = super_cfg.get("max_chunk_length", 100)

    if output_dir is None:
        output_dir = Path("tts_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_wav = output_dir / f"supertonic_{uuid.uuid4().hex[:8]}.wav"

    req = {
        "text": text,
        "output": str(out_wav),
        "voice": voice if voice else "M1",
        "lang": lang if lang else None,
        "steps": steps,
        "speed": speed,
        "silence_duration": silence_duration,
        "seed": -1,
    }
    if max_chunk_length is not None:
        req["max_chunk_length"] = int(max_chunk_length)

    resp = _supertonic_worker.generate(req)
    if resp is not None:
        if resp.get("status") != "success":
            log.warning(
                f"[Supertonic] Persistent worker returned error: {resp.get('message', 'unknown')}"
            )
        return resp

    log.info("[Supertonic] Using one-shot subprocess fallback")
    try:
        python_exe = Path(sys.executable)
        worker_script = Path(__file__).parent / "supertonic_worker.py"
        if not worker_script.exists():
            raise FileNotFoundError(f"supertonic_worker.py not found at {worker_script}")

        cmd = [
            str(python_exe),
            str(worker_script),
            f"--text={text}",
            f"--output={out_wav}",
            f"--voice={voice}",
            f"--steps={steps}",
            f"--speed={speed}",
            f"--silence-duration={silence_duration}",
        ]
        if lang:
            cmd.append(f"--lang={lang}")
        if max_chunk_length is not None:
            cmd.append(f"--max-chunk-length={max_chunk_length}")

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600)
        if result.returncode == 0 and result.stdout.strip():
            for line in reversed(result.stdout.strip().split("\n")):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
        error_msg = result.stderr.strip()[:200] if result.stderr else "Unknown error"
        log.error(f"[Supertonic] one-shot failed (code {result.returncode}): {error_msg}")
        return {"status": "error", "message": error_msg}
    except Exception as e:
        log.exception(f"[Supertonic] one-shot exception: {e}")
        return {"status": "error", "message": str(e)[:200]}


def _resolve_omnivoice_python() -> str:
    """Return the Python executable to run the OmniVoice worker."""
    custom_env_py = Path(__file__).parent.parent / "omnivoice_env" / "Scripts" / "python.exe"
    if custom_env_py.exists():
        return str(custom_env_py)
    return sys.executable


def _resolve_indicf5_python() -> str:
    """Return the Python executable to run the IndicF5 worker.

    Enforces strict isolation: returns the configured Python path from config,
    or raises RuntimeError if the environment doesn't exist. Never falls back
    to the main venv Python, as that would pollute/crash the main environment.
    """
    try:
        indicf5_cfg = load_config().get("tts", {}).get("indicf5", {})
    except Exception:
        indicf5_cfg = {}

    config_python = indicf5_cfg.get("python", "")
    if config_python:
        config_path = Path(config_python)
        if config_path.exists():
            return str(config_path)
        else:
            raise RuntimeError(
                f"IndicF5 python not found at configured path: {config_python}. "
                f"Run scripts/setup_indicf5.ps1 to create the environment."
            )

    conda_env_py = Path(__file__).parent.parent / "indicf5_env" / "Scripts" / "python.exe"
    if conda_env_py.exists():
        return str(conda_env_py)

    raise RuntimeError(
        "IndicF5 environment not found. Run scripts/setup_indicf5.ps1 to create "
        "the indicf5 conda environment, or set tts.indicf5.python in config.yaml."
    )


class _OmniVoiceWorker:
    """Persistent OmniVoice worker manager (B16 fix).

    Spawns omnivoice_worker.py --serve once, keeps the model loaded, and pipes
    line-delimited JSON requests across many segments. Thread-safe. Falls back
    gracefully: if the persistent worker can't start, callers use the one-shot path.
    """

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._failed = False  # once True, never retry the persistent path this run
        self._stdout_q = None
        self._reader_t = None

    def _start(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        if self._failed:
            return False
        worker_script = Path(__file__).parent / "omnivoice_worker.py"
        python_exe = _resolve_omnivoice_python()
        try:
            _omnivoice_env = dict(os.environ)
            _omnivoice_env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            # Windows without Developer Mode lacks SeCreateSymbolicLinkPrivilege.
            # Setting HF_HUB_DISABLE_SYMLINKS_WARNING ensures huggingface_hub
            # falls back to copy instead of raising WinError 1314.
            self._proc = subprocess.Popen(
                [python_exe, str(worker_script), "--serve"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # B-fix: send stderr to DEVNULL, NOT PIPE. Whisper/transformers emit
                # heavy stderr logging; an unread PIPE fills the OS buffer and the
                # worker blocks on write() — a deadlock that looks like a TTS stall
                # (GPU 0%, no progress). DEVNULL can never fill.
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=_omnivoice_env,
            )
            import queue

            self._stdout_q = queue.Queue()
            self._reader_t = threading.Thread(
                target=_enqueue_stdout, args=(self._proc, self._stdout_q), daemon=True
            )
            self._reader_t.start()

            # Wait for the readiness line (model load can take a while)
            import time as _t

            deadline = _t.time() + 300
            while _t.time() < deadline:
                try:
                    rem = max(0.1, deadline - _t.time())
                    line = self._stdout_q.get(timeout=rem)
                except queue.Empty:
                    raise RuntimeError("worker readiness timeout")

                if not line:
                    raise RuntimeError("worker exited during startup")

                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("status") == "ready":
                    log.info("[OmniVoice] Persistent worker ready (model loaded once)")
                    return True
                if msg.get("status") == "error":
                    raise RuntimeError(msg.get("message", "worker init error"))
            raise RuntimeError("worker readiness timeout")
        except Exception as e:
            log.warning(
                f"[OmniVoice] Persistent worker unavailable ({e}) — using one-shot fallback"
            )
            self._failed = True
            self._cleanup_proc()
            return False

    def _cleanup_proc(self):
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.kill()
            self._proc = None
        self._stdout_q = None
        self._reader_t = None

    def generate(self, req: dict[str, Any], timeout: float = 600) -> dict[str, Any] | None:
        """Send one request to the persistent worker. Returns response dict or None on failure.

        The worker emits intermediate {"status":"progress"} lines while synthesizing
        long scripts chunk-by-chunk (B21 fix). Those reset the idle timeout so a slow
        but live synthesis isn't killed; we only return on success/error/shutdown.
        """
        with self._lock:
            if not self._start():
                return None
            if self._stdout_q is None and self._proc is not None:
                import queue

                self._stdout_q = queue.Queue()
                self._reader_t = threading.Thread(
                    target=_enqueue_stdout, args=(self._proc, self._stdout_q), daemon=True
                )
                self._reader_t.start()
            try:
                self._proc.stdin.write(json.dumps(req) + "\n")
                self._proc.stdin.flush()
                import time as _t
                import queue

                # timeout is an IDLE timeout: it resets each time the worker emits a
                # line (including progress), so total time scales with the work done.
                deadline = _t.time() + timeout
                while _t.time() < deadline:
                    try:
                        rem = max(0.1, deadline - _t.time())
                        line = self._stdout_q.get(timeout=rem)
                    except queue.Empty:
                        raise RuntimeError(
                            "worker response timeout (no progress within idle window)"
                        )

                    if not line:
                        raise RuntimeError("worker died mid-request")

                    line = line.strip()
                    if not (line.startswith("{") and line.endswith("}")):
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    status = msg.get("status")
                    if status == "progress":
                        # Liveness signal — extend the idle deadline and keep reading.
                        deadline = _t.time() + timeout
                        log.debug(f"[OmniVoice] chunk {msg.get('chunk')}/{msg.get('total')}")
                        continue
                    # Terminal response (success / error / shutdown / ready)
                    return msg
                raise RuntimeError("worker response timeout (no progress within idle window)")
            except Exception as e:
                log.warning(
                    f"[OmniVoice] Persistent worker request failed ({e}) — disabling persistent mode"
                )
                self._failed = True
                self._cleanup_proc()
                return None

    def shutdown(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=10)
                except Exception:
                    pass
            self._cleanup_proc()


# Module-level singleton persistent worker (lazy-started on first use)
_omnivoice_worker = _OmniVoiceWorker()


def shutdown_omnivoice_worker():
    """Stop the persistent OmniVoice worker (call at pipeline end)."""
    _omnivoice_worker.shutdown()


# ── F5-TTS persistent worker (T1) ────────────────────────────────────────────


class _F5Worker:
    """Persistent F5-TTS worker subprocess (mirrors _OmniVoiceWorker design).

    Loads the F5 model once and serves line-delimited JSON requests over stdin/stdout.
    Falls back gracefully if f5-tts is not installed or the model is not downloaded.
    """

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._failed = False
        self._stdout_q = None
        self._reader_t = None

    def _start(self) -> bool:
        if self._failed:
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True
        try:
            python_exe = Path(sys.executable)
            worker_script = Path(__file__).parent / "f5_worker.py"
            if not worker_script.exists():
                raise FileNotFoundError(f"f5_worker.py not found at {worker_script}")

            # Resolve model path from config
            try:
                _f5_cfg = load_config().get("tts", {}).get("f5", {})
                _model_path = _f5_cfg.get(
                    "model_path", "hf_cache/hub/models--SPRINGLab--F5-Hindi-24KHz/snapshots/main"
                )
            except Exception:
                _model_path = "hf_cache/hub/models--SPRINGLab--F5-Hindi-24KHz/snapshots/main"

            # Resolve HF hub snapshot layout (snapshots/<hash>/ or snapshots/main)
            if not Path(_model_path).exists():
                try:
                    from audio.f5_worker import _resolve_model_path as _fp_resolve

                    _model_path = _fp_resolve(_model_path)
                except Exception:
                    pass
            if not Path(_model_path).exists():
                raise FileNotFoundError(
                    f"F5 model not found at '{_model_path}'. Run setup_f5.ps1 to download it."
                )

            # Critical env for the F5 subprocess on Windows:
            #  - WANDB disabled: f5-tts pulls in wandb which wraps stdout and
            #    crashes on Devanagari prints under the cp1252 console.
            #  - PYTHONIOENCODING=utf-8: f5 prints Hindi text internally.
            #  - HF_HUB_DISABLE_XET: xet backend stalls model loads on Windows.
            _f5_env = dict(os.environ)
            _f5_env.update(
                {
                    "WANDB_MODE": "disabled",
                    "WANDB_DISABLED": "true",
                    "WANDB_CONSOLE": "off",
                    "PYTHONIOENCODING": "utf-8",
                    "HF_HUB_DISABLE_XET": "1",
                    "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
                }
            )
            self._proc = subprocess.Popen(
                [str(python_exe), str(worker_script), "--serve", f"--model-path={_model_path}"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=_f5_env,
            )
            import queue

            self._stdout_q = queue.Queue()
            self._reader_t = threading.Thread(
                target=_enqueue_stdout, args=(self._proc, self._stdout_q), daemon=True
            )
            self._reader_t.start()

            import time as _t

            deadline = _t.time() + 300
            while _t.time() < deadline:
                try:
                    rem = max(0.1, deadline - _t.time())
                    line = self._stdout_q.get(timeout=rem)
                except queue.Empty:
                    raise RuntimeError("F5 worker readiness timeout")

                if not line:
                    raise RuntimeError("F5 worker exited during startup")

                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("status") == "ready":
                    log.info("[F5-TTS] Persistent worker ready (model loaded once)")
                    return True
                if msg.get("status") == "error":
                    raise RuntimeError(msg.get("message", "F5 worker init error"))
            raise RuntimeError("F5 worker readiness timeout")
        except Exception as e:
            log.warning(
                f"[F5-TTS] Persistent worker unavailable ({e}) — will fall back to omnivoice"
            )
            self._failed = True
            self._cleanup_proc()
            return False

    def _cleanup_proc(self):
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.kill()
            self._proc = None
        self._stdout_q = None
        self._reader_t = None

    def generate(self, req: dict[str, Any], timeout: float = 600) -> dict[str, Any] | None:
        """Send one request to the persistent F5 worker. Returns response dict or None on failure."""
        with self._lock:
            if not self._start():
                return None
            if self._stdout_q is None and self._proc is not None:
                import queue

                self._stdout_q = queue.Queue()
                self._reader_t = threading.Thread(
                    target=_enqueue_stdout, args=(self._proc, self._stdout_q), daemon=True
                )
                self._reader_t.start()
            try:
                self._proc.stdin.write(json.dumps(req) + "\n")
                self._proc.stdin.flush()
                import time as _t
                import queue

                deadline = _t.time() + timeout
                while _t.time() < deadline:
                    try:
                        rem = max(0.1, deadline - _t.time())
                        line = self._stdout_q.get(timeout=rem)
                    except queue.Empty:
                        raise RuntimeError("F5 worker response timeout")

                    if not line:
                        raise RuntimeError("F5 worker died mid-request")

                    line = line.strip()
                    if not (line.startswith("{") and line.endswith("}")):
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    status = msg.get("status")
                    if status == "progress":
                        deadline = _t.time() + timeout
                        continue
                    return msg
                raise RuntimeError("F5 worker response timeout")
            except Exception as e:
                log.warning(f"[F5-TTS] Persistent worker request failed ({e}) — disabling")
                self._failed = True
                self._cleanup_proc()
                return None

    def shutdown(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=10)
                except Exception:
                    pass
            self._cleanup_proc()


# Module-level singleton F5 worker (lazy-started on first use)
_f5_worker = _F5Worker()


def shutdown_f5_worker():
    """Stop the persistent F5-TTS worker (call at pipeline end)."""
    _f5_worker.shutdown()


# ── IndicF5 persistent worker ─────────────────────────────────────────────────


class _IndicF5Worker:
    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._failed = False
        self._stdout_q = None
        self._reader_t = None

    def _start(self) -> bool:
        if self._failed:
            return False
        if self._proc is not None and self._proc.poll() is None:
            return True
        try:
            python_exe = _resolve_indicf5_python()
            worker_script = Path(__file__).parent / "indicf5_worker.py"
            if not worker_script.exists():
                raise FileNotFoundError(f"indicf5_worker.py not found at {worker_script}")

            try:
                indicf5_cfg = load_config().get("tts", {}).get("indicf5", {})
            except Exception:
                indicf5_cfg = {}

            model_id = indicf5_cfg.get("model_id", "ai4bharat/IndicF5")
            cache_dir = indicf5_cfg.get("cache_dir", "hf_cache/indicf5")
            device = indicf5_cfg.get("device", "cuda")

            _indicf5_env = dict(os.environ)
            _indicf5_env.update(
                {
                    "WANDB_MODE": "disabled",
                    "WANDB_DISABLED": "true",
                    "WANDB_CONSOLE": "off",
                    "PYTHONIOENCODING": "utf-8",
                    "HF_HUB_DISABLE_XET": "1",
                    "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
                    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
                    "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
                }
            )

            self._proc = subprocess.Popen(
                [
                    python_exe,
                    str(worker_script),
                    "--serve",
                    f"--model-id={model_id}",
                    f"--cache-dir={cache_dir}",
                    f"--device={device}",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=_indicf5_env,
            )
            import queue

            self._stdout_q = queue.Queue()
            self._reader_t = threading.Thread(
                target=_enqueue_stdout, args=(self._proc, self._stdout_q), daemon=True
            )
            self._reader_t.start()

            import time as _t

            deadline = _t.time() + 300
            while _t.time() < deadline:
                try:
                    rem = max(0.1, deadline - _t.time())
                    line = self._stdout_q.get(timeout=rem)
                except queue.Empty:
                    raise RuntimeError("IndicF5 worker readiness timeout")

                if not line:
                    raise RuntimeError("IndicF5 worker exited during startup")

                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("status") == "ready":
                    log.info("[IndicF5] Persistent worker ready (model loaded once)")
                    return True
                if msg.get("status") == "error":
                    raise RuntimeError(msg.get("message", "IndicF5 worker init error"))
            raise RuntimeError("IndicF5 worker readiness timeout")
        except Exception as e:
            log.warning(
                f"[IndicF5] Persistent worker unavailable ({e}) — will fall back to supertonic"
            )
            self._failed = True
            self._cleanup_proc()
            return False

    def _cleanup_proc(self):
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.kill()
            self._proc = None
        self._stdout_q = None
        self._reader_t = None

    def generate(self, req: dict[str, Any], timeout: float = 600) -> dict[str, Any] | None:
        with self._lock:
            if not self._start():
                return None
            if self._stdout_q is None and self._proc is not None:
                import queue

                self._stdout_q = queue.Queue()
                self._reader_t = threading.Thread(
                    target=_enqueue_stdout, args=(self._proc, self._stdout_q), daemon=True
                )
                self._reader_t.start()
            try:
                self._proc.stdin.write(json.dumps(req) + "\n")
                self._proc.stdin.flush()
                import time as _t
                import queue

                deadline = _t.time() + timeout
                while _t.time() < deadline:
                    try:
                        rem = max(0.1, deadline - _t.time())
                        line = self._stdout_q.get(timeout=rem)
                    except queue.Empty:
                        raise RuntimeError("IndicF5 worker response timeout")

                    if not line:
                        raise RuntimeError("IndicF5 worker died mid-request")

                    line = line.strip()
                    if not (line.startswith("{") and line.endswith("}")):
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    status = msg.get("status")
                    if status == "progress":
                        deadline = _t.time() + timeout
                        continue
                    return msg
                raise RuntimeError("IndicF5 worker response timeout")
            except Exception as e:
                log.warning(f"[IndicF5] Persistent worker request failed ({e}) — disabling")
                self._failed = True
                self._cleanup_proc()
                return None

    def shutdown(self):
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(json.dumps({"cmd": "shutdown"}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=10)
                except Exception:
                    pass
            self._cleanup_proc()


_indicf5_worker = _IndicF5Worker()


def shutdown_indicf5_worker():
    """Stop the persistent IndicF5 worker (call at pipeline end)."""
    _indicf5_worker.shutdown()


def _call_indicf5_worker(
    text: str,
    lang: str = "hi",
    output_dir: Path | None = None,
    voice_sample: str = "",
    speed_override: float | None = None,
) -> dict[str, Any]:
    """Generate IndicF5 TTS audio."""
    try:
        indicf5_cfg = load_config().get("tts", {}).get("indicf5", {})
    except Exception:
        indicf5_cfg = {}

    ref_text = indicf5_cfg.get("ref_text", "") or ""
    ref_audio = indicf5_cfg.get("ref_audio", "") or ""
    sample_rate = int(indicf5_cfg.get("sample_rate", 24000))
    nfe_step = int(indicf5_cfg.get("nfe_step", 16))
    speed = (
        float(speed_override)
        if speed_override is not None
        else float(indicf5_cfg.get("speed", 1.0))
    )
    timeout = int(indicf5_cfg.get("timeout_seconds", 600))
    max_chars = int(indicf5_cfg.get("max_chars_per_chunk", 220))

    if output_dir is None:
        output_dir = Path("tts_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_wav = output_dir / f"indicf5_{uuid.uuid4().hex[:8]}.wav"

    final_ref_audio = ref_audio if (ref_audio and Path(ref_audio).exists()) else ""
    if not final_ref_audio and voice_sample and Path(voice_sample).exists():
        final_ref_audio = voice_sample

    req = {
        "text": text,
        "output": str(out_wav),
        "ref_audio": final_ref_audio,
        "ref_text": ref_text,
        "sample_rate": sample_rate,
        "nfe_step": nfe_step,
        "speed": speed,
        "max_chars_per_chunk": max_chars,
    }

    resp = _indicf5_worker.generate(req, timeout=timeout)
    if resp is not None:
        return resp

    log.info("[IndicF5] Using one-shot subprocess fallback")
    try:
        python_exe = _resolve_indicf5_python()
        worker_script = Path(__file__).parent / "indicf5_worker.py"

        if not worker_script.exists():
            raise FileNotFoundError(f"indicf5_worker.py not found at {worker_script}")

        temp_dir = Path("studio_checkpoints") / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / f"indicf5_input_{uuid.uuid4().hex}.txt"
        temp_file.write_text(text, encoding="utf-8", errors="replace")

        cmd = [
            python_exe,
            str(worker_script),
            f"--text-file={temp_file}",
            f"--output={out_wav}",
            f"--model-id={indicf5_cfg.get('model_id', 'ai4bharat/IndicF5')}",
            f"--cache-dir={indicf5_cfg.get('cache_dir', 'hf_cache/indicf5')}",
            f"--device={indicf5_cfg.get('device', 'cuda')}",
            f"--sample-rate={sample_rate}",
            f"--nfe-step={nfe_step}",
            f"--speed={speed}",
        ]
        if final_ref_audio:
            cmd.append(f"--ref-audio={final_ref_audio}")
        if ref_text:
            cmd.append(f"--ref-text={ref_text}")
        if max_chars:
            cmd.append(f"--max-chars={max_chars}")

        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout + 60
        )
        with contextlib.suppress(Exception):
            temp_file.unlink()

        if result.returncode == 0 and result.stdout.strip():
            for line in reversed(result.stdout.strip().split("\n")):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
        error_msg = result.stderr.strip()[:200] if result.stderr else "Unknown error"
        log.error(f"[IndicF5] one-shot failed (code {result.returncode}): {error_msg}")
        return {"status": "error", "message": error_msg}
    except Exception as e:
        log.exception(f"[IndicF5] one-shot exception: {e}")
        return {"status": "error", "message": str(e)[:200]}


def _call_f5_worker(
    text: str,
    lang: str = "hi",
    output_dir: Path | None = None,
    voice_sample: str = "",
    speed_override: float | None = None,
) -> dict[str, Any]:
    """Generate F5-TTS audio.

    T1: Tries the persistent worker first (model stays loaded across segments).
    Falls back to a one-shot subprocess if the persistent worker is unavailable.
    Returns a result dict with status/wav_path keys (same shape as omnivoice result).
    """
    try:
        f5_cfg = load_config().get("tts", {}).get("f5", {})
    except Exception:
        f5_cfg = {}

    nfe_step = int(f5_cfg.get("nfe_step", 16))
    ref_text = f5_cfg.get("ref_text", "") or ""
    speed = float(speed_override) if speed_override is not None else 1.0

    # Prefer an explicit ref_audio from config (a short mono clip optimized for
    # cloning) over the passed-in voice_sample (often the long stereo original).
    _cfg_ref_audio = f5_cfg.get("ref_audio", "") or ""
    if _cfg_ref_audio and Path(_cfg_ref_audio).exists():
        voice_sample = _cfg_ref_audio

    if output_dir is None:
        output_dir = Path("tts_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_wav = output_dir / f"f5_{uuid.uuid4().hex[:8]}.wav"

    req = {
        "text": text,
        "output": str(out_wav),
        "voice_sample": voice_sample if (voice_sample and Path(voice_sample).exists()) else "",
        "ref_text": ref_text,
        "nfe_step": nfe_step,
        "speed": speed,
    }

    # Try persistent worker first
    resp = _f5_worker.generate(req)
    if resp is not None:
        return resp

    # Fallback: one-shot subprocess
    log.info("[F5-TTS] Using one-shot subprocess fallback")
    try:
        python_exe = Path(sys.executable)
        worker_script = Path(__file__).parent / "f5_worker.py"
        try:
            _f5_cfg2 = load_config().get("tts", {}).get("f5", {})
            _model_path = _f5_cfg2.get(
                "model_path", "hf_cache/hub/models--SPRINGLab--F5-Hindi-24KHz/snapshots/main"
            )
        except Exception:
            _model_path = "hf_cache/hub/models--SPRINGLab--F5-Hindi-24KHz/snapshots/main"

        # Resolve HF hub snapshot layout
        if not Path(_model_path).exists():
            try:
                from audio.f5_worker import _resolve_model_path as _fp_resolve2

                _model_path = _fp_resolve2(_model_path)
            except Exception:
                pass

        temp_dir = Path("studio_checkpoints") / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / f"f5_input_{uuid.uuid4().hex}.txt"
        temp_file.write_text(text, encoding="utf-8", errors="replace")

        cmd = [
            str(python_exe),
            str(worker_script),
            f"--text-file={temp_file}",
            f"--output={out_wav}",
            f"--model-path={_model_path}",
            f"--nfe-step={nfe_step}",
            f"--speed={speed}",
        ]
        if voice_sample and Path(voice_sample).exists():
            cmd.append(f"--voice-sample={voice_sample}")
        if ref_text:
            cmd.append(f"--ref-text={ref_text}")

        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=600)
        with contextlib.suppress(Exception):
            temp_file.unlink()

        if result.returncode == 0 and result.stdout.strip():
            for line in reversed(result.stdout.strip().split("\n")):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
        error_msg = result.stderr.strip()[:200] if result.stderr else "Unknown error"
        log.error(f"[F5-TTS] one-shot failed (code {result.returncode}): {error_msg}")
        return {"status": "error", "message": error_msg}
    except Exception as e:
        log.exception(f"[F5-TTS] one-shot exception: {e}")
        return {"status": "error", "message": str(e)[:200]}


def _call_omnivoice_worker(
    text: str,
    lang: str = "hi",
    output_dir: Path | None = None,
    voice_sample: str = "",
    speed_override: float | None = None,
    sentence_gap_ms: int | None = None,
) -> dict[str, Any]:
    """Generate OmniVoice TTS.

    B16 fix: tries the persistent worker first (model stays loaded across segments),
    falling back to a one-shot subprocess if the persistent worker is unavailable.
    speed_override: when set, overrides the config speed for this call (B9 fix).
    sentence_gap_ms: when set, overrides the inter-chunk gap (P4-9 fix).
    """
    omnivoice_cfg = {}
    with contextlib.suppress(Exception):
        omnivoice_cfg = load_config().get("tts", {}).get("omnivoice", {})

    speed = omnivoice_cfg.get("speed", 0.85)
    num_step = omnivoice_cfg.get("num_step", 24)
    guidance_scale = omnivoice_cfg.get("guidance_scale", 2.5)

    # B9 fix: per-call speed override (mood-based) takes precedence over config
    if speed_override is not None:
        speed = float(speed_override)
        log.debug(f"OmniVoice: using mood-based speed override {speed:.2f}")

    if output_dir is None:
        output_dir = Path("tts_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_wav = output_dir / f"omnivoice_{uuid.uuid4().hex[:8]}.wav"

    req = {
        "text": text,
        "output": str(out_wav),
        "voice_sample": voice_sample if (voice_sample and Path(voice_sample).exists()) else "",
        "speed": speed,
        "num_step": num_step,
        "guidance_scale": guidance_scale,
        # Supplying the reference transcript skips the Whisper ASR load that OOMs
        # on ≤8GB GPUs (OmniVoice issue #41). Set tts.omnivoice.ref_text in config.
        "ref_text": omnivoice_cfg.get("ref_text", "") or "",
    }
    if sentence_gap_ms is not None:
        req["sentence_gap_ms"] = int(sentence_gap_ms)

    # ── 1. Try persistent worker (B16 fix) ─────────────────────────────────
    resp = _omnivoice_worker.generate(req)
    if resp is not None:
        return resp

    # ── 2. Fallback: one-shot subprocess (original behavior) ───────────────
    log.info("[OmniVoice] Using one-shot subprocess fallback")
    return _call_omnivoice_oneshot(
        text,
        output_dir=output_dir,
        out_wav=out_wav,
        voice_sample=voice_sample,
        speed=speed,
        num_step=num_step,
        guidance_scale=guidance_scale,
        ref_text=omnivoice_cfg.get("ref_text", "") or "",
    )


def _call_omnivoice_oneshot(
    text: str,
    output_dir: Path,
    out_wav: Path,
    voice_sample: str = "",
    speed: float = 0.85,
    num_step: int = 24,
    guidance_scale: float = 2.5,
    ref_text: str = "",
) -> dict[str, Any]:
    """One-shot OmniVoice subprocess (model loads per call — fallback path).

    P2-13 fix: accepts ref_text and passes --ref-text to the worker so OmniVoice
    skips the Whisper ASR load that OOMs on 6GB GPUs (issue #41).
    """
    python_exe = _resolve_omnivoice_python()
    worker_script = Path(__file__).parent / "omnivoice_worker.py"

    temp_dir = Path("studio_checkpoints") / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / f"omnivoice_input_{uuid.uuid4().hex}.txt"

    try:
        temp_file.write_text(text, encoding="utf-8", errors="replace")
        cmd = [
            str(python_exe),
            str(worker_script),
            f"--text-file={temp_file}",
            f"--output={out_wav}",
            f"--speed={speed}",
            f"--num-step={num_step}",
            f"--guidance-scale={guidance_scale}",
        ]
        if voice_sample and Path(voice_sample).exists():
            cmd.append(f"--voice-sample={voice_sample}")
        # P2-13 fix: pass --ref-text so OmniVoice skips the Whisper ASR load (OOM fix).
        if ref_text:
            cmd.append(f"--ref-text={ref_text}")

        log.info("Calling omnivoice_worker (one-shot)...")
        _oneshot_env = dict(os.environ)
        _oneshot_env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=600, env=_oneshot_env
        )

        if result.returncode == 0 and result.stdout.strip():
            for line in reversed(result.stdout.strip().split("\n")):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue
            return {"status": "error", "message": "No JSON found in response"}
        error_msg = result.stderr.strip() if result.stderr else "Unknown error"
        log.error(f"OmniVoice one-shot failed (code {result.returncode}): {error_msg[:200]}")
        return {"status": "error", "message": error_msg[:200]}
    except Exception as e:
        log.exception(f"Failed to call OmniVoice worker: {e}")
        return {"status": "error", "message": str(e)[:200]}
    finally:
        try:
            if temp_file.exists():
                temp_file.unlink()
        except Exception:
            pass


def translate_hinglish(text: str, seg: int = 0) -> str:
    """Translate English to natural Romanized Hinglish using local Ollama model.

    Falls back to edge/google translator if Ollama is not accessible.

    Args:
        text: English text to translate

    Returns:
        Translated Hinglish text or original on failure
    """
    log.info("Translating English script to Romanized Hinglish using Ollama LLM...")

    cfg = {}
    try:
        cfg = load_config()
    except Exception as e:
        log.warning(f"Could not load config in translate_hinglish: {e}")

    # P3-9 fix: use the translator model (not the creative writer model).
    # Fall back to writer if translator is not configured so existing setups
    # continue to work without a config change.
    model = cfg.get("models", {}).get(
        "translator", cfg.get("models", {}).get("writer", "zephyr-writer")
    )
    host = cfg.get("ollama", {}).get("host", "http://localhost:11434")
    f"{host.rstrip('/')}/api/generate"

    engine = cfg.get("tts", {}).get("engine", "edge")
    from config.config import get_language

    tts_lang = get_language(cfg)

    # P3-9 fix: when tts.lang == "hi" (Devanagari is the preferred output),
    # always use the Devanagari prompt regardless of the TTS engine.  The
    # Romanized-Hinglish path is only appropriate when the engine is "edge"
    # AND the operator has explicitly chosen a non-Devanagari language.
    if engine == "edge" and tts_lang != "hi":
        prompt = (
            "You are an expert bilingual translator. Translate the following English story narration into natural, conversational Hinglish.\n"
            "CRITICAL INSTRUCTION: You MUST write the ENTIRE translation using the English/Latin alphabet (Romanized Hindi). "
            "Do NOT use Devanagari script. Do NOT include any explanations, prefaces, or thinking tags. Output ONLY the translated text.\n\n"
            f"English story script to translate (see <script>...</script> below):\n<script>{html.escape(text)}</script>\n\n"
            "Hinglish translation:"
        )
    else:
        prompt = (
            "You are an expert bilingual translator. Translate the following English story narration into simple, conversational spoken Hindi.\n"
            "Write like a normal person telling a story to a friend - use everyday Hindi words, not literary or bookish Hindi.\n"
            "CRITICAL RULES:\n"
            "1. Write EVERYTHING in Devanagari script (e.g. है, और, लेकिन, में, को)\n"
            "2. Use simple spoken Hindi words, NOT complex literary/Sanskrit/Urdu words\n"
            "3. For common English words used in daily Hindi speech (phone, car, bus, school, doctor, police, video, camera, cafe), write them phonetically in Devanagari (फोन, कार, बस, स्कूल, डॉक्टर, पुलिस, वीडियो, कैमरा, कैफे)\n"
            "4. Do NOT use ANY English/Latin letters\n"
            "5. Spell out all numbers in Hindi words (100 = सौ, 5 = पांच)\n"
            "6. Keep sentences short and natural with normal pauses\n"
            "7. Do NOT add speaker labels or tags\n"
            "8. Output ONLY the translated Devanagari text, nothing else\n\n"
            f"English script to translate:\n<script>{html.escape(text)}</script>\n\n"
            "Hindi translation:"
        )

    # B1: delegate to the centralized OllamaClient (circuit breaker + unified retry).
    try:
        from utils.ollama_client import get_ollama_client

        _client = get_ollama_client(cfg)
        translated = _client.generate(prompt, model=model, temperature=0.3)
        if translated:
            # Clean LLM chat template tokens / markdown (client already strips <think>)
            translated = re.sub(r"<\|.*?\|>", "", translated).strip()
            if translated.startswith("```"):
                translated = re.sub(r"^```[a-zA-Z]*\n", "", translated)
                translated = re.sub(r"\n```$", "", translated)
                translated = translated.strip()
            if translated:
                log.info(f"Ollama Hinglish Translation successful: {len(translated)} chars")
                return translated
    except Exception as e:
        log.warning(f"Ollama Hinglish translation failed: {e}. Falling back to original text.")

    # Fallback: return original text
    log.warning("Translation failed, using original English text")
    try:
        from agents.director_agent import UIState

        UIState.add_degradation(
            seg, "translation_fallback", "Ollama translation failed, using English"
        )
    except Exception as e:
        log.warning(f"Could not add translation degradation to UIState: {e}")
    return text


def tts_generate(
    text: str,
    lang: str = "hi",
    slow: bool = False,
    output_dir: Path | None = None,
    voice_sample: Path | None = None,
    speed: float | None = None,
) -> dict:
    """Generate TTS audio using configured engine.

    Args:
        text: Text to convert to speech
        lang: Language code ("hi" for Hindi, "en" for English, etc)
        slow: Slow down speech (not used in current implementation)
        output_dir: Directory to save output WAV
        voice_sample: Voice sample for voice cloning
        speed: Per-call speed override (mood-based, from get_mood_rate). B9 fix.
               When None, uses the config default.
    """
    if output_dir is None:
        output_dir = Path("tts_output")

    output_dir.mkdir(parents=True, exist_ok=True)

    # BUG-06 FIX + OPT-03: load config ONCE using module-level cache.
    # Previously called load_config() 2-3 times per tts_generate invocation.
    _cfg = _get_config()

    # BUG-13 FIX: start from config defaults so explicit config values always win,
    # not the hardcoded fallback dict below.
    tts_cfg = _cfg.get("tts", {})
    vp_cfg = tts_cfg.get("voice_profile", {})
    edge_cfg = tts_cfg.get("edge", {})
    voice_profile = {
        "edge_voice": edge_cfg.get("voice", vp_cfg.get("edge_voice", "hi-IN-MadhurNeural")),
        "edge_rate": edge_cfg.get("rate", vp_cfg.get("edge_rate", "+5%")),
        "edge_volume": edge_cfg.get("volume", vp_cfg.get("edge_volume", "+0%")),
        "sentence_gap_ms": vp_cfg.get("sentence_gap_ms", 200),
    }
    if vp_cfg:
        log.debug(f"Voice profile loaded from config: {voice_profile}")

    # Always load narration voice sample for the entire segment, ignoring character-specific samples
    if voice_sample is None:
        # P4-11 fix: prefer narration_voice.wav explicitly (deterministic selection).
        # Exclude *_ref8s_mono* files — those are trimmed cache artifacts created by
        # _prepare_ref_audio and should not be used as the primary reference.
        narrator_sample = Path("character_voices/narration_voice.wav")
        if narrator_sample.exists():
            voice_sample = narrator_sample
            log.info(f"Voice cloning: using narration sample '{narrator_sample}'")
        else:
            # Auto-detect any wav file in the character_voices directory,
            # excluding the *_ref8s_mono* cache files.
            voices_dir = Path("character_voices")
            if voices_dir.exists():
                wav_files = [
                    f
                    for f in voices_dir.glob("*.wav")
                    if "_ref" not in f.stem and "mono" not in f.stem
                ]
                if wav_files:
                    # Sort for deterministic selection (alphabetical)
                    wav_files.sort()
                    voice_sample = wav_files[0]
                    log.info(f"Voice cloning: auto-detected narration sample '{voice_sample}'")

    # P1-7 fix: normalize the engine string from config (which may have been set
    # from the vision doc overlay) to a known engine id before dispatching.
    # Keep the documented tts_generate() behavior for truly unknown strings:
    # route them directly to edge instead of silently attempting F5.
    _raw_engine = _cfg.get("tts", {}).get("engine", "supertonic")
    if isinstance(_raw_engine, str) and _raw_engine.strip().lower() not in (
        _SUPERTONIC_ALIASES | _F5_ALIASES | _OMNIVOICE_ALIASES | _EDGE_ALIASES | _INDICF5_ALIASES
    ):
        log.warning(f"Unknown TTS engine '{_raw_engine}' — falling back to edge-tts")
        engine = "edge"
    else:
        engine = normalize_tts_engine(_raw_engine)

    log.info(f"Generating TTS audio ({lang}) using {engine}...")

    # R12.3: engine registry — adding a new engine = adding one adapter here,
    # no change to the per-segment pipeline flow.
    if engine == "supertonic":
        result = _call_supertonic_worker(
            text,
            lang=lang,
            output_dir=output_dir,
            speed_override=speed,
        )
        if result.get("status") != "success":
            log.warning(
                f"[TTS] Supertonic failed ({result.get('message', 'unknown')}) — degrading to omnivoice"
            )
            result = _call_omnivoice_worker(
                text,
                lang=lang,
                output_dir=output_dir,
                voice_sample=str(voice_sample) if voice_sample else "",
                speed_override=speed,
                sentence_gap_ms=voice_profile.get("sentence_gap_ms"),
            )
        if result.get("status") != "success":
            log.warning("[TTS] omnivoice fallback also failed — degrading to edge")
            result = _call_edge_direct(
                text, lang=lang, output_dir=output_dir, voice_profile=voice_profile, speed=speed
            )
    elif engine == "f5":
        result = _call_f5_worker(
            text,
            lang=lang,
            output_dir=output_dir,
            voice_sample=str(voice_sample) if voice_sample else "",
            speed_override=speed,
        )
        if result.get("status") != "success":
            # F5 failed — degrade to omnivoice
            log.warning("[TTS] F5 failed — degrading to omnivoice")
            try:
                from agents.director_agent import UIState as _UIState_tts

                _UIState_tts.add_degradation(
                    0, "tts_engine_fallback", "F5-TTS failed, using omnivoice"
                )
            except Exception as e:
                log.warning(f"Could not add tts degradation: {e}")
            result = _call_omnivoice_worker(
                text,
                lang=lang,
                output_dir=output_dir,
                voice_sample=str(voice_sample) if voice_sample else "",
                speed_override=speed,
                sentence_gap_ms=voice_profile.get("sentence_gap_ms"),
            )
        if result.get("status") != "success":
            # omnivoice also failed — degrade to edge
            log.warning("[TTS] omnivoice fallback also failed — degrading to edge")
            try:
                from agents.director_agent import UIState as _UIState_tts2

                _UIState_tts2.add_degradation(
                    0, "tts_engine_fallback", "omnivoice failed, using edge"
                )
            except Exception as e:
                log.warning(f"Could not add tts degradation: {e}")
            result = _call_edge_direct(
                text, lang=lang, output_dir=output_dir, voice_profile=voice_profile, speed=speed
            )
    elif engine == "omnivoice":
        # B9 fix: pass per-call speed override (mood-based) to OmniVoice
        # P4-9 fix: pass sentence_gap_ms from voice_profile so the worker uses
        # the config value instead of a hardcoded gap.
        result = _call_omnivoice_worker(
            text,
            lang=lang,
            output_dir=output_dir,
            voice_sample=str(voice_sample) if voice_sample else "",
            speed_override=speed,
            sentence_gap_ms=voice_profile.get("sentence_gap_ms"),
        )
    elif engine == "edge":
        # P1-8 fix: pass mood-based speed through to edge-tts rate conversion
        result = _call_edge_direct(
            text, lang=lang, output_dir=output_dir, voice_profile=voice_profile, speed=speed
        )
    elif engine == "indicf5":
        result = _call_indicf5_worker(
            text,
            lang=lang,
            output_dir=output_dir,
            voice_sample=str(voice_sample) if voice_sample else "",
            speed_override=speed,
        )
        if result.get("status") != "success":
            log.warning("[TTS] IndicF5 failed — degrading to supertonic")
            try:
                from agents.director_agent import UIState as _UIState_tts_if5

                _UIState_tts_if5.add_degradation(
                    0, "tts_engine_fallback", "IndicF5 failed, using supertonic"
                )
            except Exception as e:
                log.warning(f"Could not add tts degradation: {e}")
            result = _call_supertonic_worker(
                text,
                lang=lang,
                output_dir=output_dir,
                speed_override=speed,
            )
        if result.get("status") != "success":
            log.warning("[TTS] supertonic fallback also failed — degrading to omnivoice")
            try:
                from agents.director_agent import UIState as _UIState_tts_if52

                _UIState_tts_if52.add_degradation(
                    0, "tts_engine_fallback", "supertonic failed, using omnivoice"
                )
            except Exception as e:
                log.warning(f"Could not add tts degradation: {e}")
            result = _call_omnivoice_worker(
                text,
                lang=lang,
                output_dir=output_dir,
                voice_sample=str(voice_sample) if voice_sample else "",
                speed_override=speed,
                sentence_gap_ms=voice_profile.get("sentence_gap_ms"),
            )
        if result.get("status") != "success":
            log.warning("[TTS] omnivoice fallback also failed — degrading to edge")
            result = _call_edge_direct(
                text, lang=lang, output_dir=output_dir, voice_profile=voice_profile, speed=speed
            )
    else:
        # Unknown engine → documented fallback to edge-tts (R12.7)
        log.warning(f"Unknown TTS engine '{engine}' — falling back to edge-tts")
        # P1-8 fix: pass mood-based speed through to edge-tts rate conversion
        result = _call_edge_direct(
            text, lang=lang, output_dir=output_dir, voice_profile=voice_profile, speed=speed
        )

    if result.get("status") == "success":
        wav_path = Path(result.get("wav_path", ""))
        word_timestamps = result.get("word_timestamps")
        if word_timestamps:
            word_timestamps = Path(word_timestamps)
            if not word_timestamps.exists():
                word_timestamps = None

        if wav_path.exists():
            log.info(f"TTS generated: {wav_path}")
            return {"wav_path": wav_path, "word_timestamps": word_timestamps}
        log.error(f"TTS returned path that doesn't exist: {wav_path}")
        raise RuntimeError(f"TTS file not found at {wav_path}")

    # If failed, raise with error details
    msg = result.get("message", "Unknown TTS error")
    log.error(f"TTS generation failed: {msg}")
    raise RuntimeError(f"TTS generation failed: {msg}")


def get_audio_duration(wav_path: Path) -> float:
    """Get audio duration using ffprobe (delegates to shared utils.get_audio_duration).

    Args:
        wav_path: Path to audio file

    Returns:
        Duration in seconds (or 30.0 if error)
    """
    return _get_audio_duration_utils(wav_path)


def rvc_convert(
    src_wav: Path,
    output_dir: Path | None = None,
    rvc_model: Path | None = None,
    rvc_index: Path | None = None,
) -> Path:
    """RVC voice conversion using rvc_worker.py subprocess.

    Calls rvc_worker.py to convert TTS output through an RVC voice model.
    Falls back to returning original if RVC fails or no model is configured.

    Args:
        src_wav: Source WAV path (TTS output)
        output_dir: Output directory for converted WAV
        rvc_model: Path to .pth RVC model file
        rvc_index: Optional path to .index file

    Returns:
        Converted WAV path, or original if RVC unavailable/fails
    """
    # Load RVC config
    rvc_cfg = {}
    with contextlib.suppress(Exception):
        rvc_cfg = load_config().get("rvc", {})

    if not rvc_cfg.get("enabled", False):
        log.info("RVC disabled in config — returning original audio")
        return src_wav

    model_path = rvc_model or Path(rvc_cfg.get("model_path", ""))
    index_path = (
        rvc_index or Path(rvc_cfg.get("index_path", "")) if rvc_cfg.get("index_path") else None
    )

    if not model_path or not Path(model_path).exists():
        log.warning(f"RVC model not found: {model_path} — returning original audio")
        return src_wav

    if output_dir is None:
        output_dir = src_wav.parent
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    uid = uuid.uuid4().hex[:8]
    output_wav = output_dir / f"rvc_converted_{uid}.wav"

    # Build rvc_worker.py command
    python_exe = Path(sys.executable)
    worker_script = Path(__file__).parent.parent / "utils" / "rvc_worker.py"

    index_rate = rvc_cfg.get("index_rate", 0.75)
    protect = rvc_cfg.get("protect", 0.33)

    cmd = [
        str(python_exe),
        str(worker_script),
        f"--input={src_wav}",
        f"--model={model_path}",
        f"--output={output_wav}",
        f"--pitch={rvc_cfg.get('pitch_shift', 0)}",
        f"--f0={rvc_cfg.get('f0_method', 'harvest')}",
        f"--index_rate={index_rate}",
        f"--protect={protect}",
    ]
    if index_path and Path(index_path).exists():
        cmd.append(f"--index={index_path}")

    log.info(f"Running RVC voice conversion (model: {Path(model_path).name})...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=300)

        for line in result.stdout.strip().split("\n"):
            if line.startswith("{"):
                try:
                    resp = json.loads(line)
                    if resp.get("status") == "success":
                        log.info(f"RVC conversion complete: {output_wav}")
                        return output_wav
                except json.JSONDecodeError:
                    continue

        log.warning(
            f"RVC conversion failed: {result.stderr[:200] if result.stderr else 'unknown'} — returning original"
        )
        return src_wav

    except subprocess.TimeoutExpired:
        log.warning("RVC conversion timeout (300s) — returning original audio")
        return src_wav
    except Exception as e:
        log.warning(f"RVC conversion error: {e} — returning original audio")
        return src_wav


# ── Engine capability profiles (R12.5) ────────────────────────────────────────


def tts_capabilities() -> dict[str, dict[str, Any]]:
    """Return a documented capability profile per TTS engine.

    Used by the model-eval harness and operators to compare engine options.
    Candidate engines (IndicF5, OpenVoice v2) can be added here once integrated.
    """
    return {
        "supertonic": {
            "voice_cloning": True,
            "languages": ["hi", "en", "multi", "31 langs"],
            "vram_hint_gb": 0.0,
            "notes": "Default. CPU ONNX, 4.5x faster than OmniVoice. Zero VRAM. Custom voice JSON.",
            "recommended": {
                "voice": "character_voices/dhruv_voice_polished.json",
                "steps": 16,
                "speed": 1.0,
            },
        },
        "omnivoice": {
            "voice_cloning": True,
            "languages": ["hi", "en", "multi"],
            "vram_hint_gb": 4.0,
            "notes": "GPU-based fallback. Voice cloning from a reference sample. Persistent worker supported.",
            "recommended": {"speed": 0.85, "num_step": 40, "guidance_scale": 2.5},
        },
        "f5": {
            "voice_cloning": True,
            "languages": ["hi", "en"],
            "vram_hint_gb": 3.0,
            "notes": "F5-TTS. Hindi fine-tune. GPU-based. Falls back to omnivoice.",
            "recommended": {"model": "SPRINGLab/F5-Hindi-24KHz", "nfe_step": 16},
        },
        "edge": {
            "voice_cloning": False,
            "languages": ["hi", "en", "multi"],
            "vram_hint_gb": 0.0,
            "notes": "Cloud edge-tts fallback. No cloning. Fast. Requires internet.",
            "recommended": {"voice": "hi-IN-MadhurNeural", "rate": "+5%"},
        },
    }


# Backward compatibility alias for discoverability
get_tts_capabilities = tts_capabilities


# Backward compatibility exports
__all__ = [
    "get_audio_duration",
    "normalize_tts_engine",
    "rvc_convert",
    "shutdown_f5_worker",
    "shutdown_omnivoice_worker",
    "shutdown_supertonic_worker",
    "translate_hinglish",
    "tts_capabilities",
    "tts_generate",
]
