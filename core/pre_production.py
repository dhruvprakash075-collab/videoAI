"""pre_production.py - Pre-production phase: Director research, analysis, consultation.

Extracted from pipeline_long.py (Task 1: split god module). Owns everything that
runs ONCE before the per-segment loop:

  • Pre-flight health checks (Ollama, FFmpeg, TTS, disk)
  • Director research + analysis + user consultation
  • Story outline (planning)
  • LoRA Studio Session (upfront character face-lock training)
  • Director memory seeding (StoryMemory, WorldState, PermanentMemoryLog)
  • DecisionRecord build + persist to blackboard
  • Config overlay save for debugging/series reuse

This module NEVER touches Stable Diffusion, TTS, FFmpeg, or the per-segment loop.
It is pure LLM/config/state work, so it can be unit-tested in isolation.
"""

from __future__ import annotations

import json as _json
import logging
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any

from utils import _safe_filename

log = logging.getLogger(__name__)


# ── Config merging helper (shared across all phases) ──────────────────────


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Returns merged dict."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            seen = {str(v) for v in result[key]}
            for v in value:
                if str(v) not in seen:
                    result[key].append(v)
                    seen.add(str(v))
        else:
            result[key] = value
    return result


# ── Narration sanitization (W3 — shared with segment_runner) ──────────────


def _sanitize_narration(script: str) -> str:
    """Strip all non-spoken artifacts from a script before TTS/translation.

    Removes:
      - Story-structure tags: [narration], [/narration], [section], [pause], [scene]
      - LLM XML-ish tags: <answer>, </answer>, <think>...</think>, <|...|>
      - Markdown code fences and headers
      - Parenthetical stage directions: (softly), (whispering), [SFX: ...]
      - Leading labels like "Narration:", "Script:", "Segment 1:"
      - W3: Meta-commentary sentences from the LLM about its own writing
      - W3: Bold markers (**text**), HTML comments, [END_OF_TEXT] tokens
    Returns clean spoken text only.
    """
    import re as _re

    if not script:
        return ""
    s = script
    s = _re.sub(r"<think>.*?</think>", "", s, flags=_re.DOTALL | _re.IGNORECASE)
    s = _re.sub(r"</?[a-zA-Z][a-zA-Z0-9_]*(?:\s[^>]*)?\s*/?>", "", s)
    s = _re.sub(r"<!--.*?-->", "", s, flags=_re.DOTALL)
    s = _re.sub(r"<\|.*?\|>", "", s)
    s = _re.sub(r"```[a-zA-Z]*", "", s)
    s = _re.sub(r"\[END_OF_TEXT\]|\[END\]|\[STOP\]", "", s, flags=_re.IGNORECASE)
    s = _re.sub(r"\*\*([^*]*)\*\*", r"\1", s)
    s = _re.sub(r"\*([^*]*)\*", r"\1", s)
    _meta_patterns = [
        r"\bIn response to (?:your|the) (?:critique|feedback|instructions)\b[^.!?।]{0,150}[.!?।]",
        r"\bThe changes reflect\b[^.!?।]{0,150}[.!?।]",
        r"\bThis version (?:aims|is|reflects)\b[^.!?।]{0,150}[.!?।]",
        r"\bRevised Script\s*:?",
        r"\bHere'?s? (?:is )?the (?:revised|rewritten|updated)\b[^.!?।]{0,150}?(?:script|version|text|story|narration)\b[^.!?।]{0,80}?\s*[:\-]",
        r"\bHere'?s? (?:is )?the (?:revised|rewritten|updated)\b[^.!?।]{0,100}[.!?।]",
        r"\bNow,? each (?:detail|layer)\b[^.!?।]{0,150}[.!?।]",
        r"\bI have (?:revised|rewritten|updated|incorporated)\b[^.!?।]{0,150}[.!?।]",
        r"\bAs (?:requested|instructed|per your)\b[^.!?।]{0,150}?(?:script|version|text|story|narration)\b[^.!?।]{0,80}?\s*[:\-]",
        r"\bAs (?:requested|instructed|per your)\b[^.!?।]{0,100}[.!?।]",
        r"\b(?:Below|Here) (?:is|are) the (?:revised|updated|rewritten)\b[^.!?।]{0,150}[.!?।]",
        r"\bOutput plain text only[^.!?।]{0,150}[.!?।]",
    ]
    for pat in _meta_patterns:
        s = _re.sub(pat, "", s, flags=_re.IGNORECASE | _re.MULTILINE)
    s = _re.sub(
        r"\[/?(?:narration|section|pause|scene|sfx|music|cut|fade)[^\]]*\]",
        "",
        s,
        flags=_re.IGNORECASE,
    )
    s = _re.sub(r"\[[^\]]{0,60}\]", "", s)
    s = _re.sub(
        r"^\s*(?:narration|script|segment\s*\d*|title|hook|insight|escalation)\s*:\s*",
        "",
        s,
        flags=_re.IGNORECASE | _re.MULTILINE,
    )
    s = _re.sub(r"\s+", " ", s).strip()
    return s


