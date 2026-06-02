"""concurrency.py - Concurrency Workload Task Scheduler for Video.AI.

Enforces that heavy GPU-bound tasks (Stable Diffusion, Coqui XTTS) never run
concurrently to prevent VRAM Out-of-Memory crashes, while allowing light tasks
(FFmpeg assembly, translations, story planning, etc.) to run concurrently.
"""

import contextlib
import logging
import threading
import time

log = logging.getLogger("concurrency")


class _Count(int):
    """int subclass that is also callable, returning itself.

    Lets callers use ``s.active_heavy_count`` as an int (e.g. ``> 0``) or
    call it as a method (e.g. ``s.active_heavy_count()``) without TypeError.
    ``__iadd__`` / ``__isub__`` preserve the ``_Count`` type so the callable
    property survives ``self.active_heavy_count += 1``.
    """
    __slots__ = ()

    def __call__(self):  # type: ignore[override]
        return self

    def __iadd__(self, other):
        return _Count(int.__add__(self, other))

    def __isub__(self, other):
        return _Count(int.__sub__(self, other))


class WorkloadScheduler:
    """Thread-safe workload scheduler for throttling high-GPU operations."""

    def __init__(self):
        self.heavy_semaphore = threading.Semaphore(1)
        self.light_semaphore = threading.Semaphore(16)  # Match Ryzen 7 7840HS thread count (was 20)
        self.lock = threading.Lock()
        self.active_heavy_count = _Count(0)
        self.active_light_count = _Count(0)

    @contextlib.contextmanager
    def task(self, weight: str, task_name: str = "Task"):
        """Context manager to wrap execution blocks based on task weight."""
        weight = weight.upper()
        if weight == "HEAVY":
            t0 = time.time()
            log.info(f"[SCHEDULER] [WAIT] Queuing HEAVY task '{task_name}' (VRAM Protection)...")
            if not self.heavy_semaphore.acquire(timeout=1800):
                log.error(f"[SCHEDULER] Timed out waiting for HEAVY slot: {task_name}")
                raise TimeoutError(f"HEAVY task '{task_name}' timed out waiting for slot")
            with self.lock:
                self.active_heavy_count += 1
            elapsed = time.time() - t0
            log.info(
                f"[SCHEDULER] [START] Started HEAVY task '{task_name}' "
                f"(Active Heavy: {self.active_heavy_count}, Active Light: {self.active_light_count}) "
                f"after waiting {elapsed:.2f}s"
            )
            try:
                yield
            finally:
                with self.lock:
                    self.active_heavy_count -= 1
                self.heavy_semaphore.release()
                log.info(
                    f"[SCHEDULER] [DONE] Finished HEAVY task '{task_name}' "
                    f"(Active Heavy: {self.active_heavy_count}, Active Light: {self.active_light_count})"
                )
        else:  # LIGHT
            # 60s ceiling: the light semaphore has 16 slots, so waiting >60s
            # means the pipeline is severely stuck — surface it fast instead of
            # masking with a 5-minute timeout. (P4-18 fix: 300s → 60s.)
            if not self.light_semaphore.acquire(timeout=60):
                log.error(f"[SCHEDULER] Timed out waiting for LIGHT slot: {task_name}")
                raise TimeoutError(f"LIGHT task '{task_name}' timed out waiting for slot")
            with self.lock:
                self.active_light_count += 1
            log.info(
                f"[SCHEDULER] [START] Started LIGHT task '{task_name}' "
                f"(Active Heavy: {self.active_heavy_count}, Active Light: {self.active_light_count})"
            )
            try:
                yield
            finally:
                with self.lock:
                    self.active_light_count -= 1
                self.light_semaphore.release()
                log.info(
                    f"[SCHEDULER] [DONE] Finished LIGHT task '{task_name}' "
                    f"(Active Heavy: {self.active_heavy_count}, Active Light: {self.active_light_count})"
                )

# Global singleton instance for the pipeline to share
global_scheduler = WorkloadScheduler()


# Shared CrewAI serialization lock (B15 fix).
# CrewAI's internal executor cannot run concurrently — every kickoff() across
# the codebase (pipeline_long writer crews AND context_manager compression)
# must serialize through this single lock to prevent executor corruption.
# P3-14 fix: use RLock instead of Lock to prevent latent deadlock if the same
# thread re-acquires the lock (e.g. nested compression calls).
crewai_lock = threading.RLock()
