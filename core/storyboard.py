"""storyboard.py - Pre-production storyboard sheet generation + approval gate.

Builds a multi-panel storyboard from the final shaped outline, one LLM call,
and the existing image-gen path. The approved sheet is persisted per-story in
StoryStore for reuse on re-runs, optionally fed back as a style reference for
scene generation, and its per-panel camera/duration metadata rides in the plan
dict that ``enrich_prompts`` already consumes.

Flow: gate -> reuse check -> 1 LLM call -> parse panels -> assemble prompts ->
generate panel images -> compose sheet -> consult_user approval -> persist.
"""

import contextlib
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
        '{"panels": [{"beat": "...", "shot_size": "wide|medium|close-up", '
        '"camera": "...", "action": "...", "environment": "...", '
        '"dialogue": "...", "duration_sec": number}]}. '
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


def _scoped_config(config: dict) -> dict:
    """Config copy with panel compositing disabled — storyboard wants raw panels.

    generate_images composes pages when image_gen.panel_composite is on;
    composing them again in the sheet would double-compose. The sheet walk
    below rebuilds the geometry from the same layout chain.
    """
    cfg = dict(config)
    ig = dict(cfg.get("image_gen", {}) or {})
    pc = dict(ig.get("panel_composite", {}) or {})
    pc["enabled"] = False
    ig["panel_composite"] = pc
    cfg["image_gen"] = ig
    return cfg


def _character_view_prompts(char: dict, style: str, char_key: str) -> list[str]:
    """4 prompts: front/back full-body + portrait/side-profile, plain bg."""
    name = char.get("name") or char_key
    desc = char.get("description", "")
    base = f"{name}, {desc}, {style}, character reference sheet"
    return [
        f"{base}, full body, front view, plain background",
        f"{base}, full body, back view, plain background",
        f"{base}, portrait, head and shoulders, front view, plain background",
        f"{base}, portrait, head and shoulders, side profile view, plain background",
    ]


def _generate_character_sheets(
    config: dict, out_dir: Path, project_name: str | None
) -> dict[str, dict]:
    """Best-effort 4-view reference sheet per configured character.

    Returns {char_key: {"front", "back", "portrait", "side", "sheet"}} with
    absolute paths. Any failure degrades to fewer/no sheets — the storyboard
    gate must never block on reference assets.
    """
    sb = config.get("storyboard", {}) or {}
    char_keys = sb.get("character_sheet_chars") or []
    if not char_keys:
        return {}
    chars = config.get("characters", {}) or {}
    style = str(sb.get("style")) if sb.get("style") else (
        config.get("visual", {}).get("style") or "anime"
    )
    if isinstance(style, dict):
        style = ", ".join(str(style.get(k, "")) for k in ("tone", "elements") if style.get(k))

    from video.image_gen.image_gen import generate_images
    from video.image_gen.panel_compositor import compose_character_sheet

    sheets = {}
    for key in char_keys:
        char = chars.get(key)
        if not isinstance(char, dict) or not char.get("description"):
            log.info(f"[STORYBOARD] No description for '{key}' — skipping reference sheet")
            continue
        try:
            views = generate_images(
                _character_view_prompts(char, style, key),
                out_dir / "characters" / _safe(key),
                _scoped_config(config),
                char_presence=[{key: 1.0}] * 4,
                project_id=project_name,
            )
        except Exception as e:
            log.warning(f"[STORYBOARD] Reference sheet gen failed for '{key}' ({e}) — skipping")
            continue
        if len(views) < 4:
            log.warning(
                f"[STORYBOARD] Reference sheet for '{key}' yielded {len(views)}/4 views — skipping"
            )
            continue
        try:
            sheet = compose_character_sheet(
                views[0], views[1], views[2], views[3],
                out_dir / "characters" / _safe(key) / f"{_safe(key)}_sheet.png",
                char_name=char.get("name") or key,
            )
        except Exception as e:
            log.warning(f"[STORYBOARD] Reference sheet compose failed for '{key}' ({e}) — skipping")
            continue
        sheets[key] = {
            "front": str(views[0]),
            "back": str(views[1]),
            "portrait": str(views[2]),
            "side": str(views[3]),
            "sheet": str(sheet),
        }
        log.info(f"[STORYBOARD] Character reference sheet ready: {key}")
    return sheets


