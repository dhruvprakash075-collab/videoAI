"""Outline post-processing — apply structural locks from DecisionRecord.

Run once after Director produces the outline, before the per-segment loop.

Responsibilities:
1. Lock images_per_segment when DecisionRecord.images_per_segment.locked
2. Normalize char_presence positional aliases (protagonist/mentor/guardian → story keys)
3. Enforce minimum environment frame ratio (config: visual.environment_frame_ratio)
4. Merge colliding aliases by max weight (ponytail: heuristic until stable IDs)
5. Validate director plan contract (target_word_count, num_images, segment_duration)

All mutations are in-place on the outline list of dicts.
"""
from __future__ import annotations

import logging
from typing import Any, cast

__all__ = ["shape_outline", "validate_director_plan"]

log = logging.getLogger("core.pipeline_long")


def validate_director_plan(outline: list[dict], *, words_locked: bool, images_locked: bool) -> None:
    """Validate that Director's plan satisfies the rigid contract.

    Every segment MUST have:
      - target_word_count: int ≥ 20 (unless words_locked=True via CLI flag)
      - num_images: int ≥ 1 (unless images_locked=True via CLI flag)
      - segment_duration: float > 0 (always required)

    Raises ValueError if any segment violates the contract.
    """
    for seg in outline:
        seg_id = seg.get("seg", "?")
        if not words_locked:
            wc = seg.get("target_word_count")
            if not isinstance(wc, int) or wc < 20:
                raise ValueError(f"Seg {seg_id}: target_word_count REQUIRED int ≥ 20")
        if not images_locked:
            ni = seg.get("num_images")
            if not isinstance(ni, int) or ni < 1:
                raise ValueError(f"Seg {seg_id}: num_images REQUIRED int ≥ 1")
        dur = seg.get("segment_duration")
        if not isinstance(dur, (int, float)) or dur <= 0:
            raise ValueError(f"Seg {seg_id}: segment_duration REQUIRED > 0")


def shape_outline(
    outline: list[dict],
    config: dict,
    *,
    images_per_segment_locked: bool,
) -> list[dict]:
    """Apply image locks, char_presence normalization, positional-alias mapping,
    and env-frame-ratio enforcement. outline in, outline out.

    Args:
        outline: List of segment plan dicts from Director
        config: Full pipeline config (for caps and ratios)
        images_per_segment_locked: True if DecisionRecord.images_per_segment.locked

    Returns:
        Mutated outline (same list object returned for chaining)
    """
    # ── moved verbatim from run_long_pipeline ──

    # Lock images per segment when DecisionRecord says so
    _default_imgs = config.get("script", {}).get("default_images_per_segment", 6)
    for seg_plan in outline:
        if images_per_segment_locked:
            _old_ni = seg_plan.get("num_images", _default_imgs)
            if _old_ni != _default_imgs:
                log.info(
                    f"  Seg {seg_plan.get('seg', '?')}: images locked "
                    f"{_old_ni} → {_default_imgs}"
                )
            seg_plan["num_images"] = _default_imgs
            cp_list = seg_plan.get("char_presence")
            if isinstance(cp_list, list):
                if len(cp_list) >= _default_imgs:
                    # Keep one establishing frame, then choose the strongest
                    # character frames. Truncating the first N selected only the
                    # Director's low-weight world shots.
                    _env = min(
                        cp_list,
                        key=lambda f: max(f.values()) if isinstance(f, dict) and f else 0,
                    )
                    _character_frames = sorted(
                        cp_list,
                        key=lambda f: (
                            sum(float(v) >= 0.3 for v in f.values()),
                            sum(float(v) for v in f.values()),
                            max(f.values()) if f else 0,
                        ) if isinstance(f, dict) and f else (0, 0, 0),
                        reverse=True,
                    )
                    seg_plan["char_presence"] = [
                        _env,
                        *_character_frames[: max(0, _default_imgs - 1)],
                    ]
                elif cp_list:
                    seg_plan["char_presence"] = cp_list + [cp_list[-1]] * (
                        _default_imgs - len(cp_list)
                    )

                # ponytail: positional aliases are the existing heuristic; keep the maximum
                # weight on collisions until the planner emits stable character IDs.
                _story_keys = [
                    k for k in config.get("characters", {})
                    if k not in {"protagonist", "mentor", "guardian"}
                ]
                _aliases = {
                    "protagonist": _story_keys[0] if _story_keys else "protagonist",
                    "mentor": _story_keys[1] if len(_story_keys) > 1 else (_story_keys[0] if _story_keys else "mentor"),
                    "guardian": _story_keys[2] if len(_story_keys) > 2 else (_story_keys[-1] if _story_keys else "guardian"),
                }
                _normalized = []
                for _frame in seg_plan.get("char_presence", []):
                    if not isinstance(_frame, dict):
                        _normalized.append(_frame)
                        continue
                    _mapped: dict = {}
                    for k, v in _frame.items():
                        if k == "environment":
                            continue
                        _target = _aliases.get(k, k)
                        if _target in _mapped:
                            _mapped[_target] = max(_mapped[_target], v)
                        else:
                            _mapped[_target] = v
                    _normalized.append(_mapped)
                seg_plan["char_presence"] = _normalized
            continue

    # P3: enforce minimum environment/world frames
    _env_ratio = config.get("visual", {}).get("environment_frame_ratio", 0.4)
    for seg_plan in outline:
        cp_list = seg_plan.get("char_presence")
        if not isinstance(cp_list, list) or not cp_list:
            continue
        cp_frames = cast(list[Any], cp_list)
        n_frames = len(cp_list)
        n_env_needed = max(1, int(n_frames * _env_ratio))
        def _presence_weight(frame: object) -> float:
            return max(frame.values()) if isinstance(frame, dict) and frame else 0.0

        env_indices = [
            j
            for j, frame in enumerate(cp_frames)
            if isinstance(frame, dict) and _presence_weight(frame) <= 0.2
        ]
        if len(env_indices) < n_env_needed:
            sorted_by_weight = sorted(
                range(n_frames),
                key=lambda j: _presence_weight(cp_frames[j]),
            )
            for j in sorted_by_weight:
                if len(env_indices) >= n_env_needed:
                    break
                if j not in env_indices:
                    if isinstance(cp_frames[j], dict):
                        cp_frames[j] = {k: min(0.1, v) for k, v in cp_frames[j].items()}
                    else:
                        cp_frames[j] = {}
                    env_indices.append(j)
        # With only two frames, forcing both first and last to environment
        # eliminates every character shot. Keep the final frame character-led.
        _forced_environment_indices = [0] if n_frames <= 2 else [0, n_frames - 1]
        for _force_idx in _forced_environment_indices:
            if isinstance(cp_frames[_force_idx], dict) and cp_frames[_force_idx]:
                cp_frames[_force_idx] = {k: min(0.15, v) for k, v in cp_frames[_force_idx].items()}
            else:
                cp_frames[_force_idx] = {}

    return outline
