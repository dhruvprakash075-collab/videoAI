"""Retry wrapper for per-segment processing (extracted from segment_runner)."""
from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)


def build_retry_wrapper(
    process_segment, max_retries: int, segment_idx: int, retry_counts: dict
) -> Callable[[int], None]:
    """Wrap process_segment with the A7 per-segment retry budget."""
    def _with_budget(i: int) -> None:
        retry_counts.setdefault(i, 0)
        while retry_counts[i] <= max_retries:
            try:
                process_segment(i)
                return
            except Exception as _e:
                retry_counts[i] += 1
                if retry_counts[i] > max_retries:
                    log.exception(
                        f"Segment {i}: retry budget exhausted ({max_retries} retries). "
                        f"Skipping segment. Last error: {_e}"
                    )
                    try:
                        from agents.director_agent import UIState as _UIS
                        _UIS.add_degradation(
                            i, "segment_skip", f"retry budget exhausted: {str(_e)[:100]}"
                        )
                    except Exception as exc:
                        log.debug(f"UIState degradation record skipped: {exc}")
                    return
                log.warning(
                    f"Segment {i}: attempt {retry_counts[i]}/{max_retries} failed ({_e}), retrying..."
                )
    return _with_budget
