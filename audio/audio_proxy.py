"""audio_proxy.py - TTS audio generation proxy.

This module provides TTS generation using Supertonic and OmniVoice engines.

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


# TTS engine normalization whitelist.
# Vision docs and user responses can contain arbitrary strings (e.g.
# "Calm, measured, storytelling voice"). Map everything to the supported
# engine ids that tts_generate dispatches: "supertonic" or "omnivoice".
_OMNIVOICE_ALIASES = frozenset({"omnivoice", "omni", "voice_clone", "clone"})
_SUPERTONIC_ALIASES = frozenset({"supertonic", "supertone", "supertonic3", "supertonic-3"})


def normalize_tts_engine(engine: str) -> str:
    """Normalize an arbitrary TTS engine string to a supported engine id.

    Supported engines: "supertonic" (default) and "omnivoice".
    Everything else (including free-text descriptions) → "supertonic" (default).

    Args:
        engine: Raw engine string from vision doc, config overlay, or user input.

    Returns:
        "supertonic" or "omnivoice".
    """
    if not isinstance(engine, str):
        log.warning(
            f"[TTS] normalize_tts_engine: non-string engine value {engine!r} — defaulting to 'supertonic'"
        )
        return "supertonic"

    normalized = engine.strip().lower()
    if normalized in _OMNIVOICE_ALIASES:
        return "omnivoice"
    if normalized in _SUPERTONIC_ALIASES:
        return "supertonic"

    log.warning(
        f"[TTS] Unknown TTS engine string {engine!r} — defaulting to 'supertonic'."
    )
    return "supertonic"


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
                except queue.Empty as exc:
                    raise RuntimeError("Supertonic worker readiness timeout") from exc

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
            with contextlib.suppress(OSError):
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
                import queue
                import time as _t

                deadline = _t.time() + timeout
                while _t.time() < deadline:
                    try:
                        rem = max(0.1, deadline - _t.time())
                        line = self._stdout_q.get(timeout=rem)
                    except queue.Empty as exc:
                        raise RuntimeError("Supertonic worker response timeout") from exc

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
    with contextlib.suppress(FileNotFoundError):
        super_cfg = _get_config().get("tts", {}).get("supertonic", {})

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
                except queue.Empty as exc:
                    raise RuntimeError("worker readiness timeout") from exc

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
            with contextlib.suppress(OSError):
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
                import queue
                import time as _t

                # timeout is an IDLE timeout: it resets each time the worker emits a
                # line (including progress), so total time scales with the work done.
                deadline = _t.time() + timeout
                while _t.time() < deadline:
                    try:
                        rem = max(0.1, deadline - _t.time())
                        line = self._stdout_q.get(timeout=rem)
                    except queue.Empty as exc:
                        raise RuntimeError(
                            "worker response timeout (no progress within idle window)"
                        ) from exc

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
    with contextlib.suppress(FileNotFoundError):
        omnivoice_cfg = _get_config().get("tts", {}).get("omnivoice", {})

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

    Falls back to the original text if Ollama is not accessible.

    Args:
        text: English text to translate

    Returns:
        Translated Hinglish text or original on failure
    """
    log.info("Translating English script to Romanized Hinglish using Ollama LLM...")

    cfg = {}
    try:
        cfg = _get_config()
    except Exception as e:
        log.warning(f"Could not load config in translate_hinglish: {e}")

    # P3-9 fix: use the translator model (not the creative writer model).
    # Fall back to writer if translator is not configured so existing setups
    # continue to work without a config change.
    model = cfg.get("models", {}).get(
        "translator", cfg.get("models", {}).get("writer", "zephyr-writer")
    )
    from config.config import get_language

    tts_lang = get_language(cfg)

    # P3-9 fix: when tts.lang == "hi" (Devanagari is the preferred output),
    # always use the Devanagari prompt regardless of the TTS engine.  The
    # Romanized-Hinglish path is only appropriate when the operator has
    # explicitly chosen a non-Devanagari language.
    if tts_lang != "hi":
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
    output_dir: Path | None = None,
    voice_sample: Path | None = None,
    speed: float | None = None,
) -> dict:
    """Generate TTS audio using configured engine.

    Args:
        text: Text to convert to speech
        lang: Language code ("hi" for Hindi, "en" for English, etc)
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
    voice_profile = {
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

    _raw_engine = _cfg.get("tts", {}).get("engine", "supertonic")
    engine = normalize_tts_engine(_raw_engine)

    log.info(f"Generating TTS audio ({lang}) using {engine}...")

    if engine == "omnivoice":
        result = _call_omnivoice_worker(
            text,
            lang=lang,
            output_dir=output_dir,
            voice_sample=str(voice_sample) if voice_sample else "",
            speed_override=speed,
            sentence_gap_ms=voice_profile.get("sentence_gap_ms"),
        )
    else:
        # Default: Supertonic (CPU ONNX, zero VRAM). Degrade to OmniVoice on failure.
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
    if result.get("status") == "success":
        wav_path = Path(result.get("wav_path", ""))
        word_timestamps = result.get("word_timestamps")
        if word_timestamps:
            word_timestamps = Path(word_timestamps)
            if not word_timestamps.exists():
                word_timestamps = None

        if wav_path.exists():
            if word_timestamps is None:
                align_cfg = _cfg.get("tts", {}).get("alignment", {}) or {}
                if align_cfg.get("enabled", True):
                    try:
                        from audio.tts_alignment import align_audio

                        aligned = align_audio(
                            wav_path,
                            model_name=align_cfg.get("model", "base"),
                            device=align_cfg.get("device", "cpu"),
                            compute_type=align_cfg.get("compute_type", "int8"),
                        )
                        if aligned and Path(aligned).exists():
                            word_timestamps = Path(aligned)
                    except Exception as align_err:
                        log.warning(f"TTS alignment failed for {wav_path.name}: {align_err}")
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


# ── Engine capability profiles (R12.5) ────────────────────────────────────────


def tts_capabilities() -> dict[str, dict[str, Any]]:
    """Return a documented capability profile per TTS engine.

    Used by the model-eval harness and operators to compare engine options.
    """
    return {
        "supertonic": {
            "voice_cloning": True,
            "languages": ["hi", "en", "multi", "31 langs"],
            "vram_hint_gb": 0.0,
            "notes": "Default. CPU ONNX, 4.5x faster than OmniVoice. Zero VRAM. Custom voice JSON.",
            "recommended": {
                "voice": "character_voices/dhruv_narration.json",
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
    }


# Backward compatibility alias for discoverability
get_tts_capabilities = tts_capabilities


# Backward compatibility exports
__all__ = [
    "get_audio_duration",
    "normalize_tts_engine",
    "shutdown_omnivoice_worker",
    "shutdown_supertonic_worker",
    "translate_hinglish",
    "tts_capabilities",
    "tts_generate",
]