# ── Preflight health checks ───────────────────────────────────────────────


def run_preflight_checks(config: dict, dry_run: bool = False) -> None:
    """Run startup checks to ensure all requirements are met before starting the long pipeline."""
    log.info("=" * 60)
    log.info("         RUNNING PRE-FLIGHT SYSTEM HEALTH CHECKS")
    log.info("=" * 60)

    ollama_host = config.get("ollama", {}).get("host", "http://localhost:11434")
    director_model = config.get("models", {}).get("director", "hermes-director")
    writer_model = config.get("models", {}).get("writer", "zephyr-writer")

    checks: dict[str, dict[str, str]] = {
        "Ollama Endpoint Connection": {"status": "PENDING", "info": ollama_host},
        f"Ollama Model '{director_model}'": {"status": "PENDING", "info": "Required for outlining"},
        f"Ollama Model '{writer_model}'": {"status": "PENDING", "info": "Required for scripting"},
        "FFmpeg Executable on PATH": {"status": "PENDING", "info": ""},
        "OmniVoice Python Environment": {
            "status": "PENDING",
            "info": "omnivoice_env/Scripts/python.exe",
        },
    }

    # 1. FFmpeg
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        checks["FFmpeg Executable on PATH"]["status"] = "OK"
        checks["FFmpeg Executable on PATH"]["info"] = ffmpeg_path
    else:
        checks["FFmpeg Executable on PATH"]["status"] = "FAILED"
        checks["FFmpeg Executable on PATH"]["info"] = "NOT FOUND on PATH!"

    # 2. OmniVoice Python
    omnivoice_python = Path("omnivoice_env/Scripts/python.exe")
    if omnivoice_python.exists():
        checks["OmniVoice Python Environment"]["status"] = "OK"
        checks["OmniVoice Python Environment"]["info"] = str(omnivoice_python.resolve())
    else:
        checks["OmniVoice Python Environment"]["status"] = "OK"
        checks["OmniVoice Python Environment"]["info"] = f"Using system Python: {os.sys.executable}"

    # 2.7 TTS engine
    tts_engine = config.get("tts", {}).get("engine", "omnivoice")
    checks[f"TTS Engine '{tts_engine}'"] = {"status": "PENDING", "info": ""}
    if tts_engine == "omnivoice":
        worker_script = Path("audio/omnivoice_worker.py")
        if worker_script.exists():
            checks[f"TTS Engine '{tts_engine}'"]["status"] = "OK"
            checks[f"TTS Engine '{tts_engine}'"]["info"] = "OmniVoice worker script available"
        else:
            checks[f"TTS Engine '{tts_engine}'"]["status"] = "FAILED"
            checks[f"TTS Engine '{tts_engine}'"]["info"] = "audio/omnivoice_worker.py NOT FOUND!"
    elif tts_engine == "edge":
        try:
            import edge_tts

            checks[f"TTS Engine '{tts_engine}'"]["status"] = "OK"
            checks[f"TTS Engine '{tts_engine}'"]["info"] = "edge-tts module installed"
        except ImportError:
            checks[f"TTS Engine '{tts_engine}'"]["status"] = "FAILED"
            checks[f"TTS Engine '{tts_engine}'"]["info"] = "edge-tts module NOT installed!"
    else:
        checks[f"TTS Engine '{tts_engine}'"]["status"] = "OK"
        checks[f"TTS Engine '{tts_engine}'"]["info"] = f"Engine '{tts_engine}' assumed working"

    # 2.5 Disk space
    checks.setdefault("Disk Space Availability", {})
    try:
        _total, _used, free = shutil.disk_usage(".")
        free_gb = free / (1024**3)
        if free_gb > 10.0:
            checks["Disk Space Availability"]["status"] = "OK"
            checks["Disk Space Availability"]["info"] = f"{free_gb:.1f} GB free"
        else:
            checks["Disk Space Availability"]["status"] = "FAILED"
            checks["Disk Space Availability"]["info"] = (
                f"Only {free_gb:.1f} GB free (10GB recommended)"
            )
    except Exception as e:
        checks["Disk Space Availability"]["status"] = "FAILED"
        checks["Disk Space Availability"]["info"] = f"Check failed: {e}"

    # 3. Ollama
    try:
        req = urllib.request.Request(
            f"{ollama_host}/api/tags",
            headers={"User-Agent": "Video.AI Preflight"},
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            data = _json.loads(response.read().decode("utf-8"))
            checks["Ollama Endpoint Connection"]["status"] = "OK"
            checks["Ollama Endpoint Connection"]["info"] = f"Connected to {ollama_host}"
            tags = [t["name"] for t in data.get("models", [])]
            found_dir = any(director_model in t or t.startswith(director_model) for t in tags)
            if found_dir:
                checks[f"Ollama Model '{director_model}'"]["status"] = "OK"
                checks[f"Ollama Model '{director_model}'"]["info"] = "Available in Ollama"
            else:
                checks[f"Ollama Model '{director_model}'"]["status"] = "FAILED"
                checks[f"Ollama Model '{director_model}'"]["info"] = (
                    f"Model '{director_model}' not loaded in Ollama!"
                )
            found_writer = any(writer_model in t or t.startswith(writer_model) for t in tags)
            if found_writer:
                checks[f"Ollama Model '{writer_model}'"]["status"] = "OK"
                checks[f"Ollama Model '{writer_model}'"]["info"] = "Available in Ollama"
            else:
                checks[f"Ollama Model '{writer_model}'"]["status"] = "WARN"
                checks[f"Ollama Model '{writer_model}'"]["info"] = (
                    f"Model '{writer_model}' not pulled yet — run: ollama pull {writer_model}"
                )
    except Exception as e:
        checks["Ollama Endpoint Connection"]["status"] = "FAILED"
        checks["Ollama Endpoint Connection"]["info"] = f"Cannot connect: {e}"
        checks[f"Ollama Model '{director_model}'"]["status"] = "FAILED"
        checks[f"Ollama Model '{director_model}'"]["info"] = "Ollama connection failed"
        checks[f"Ollama Model '{writer_model}'"]["status"] = "FAILED"
        checks[f"Ollama Model '{writer_model}'"]["info"] = "Ollama connection failed"

    # Print table
    log.info(f"{'Check Name':<35} | {'Status':<8} | Details")
    log.info("-" * 80)
    failed = False
    for name, result in checks.items():
        if result["status"] == "OK":
            status_symbol = "[OK]"
        elif result["status"] == "WARN":
            status_symbol = "[WARN]"
        else:
            status_symbol = "[FAILED]"
            failed = True
        log.info(f"{name:<35} | {status_symbol:<8} | {result['info']}")
    log.info("=" * 80)

    if failed:
        log.warning("WARNING: Some preflight system health checks failed. Run may fail!")
        if checks["FFmpeg Executable on PATH"]["status"] == "FAILED" and not dry_run:
            raise RuntimeError(
                "Fatal: FFmpeg is missing from PATH. Video generation is impossible."
            )


# ── Upfront LoRA Studio Session ───────────────────────────────────────────


def _run_studio_session(
    config: dict, out_base: Path, dry_run: bool = False, global_scheduler=None
) -> dict:
    """Pre-train Face-Lock LoRAs for all characters before video generation."""
    try:
        from train_lora import train_protagonist_lora
        from video.image_gen.image_gen import generate_images, unload_sd_pipeline
    except ImportError:
        return {}

    trained_loras: dict[str, Any] = {}
    chars = config.get("characters", {})
    if not chars:
        return trained_loras

    ck_dir = Path(config.get("checkpoint", {}).get("dir", "studio_checkpoints"))
    ck_dir.mkdir(parents=True, exist_ok=True)

    for c_key, c_data in chars.items():
        char_name = c_data.get("name", c_key)
        char_desc = c_data.get("description", "")

        if len(char_desc) < 50:
            log.info(
                f"[Studio Session] Skipping {char_name} — description too short ({len(char_desc)} chars, need 50+)"
            )
            continue
        desc_lower = char_desc.lower()
        visual_terms = [
            "hair",
            "eye",
            "wearing",
            "cloak",
            "armor",
            "tall",
            "short",
            "beard",
            "scar",
            "muscular",
            "slim",
            "pale",
            "dark",
            "bright",
            "hood",
            "coat",
            "sword",
            "staff",
            "mask",
            "tattoo",
            "mark",
            "symbol",
            "glove",
            "boot",
        ]
        if not any(t in desc_lower for t in visual_terms):
            log.info(
                f"[Studio Session] Skipping {char_name} — no visual detail keywords in description"
            )
            continue
        log.info(
            f"[Studio Session] Description OK for {char_name}: {len(char_desc)} chars, visual terms found"
        )

        existing_loras = list(
            ck_dir.glob(f"protagonist_{char_name.lower().replace(' ', '_')}*lora.safetensors")
        )
        if existing_loras:
            log.info(
                f"[Studio Session] LoRA already exists for {char_name}: {existing_loras[0].name}"
            )
            trained_loras[c_key] = existing_loras[0]
            continue

        log.info(f"[Studio Session] Generating reference images for {char_name}...")

        prompts = "; ".join([f"close up portrait, detailed face, {char_desc}"] * 5)
        ref_dir = out_base.parent / "studio_refs" / c_key
        ref_dir.mkdir(parents=True, exist_ok=True)

        seg_config = dict(config)
        seg_config["image_gen"] = dict(config.get("image_gen", {}))
        seg_config["image_gen"]["negative_prompt"] = (
            "photorealistic, real life, 3d, extra limbs, bad anatomy, disfigured, blurry"
        )

        if global_scheduler is not None:
            with global_scheduler.task("heavy", f"Studio:{c_key}:gen"):
                try:
                    images = generate_images(prompts, ref_dir, seg_config)
                except Exception as e:
                    log.warning(f"[Studio Session] Image gen failed for {char_name}: {e}")
                    continue
        else:
            try:
                images = generate_images(prompts, ref_dir, seg_config)
            except Exception as e:
                log.warning(f"[Studio Session] Image gen failed for {char_name}: {e}")
                continue

        if not images or len(images) < 5:
            log.warning(f"[Studio Session] Failed to generate 5 references for {char_name}")
            continue

        if global_scheduler is not None:
            with global_scheduler.task("heavy", f"Studio:{c_key}:unload"):
                unload_sd_pipeline()
        else:
            unload_sd_pipeline()

        log.info(f"[Studio Session] Training Face-Lock LoRA for {char_name} (~2 min)...")
        if global_scheduler is not None:
            with global_scheduler.task("heavy", f"Studio:{c_key}:train"):
                lora_path = train_protagonist_lora(
                    image_paths=images[:5],
                    char_name=char_name,
                    output_dir=ck_dir,
                    char_description=char_desc,
                    mock=dry_run,
                )
        else:
            lora_path = train_protagonist_lora(
                image_paths=images[:5],
                char_name=char_name,
                output_dir=ck_dir,
                char_description=char_desc,
                mock=dry_run,
            )

        if lora_path:
            trained_loras[c_key] = lora_path
            log.info(
                f"[Studio Session] Successfully trained LoRA for {char_name}: {lora_path.name}"
            )
        else:
            log.warning(f"[Studio Session] LoRA training failed for {char_name}")

    return trained_loras


# ── Director memory seeding ───────────────────────────────────────────────


def _seed_director_memory(topic: str, overlay: dict, config: dict) -> None:
    """Feed Director pre-production findings into StoryMemory + WorldState."""
    from memory import StoryMemory, WorldState
    from memory.permanent_memory import PermanentMemoryLog

    perm = PermanentMemoryLog(topic=topic)
    perm.data.setdefault("director_knowledge", {})

    for c_key, c_data in overlay.get("characters", {}).items():
        name = c_data.get("name", c_key)
        desc = c_data.get("description", "")
        if name and desc:
            perm.log_character(name, desc, "")
        perm.data["director_knowledge"][c_key] = {
            "name": name,
            "description": desc,
            "source": "director_pre_production",
        }

    vision = overlay.get("_director_vision", {})
    if vision.get("theme"):
        perm.log_recurring_motif("theme", vision["theme"])
    if vision.get("emotions"):
        perm.log_recurring_motif("emotions", vision["emotions"])
    perm.data["director_knowledge"]["production_notes"] = overlay.get("production_notes", {})
    perm._save_memory()

    ck_dir = Path(config.get("checkpoint", {}).get("dir", "studio_checkpoints"))
    ws = WorldState(topic=topic, checkpoint_dir=ck_dir)

    for c_data in overlay.get("characters", {}).values():
        name = c_data.get("name", "")
        desc = c_data.get("description", "")
        if name:
            ws._data.setdefault("characters", {})
            ws._data["characters"][name] = {
                "first_seen_seg": 0,
                "moods_seen": [],
                "status": "active",
                "description": desc,
            }
            fact = f"{name}: {desc[:150]}" if desc else f"Character: {name}"
            if fact not in ws._data.get("world_facts", []):
                ws._data.setdefault("world_facts", []).append(fact)

    for rec in overlay.get("production_notes", {}).get("recommendations", []):
        if rec and rec not in ws._data.get("world_facts", []):
            ws._data.setdefault("world_facts", []).append(f"[Director] {rec}")

    ws._save()
    log.info(
        f"[MEMORY] Director knowledge seeded: "
        f"{len(overlay.get('characters', {}))} characters, "
        f"{len(ws._data.get('world_facts', []))} facts"
    )

    mem_file = config.get("memory", {}).get("memory_file", "studio_checkpoints/story_memory.json")
    sm = StoryMemory(Path(mem_file))
    char_summaries = ", ".join(
        f"{c_data.get('name', k)}" for k, c_data in overlay.get("characters", {}).items()
    )
    theme = vision.get("theme", "Unknown")
    sm.save(
        topic,
        0,
        f"Director pre-production for {topic}. "
        f"Theme: {theme}. Characters: {char_summaries}. "
        f"Style: {vision.get('visual_style', '')}. "
        f"Emotions: {vision.get('emotions', '')}. ",
        f"Director Vision: {theme} — {char_summaries}",
    )
    log.info("[MEMORY] StoryMemory pre-seeded with Director context")


# ── Main pre-production entry point ───────────────────────────────────────


def run_pre_production(
    topic: str,
    config: dict,
    skip_consultation: bool = False,
    content_text: str | None = None,
    force_refresh: bool = False,
    project_name: str | None = None,
    cli_flags: dict | None = None,
    run_mode: str = "one_time",
) -> dict:
    """Run Director pre-production before any segment generation.

    Phases:
      1. Web research (Wikipedia + DuckDuckGo, no spoilers)
      2. Director analyzes story + research -> Vision Document
      3. User consultation (CLI multiple-choice prompts)
      4. Writer collaboration (scene breakdown suggestions)
      5. Build runtime config overlay
      6. Build & persist DecisionRecord to blackboard
      7. Seed StoryMemory + WorldState + PermanentMemoryLog

    Returns config_overlay dict that overrides config.yaml defaults.
    """
    from agents.director_agent import DirectorAgent

    log.info("=" * 60)
    log.info("  DIRECTOR PRE-PRODUCTION")
    log.info("=" * 60)

    director = DirectorAgent(config)
    director._force_refresh = force_refresh
    config_overlay: dict[str, Any] = {"video": {}}

    output_mode = os.environ.get("DIRECTOR_MODE", "full").lower()
    if output_mode not in ("full", "video-only", "voice-only"):
        output_mode = "full"
    log.info(f"[DIRECTOR] Output mode: {output_mode}")

    log.info("[DIRECTOR] Phase 0: Pre-flight decisions...")
    try:
        do_search = director.ask_search_online()
    except Exception as e:
        log.warning(f"[DIRECTOR] Consultation failed ({e}) — proceeding with defaults")
        do_search = False

    try:
        director.ask_cache_ttl()
    except Exception as e:
        log.warning(f"[DIRECTOR] Cache TTL consultation failed ({e}) — using defaults")

    log.info(f"[DIRECTOR] Web search: {'ON' if do_search else 'OFF'}")

    if content_text and content_text.strip():
        create_scratch, scratch_notes = False, ""
        log.info("[DIRECTOR] Story file provided — using it as-is (skipping create-from-scratch)")
    else:
        try:
            create_scratch, scratch_notes = director.ask_create_from_scratch(topic)
        except Exception as e:
            log.warning(
                f"[DIRECTOR] Create-from-scratch consultation failed ({e}) — proceeding with defaults"
            )
            create_scratch, scratch_notes = False, ""
        log.info(f"[DIRECTOR] Create from scratch: {'YES' if create_scratch else 'NO'}")

    _models = config.setdefault("models", {})
    if create_scratch:
        _chosen_writer = _models.get("writer_scratch", _models.get("writer", "cra-guided-7b"))
        log.info(
            f"[DIRECTOR] Writer mode: CREATE-FROM-SCRATCH → creative writer '{_chosen_writer}'"
        )
    else:
        _chosen_writer = _models.get("writer_adapt", _models.get("writer", "zephyr-writer"))
        log.info(f"[DIRECTOR] Writer mode: ADAPTATION → faithful writer '{_chosen_writer}'")
    _models["writer"] = _chosen_writer

    if create_scratch:
        story_text = director.invent_story(topic, scratch_notes)
        vision_doc = director.analyze_with_research(
            topic,
            {"combined_summary": story_text, "result_count": 0},
            content_text=content_text,
        )
        user_responses, writer_input = director.consult_on_config(vision_doc)
        config_overlay = director.produce_runtime_config(
            vision_doc, user_responses, writer_input, mode=output_mode
        )
        config_overlay["_invented_story"] = story_text
        _ov_chars = config_overlay.get("characters", {})
        _usable = {
            k: v
            for k, v in _ov_chars.items()
            if isinstance(v, dict) and len(str(v.get("description", "")).strip()) >= 30
        }
        if not _usable:
            log.warning(
                "[PRE-PROD] Scratch mode produced no characters with visual detail — "
                "keeping config.yaml characters for visual consistency"
            )
            config_overlay.pop("characters", None)
        else:
            config_overlay["characters"] = _usable
        log.info("[PRE-PROD] Original story created! Skipping web research.")

        try:
            from agents.decision_engine import build_decision_record

            _scratch_user_locks = {"run_mode": run_mode}
            if project_name:
                _scratch_user_locks["project_name"] = project_name
            _scratch_duration = config_overlay.get("video", {}).get("total_duration_min")
            if _scratch_duration:
                _scratch_user_locks["total_duration_min"] = _scratch_duration
            _scratch_rec = build_decision_record(
                director=director,
                vision_doc=vision_doc,
                writer_input=writer_input,
                user_locks=_scratch_user_locks,
                cli_flags=dict(cli_flags or {}),
                config=config,
            )
            from memory.blackboard import get_blackboard

            _scratch_bb = get_blackboard(config, topic_slug=_safe_filename(topic))
            _scratch_bb.write_decision(_scratch_rec)
            config_overlay = _deep_merge(config_overlay, _scratch_rec.to_overlay())
            log.info(
                f"[PRE-PROD] Scratch DecisionRecord built and persisted — "
                f"segments={_scratch_rec.segment_count.value}, "
                f"duration={_scratch_rec.total_duration_min.value}min, "
                f"words/seg={_scratch_rec.words_per_segment.value}"
            )
        except Exception as _e:
            log.warning(f"[PRE-PROD] Scratch DecisionEngine failed ({_e}) — overlay used as-is")

        return config_overlay

    # Phase 1: Web Research
    if do_search:
        research = director.research_story(topic)
    else:
        log.info("[DIRECTOR] Phase 1/5: Web research SKIPPED by user")
        research = {"topic": topic, "combined_summary": "", "result_count": 0}

    # Series continuity
    overlay_dir = Path("studio_checkpoints")
    prev_overlay_path = overlay_dir / f"config_overlay_{_safe_filename(topic)}.json"
    prev_overlay = None
    if prev_overlay_path.exists():
        try:
            prev_overlay = _json.loads(prev_overlay_path.read_text(encoding="utf-8"))
            log.info(f"[PRE-PROD] Loaded previous config overlay: {prev_overlay_path.name}")
        except Exception:
            pass

    if skip_consultation and prev_overlay:
        log.info("[PRE-PROD] Series resume detected — skipping phases 1-3, reusing previous config")
        vision_doc = prev_overlay.get("_director_vision", {})
        if not vision_doc:
            vision_doc = {}
        chars = prev_overlay.get("characters", {})
        if isinstance(chars, dict):
            chars_list = []
            for name, details in chars.items():
                if isinstance(details, dict):
                    c = details.copy()
                    c.setdefault("name", name)
                else:
                    c = {"name": name, "description": str(details)}
                chars_list.append(c)
            chars = chars_list
        vision_doc["characters"] = chars
        user_responses = {
            "visual_style": prev_overlay.get("visual", {}).get("style", ""),
            "subtitle_style": prev_overlay.get("subtitles", {}).get("format", "classic"),
            "tts_engine": prev_overlay.get("tts", {}).get("engine", "omnivoice"),
            "custom_instructions": prev_overlay.get("production_notes", {}).get(
                "custom_instructions", ""
            ),
        }
        writer_input = director.consult_with_writer(vision_doc, user_responses)
        config_overlay = director.produce_runtime_config(
            vision_doc, user_responses, writer_input, mode=output_mode
        )
        config_overlay = _deep_merge(prev_overlay, config_overlay)
        log.info(
            f"[PRE-PROD] Series resume: overlay merged with previous ({len(config_overlay.get('characters', {}))} total chars)"
        )

        try:
            from agents.decision_engine import build_decision_record

            _series_user_locks = {"run_mode": run_mode}
            if project_name:
                _series_user_locks["project_name"] = project_name
            _series_duration = config_overlay.get("video", {}).get("total_duration_min")
            if _series_duration:
                _series_user_locks["total_duration_min"] = _series_duration
            _series_rec = build_decision_record(
                director=director,
                vision_doc=vision_doc,
                writer_input=writer_input,
                user_locks=_series_user_locks,
                cli_flags=dict(cli_flags or {}),
                config=config,
            )
            from memory.blackboard import get_blackboard

            _series_bb = get_blackboard(config, topic_slug=_safe_filename(topic))
            _series_bb.write_decision(_series_rec)
            config_overlay = _deep_merge(config_overlay, _series_rec.to_overlay())
            log.info(
                f"[PRE-PROD] Series DecisionRecord built and persisted — "
                f"segments={_series_rec.segment_count.value}, "
                f"duration={_series_rec.total_duration_min.value}min, "
                f"words/seg={_series_rec.words_per_segment.value}"
            )
        except Exception as _e:
            log.warning(f"[PRE-PROD] Series DecisionEngine failed ({_e}) — overlay used as-is")

        return config_overlay

    # Phase 2: Director analysis
    vision_doc = director.analyze_with_research(
        topic,
        research,
        config.get("video", {}).get("total_duration_min", 10),
        content_text=content_text,
    )

    # Phase 2.5: Duration
    director_recommended = vision_doc.get("recommended_duration_min", 0)
    config_default = config.get("video", {}).get("total_duration_min", 10)
    if director_recommended and director_recommended > 0:
        est_minutes = int(director_recommended)
        log.info(f"[DURATION] Director recommends {est_minutes} min based on content analysis")
        config_overlay["video"]["total_duration_min"] = est_minutes
        config_overlay["video"]["_director_recommended"] = True
    else:
        est_minutes = config_default
    if content_text and len(content_text) > 500:
        duration_choice = director.consult_on_duration(est_minutes)
        if duration_choice["action"] == "cliffhanger":
            cliffhangers = director.suggest_cliffhangers(content_text, est_minutes)
            cliff_options = [
                "{} — {} (approx {} min)".format(
                    f"Option {i + 1}",
                    c["outcome"],
                    max(5, int(est_minutes * c["point"] / 100)),
                )
                for i, c in enumerate(cliffhangers)
            ]
            chosen = director.consult_user(
                "Which cliffhanger point would you like the video to end at?",
                options=cliff_options,
                allow_custom=True,
            )
            chosen_idx = 0
            for i, opt in enumerate(cliff_options):
                if opt in chosen:
                    chosen_idx = i
                    break
            chosen_point = cliffhangers[chosen_idx]["point"]
            est_minutes = max(5, int(est_minutes * chosen_point / 100))
            config_overlay["video"]["total_duration_min"] = est_minutes
            config_overlay["video"]["_cliffhanger_point"] = chosen_point
            config_overlay["video"]["_cliffhanger_reason"] = cliffhangers[chosen_idx]["reason"]
            log.info(f"[DURATION] Cliffhanger at {chosen_point}% -> {est_minutes} min video")

        elif duration_choice["action"] == "compact":
            target = duration_choice["target_minutes"]
            compacted = director.compact_story(content_text, target, est_minutes)
            content_text = compacted
            log.info("[DURATION] Re-analyzing with compacted content...")
            vision_doc = director.analyze_with_research(
                topic, research, target, content_text=compacted
            )
            config_overlay["video"]["total_duration_min"] = target
            config_overlay["video"]["_content_compacted"] = True
            est_minutes = target

        elif duration_choice["action"] == "custom":
            config_overlay["video"]["total_duration_min"] = duration_choice["target_minutes"]
            est_minutes = duration_choice["target_minutes"]

        elif duration_choice["action"] == "adjusted":
            target = duration_choice.get("target_minutes", est_minutes)
            config_overlay["video"]["total_duration_min"] = target
            config_overlay["video"]["_user_adjusted"] = True
            est_minutes = target
            log.info(f"[DURATION] User adjusted duration to {est_minutes} min")

    # Phase 3: User consultation
    user_responses, writer_input = director.consult_on_config(vision_doc)

    # Phase 4: Writer collaboration
    if not skip_consultation:
        try:
            writer_structural = director.consult_with_writer(vision_doc, user_responses)
            for k in (
                "segment_count",
                "words_per_segment",
                "image_count_per_segment",
                "opening_hook_style",
                "pacing_notes",
            ):
                if writer_structural.get(k):
                    writer_input.setdefault(k, writer_structural[k])
        except Exception as e:
            log.warning(f"[PRE-PROD] Writer consultation failed ({e}) — using Director proposals")

    # Phase 5: Build overlay
    config_overlay = director.produce_runtime_config(
        vision_doc, user_responses, writer_input, mode=output_mode
    )

    # Build DecisionRecord (single source of truth)
    _cli_flags = dict(cli_flags or {})
    _user_locks: dict[str, Any] = {}
    user_chosen_duration = config_overlay.get("video", {}).get("total_duration_min")
    _video_ov = config_overlay.get("video", {})
    _user_picked_duration = (
        _video_ov.get("_cliffhanger_point") is not None
        or _video_ov.get("_content_compacted")
        or _video_ov.get("_user_adjusted")
        or (
            user_chosen_duration
            and not _video_ov.get("_director_recommended")
            and user_chosen_duration != config.get("video", {}).get("total_duration_min")
        )
    )
    if user_chosen_duration and _user_picked_duration:
        _user_locks["total_duration_min"] = user_chosen_duration
    _user_locks["run_mode"] = run_mode
    if project_name:
        _user_locks["project_name"] = project_name

    try:
        from agents.decision_engine import build_decision_record

        rec = build_decision_record(
            director=director,
            vision_doc=vision_doc,
            writer_input=writer_input,
            user_locks=_user_locks,
            cli_flags=_cli_flags,
            config=config,
        )
        from memory.blackboard import get_blackboard

        bb = get_blackboard(config, topic_slug=_safe_filename(topic))
        bb.write_decision(rec)
        config_overlay = _deep_merge(config_overlay, rec.to_overlay())
        log.info(
            f"[PRE-PROD] DecisionRecord built and persisted — "
            f"segments={rec.segment_count.value}, "
            f"duration={rec.total_duration_min.value}min, "
            f"words/seg={rec.words_per_segment.value}"
        )
    except Exception as e:
        log.warning(f"[PRE-PROD] DecisionEngine failed ({e}) — overlay used as-is")

    seg_count = config_overlay.get("video", {}).get("total_duration_min", "?")
    log.info(
        f"Pre-production complete! Config overlay: "
        f"{len(config_overlay.get('characters', {}))} characters, "
        f"~{seg_count} min"
    )

    # Normalize TTS engine in overlay
    try:
        from audio.audio_proxy import normalize_tts_engine as _norm_engine

        _raw_ov_engine = config_overlay.get("tts", {}).get("engine", "")
        if _raw_ov_engine:
            _norm_ov_engine = _norm_engine(_raw_ov_engine)
            if _norm_ov_engine != _raw_ov_engine:
                log.warning(
                    f"[PRE-PROD] TTS engine {_raw_ov_engine!r} in overlay "
                    f"normalized to {_norm_ov_engine!r}"
                )
            config_overlay.setdefault("tts", {})["engine"] = _norm_ov_engine
    except Exception as _ne:
        log.debug(f"[PRE-PROD] TTS engine normalization skipped: {_ne}")

    # Save overlay for series reuse
    overlay_dir = Path("studio_checkpoints")
    overlay_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = overlay_dir / f"config_overlay_{_safe_filename(topic)}.json"
    with open(overlay_path, "w", encoding="utf-8") as f:
        _json.dump(config_overlay, f, indent=2, ensure_ascii=False)
    log.info(f"Config overlay saved: {overlay_path}")

    return config_overlay


# ── Story outline (called once before segment loop) ───────────────────────


def plan_outline(
    topic: str, n_segs: int, config: dict, director_agent, cp_mgr, resume: bool
) -> list[dict]:
    """Plan the story outline (once, before segments). Loads from checkpoint if available."""
    from utils.story_planner import plan_story

    ck_meta = cp_mgr.get(f"{topic}_meta") if resume else None
    if ck_meta and "outline" in ck_meta:
        outline = ck_meta["outline"]["data"]
        log.info("[OK] Story outline loaded from checkpoint")
    else:
        log.info("Planning story outline...")
        outline = plan_story(topic, n_segs, config, director_agent)
        cp_mgr.save(f"{topic}_meta", "outline", {"data": outline})
        log.info(f"[OK] Story outline: {len(outline)} segments")

    return outline


# ── Time formatters (shared with post_production) ─────────────────────────


def format_time_hms(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def format_chapters_time(sec: float) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_video_duration(mp4: Path) -> float:
    """Read a video's actual duration via ffprobe. Returns 30.0 on error."""
    import json as _json
    import subprocess

    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(mp4)],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
        )
        return float(_json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 30.0
