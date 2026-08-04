"""storyboard.py - Pre-production storyboard sheet generation + approval gate.

Builds a multi-panel storyboard from the final shaped outline, one LLM call,
and the existing image-gen path. The approved sheet is persisted per-story in
StoryStore for reuse on re-runs, optionally fed back as a style reference for
scene generation, and its per-panel camera/duration metadata rides in the plan
dict that ``enrich_prompts`` already consumes.

Flow: gate -> reuse check -> 1 LLM call -> parse panels -> assemble prompts ->
generate panel images -> compose sheet -> consult_user approval -> persist.
"""

import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_SHOT_ORDER = ["wide", "medium", "close-up"]


def _gate_enabled(config: dict, cli_flags: dict | None) -> bool:
    """Storyboard runs unless disabled in config or by --no-storyboard."""
    if cli_flags and cli_flags.get("no_storyboard"):
        return False
    sb = config.get("storyboard", {}) or {}
    return bool(sb.get("enabled", True))


def _build_llm_prompt(
    director_agent, outline: list[dict], config: dict, panel_count: int
) -> str:
    """Assemble the storyboard_plan prompt: outline beats + character DNA + style."""
    chars_text = []
    for c in (config.get("characters") or {}).values():
        if isinstance(c, dict):
            name = c.get("name", "")
            desc = c.get("description", "")
            if name and desc:
                chars_text.append(f"{name}: {desc}")
    style = config.get("visual", {}).get("style") or "anime"
    if isinstance(style, dict):
        style = ", ".join(str(style.get(k, "")) for k in ("tone", "elements") if style.get(k))

    beats = []
    for seg in outline:
        if isinstance(seg, dict):
            beats.append(
                f"[{seg.get('seg', '?')}] {seg.get('title', '')} ({seg.get('mood', '')}): {seg.get('key_event', '')}"
            )

    template = director_agent._prompt("storyboard_plan")
    if template:
        try:
            return template.format(
                outline="\n".join(beats),
                characters="\n".join(chars_text) or "No characters defined.",
                style=style,
                panel_count=panel_count,
            )
        except Exception as e:
            log.warning(f"[STORYBOARD] Prompt template format failed, using fallback: {e}")

    # ponytail: direct fallback so the module is self-contained if the template
    # is missing or unformattable; keeps the same JSON contract.
    return (
        "You are a storyboard artist. Given the story outline, characters, and "
        "visual style, produce a storyboard plan as JSON. STORY OUTLINE:\n"
        + "\n".join(beats)
        + "\n\nCHARACTERS:\n" + ("\n".join(chars_text) or "No characters defined.")
        + f"\n\nVISUAL STYLE:\n{style}\n\n"
        f"Produce exactly {panel_count} panels as a JSON object: "
        '{{"panels": [{{"beat": "...", "shot_size": "wide|medium|close-up", '
        '"camera": "...", "action": "...", "environment": "...", '
        '"dialogue": "...", "duration_sec": number}}]}}. '
        "Alternate wide, medium, close-up. Output ONLY valid JSON."
    )