def _wire_character_sheets(
    sheets: dict, project_name: str | None, root: Path | None
) -> None:
    """On approval: portrait → master portrait; front/back + sheet → character assets."""
    if not sheets or not project_name:
        return
    import hashlib

    from memory.project_store import ProjectStore

    try:
        store = ProjectStore(project_name, root=root)
        for key, assets in sheets.items():
            portrait = assets["portrait"]
            digest = ""
            with contextlib.suppress(OSError):
                digest = hashlib.sha256(Path(portrait).read_bytes()).hexdigest()
            store.set_master_portrait(key, portrait, digest)
            store.set_character_assets(
                key,
                character_sheet_path=assets["sheet"],
                face_reference_path=portrait,
                full_body_reference_path=assets["front"],
                identity_hash=digest,
                approved=True,
            )
    except Exception as e:
        log.warning(f"[STORYBOARD] Reference sheet wiring failed ({e}) — continuing")


def _dynamic_panel_count(outline: list, fallback: int, cap: int = 12) -> int:
    """Storyboard scale follows the outline: one panel per planned scene image.

    ponytail: capped at 12 (~3 sheet pages) so the approval sheet stays
    reviewable; upgrade path = config field for the cap if a story ever needs
    more.
    """
    derived = sum(
        int(seg.get("num_images") or 0) for seg in outline if isinstance(seg, dict)
    )
    if derived:
        return max(1, min(derived, cap))
    return fallback


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
    panel_count = _dynamic_panel_count(outline, int(sb.get("panel_count", 6)))
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
    feedback = ""
    while True:
        attempt += 1
        log.info(f"[STORYBOARD] Planning attempt {attempt}/{retries + 1}")

        prompt = _build_llm_prompt(director_agent, outline, config, panel_count)
        if feedback:
            prompt += f"\n\nUSER FEEDBACK (address it in the revised plan): {feedback}"
        llm_text = director_agent.llm._call_ollama(prompt, format_json=True)
        if not llm_text:
            log.warning("[STORYBOARD] LLM returned empty — skipping storyboard")
            return None
        try:
            panels = _parse_panels(llm_text, panel_count)
        except Exception as e:
            log.warning(f"[STORYBOARD] Unparseable LLM response — skipping storyboard: {e}")
            return None

        # Generate panel images via the existing path. The storyboard-scoped
        # config copy disables panel compositing so RAW panels come out —
        # generate_images would otherwise compose manga pages itself and the
        # sheet below would compose them a second time (double-compose).
        panel_prompts = _assemble_storyboard_prompts(panels, config)
        out_dir = Path("studio_outputs") / _safe(topic) / "storyboard"
        try:
            from video.image_gen.image_gen import generate_images

            panel_paths = generate_images(
                panel_prompts, out_dir, _scoped_config(config), project_id=project_name
            )
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
            # Same defaults as image_gen._panel_sizes — mirror exactly or the
            # dataset-walk geometry drifts between generation and composition.
            layout_file = _pc.get("layout_file", "config/panel_layouts.json")
            fallback_layout_file = _pc.get("fallback_layout_file", "config/panel_layouts.json")
        else:
            width, height = 1920, 1080
            # ponytail: fixed 16:9 fallback sheet (landscape approval sheet).
            # The old `aspect` config knob was dead while panel_composite is
            # enabled (geometry mirrors image_gen._panel_sizes), so it was
            # removed rather than kept as a lie.
            page_aspect = 16 / 9
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
                labels=[f"{i + 1} · {p.get('shot_size', '')}" for i, p in enumerate(panels)],
            )
        except Exception as e:
            log.warning(f"[STORYBOARD] Sheet composition failed ({e}) — skipping storyboard")
            return None

        if not pages:
            log.warning("[STORYBOARD] No sheet pages produced — skipping storyboard")
            return None
        sheet_path = _primary_sheet_page(pages, len(panel_paths), layout_file, fallback_layout_file)
        if len(pages) > 1:
            # ponytail: the primary sheet is the fullest page (approval target
            # shows the majority of beats); later pages stay on disk and are
            # listed in the record (never dropped) but not merged — merging
            # would break the single-sheet contract.
            log.info(
                f"[STORYBOARD] Sheet spans {len(pages)} pages — "
                f"primary {sheet_path.name}; extras: "
                f"{[p.name for p in pages if p != sheet_path]}"
            )

        # Approval gate. Any non-"regenerate" reply (Approve, "Proceed as
        # planned." from the UI default, custom text, empty) approves — the
        # storyboard is an advisory gate, never a blocker.
        choice = director_agent.consult_user(
            f"Approve storyboard sheet? ({sheet_path.name})", ["Approve", "Regenerate"]
        )
        if "regenerate" not in choice.lower():
            return _approve_and_persist(
                story_store, prompt, panels, sheet_path, pages, config, out_dir,
                project_name, root,
            )
        if attempt > retries:
            log.warning("[STORYBOARD] Regenerate limit reached — auto-approving last sheet")
            return _approve_and_persist(
                story_store, prompt, panels, sheet_path, pages, config, out_dir,
                project_name, root,
            )
        # Loop back with feedback: ask what should change so the next attempt
        # is informed, not a blind re-roll. Unattended modes return the default
        # "Proceed as planned." — filter it out and keep prior feedback.
        _fb = director_agent.consult_user(
            "What should change in the storyboard plan?", allow_custom=True
        )
        if (
            isinstance(_fb, str)
            and _fb.strip()
            and _fb.strip() not in ("Proceed as planned.", "Proceed with default settings.")
        ):
            feedback = _fb.strip()
        log.info(
            "[STORYBOARD] Regenerating"
            + (f" with feedback: {feedback}" if feedback else " with same inputs")
        )


