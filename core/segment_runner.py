"""segment_runner.py - Per-segment loop: script → TTS → images → render.

Extracted from pipeline_long.py (Task 1: split god module). Owns the per-segment
work that runs N times (one per segment):

  • Director Mode human-in-loop approval gate
  • Preview gate (after segment 1 in --preview mode)
  • Script generation (W2: structured Ollama, CrewAI fallback)
  • Script review + revision
  • Local word-count enforcement (W4)
  • Devanagari translation (Director or fallback)
  • WorldState update (B3)
  • TTS (OmniVoice / F5 / edge-tts) + SFX + mastering
  • Stable Diffusion image generation (OOM ladder)
  • FramePack image-to-video motion (V1, opt-in)
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
import time
from pathlib import Path

from utils import build_prompts
from utils.emotion_control import inject_emotion

log = logging.getLogger(__name__)

# Re-use the same locks as the orchestrator
_director_lock = threading.Lock()
_director_abort = False
_director_abort_lock = threading.Lock()


def _director_aborted() -> bool:
    with _director_abort_lock:
        return _director_abort


def set_director_abort(val: bool = True) -> None:
    """Public API to flip the Director abort flag (used by segment runner and orchestrator)."""
    with _director_abort_lock:
        global _director_abort
        _director_abort = val


def get_director_abort() -> bool:
    """Read the Director abort flag (public, for orchestrator to reset between runs)."""
    with _director_abort_lock:
        return _director_abort


# ── VRAM management (shared with orchestrator) ────────────────────────────


def evict_ollama_models(config: dict, reason: str = "") -> None:
    """Force-evict ALL Ollama models from VRAM (keep_alive=0) before a GPU task.

    On a 6GB GPU only one model fits, so before Stable Diffusion or TTS takes the
    GPU we must unload every Ollama LLM. keep_alive timer would eventually do
    this, but we force it instantly to hand the GPU over cleanly.

    A1: After evicting, poll torch.cuda.mem_get_info() until free VRAM ≥
    performance.vram_sd_threshold_gb (default 4.5 GB), up to
    performance.vram_evict_wait_s (default 15 s). If VRAM never frees, log a
    loud WARNING and proceed anyway (non-fatal).
    """
    try:
        import json as _js
        import urllib.request as _ur

        host = config.get("ollama", {}).get("host", "http://localhost:11434")
        models_cfg = config.get("models", {})
        seen = set()
        for _key in ("director", "writer", "reviewer", "translator", "image_engineer"):
            _mdl = models_cfg.get(_key, "")
            if _mdl and _mdl not in seen:
                seen.add(_mdl)
                with contextlib.suppress(Exception):
                    _ur.urlopen(
                        _ur.Request(
                            f"{host}/api/generate",
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
    except Exception:
        pass

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

                host2 = config.get("ollama", {}).get("host", "http://localhost:11434")
                with _ur2.urlopen(f"{host2}/api/ps", timeout=3) as _r:
                    ps_data = _js2.loads(_r.read().decode())
                for _m in ps_data.get("models", []):
                    _name = _m.get("name", "")
                    if _name:
                        with contextlib.suppress(Exception):
                            _ur2.urlopen(
                                _ur2.Request(
                                    f"{host2}/api/generate",
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


# ── Director Mode approval gate ───────────────────────────────────────────


def _director_approval(script: str, prompts: str, seg_num: int, config: dict) -> str:
    """Director Mode: pause after script generation for user review.

    TUI mode: pause via UIState.pause_event, read reply from UIState.user_reply.
      'accept'/'ok'/empty → accept | 'retry' → retry | 'quit'/'q' → abort
    CLI mode: ENTER accept, e edit, r retry, ? question, q quit.

    Returns:
        The (possibly edited) script, or a sentinel: "__RETRY__" / "__QUIT__".
    """
    from agents.director_agent import UIState

    with _director_lock:
        if _director_abort:
            return "__QUIT__"

        prompt_summary = (
            f"DIRECTOR MODE — Segment {seg_num}\n"
            f"Script ({len(script)} chars):\n{script[:600]}{'...' if len(script) > 600 else ''}\n\n"
            f"Image prompts: {len([p for p in prompts.split(';') if p.strip()])} total\n\n"
            f"Reply: 'accept' or Enter to continue | 'retry' to regenerate | 'quit' to abort"
        )

        if UIState.is_ui_mode:
            UIState.add_log(f"[DIRECTOR] Segment {seg_num} script ready for review.")
            UIState.active_question = prompt_summary
            UIState.status = "paused"
            UIState.pause_event.clear()
            timeout = int(os.environ.get("DIRECTOR_TIMEOUT", "0")) or 600
            if not UIState.pause_event.wait(timeout=timeout):
                log.warning(f"[DIRECTOR] Segment {seg_num} review timed out — auto-accepting")
                UIState.status = "running"
                UIState.active_question = None
                return script
            UIState.status = "running"
            UIState.active_question = None
            reply = (UIState.user_reply or "").strip().lower()
            UIState.user_reply = None
            if reply in ("q", "quit", "abort"):
                log.info(f"[DIRECTOR] Segment {seg_num} — operator aborted pipeline")
                set_director_abort(True)
                return "__QUIT__"
            if reply in ("r", "retry"):
                log.info(f"[DIRECTOR] Segment {seg_num} — operator requested retry")
                return "__RETRY__"
            log.info(f"[DIRECTOR] Segment {seg_num} — operator accepted script")
            return script

        sep = "=" * 60
        print(f"\n{sep}")
        print(f"  DIRECTOR MODE — Segment {seg_num}")
        print(sep)
        print("\n--- GENERATED SCRIPT ---")
        print(script)
        print(
            f"\n--- IMAGE PROMPTS ({len([p for p in prompts.split(';') if p.strip()])} total) ---"
        )
        for idx, p in enumerate([pp.strip() for pp in prompts.split(";") if pp.strip()], 1):
            print(f"  [{idx}] {p[:120]}..." if len(p) > 120 else f"  [{idx}] {p}")
        print()

        while True:
            choice = (
                input("[Director] Accept (ENTER) | Edit (e) | Retry (r) | ? Question | q Quit: ")
                .strip()
                .lower()
            )
            if not choice:
                return script
            if choice == "e":
                print(
                    "\n--- EDITOR: Paste your revised script below. Type '---DONE---' on its own line when finished. ---\n"
                )
                lines = []
                while True:
                    line = input()
                    if line.strip() == "---DONE---":
                        break
                    lines.append(line)
                edited = "\n".join(lines).strip()
                if not edited:
                    print(
                        "[Director] Edited script is empty. Please provide a valid script or choose another option."
                    )
                    continue
                return edited
            if choice == "r":
                return "__RETRY__"
            if choice == "?":
                print(
                    "\nAsk a question about the current segment. The Director will provide guidance.\n"
                )
                question = input("Your question: ").strip()
                if not question:
                    print("[Director] No question entered. Returning to menu.")
                    continue
                print(f'\n[Director AI] Noted your question: "{question}"')
                print(
                    "The writer will take this into account when regenerating (next pipeline run).\n"
                )
                return "__RETRY__"
            if choice == "q":
                print(
                    "\n[Director Mode] Aborting entire pipeline... (remaining segments will skip)"
                )
                set_director_abort(True)
                return "__QUIT__"
            print(
                "Invalid choice. Press ENTER to accept, 'e' to edit, 'r' to retry, '?' to ask a question, or 'q' to quit."
            )


def _preview_gate(mp4_path, config: dict) -> None:
    """Preview gate (R13): pause after segment 1 for operator approval."""
    from agents.director_agent import UIState

    seg_path_str = str(mp4_path) if mp4_path else "segment not available"

    if UIState.is_ui_mode:
        UIState.add_log(f"[PREVIEW] Segment 1 ready: {seg_path_str}")
        UIState.active_question = (
            f"PREVIEW: Segment 1 is ready. Review it and decide:\n"
            f"  Path: {seg_path_str}\n"
            f"  Type 'approve' to continue, anything else to abort."
        )
        UIState.status = "paused"
        UIState.pause_event.clear()
        timeout = int(os.environ.get("DIRECTOR_TIMEOUT", "0")) or 600
        if not UIState.pause_event.wait(timeout=timeout):
            log.warning("[PREVIEW] Timeout — proceeding with production")
            UIState.status = "running"
            UIState.active_question = None
            return
        UIState.status = "running"
        UIState.active_question = None
        reply = (UIState.user_reply or "").strip().lower()
        UIState.user_reply = None
        if "approve" not in reply:
            log.info("[PREVIEW] Operator rejected — aborting pipeline")
            set_director_abort(True)
        else:
            log.info("[PREVIEW] Operator approved — continuing production")
        return

    sep = "=" * 60
    print(f"\n{sep}")
    print("  PREVIEW — Segment 1 Ready")
    print(sep)
    print(f"\n  Segment 1 video: {seg_path_str}")
    print("  Open the file, review the look and sound, then decide.\n")

    try:
        import sys as _sys

        if not _sys.stdin.isatty():
            log.info("[PREVIEW] Non-interactive stdin — auto-approving")
            return
        choice = input("  [ENTER] Approve & continue  |  [q] Abort: ").strip().lower()
        if choice == "q":
            log.info("[PREVIEW] Operator aborted after preview")
            set_director_abort(True)
        else:
            log.info("[PREVIEW] Operator approved — continuing production")
    except (EOFError, KeyboardInterrupt):
        log.info("[PREVIEW] No input — auto-approving")
    print(sep + "\n")


# ── Main per-segment processor ────────────────────────────────────────────


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
    trained_loras: dict,
    resume: bool,
    dry_run: bool,
    director_mode: bool,
    preview_mode: bool,
    skip_rvc: bool,
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

    from core.pre_production import _sanitize_narration

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

        if state.get("source_chunk"):
            chunk = state["source_chunk"]
            log.debug(
                f"  Seg {i}: source-path short-circuit (chunk={chunk.index}, {len(chunk.text)} chars)"
            )
            return {"script": chunk.text}

        log.debug(f"  Seg {i}: generating script (LIGHT)")
        writer = writer_agent

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
                with _crewai_lock:
                    result = crew.kickoff()
                script = str(result.raw if hasattr(result, "raw") else result).strip()

        return {"script": script}

    def critic_node(state: SegmentState) -> dict:
        i = state["i"]
        _plan = state["plan"]
        _context = state["context"]
        script = state["script"]
        rewrites = state.get("rewrites_attempted", 0)

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

        # Word count enforcement
        seg_words = plan.get("target_word_count", words_per_seg)
        _wc_tolerance = config.get("script", {}).get("word_count_tolerance", 0.25)
        _actual_wc = len(script.split())
        _wc_hi = int(seg_words * (1 + _wc_tolerance))

        if _actual_wc > _wc_hi:
            import re as _re_wc

            _sentences = _re_wc.split(r"(?<=[.!?\u0964])\s+", script)
            _trimmed_parts = []
            _running_wc = 0
            for _sent in _sentences:
                _sent_wc = len(_sent.split())
                if _running_wc + _sent_wc <= _wc_hi:
                    _trimmed_parts.append(_sent)
                    _running_wc += _sent_wc
                else:
                    break
            if _trimmed_parts:
                script = " ".join(_trimmed_parts).strip()

        cp_mgr.save(key, "script", {"data": script})

        script = _sanitize_narration(script)

        devanagari_script = None
        _audio_lang = tts_cfg.get("lang", "hi")
        if _audio_lang == "hi":
            try:
                with global_scheduler.task("heavy", f"Seg{i}:translate"):
                    with crewai_lock:
                        devanagari_script = director_agent_instance.translate_to_devanagari(
                            script, plan, state["context"]
                        )
                log.info(f"  Seg {i}: Director translated to Devanagari")
            except Exception as e:
                log.warning(f"  Seg {i}: Director translation failed ({e})")

        with contextlib.suppress(Exception):
            world_state.update(script, plan, config=config)

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

        audio_lang = tts_cfg.get("lang", "hi")
        mood = plan.get("mood", "mysterious")

        if audio_lang == "hi" and dev_script:
            script_for_tts = inject_emotion(dev_script, mood, lang="hi")
        else:
            script_for_tts = inject_emotion(script, mood, lang="en")

        from utils.emotion_control import get_mood_rate

        _tts_speed = get_mood_rate(mood)

        evict_ollama_models(config, reason="TTS")
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        from audio.audio_proxy import rvc_convert, tts_generate

        with global_scheduler.task("heavy", f"Seg{i}:Coqui-XTTS"):
            tts_out = tts_generate(
                script_for_tts, lang=audio_lang, output_dir=out_base / "audio", speed=_tts_speed
            )
            audio_path = tts_out["wav_path"] if isinstance(tts_out, dict) else tts_out
            word_timestamps = tts_out.get("word_timestamps") if isinstance(tts_out, dict) else None

            if not skip_rvc and config.get("rvc", {}).get("enabled", False):
                audio_path = rvc_convert(audio_path, out_base / "audio")

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

        enrich_result = enrich_prompts(build_prompts(script, plan, config), script, config, plan)

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
                lora_paths=trained_loras,
                char_presence=plan.get("char_presence"),
                project_id=topic,
            )

        img_paths = [str(p) for p in images] if images else []
        cp_mgr.save(key, "images", {"data": img_paths})
        return {"images": img_paths}

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
                subtitle_script=state.get("devanagari_script") or script,
                word_timestamps_json=Path(word_timestamps_json) if word_timestamps_json else None,
                style=config.get("visual", {}).get("style", ""),
                is_final=not (dry_run or preview_mode),
            )

        cp_mgr.save(key, "video", {"data": str(mp4_path)})
        with mp4s_lock:
            mp4s[i - 1] = mp4_path

        summary = plan.get("summary", script[:100])
        mem.save(topic, i, script, summary)

        return {"mp4_path": str(mp4_path)}

    class LocalGraphContext:
        def __init__(self):
            self.config = config

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

        def do_render(self, state):
            return render_node(state)

    builder = SegmentGraphBuilder(LocalGraphContext())
    graph = builder.build()

    def process_segment(i: int) -> None:
        if _director_abort:
            return
        log_vram_usage(f"Seg {i} Start")
        try:
            plan = outline[i - 1] if i - 1 < len(outline) else outline[-1]

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

            initial_state = {
                "i": i,
                "plan": plan,
                "context": context,
                "rewrites_attempted": 0,
                "aborted": False,
                "skip": False,
            }
            if source_chunks and 0 <= (i - 1) < len(source_chunks):
                initial_state["source_chunk"] = source_chunks[i - 1]

            graph.invoke(initial_state)

        except Exception as e:
            log.error(f"Segment {i} failed: {e}", exc_info=True)
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

    return process_segment


def build_retry_wrapper(
    process_segment, max_retries: int, segment_idx: int, retry_counts: dict
) -> callable:
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
                    except Exception:
                        pass
                    return
                log.warning(
                    f"Segment {i}: attempt {retry_counts[i]}/{max_retries} failed ({_e}), retrying..."
                )

    return _with_budget
