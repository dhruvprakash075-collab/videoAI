"""segment_runner.py - Per-segment loop: script → TTS → images → render.

Extracted from pipeline_long.py (Task 1: split god module). Owns the per-segment
work that runs N times (one per segment):

  • Preview gate (after segment 1 in --preview mode)
  • Script generation (W2: structured Ollama, CrewAI fallback)
  • Script review + revision
  • Devanagari translation (Director or fallback)
  • WorldState update (B3)
  • TTS (Supertonic / OmniVoice) + SFX + mastering
  • Stable Diffusion image generation (OOM ladder)
  • FFmpeg MP4 assembly (Hyperframes renderer or fallback)
  • Checkpoint save
  • Memory write

This module NEVER calls pre-production. It receives an already-resolved config
and outline, and runs each segment independently. process_segment() is the hot
path; _process_segment_with_budget() wraps it in a retry budget.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

# ── moved verbatim from core/runtime/abort.py ──
from core.runtime.abort import (  # noqa: F401
    _director_aborted,
    get_director_abort,
    set_director_abort,
)
from utils import build_prompts
from utils.emotion_control import inject_emotion

log = logging.getLogger(__name__)

# ── moved verbatim to core/runtime/ollama.py ──
from core.preview import _preview_gate  # noqa: F401
from core.runtime.ollama import (  # noqa: F401
    _ollama_alive,
    evict_ollama_models,
    schedule_ollama_stop,
    start_ollama_server,
    stop_ollama_server,
    touch_ollama_active,
)
from core.runtime.vram import (
    aggressive_vram_cleanup,
    log_vram_usage,
)

# ── moved verbatim from core/segment/budget.py / identity.py ──
from core.segment.budget import _trim_script_to_word_limit, _tts_word_budget  # noqa: F401
from core.segment.identity import _detect_important_trigger, _perceptual_hash

# ── Main per-segment processor ─────────────────────────────────


def make_process_segment(
    *,
    topic: str,
    config: dict,
    outline: list[dict],
    n_segs: int,
    out_base: Path,
    tts_cfg: dict,
    cp_mgr,
    world_state,
    mem,
    ctx_mgr,
    director_agent_instance,
    writer_agent,
    resume: bool,
    dry_run: bool,
    fast_dry_run: bool = False,
    preview_mode: bool,
    words_per_seg: int,
    seg_min: int,
    shared_prompt_executor,
    global_scheduler,
    _crewai_lock,
    crewai_lock: threading.RLock,
    completed_segs_counter_holder: list,
    completed_segs_lock: threading.Lock,
    mp4s: list[Path | None],
    mp4s_lock: threading.Lock,
    run_start_ts: float,
    source_chunks: list | None = None,
):
    """Build the per-segment closure. Returns (process_segment, _process_segment_with_budget).

    All shared state is captured in the closure so process_segment(i) can be passed
    directly to executor.submit(). The retry budget wrapper retries up to
    performance.max_segment_retries (default 2) on exception.
    """
    # Lazy imports to avoid circular import at module load

    from core.pre_production import _reject_unsafe_narration, _sanitize_narration

    try:
        from video.image_gen.image_gen import generate_images
    except ImportError:
        generate_images = None
        log.warning("image_gen not installed — using black-frame videos")

    with contextlib.suppress(ImportError):
        pass

    from core.pipeline_graph import SegmentGraphBuilder, SegmentState

    def write_script_node(state: SegmentState) -> dict:
        i = state["i"]
        plan = state["plan"]
        context = state["context"]
        key = f"{topic}_seg{i:02d}"
        ck = cp_mgr.get(key) if resume else None

        if ck and "script" in ck:
            script = ck["script"]["data"]
            log.debug(f"  Seg {i}: script from checkpoint")
            return {"script": script}

        chunk = state.get("source_chunk")
        if chunk:
            log.debug(
                f"  Seg {i}: source-path short-circuit (chunk={chunk.index}, {len(chunk.text)} chars)"
            )
            return {"script": chunk.text}

        if fast_dry_run:
            _title = plan.get("title", f"Part {i}")
            _summary = plan.get("summary", "")
            script = f"{_title}. {_summary} This is a fast dry-run placeholder."
            log.debug(f"  Seg {i}: fast-dry-run stub script ({len(script)} chars)")
            return {"script": script}

        log.debug(f"  Seg {i}: generating script (LIGHT)")
        writer = writer_agent

        # A 6 GB GPU cannot keep the Director and Writer resident together.
        evict_ollama_models(config, reason="Writer model handoff")

        seg_words = plan.get("target_word_count", words_per_seg)
        tolerance = config.get("script", {}).get("word_count_tolerance", 0.25)
        lo = int(words_per_seg * (1 - tolerance))
        hi = int(words_per_seg * (1 + tolerance))
        seg_words = max(lo, min(hi, seg_words))
        persona = config.get("narrator_persona", "")

        from utils.story_planner import build_segment_prompt

        _include_char_desc = config.get("narrator", {}).get("include_character_descriptions", False)
        prompt = build_segment_prompt(
            plan,
            context,
            n_segs,
            seg_words,
            world_state_block="",
            narrator_persona=persona,
            include_character_descriptions=_include_char_desc,
        )
        _director_instruction = config.get("production_notes", {}).get(
            "custom_instructions", ""
        )
        if _director_instruction:
            prompt += f"\n\nDIRECTOR PRODUCTION INSTRUCTION:\n{_director_instruction}"
        feedback = state.get("critic_feedback") or ""
        if feedback:
            prompt = (
                prompt
                + "\n\nCRITIC FEEDBACK FROM PREVIOUS ATTEMPT (address these issues):\n"
                + feedback
            )

        with global_scheduler.task("heavy", f"Seg{i}:script-writer"):
            _writer_model = config.get("models", {}).get("writer", "zephyr-writer")
            _structured_prompt = (
                prompt
                + "\n\nReturn ONLY valid JSON in this exact format — no other text:\n"
                + '{"narration": "<spoken narration text only, no HTML, no markdown, '
                + 'no stage directions, no commentary about your writing>"}'
            )
            _script_from_structured = None
            try:
                from utils.crewai_breaker import guarded_ollama_call

                _raw_json = guarded_ollama_call(
                    _structured_prompt,
                    model=_writer_model,
                    format_json=True,
                    temperature=0.7,
                    num_predict=config.get("script", {}).get("writer_max_tokens", 1024),
                )
                if _raw_json:
                    import json as _json_w

                    _parsed = _json_w.loads(_raw_json)
                    _narration = _parsed.get("narration", "").strip()
                    if _narration:
                        _script_from_structured = _narration
                        log.debug(
                            f"  Seg {i}: structured Ollama writer OK ({len(_narration)} chars)"
                        )
            except Exception as _w2_e:
                log.warning(
                    f"  Seg {i}: structured writer failed ({_w2_e}) — falling back to CrewAI"
                )

            if _script_from_structured:
                script = _script_from_structured
            else:
                log.debug(f"  Seg {i}: using CrewAI writer fallback")
                from crewai import Crew, Task
                from crewai.process import Process

                from utils.crewai_breaker import (
                    BreakerOpen,
                    guarded_crewai_kickoff,
                    record_breaker_failure,
                    record_breaker_success,
                )

                crew = Crew(
                    agents=[writer],
                    tasks=[
                        Task(
                            description=prompt,
                            agent=writer,
                            expected_output=f"Script for segment {i}",
                        )
                    ],
                    process=Process.sequential,
                    cache=True,
                    verbose=False,
                )
                _writer_model = config.get("models", {}).get("writer", "zephyr-writer")
                with _crewai_lock:
                    try:
                        result = guarded_crewai_kickoff(crew, model_name=_writer_model)
                        record_breaker_success(_writer_model)
                    except BreakerOpen:
                        log.warning(
                            f"  Seg {i}: circuit breaker OPEN for {_writer_model} — using raw kickoff"
                        )
                        result = crew.kickoff()
                    except Exception:
                        record_breaker_failure(_writer_model)
                        raise
                script = str(getattr(result, "raw", result)).strip()

        return {"script": script}

    def critic_node(state: SegmentState) -> dict:
        i = state["i"]
        _plan = state["plan"]
        _context = state["context"]
        script = state["script"]
        rewrites = state.get("rewrites_attempted", 0)

        if fast_dry_run:
            return {"critic_approved": True, "critic_feedback": ""}

        from utils import validate_script

        if not validate_script(script, config):
            log.warning(f"  Seg {i}: script validation failed")
            return {
                "critic_approved": False,
                "critic_feedback": "Validation failed",
                "rewrites_attempted": rewrites + 1,
            }

        if state.get("source_chunk"):
            log.debug(f"  Seg {i}: source-path — critic auto-approves verbatim source")
            return {"critic_approved": True, "critic_feedback": ""}

        from utils.critic import is_approved, score_script

        threshold = int(config.get("critic", {}).get("threshold", 60))

        score = score_script(script, config)
        if score is None:
            log.warning(f"  Seg {i}: critic LLM unavailable; auto-approving")
            return {"critic_approved": True, "critic_feedback": ""}

        if is_approved(score, threshold):
            log.info(f"  Seg {i}: critic approved ({score.total}/100)")
            return {"critic_approved": True, "critic_feedback": ""}

        log.info(
            f"  Seg {i}: critic scored {score.total}/100 (threshold {threshold}) — rejecting for rewrite"
        )
        return {
            "critic_approved": False,
            "critic_feedback": "; ".join(score.issues + score.suggestions),
            "rewrites_attempted": rewrites + 1,
        }

    def translate_node(state: SegmentState) -> dict:
        i = state["i"]
        plan = state["plan"]
        script = state["script"]
        key = f"{topic}_seg{i:02d}"

        if fast_dry_run:
            cp_mgr.save(key, "script", {"data": script})
            _drs = f"[DRY-RUN] {script}" if dry_run or fast_dry_run else script
            try:
                world_state.update(_drs, plan, config=config)
            except Exception as _ws_e:
                log.warning(f"  Seg {i}: world_state.update (translate, dry-run) failed: {_ws_e}")
            return {"devanagari_script": None, "script_for_tts": script}

        # Sanitize BEFORE checkpointing so TTS never sees artifacts
        script = _sanitize_narration(script)

        # Reject unsafe leftovers after sanitization
        if _reject_unsafe_narration(script) is None:
            log.warning(
                f"  Seg {i}: narration unsafe after sanitization — falling back to sanitized text"
            )
            if not script or len(script) < 10:
                log.error(f"  Seg {i}: narration rejected entirely after sanitization")
                return {
                    "devanagari_script": None,
                    "script_for_tts": script,
                    "narration_rejected": True,
                }

        cp_mgr.save(key, "script", {"data": script})

        devanagari_script = None
        from config.config import get_language

        _audio_lang = get_language(config)
        if _audio_lang == "hi":
            try:
                evict_ollama_models(config, reason="Translator model handoff")
                with global_scheduler.task("heavy", f"Seg{i}:translate"):
                    with crewai_lock:
                        devanagari_script = director_agent_instance.translate_to_devanagari(
                            script, plan, state["context"]
                        )
                if not devanagari_script:
                    log.warning(
                        f"  Seg {i}: Director translation failed; falling back to English TTS"
                    )
                if devanagari_script:
                    en_words = max(1, len(script.split()))
                    hi_words = len(devanagari_script.split())
                    # Modern Hinglish is roughly word-for-word with English, so judge
                    # by WORD count (what the Director controls via the script), not by
                    # Devanagari character count (Devanagari uses more chars per word).
                    if hi_words > en_words * 1.8:
                        log.warning(
                            f"  Seg {i}: Director translation bloated "
                            f"({hi_words} Hindi words vs {en_words} English words); "
                            "falling back to English TTS"
                        )
                        devanagari_script = None
                    else:
                        log.info(f"  Seg {i}: Director translated to Devanagari")
            except Exception as e:
                log.warning(f"  Seg {i}: Director translation failed ({e}); falling back to English TTS")
                devanagari_script = None

        _ws_script = f"[DRY-RUN] {script}" if dry_run or fast_dry_run else script
        try:
            world_state.update(_ws_script, plan, config=config)
        except Exception as _ws_e:
            log.warning(f"  Seg {i}: world_state.update (translate) failed: {_ws_e}")

        return {"devanagari_script": devanagari_script, "script_for_tts": script}

    def tts_node(state: SegmentState) -> dict:
        i = state["i"]
        plan = state["plan"]
        script = state["script_for_tts"]
        dev_script = state.get("devanagari_script")
        key = f"{topic}_seg{i:02d}"
        ck = cp_mgr.get(key) if resume else None

        if ck and "audio" in ck and Path(ck["audio"]["data"]).exists():
            return {
                "audio_path": ck["audio"]["data"],
                "word_timestamps_json": ck["audio"].get("word_timestamps"),
            }

        if dry_run:
            return {"audio_path": None, "word_timestamps_json": None}

        from config.config import get_language

        audio_lang = get_language(config)
        mood = plan.get("mood", "mysterious")

        if audio_lang == "hi" and dev_script:
            tts_lang = "hi"
            script_for_tts = inject_emotion(dev_script, mood, lang="hi")
        else:
            tts_lang = "en"
            script_for_tts = inject_emotion(script, mood, lang="en")

        # Normalize Hindi characters for Supertonic compatibility
        if tts_lang == "hi":
            from core.pre_production import _normalize_hindi_for_tts as _norm_hin

            _normalized = _norm_hin(script_for_tts)
            if _normalized != script_for_tts:
                log.info(f"  Seg {i}: normalized Hindi characters for TTS")
                script_for_tts = _normalized

        from utils.emotion_control import get_mood_rate

        _tts_speed = get_mood_rate(mood)

        evict_ollama_models(config, reason="TTS")
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        from audio.audio_proxy import tts_generate
        with global_scheduler.task("heavy", f"Seg{i}:TTS"):
            tts_out = tts_generate(
                script_for_tts, lang=tts_lang, output_dir=out_base / "audio", speed=_tts_speed
            )
            audio_path = tts_out["wav_path"] if isinstance(tts_out, dict) else tts_out
            word_timestamps = (
                tts_out.get("word_timestamps") if isinstance(tts_out, dict) else None
            )

        if audio_path is None:
            raise RuntimeError(f"Seg {i}: TTS did not produce audio")

        cp_mgr.save(
            key,
            "audio",
            {
                "data": str(audio_path),
                "word_timestamps": str(word_timestamps) if word_timestamps else None,
            },
        )
        return {
            "audio_path": str(audio_path),
            "word_timestamps_json": str(word_timestamps) if word_timestamps else None,
        }

    def image_node(state: SegmentState) -> dict:
        i = state["i"]
        plan = state["plan"]
        script = state.get("script", "")
        key = f"{topic}_seg{i:02d}"
        ck = cp_mgr.get(key) if resume else None

        if (
            ck
            and "images" in ck
            and ck["images"]["data"]
            and all(Path(p).exists() for p in ck["images"]["data"])
        ):
            return {"images": ck["images"]["data"]}

        if dry_run or not generate_images:
            return {"images": []}

        _visual_style = config.get("visual", {}).get("style", "")
        from utils.scene_director import enrich_prompts

        _memory_items_for_image = []
        try:
            _mem_data = state.get("memory_data") or mem.read()
            for _sk in ("project", "story"):
                _items = _mem_data.get("memory_items", {}).get(_sk, [])
                if isinstance(_items, list):
                    _memory_items_for_image.extend(_items)
        except Exception:
            pass

        enrich_result = enrich_prompts(
            build_prompts(script, plan, config),
            script,
            config,
            plan,
            memory_items=_memory_items_for_image,
        )

        enriched_prompts = enrich_result[0] if isinstance(enrich_result, tuple) else enrich_result
        seg_config = dict(config)
        seg_config["image_gen"] = dict(config.get("image_gen", {}))
        if isinstance(enrich_result, tuple):
            seg_config["image_gen"]["negative_prompt"] = enrich_result[1]

        evict_ollama_models(config, reason="ImageGeneration")

        with global_scheduler.task("heavy", f"Seg{i}:ImageGeneration"):
            images = generate_images(
                enriched_prompts,
                out_base / "images",
                seg_config,
                char_presence=plan.get("char_presence"),
                project_id=topic,
            )

        img_paths = [str(p) for p in images] if images else []
        cp_mgr.save(key, "images", {"data": img_paths})
        return {
            "images": img_paths,
            "enriched_prompts": enriched_prompts,
        }

    def render_node(state: SegmentState) -> dict:
        i = state["i"]
        plan = state["plan"]
        script = state.get("script", "")
        audio_path = state.get("audio_path")
        images = state.get("images", [])
        word_timestamps_json = state.get("word_timestamps_json")
        key = f"{topic}_seg{i:02d}"

        if dry_run:
            mp4_path = out_base / f"segment_{i:02d}.mp4"
            with mp4s_lock:
                mp4s[i - 1] = mp4_path
            return {"mp4_path": str(mp4_path)}

        from video.renderer.renderer import render_with_assets

        with global_scheduler.task("light", f"Seg{i}:Hyperframes-render"):
            comp_dir = out_base / "compositions"
            os.makedirs(str(comp_dir), exist_ok=True)
            mp4_path = render_with_assets(
                compositions_dir=comp_dir,
                output_path=out_base / f"segment_{i:02d}.mp4",
                audio_path=Path(audio_path) if audio_path else None,
                image_paths=[Path(p) for p in images],
                script=script,
                subtitle_script=(
                    script
                    if config.get("subtitles", {}).get("language") == "en"
                    else state.get("devanagari_script") or script
                ),
                word_timestamps_json=Path(word_timestamps_json) if word_timestamps_json else None,
                style=config.get("visual", {}).get("style", ""),
                is_final=not (dry_run or preview_mode),
                config=config,
            )

        cp_mgr.save(key, "video", {"data": str(mp4_path)})
        with mp4s_lock:
            mp4s[i - 1] = mp4_path

        summary = plan.get("summary", script[:100])
        mem.save(topic, i, script, summary)

        return {"mp4_path": str(mp4_path)}

    # ── moved verbatim to core/segment/identity.py ──

    class LocalGraphContext:
        def __init__(self):
            self.config = config
            self.director_agent_instance = director_agent_instance
            self.topic = topic
            self.mem = mem
            self.world_state = world_state

        def do_write_script(self, state):
            return write_script_node(state)

        def do_critic(self, state):
            return critic_node(state)

        def do_translate(self, state):
            return translate_node(state)

        def do_tts(self, state):
            return tts_node(state)

        def do_image_gen(self, state):
            return image_node(state)

        def do_important_image_review(self, state):
            images = state.get("images", [])
            plan = state["plan"]
            script = state.get("script", "")

            if not images:
                return {}

            from utils import build_prompts
            from utils.scene_director import enrich_prompts

            _mem_items = []
            try:
                _d = state.get("memory_data") or self.mem.read()
                for _sk in ("project", "story"):
                    _items = _d.get("memory_items", {}).get(_sk, [])
                    if isinstance(_items, list):
                        _mem_items.extend(_items)
            except Exception as e:
                from agents.director_agent import UIState as _UIState
                _UIState.add_degradation(state["i"], "memory_context_injection", str(e))
                pass

            enriched_prompts = state.get("enriched_prompts")
            if not enriched_prompts:
                raw_prompts = build_prompts(script, plan, self.config)
                enrich_result = enrich_prompts(
                    raw_prompts, script, self.config, plan, memory_items=_mem_items
                )
                enriched_prompts = (
                    enrich_result[0] if isinstance(enrich_result, tuple) else enrich_result
                )

            results = []
            for idx, img_path in enumerate(images):
                if idx >= len(enriched_prompts):
                    break

                current_hash = _perceptual_hash(img_path)
                prompt = enriched_prompts[idx]
                cp = plan.get("char_presence", [])
                frame_cp = cp[idx] if (isinstance(cp, list) and idx < len(cp)) else {}

                is_important, _ = _detect_important_trigger(
                    idx,
                    frame_cp,
                    prompt,
                    script,
                )

                # Identity-hash change detection: if the dominant char's stored
                # identity hash differs from the current frame, force a review.
                if (
                    not is_important
                    and frame_cp
                    and getattr(self.mem, "_project", None) is not None
                ):
                    try:
                        dom_char = max(frame_cp, key=lambda name: frame_cp[name])
                        if frame_cp[dom_char] >= 0.3:
                            stored = self.mem._project.get_character_assets(dom_char)
                            stored_hash = (stored or {}).get("identity_hash", "")
                            if stored_hash and current_hash and current_hash != stored_hash:
                                is_important = True
                    except Exception:
                        pass

                if is_important:
                    try:
                        decision_res = self.director_agent_instance.review_important_image(
                            image_path=img_path,
                            prompt=prompt,
                            char_presence=frame_cp,
                            project_id=self.topic,
                        )
                    except Exception as e:
                        err_str = str(e)
                        if "vision" in err_str.lower() or "model" in err_str.lower():
                            log.warning(
                                f"[DIRECTOR] Vision model unavailable for {img_path}: {e} — auto-approving"
                            )
                        else:
                            log.warning(
                                f"[DIRECTOR] Important image review failed for {img_path}: {e}"
                            )
                        decision_res = {
                            "decision": "approve",
                            "reason": "review_failed",
                            "locked": False,
                        }

                    if frame_cp:
                        dom_char = max(frame_cp, key=lambda name: frame_cp[name])
                        if frame_cp[dom_char] >= 0.3:
                            try:
                                if getattr(self.mem, "_project", None) is None:
                                    log.info(
                                        "[DIRECTOR] One-time mode — skipping asset review (no project store)"
                                    )
                                else:
                                    decision = decision_res.get("decision", "approve")
                                    lora_meta = None
                                    if decision == "lora_candidate":
                                        lora_meta = {
                                            "trigger_word": f"{dom_char}_v1",
                                            "minimum_needed": 20,
                                        }
                                    _id_hash = current_hash or None
                                    self.mem._project.record_asset_review(
                                        char_key=dom_char,
                                        asset_path=img_path,
                                        decision=decision,
                                        reason=decision_res.get("reason", ""),
                                        locked=decision_res.get("locked", False),
                                        lora_metadata=lora_meta,
                                        ip_adapter_ref=(decision == "ip_ref"),
                                        negative_example=(decision == "reject"),
                                        identity_hash=_id_hash,
                                    )
                            except Exception as e:
                                log.warning(f"[DIRECTOR] Failed to record asset review: {e}")

                    results.append({"image": img_path, "decision": decision_res})

            return {"important_image_reviews": results}

        def do_render(self, state):
            return render_node(state)

        def do_memory_review(self, state):
            if fast_dry_run:
                return {"memory_items": []}

            script = state.get("script", "")
            plan = state["plan"]
            images = state.get("images", [])

            from utils import build_prompts
            from utils.scene_director import enrich_prompts

            _mem_items = []
            try:
                _d = state.get("memory_data") or self.mem.read()
                for _sk in ("project", "story"):
                    _items = _d.get("memory_items", {}).get(_sk, [])
                    if isinstance(_items, list):
                        _mem_items.extend(_items)
            except Exception as e:
                from agents.director_agent import UIState as _UIState
                _UIState.add_degradation(state["i"], "memory_context_injection", str(e))
                pass

            enriched_prompts = state.get("enriched_prompts")
            if not enriched_prompts:
                raw_prompts = build_prompts(script, plan, self.config)
                enrich_result = enrich_prompts(
                    raw_prompts, script, self.config, plan, memory_items=_mem_items
                )
                enriched_prompts = (
                    enrich_result[0] if isinstance(enrich_result, tuple) else enrich_result
                )

            current_mem = state.get("memory_data") or self.mem.read()
            ws_block = self.world_state.to_prompt_block()

            try:
                # ComfyUI may have just rendered this segment. Release its model
                # before loading the Director again on constrained GPUs.
                if self.config.get("image_gen", {}).get("backend") == "comfyui":
                    from video.image_gen.image_gen import _free_comfyui_memory

                    _free_comfyui_memory(self.config.get("image_gen", {}))
                evict_ollama_models(self.config, reason="Memory review model handoff")
                review_result = self.director_agent_instance.review_segment_memory(
                    segment_script=script,
                    image_plan=plan,
                    generated_prompts=enriched_prompts,
                    current_memory=current_mem,
                    world_state=ws_block,
                    generated_images=images,
                )
            except Exception as e:
                log.warning(f"[DIRECTOR] Segment memory review failed for seg {state['i']}: {e}")
                review_result = {"memory_items": []}

            memory_items = review_result.get("memory_items", [])
            if memory_items:
                try:
                    from memory.permanent_memory import PermanentMemoryLog

                    _perm = PermanentMemoryLog(topic=topic)
                    for item in memory_items:
                        _perm.save_memory_item(item)
                except Exception as e:
                    log.warning(
                        f"[DIRECTOR] Failed to persist memory item via PermanentMemoryLog: {e}"
                    )

            return {"memory_items": memory_items}

    builder = SegmentGraphBuilder(LocalGraphContext())
    graph = builder.build()

    # ── Task-wise phase helpers (ponytail: sequential phases, checkpoint exchange) ──

    def _build_segment_state(seg_idx: int) -> SegmentState:
        """Build initial state dict for a segment index, shared across all phases."""
        plan = outline[seg_idx - 1] if seg_idx - 1 < len(outline) else outline[-1]
        ws_block = world_state.to_prompt_block()
        raw_entries = []
        if ctx_mgr:
            raw_entries = (
                [
                    {
                        "segment": s["segment"],
                        "summary": s["summary"],
                        "script": s.get("script", ""),
                    }
                    for s in (mem._load_all().get(topic, {}).get("segments", []))
                ]
                if hasattr(mem, "_load_all")
                else []
            )
            context = ctx_mgr.build_context_for_prompt(
                memory_entries=raw_entries, world_state_block=ws_block, agent=None
            )
        else:
            from memory import build_context
            context = f"{ws_block}\n{build_context(mem.load(topic))}"

        mem_data = {}
        try:
            mem_data = mem.read()
            all_items = []
            for scope_key in ("project", "story"):
                items = mem_data.get("memory_items", {}).get(scope_key, [])
                if isinstance(items, list):
                    all_items.extend(items)
            if all_items:
                plan_cp = plan.get("char_presence", [])
                chars_in_segment = set()
                for frame_cp in plan_cp if isinstance(plan_cp, list) else [plan_cp]:
                    if isinstance(frame_cp, dict):
                        for cname in frame_cp:
                            chars_in_segment.add(cname.lower().replace(" ", "_"))
                blocks = []
                for item in all_items:
                    owner = (item.get("owner") or "").lower().replace(" ", "_")
                    if owner and owner not in chars_in_segment:
                        continue
                    importance = item.get("importance", "medium")
                    name = item.get("name", "")
                    desc = item.get("description", "")
                    lines = [f"- {name} ({importance}): {desc}"]
                    for rule in item.get("visual_rules", []):
                        lines.append(f"  Visual: {rule}")
                    for rule in item.get("negative_rules", []):
                        lines.append(f"  Avoid: {rule}")
                    blocks.append("\n".join(lines))
                if blocks:
                    context += (
                        "\n\n[Character Memory]\n"
                        + "\n---\n".join(blocks)
                        + "\n[/Character Memory]\n"
                    )
        except Exception as e:
            log.warning(f"[DIRECTOR] Failed to inject memory items into context: {e}")
            from agents.director_agent import UIState as _UIState
            _UIState.add_degradation(seg_idx, "memory_context_injection", str(e))

        state: dict[str, Any] = {
            "i": seg_idx,
            "plan": plan,
            "context": context,
            "rewrites_attempted": 0,
            "aborted": False,
            "skip": False,
            "memory_data": mem_data,
        }
        if source_chunks and 0 <= (seg_idx - 1) < len(source_chunks):
            state["source_chunk"] = source_chunks[seg_idx - 1]
        return cast(SegmentState, state)

    _MAX_REWRITES = config.get("critic", {}).get("max_rewrites", 2)
    _MAX_PHASE_RETRIES = int(config.get("performance", {}).get("max_segment_retries", 2))

    def _retry_segment_phase(seg_idx: int, phase_fn: Callable[[int], None]) -> None:
        """Retry wrapper for a single segment-phase call (matches build_retry_wrapper pattern)."""
        for _attempt in range(_MAX_PHASE_RETRIES + 1):
            try:
                phase_fn(seg_idx)
                return
            except Exception as _e:
                if _attempt >= _MAX_PHASE_RETRIES:
                    log.exception(
                        f"Segment {seg_idx}: phase retry budget exhausted ({_MAX_PHASE_RETRIES} retries). Skipping."
                    )
                    try:
                        from agents.director_agent import UIState as _UIS
                        _UIS.add_degradation(seg_idx, "segment_skip", str(_e)[:100])
                    except Exception:
                        pass
                    return
                log.warning(f"Segment {seg_idx}: attempt {_attempt + 1}/{_MAX_PHASE_RETRIES} failed ({_e}), retrying...")

    def run_scripts_phase(segment_indices: list[int]) -> None:
        """Phase 1: write_script + critic for all segments in batch."""
        for _si in segment_indices:
            if _director_aborted():
                break
            _key = f"{topic}_seg{_si:02d}"
            ck = cp_mgr.get(_key) if resume else None
            if ck and "script" in ck:
                continue

            def _do(_si, _key=_key):
                state = _build_segment_state(_si)
                r = write_script_node(state)
                state.update(r)
                for _rw in range(_MAX_REWRITES + 1):
                    r = critic_node(state)
                    state.update(r)
                    if state.get("critic_approved", True):
                        break
                    r = write_script_node(state)
                    state.update(r)
                cp_mgr.save(_key, "script", {"data": state.get("script", "")})

            _retry_segment_phase(_si, _do)

    def run_translations_phase(segment_indices: list[int]) -> None:
        """Phase 2: translate + world_state update for all segments (SEQUENTIAL, order-dependent)."""
        for _si in segment_indices:
            if _director_aborted():
                break
            _key = f"{topic}_seg{_si:02d}"
            ck = cp_mgr.get(_key) if resume else None
            if ck and "devanagari_script" in ck and ck.get("world_state_applied"):
                continue

            def _do(_si, _key=_key):
                state = _build_segment_state(_si)
                ck = cp_mgr.get(f"{topic}_seg{_si:02d}")
                if ck and "script" in ck:
                    state["script"] = ck["script"]["data"]
                result = translate_node(state)
                state.update(result)
                cp_mgr.save(_key, "devanagari_script", {
                    "data": state.get("devanagari_script"),
                    "script_for_tts": state.get("script_for_tts"),
                })
                cp_mgr.save(_key, "world_state_applied", {"done": True})

            _retry_segment_phase(_si, _do)

    def run_tts_phase(segment_indices: list[int]) -> None:
        """Phase 3: TTS for all segments."""
        for _si in segment_indices:
            if _director_aborted():
                break
            _key = f"{topic}_seg{_si:02d}"
            ck = cp_mgr.get(_key) if resume else None
            if ck and "audio" in ck and Path(ck["audio"]["data"]).exists():
                continue

            def _do(_si):
                state = _build_segment_state(_si)
                ck = cp_mgr.get(f"{topic}_seg{_si:02d}")
                if ck and "devanagari_script" in ck:
                    ds = ck["devanagari_script"]
                    state["devanagari_script"] = ds.get("data")
                    state["script_for_tts"] = ds.get("script_for_tts", "")
                if not state.get("script_for_tts") and ck and "script" in ck:
                    state["script_for_tts"] = ck["script"]["data"]
                result = tts_node(state)
                state.update(result)

            _retry_segment_phase(_si, _do)

    def run_images_phase(segment_indices: list[int]) -> None:
        """Phase 4: images + important_image_review for all segments."""
        for _si in segment_indices:
            if _director_aborted():
                break
            _key = f"{topic}_seg{_si:02d}"
            ck = cp_mgr.get(_key) if resume else None
            if ck and "images" in ck and ck["images"]["data"] and all(Path(p).exists() for p in ck["images"]["data"]) and ck.get("image_review_done"):
                continue

            def _do(_si, _key=_key):
                state = _build_segment_state(_si)
                ck = cp_mgr.get(f"{topic}_seg{_si:02d}")
                if ck and "script" in ck:
                    state["script"] = ck["script"]["data"]
                result = image_node(state)
                state.update(result)
                if state.get("images"):
                    _ir = builder.important_image_review_node(state)
                    state.update(_ir)
                cp_mgr.save(_key, "image_review_done", {"done": True})

            _retry_segment_phase(_si, _do)

    def run_renders_phase(segment_indices: list[int]) -> None:
        """Phase 5: render + memory_review for all segments."""
        for _si in segment_indices:
            if _director_aborted():
                break
            _key = f"{topic}_seg{_si:02d}"
            ck = cp_mgr.get(_key) if resume else None
            if ck and "video" in ck and Path(ck["video"]["data"]).exists() and ck.get("render_done"):
                continue

            def _do(_si, _key=_key):
                state = _build_segment_state(_si)
                ck = cp_mgr.get(f"{topic}_seg{_si:02d}")
                if ck and "script" in ck:
                    state["script"] = ck["script"]["data"]
                if ck and "audio" in ck:
                    state["audio_path"] = ck["audio"]["data"]
                    state["word_timestamps_json"] = ck["audio"].get("word_timestamps")
                if ck and "images" in ck:
                    state["images"] = ck["images"]["data"]
                render_node(state)
                cp_mgr.save(_key, "render_done", {"done": True})
                # ponytail: memory_review also loads Ollama; it's part of same phase as render
                builder.memory_review_node(state)
                with completed_segs_lock:
                    completed_segs_counter_holder[0] += 1
                    try:
                        from agents.director_agent import UIState as _UIState
                        _UIState.set_progress(current=completed_segs_counter_holder[0])
                    except Exception:
                        pass
                aggressive_vram_cleanup(global_scheduler)

            _retry_segment_phase(_si, _do)

    def process_segment(i: int) -> None:
        if _director_aborted():
            return
        log_vram_usage(f"Seg {i} Start")
        touch_ollama_active()
        if not _ollama_alive(config):
            start_ollama_server(config, reason="recovery")
        try:
            initial_state = _build_segment_state(i)
            _plan = initial_state.get("plan", {})
            graph.invoke(initial_state)

            from agents.director_agent import UIState as _UIState

            path_str = None
            duration_s = 0.0
            if i - 1 < len(mp4s) and mp4s[i - 1] is not None:
                path_str = str(mp4s[i - 1])
                try:
                    from core.pre_production import get_video_duration

                    mp4 = mp4s[i - 1]
                    if mp4 is not None:
                        duration_s = round(get_video_duration(mp4), 1)
                except Exception:
                    pass

            _UIState.set_segment_manifest(i, {
                "segment": i,
                "status": "success",
                "title": _plan.get("title", f"Part {i}"),
                "video_path": path_str,
                "duration_seconds": duration_s,
            })

        except Exception as e:
            log.error(f"Segment {i} failed: {e}", exc_info=True)
            from agents.director_agent import UIState as _UIState
            _plan_title = (
                outline[i - 1].get("title", f"Part {i}")
                if i - 1 < len(outline) and isinstance(outline[i - 1], dict)
                else f"Part {i}"
            )

            _UIState.set_segment_manifest(i, {
                "segment": i,
                "status": "error",
                "reason": str(e),
                "title": _plan_title,
            })
            if not resume:
                raise
            log.info(f"  Skipping segment {i}, will resume from next")
        finally:
            with completed_segs_lock:
                completed_segs_counter_holder[0] += 1
                try:
                    from agents.director_agent import UIState as _UIState

                    _UIState.set_progress(current=completed_segs_counter_holder[0])
                except Exception:
                    pass
            aggressive_vram_cleanup(global_scheduler)
            log_vram_usage(f"Seg {i} After Cleanup")
            schedule_ollama_stop(config)

    return process_segment, run_scripts_phase, run_translations_phase, run_tts_phase, run_images_phase, run_renders_phase


from core.segment.retry import build_retry_wrapper  # noqa: F401
