"""Ollama server management helpers (extracted from segment_runner)."""
from __future__ import annotations

import contextlib
import logging
import threading
import time

from utils.url_security import build_validated_url, validate_service_base_url

log = logging.getLogger("core.segment_runner")

_pending_ollama_timer = None
_pending_ollama_timer_lock = threading.Lock()


def touch_ollama_active():
    """Cancel any pending Ollama server stop (task still needs it)."""
    global _pending_ollama_timer
    with _pending_ollama_timer_lock:
        if _pending_ollama_timer is not None:
            _pending_ollama_timer.cancel()
            _pending_ollama_timer = None


def schedule_ollama_stop(config, delay: float = 3.0):
    """Schedule Ollama server stop in `delay` seconds.
    Automatically cancels any previously scheduled stop (debounce).
    """
    global _pending_ollama_timer
    touch_ollama_active()
    import threading as _t
    with _pending_ollama_timer_lock:
        _pending_ollama_timer = _t.Timer(
            delay,
            lambda: stop_ollama_server(config, reason="debounced-timer"),
        )
        _pending_ollama_timer.daemon = True
        _pending_ollama_timer.start()


def _ollama_alive(config, timeout: float = 2.0) -> bool:
    """Quick check if Ollama server is reachable (no process restart)."""
    import urllib.error as _ue
    host = validate_service_base_url(config.get("ollama", {}).get("host", "http://localhost:11434"))
    try:
        from utils.url_security import open_validated_url
        with open_validated_url(build_validated_url(host, "/api/tags"), timeout=timeout):
            return True
    except (ConnectionRefusedError, _ue.URLError, OSError):
        return False


def evict_ollama_models(config: dict, reason: str = "") -> None:
    """Force-evict ALL Ollama models from VRAM (keep_alive=0) before a GPU task."""
    try:
        import json as _js
        import urllib.request as _ur
        host = validate_service_base_url(config.get("ollama", {}).get("host", "http://localhost:11434"))
        models_cfg = config.get("models", {})
        seen = set()
        for _key in ("director", "writer", "reviewer", "translator", "image_engineer"):
            _mdl = models_cfg.get(_key, "")
            if _mdl and _mdl not in seen:
                seen.add(_mdl)
                import urllib.error as _ue
                with contextlib.suppress(_ue.URLError, TimeoutError, OSError):
                    from utils.url_security import open_validated_url
                    open_validated_url(
                        _ur.Request(
                            build_validated_url(host, "/api/generate"),
                            data=_js.dumps({"model": _mdl, "keep_alive": 0}).encode(),
                            headers={"Content-Type": "application/json"},
                        ),
                        timeout=3,
                    )
        log.debug(f"  Ollama VRAM released{(' before ' + reason) if reason else ''}")
    except Exception as e:
        log.debug(f"Ollama VRAM release failed: {e}")
    try:
        import gc

        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as exc:
        log.debug(f"Torch cache cleanup skipped: {exc}")
    try:
        import torch
        if not torch.cuda.is_available():
            return
        perf = config.get("performance", {})
        wait_s = float(perf.get("vram_evict_wait_s", 15))
        threshold_gb = float(perf.get("vram_sd_threshold_gb", 4.5))
        threshold_bytes = threshold_gb * (1024**3)
        deadline = time.time() + wait_s
        while time.time() < deadline:
            free, _total = torch.cuda.mem_get_info()
            free_gb = free / (1024**3)
            if free >= threshold_bytes:
                log.info(
                    f"[VRAM] Free: {free_gb:.2f} GB — threshold met ({threshold_gb} GB), SD can load"
                )
                return
            time.sleep(0.5)
        free, _total = torch.cuda.mem_get_info()
        free_gb = free / (1024**3)
        if free < threshold_bytes:
            log.warning(
                f"[VRAM] WARNING: VRAM still low after {wait_s:.0f}s wait "
                f"({free_gb:.2f} GB free, need {threshold_gb} GB). "
                "Attempting harder evict via /api/ps..."
            )
            try:
                import json as _js2
                import urllib.request as _ur2
                host2 = validate_service_base_url(config.get("ollama", {}).get("host", "http://localhost:11434"))
                from utils.url_security import open_validated_url
                with open_validated_url(build_validated_url(host2, "/api/ps"), timeout=3) as _r:
                    ps_data = _js2.loads(_r.read().decode())
                for _m in ps_data.get("models", []):
                    _name = _m.get("name", "")
                    if _name:
                        import urllib.error as _ue2
                        with contextlib.suppress(_ue2.URLError, TimeoutError, OSError):
                            open_validated_url(
                                _ur2.Request(
                                    build_validated_url(host2, "/api/generate"),
                                    data=_js2.dumps({"model": _name, "keep_alive": 0}).encode(),
                                    headers={"Content-Type": "application/json"},
                                ),
                                timeout=3,
                            )
                torch.cuda.empty_cache()
            except Exception as _he:
                log.debug(f"[VRAM] Harder evict failed: {_he}")
            log.warning("[VRAM] Proceeding with SD load despite low VRAM — may OOM")
    except ImportError:
        pass
    except Exception as _ve:
        log.debug(f"[VRAM] Poll failed: {_ve}")


def stop_ollama_server(config: dict, reason: str = "") -> None:
    """Kill the Ollama server process to free ~1-2 GB RAM between staged batches."""
    import subprocess
    import sys
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/IM", "ollama.exe"],
                capture_output=True, timeout=5,
            )
        else:
            subprocess.run(["pkill", "-f", "ollama serve"], capture_output=True, timeout=5)
        log.info(f"[Ollama] Server stopped{(' (' + reason + ')') if reason else ''} — RAM freed")
    except Exception as e:
        log.debug(f"[Ollama] Server stop failed (non-fatal): {e}")


def start_ollama_server(config: dict, reason: str = "") -> bool:
    """Start Ollama server in background and wait until it responds. Returns True if reachable."""
    import subprocess
    import sys
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["ollama", "serve"],
                creationflags=subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        log.warning(f"[Ollama] Failed to start server: {e}")
        return False
    host = validate_service_base_url(config.get("ollama", {}).get("host", "http://localhost:11434"))
    import urllib.error as _ue
    for _i in range(20):
        time.sleep(0.5)
        try:
            from utils.url_security import open_validated_url
            with open_validated_url(build_validated_url(host, "/api/tags"), timeout=2):
                log.info(f"[Ollama] Server started{(' (' + reason + ')') if reason else ''}")
                return True
        except (ConnectionRefusedError, _ue.URLError, OSError):
            continue
    log.warning("[Ollama] Server started but not reachable after 10s — LLM calls may fail")
    return False
