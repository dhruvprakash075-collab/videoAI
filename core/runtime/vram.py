"""VRAM management helpers (extracted from segment_runner)."""
from __future__ import annotations

import logging

log = logging.getLogger("core.segment_runner")


def log_vram_usage(label: str = "") -> None:
    """Log current CUDA VRAM usage (free / total GB). Safe to call if torch isn't available."""
    try:
        import torch
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            used = total - free
            free_gb = free / (1024**3)
            used_gb = used / (1024**3)
            total_gb = total / (1024**3)
            pct = (used / total) * 100 if total > 0 else 0
            tag = f"[{label}] " if label else ""
            vram_str = f"{used_gb:.1f}/{total_gb:.1f}GB ({pct:.0f}%)"
            log.info(
                f"{tag}VRAM: {used_gb:.2f}GB / {total_gb:.2f}GB used ({pct:.0f}%) — {free_gb:.2f}GB free"
            )
            try:
                from agents.director_agent import UIState
                UIState.vram_text = vram_str
                UIState.vram_peaks.append(round(used_gb, 2))
            except Exception:
                pass
    except ImportError:
        pass
    except Exception as e:
        log.debug(f"VRAM check failed ({e})")


def aggressive_vram_cleanup(global_scheduler) -> None:
    """Aggressive VRAM + GC cleanup. Called after every segment via finally block."""
    import gc
    gc.collect()
    if global_scheduler.active_heavy_count > 0:
        return
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            import time as _t
            _t.sleep(0.3)
    except ImportError:
        pass
    except Exception:
        pass
