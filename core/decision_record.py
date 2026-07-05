"""Single DecisionRecord builder replacing the 3 duplicated inline blocks.

Consolidated from the scratch / series-resume / normal branches in
pre_production.py. Each did: build user_locks -> build_decision_record ->
blackboard.write_decision -> _deep_merge(overlay, rec.to_overlay()).
"""
from __future__ import annotations

from typing import Any


def build_and_persist_decision_record(
    *, director, topic: str, config: dict, config_overlay: dict,
    vision_doc: dict, writer_input: dict, run_mode: str,
    project_name: str | None = None, cli_flags: dict | None = None,
    extra_user_locks: dict[str, Any] | None = None,
) -> tuple[dict, Any]:
    """Build + persist the DecisionRecord, return (merged_overlay, rec).

    Consolidated from the scratch / series-resume / normal branches, which each
    did: build user_locks -> build_decision_record -> blackboard.write_decision
    -> _deep_merge(overlay, rec.to_overlay()).
    """
    from agents.decision_engine import build_decision_record
    from config import _safe_filename
    from core.pre_production import _deep_merge
    from memory.blackboard import get_blackboard

    user_locks: dict[str, Any] = {"run_mode": run_mode}
    if project_name:
        user_locks["project_name"] = project_name
    if extra_user_locks:
        user_locks.update(extra_user_locks)

    rec = build_decision_record(
        director=director, vision_doc=vision_doc, writer_input=writer_input,
        user_locks=user_locks, cli_flags=dict(cli_flags or {}), config=config,
    )
    bb = get_blackboard(config, topic_slug=_safe_filename(topic))
    bb.write_decision(rec)
    return _deep_merge(config_overlay, rec.to_overlay()), rec