def _primary_sheet_page(
    pages: list[Path], n_images: int, layout_file, fallback_layout_file
) -> Path:
    """Pick the approval sheet: the page carrying the most panels.

    The dataset walk can lead with a 1-panel splash layout (roboflow order);
    the primary sheet must show the majority of the story's beats.
    ponytail: falls back to pages[0] if the count plan ever disagrees with the
    returned pages (should not happen — same plan_page_counts chain).
    """
    from video.image_gen.panel_compositor import plan_page_counts

    counts = plan_page_counts(n_images, layout_file, fallback_layout_file)
    if len(counts) != len(pages) or not counts:
        return pages[0]
    return pages[counts.index(max(counts))]


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


def wire_storyboard(config: dict, outline: list[dict], storyboard: dict | None) -> None:
    """Merge an approved storyboard record into config + outline; no-op on None.

    Idempotent. Sets config.storyboard.approved_sheet/panels, rides the
    per-panel camera/duration hints onto the outline segments (the per-segment
    plan dicts).
    """
    if not storyboard:
        return
    sb_cfg = config.setdefault("storyboard", {})
    sb_cfg["approved_sheet"] = storyboard.get("sheet_path")
    sb_cfg["panels"] = storyboard.get("panels", [])
    attach_shot_metadata(outline, storyboard.get("panels") or [])


def _persist(
    story_store, prompt: str, panels: list[dict], sheet_path: Path,
    sheet_pages: list[Path] | None = None, char_sheets: dict | None = None,
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
    if char_sheets:
        record["character_sheets"] = char_sheets
    story_store.save_storyboard(record)
    log.info(f"[STORYBOARD] Approved and persisted: {sheet_path}")
    return record


def _approve_and_persist(
    story_store, prompt, panels, sheet_path, pages, config, out_dir,
    project_name, root,
) -> dict:
    """Post-approval: build + wire reference sheets, then persist the record.

    Character reference sheets ride the same approval: portrait becomes the
    master portrait (IP-Adapter reference), full-body + sheet land in
    character assets. Both are advisory — wiring failures degrade the record,
    never the gate.
    """
    char_sheets = _generate_character_sheets(config, out_dir, project_name)
    _wire_character_sheets(char_sheets, project_name, root)
    return _persist(story_store, prompt, panels, sheet_path, pages, char_sheets=char_sheets)


def _safe(topic: str) -> str:
    from utils import _safe_filename

    return _safe_filename(topic)