def _parse_panels(llm_text: str, panel_count: int) -> list[dict]:
    """Parse the LLM JSON response into a list of panel dicts (best-effort)."""
    from utils.utils import extract_json

    data = extract_json(llm_text)
    if isinstance(data, dict):
        panels = data.get("panels", [])
    elif isinstance(data, list):
        panels = data
    else:
        panels = []
    valid = []
    for p in panels[:panel_count]:
        if isinstance(p, dict):
            try:
                duration = float(p.get("duration_sec", 0.0) or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
            valid.append(
                {
                    "beat": str(p.get("beat", "")),
                    "shot_size": str(p.get("shot_size", "medium")),
                    "camera": str(p.get("camera", "")),
                    "action": str(p.get("action", "")),
                    "environment": str(p.get("environment", "")),
                    "dialogue": str(p.get("dialogue", "")),
                    "duration_sec": duration,
                }
            )
    # ponytail: if the LLM under-delivers, pad with beat placeholders so the
    # sheet still renders; upgrade path = requesting more from the LLM.
    while len(valid) < panel_count:
        valid.append(
            {
                "beat": f"Beat {len(valid) + 1}",
                "shot_size": _SHOT_ORDER[len(valid) % len(_SHOT_ORDER)],
                "camera": "cinematic shot",
                "action": "",
                "environment": "",
                "dialogue": "",
                "duration_sec": 0.0,
            }
        )
    return valid


def _assemble_storyboard_prompts(panels: list[dict], config: dict) -> list[str]:
    """Deterministic per-panel prompt assembly reusing scene_director helpers."""
    from utils.scene_director import assemble_prompt

    style = config.get("visual", {}).get("style") or "anime"
    if isinstance(style, dict):
        style = ", ".join(str(style.get(k, "")) for k in ("tone", "elements") if style.get(k))

    chars = config.get("characters", {}) or {}
    identity = ", ".join(
        str(c.get("description", "")) for c in chars.values() if isinstance(c, dict) and c.get("description")
    )

    prompts = []
    for panel in panels:
        scene = ", ".join(
            part
            for part in [
                panel.get("action", ""),
                panel.get("environment", ""),
                str(panel.get("shot_size", "")) + " shot",
                panel.get("camera", ""),
            ]
            if part
        )
        prompt = assemble_prompt(
            identity_tokens=identity,
            scene_tokens=scene,
            style_tokens=f"{style}, storyboard panel, cinematic",
            budget=70,
        )
        prompts.append(prompt)
    return prompts


def _page_aspect_from_config(aspect: str, width: int = 1920, height: int = 1080) -> float:
    """Derive compositor page_aspect from the storyboard aspect string."""
    if aspect and ":" in aspect:
        try:
            w, h = aspect.split(":", 1)
            return float(w) / float(h)
        except ValueError:
            pass
    return width / height


def run_storyboard(
    director_agent,
    outline: list[dict],
    config: dict,
    topic: str,
    project_name: str | None = None,
    cli_flags: dict | None = None,
    root: Path | None = None,
) -> dict | None:
    """Build + approve + persist a storyboard. Returns the record or None if skipped.

    ``root`` overrides the StoryStore base dir (used by tests to isolate writes).
    """
    if not _gate_enabled(config, cli_flags):
        log.info("[STORYBOARD] Skipped (disabled or --no-storyboard)")
        return None

    sb = config.get("storyboard", {}) or {}
    panel_count = max(1, int(sb.get("panel_count", 6)))
    retries = max(0, int(sb.get("approval_retries", 2)))

    from memory.project_store import StoryStore

    story_store = StoryStore(topic, project_name=project_name, root=root)

    # Reuse check
    if sb.get("reuse_existing", True):
        existing = story_store.get_storyboard()
        if existing and existing.get("status") == "approved" and not (cli_flags or {}).get("force_storyboard"):
            sheet = existing.get("sheet_path")
            if sheet and Path(sheet).is_file():
                log.info("[STORYBOARD] Reusing approved storyboard from memory")
                return existing
            log.warning(
                "[STORYBOARD] Approved storyboard record found but sheet file is "
                "missing — regenerating"
            )

    attempt = 0
    while True:
        attempt += 1
        log.info(f"[STORYBOARD] Planning attempt {attempt}/{retries + 1}")

        prompt = _build_llm_prompt(director_agent, outline, config, panel_count)
        llm_text = director_agent.llm._call_ollama(prompt, format_json=True)
        if not llm_text:
            log.warning("[STORYBOARD] LLM returned empty — skipping storyboard")
            return None
        try:
            panels = _parse_panels(llm_text, panel_count)
        except Exception as e:
            log.warning(f"[STORYBOARD] Unparseable LLM response — skipping storyboard: {e}")
            return None

        # Generate panel images via the existing path
        panel_prompts = _assemble_storyboard_prompts(panels, config)
        out_dir = Path("studio_outputs") / _safe(topic) / "storyboard"
        try:
            from video.image_gen.image_gen import generate_images

            panel_paths = generate_images(panel_prompts, out_dir, config, project_id=project_name)
        except Exception as e:
            log.warning(f"[STORYBOARD] Image generation failed ({e}) — skipping storyboard")
            return None

        # Compose one sheet. Geometry mirrors image_gen._panel_sizes (the same
        # chain that decided generation sizes), so rects and sizes always agree.
        from video.image_gen.panel_compositor import compose_panel_pages

        sheet_dir = out_dir / "sheet"
        _pc = (config.get("image_gen") or {}).get("panel_composite") or {}
        if _pc.get("enabled"):
            width = int(_pc.get("width", 1920))
            height = int(_pc.get("height", 1080))
            page_aspect = float(_pc.get("page_aspect", 1.414))
            layout_file = _pc.get("layout_file")
            fallback_layout_file = _pc.get("fallback_layout_file")
        else:
            width, height = 1920, 1080
            page_aspect = _page_aspect_from_config(sb.get("aspect", "16:9"), width, height)
            layout_file = fallback_layout_file = None
        try:
            pages = compose_panel_pages(
                panel_paths,
                sheet_dir,
                width=width,
                height=height,
                prefix="storyboard",
                page_aspect=page_aspect,
                layout_file=layout_file,
                fallback_layout_file=fallback_layout_file,
            )
        except Exception as e:
            log.warning(f"[STORYBOARD] Sheet composition failed ({e}) — skipping storyboard")
            return None

        if not pages:
            log.warning("[STORYBOARD] No sheet pages produced — skipping storyboard")
            return None
        sheet_path = pages[0]
        if len(pages) > 1:
            # ponytail: the primary sheet is page 1; later pages stay on disk
            # and are listed in the record (never dropped) but not merged —
            # merging would break the single-sheet contract.
            log.info(
                f"[STORYBOARD] Sheet spans {len(pages)} pages — "
                f"primary {pages[0].name}; extras: {[p.name for p in pages[1:]]}"
            )

        # Approval gate. Any non-"regenerate" reply (Approve, "Proceed as
        # planned." from the UI default, custom text, empty) approves — the
        # storyboard is an advisory gate, never a blocker.
        choice = director_agent.consult_user(
            f"Approve storyboard sheet? ({sheet_path.name})", ["Approve", "Regenerate"]
        )
        if "regenerate" not in choice.lower():
            return _persist(story_store, prompt, panels, sheet_path, pages)
        if attempt > retries:
            log.warning("[STORYBOARD] Regenerate limit reached — auto-approving last sheet")
            return _persist(story_store, prompt, panels, sheet_path, pages)
        log.info("[STORYBOARD] Regenerating with same inputs (user requested)")


def attach_shot_metadata(outline: list[dict], panels: list[dict]) -> None:
    """Attach per-segment camera/duration hints onto the outline segments.

    The per-segment plan dicts ARE the outline segments
    (segment_runner._build_segment_state), so enrich_prompts picks the hint up
    via plan["shot_metadata"] without any signature change.
    ponytail: round-robin mapping when panel/segment counts differ; upgrade
    path = beat-title matching once beats carry segment titles.
    """
    if not panels:
        return
    for i, seg in enumerate(outline):
        if not isinstance(seg, dict):
            continue
        p = panels[i % len(panels)]
        parts = []
        if p.get("camera"):
            parts.append(str(p["camera"]))
        if p.get("duration_sec"):
            parts.append(f"{float(p['duration_sec']):.1f}s")
        if parts:
            seg["shot_metadata"] = ", ".join(parts)


def _persist(
    story_store, prompt: str, panels: list[dict], sheet_path: Path,
    sheet_pages: list[Path] | None = None,
) -> dict:
    """Build the approved storyboard record and save it to StoryStore."""
    record = {
        "status": "approved",
        "sheet_path": str(sheet_path),
        "prompt": prompt,
        "panels": panels,
        "approved_at": time.time(),
    }
    if sheet_pages:
        record["sheet_pages"] = [str(p) for p in sheet_pages]
    story_store.save_storyboard(record)
    log.info(f"[STORYBOARD] Approved and persisted: {sheet_path}")
    return record


def _safe(topic: str) -> str:
    from utils import _safe_filename

    return _safe_filename(topic)
