"""
local_ui.py - Local-only FastAPI Backend

Defines the local-only FastAPI backend that serves the HTML/JS frontend.
Provides the "Functional UI" to track all backend processes, verify systems,
and handle the human-in-the-loop Proactive/Manual Pausing.
"""

import logging
import os
import re
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agents.director_agent import UIState
from utils import load_config
from utils.concurrency import global_scheduler

UIState.is_ui_mode = True

# Setup logging
log = logging.getLogger(__name__)

app = FastAPI(title="Dynamic Narrative Video Engine - Local UI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],  # P2-2: restrict to local dashboard only
    allow_methods=["*"],
    allow_headers=["*"],
)

# A/B Director's Chair — in-memory job store
_ab_jobs_lock = threading.Lock()
_ab_jobs: dict = {}  # {job_id: {"status": str, "images_a": [...], "images_b": [...], "error": str}}

# Output root for A/B test artefacts — all resolved paths must stay under this
_AB_OUTPUT_ROOT = Path("studio_outputs").resolve()


def _ab_segment_num_validate(segment_num: int) -> int:
    """Validate AB segment number.

    UI sends this as a form field; treat it as untrusted input.
    """
    seg = int(segment_num)
    if seg < 1:
        raise ValueError("segment_num must be >= 1")
    if seg > 9999:
        raise ValueError("segment_num too large")
    return seg


def _sanitize_path_component(value: str) -> str:
    """P2-3: Sanitize a user-supplied path component.

    Rejects values containing path separators, ``..``, or absolute-path
    indicators.  The value is then reduced to safe characters
    (alphanumerics, underscores, hyphens) so it can be used as a directory
    name without risk of traversal.

    Raises ``ValueError`` if the raw value looks malicious.
    """
    # Reject obvious traversal attempts before any transformation
    if ".." in value:
        raise ValueError(f"Path component contains '..': {value!r}")
    if "/" in value or "\\" in value:
        raise ValueError(f"Path component contains a separator: {value!r}")
    if os.path.isabs(value):
        raise ValueError(f"Path component is absolute: {value!r}")
    # Reduce to safe characters
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", value)


# Mount output directory to serve final videos
if not os.path.exists("studio_outputs"):
    os.makedirs("studio_outputs", exist_ok=True)
app.mount("/studio_outputs", StaticFiles(directory="studio_outputs"), name="studio_outputs")

# Mount static files (like ab_picker.html)
if not os.path.exists("static"):
    os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


def run_pipeline_thread(script_content: str, topic: str):
    """
    Runs the full end-to-end pipeline via the unified run_long_pipeline pathway.
    Executed in a background thread so the UI remains fully responsive.

    Previously used a separate read_story → define_pacing_and_length flow.
    Now delegates to the same run_long_pipeline as the CLI so behavior is
    identical and the DecisionRecord is built and honored (Req 5).
    """
    try:
        UIState.topic = topic
        UIState.add_log(f"Starting pipeline for: '{topic}'...")
        UIState.status = "running"

        from core.pipeline_long import run_long_pipeline

        result = run_long_pipeline(
            topic=topic,
            content_text=script_content if script_content else None,
            # P2-4: run_long_pipeline has no run_mode param; mode is derived from
            # project_name (None → one_time, set → project). UI runs are always
            # one-time unless a project_name is supplied.
        )

        if result.get("status") == "success":
            output = result.get("output", "") or ""
            output_path = Path(str(output)).resolve()

            try:
                rel = output_path.relative_to(_AB_OUTPUT_ROOT)
                web_path = "/studio_outputs" + "/" + str(rel).replace("\\", "/")
            except Exception:
                # Fallback: best-effort URL building without assumptions about
                # the output string contents.
                p = str(output).replace("\\", "/")
                if "studio_outputs" in p:
                    web_path = "/studio_outputs" + p.split("studio_outputs", 1)[-1]
                else:
                    web_path = ""

            if web_path:
                UIState.output_video = web_path
                UIState.status = "complete"
                UIState.add_log(f"SUCCESS: Video completed! {web_path}")
            else:
                UIState.status = "error"
                UIState.add_log(
                    "Pipeline succeeded but output video path could not be resolved for UI link."
                )
        else:
            UIState.status = "error"
            UIState.add_log(f"Pipeline ended: {result.get('status')} — {result.get('reason', '')}")

    except Exception as e:
        UIState.status = "error"
        UIState.add_log(f"FATAL ERROR: {e}")
        log.error(f"Dashboard pipeline error: {e}", exc_info=True)
    finally:
        UIState.current_script = ""


@app.get("/", response_class=HTMLResponse)
async def read_index():
    """
    Serves the main frontend dashboard.
    """
    try:
        with open("static/index.html", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Static UI not built yet.</h1>"


@app.post("/api/upload_script")
async def upload_script(file: UploadFile = File(...), topic: str = Form("Narrative")):
    """
    Endpoint to receive uploaded light novels, stories, or scripts.
    """
    if getattr(UIState, "status", "idle") in ["running", "paused"]:
        return JSONResponse(
            status_code=409,
            content={
                "status": "error",
                "message": "A pipeline is already running or paused. Please wait.",
            },
        )

    try:
        content = await file.read()
        script_text = content.decode("utf-8")
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Failed to read script file: {e}"},
        )

    # Start execution in a separate background thread
    UIState.logs = ["Engine initialized."]
    UIState.current_script = script_text
    UIState.active_question = None
    UIState.user_reply = None
    UIState.output_video = ""
    UIState.status = "running"
    UIState.pause_event = threading.Event()

    t = threading.Thread(target=run_pipeline_thread, args=(script_text, topic))
    t.daemon = True
    t.start()
    UIState.run_thread = t

    return {
        "status": "success",
        "filename": file.filename,
        "message": "Pipeline started in background.",
    }


@app.post("/api/upload_voice")
async def upload_voice(file: UploadFile = File(...), character_name: str = Form("narrator")):
    """
    Endpoint to upload a custom character reference voice sample.
    Saves to character_voices/{character_name}.wav
    Automatically trims to 10 seconds and normalizes to Mono 22050Hz for XTTS.
    """
    try:
        import subprocess

        # Create directory if it doesn't exist
        os.makedirs("character_voices", exist_ok=True)

        # Clean character name to be safe
        safe_name = "".join([c if c.isalnum() else "_" for c in character_name]).strip("_")
        out_path = Path(f"character_voices/{safe_name}.wav")
        temp_path = out_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temp_out = out_path.with_suffix(f".{uuid.uuid4().hex}.out.wav")

        # Write file content and optimize
        try:
            content = await file.read()
            with open(temp_path, "wb") as f:
                f.write(content)

            # Optimize for XTTS: Trim to 10s, convert to Mono 22050Hz
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(temp_path),
                    "-t",
                    "10",
                    "-ac",
                    "1",
                    "-ar",
                    "22050",
                    str(temp_out),
                ],
                capture_output=True,
                check=True,
            )

            # Atomic replace to prevent concurrent clobbering
            shutil.move(str(temp_out), str(out_path))

            UIState.add_log(
                f"Backend: Custom character voice sample optimized (10s) and uploaded to '{out_path}'"
            )
            return {
                "status": "success",
                "message": f"Successfully optimized {file.filename} as reference voice.",
            }
        finally:
            # Secure cleanup of temporary files under all branches
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
            try:
                if temp_out.exists():
                    temp_out.unlink()
            except Exception:
                pass
    except Exception as e:
        log.error(f"Voice upload failed: {e}", exc_info=True)
        return JSONResponse(
            status_code=500, content={"status": "error", "message": f"Failed to upload voice: {e}"}
        )


@app.get("/api/status")
async def get_system_status():
    """
    Endpoint for the frontend to track backend processes, logs, and verify
    that everything is working correctly.
    """
    logs_list = []
    logs_obj = getattr(UIState, "logs", [])
    # Make reads thread-safe: UIState.add_log mutates under UIState._log_lock
    # but this endpoint previously read without locking.
    try:
        lock = getattr(UIState, "_log_lock", None)
        if lock is not None:
            with lock:
                logs_list = list(getattr(UIState, "logs", []))[-100:]
        else:
            logs_list = list(logs_obj)[-100:]
    except Exception:
        logs_list = []

    return {
        "status": getattr(UIState, "status", "idle"),
        "active_question": getattr(UIState, "active_question", None),
        "logs": logs_list,
        "output_video": getattr(UIState, "output_video", ""),
    }


@app.get("/api/voices")
async def get_voices():
    """
    List all available character voices.
    """
    voices_dir = Path("character_voices")
    if not voices_dir.exists():
        return {"voices": []}

    voices = []
    for f in voices_dir.glob("*.wav"):
        voices.append({"name": f.stem, "filename": f.name, "size": f.stat().st_size})
    return {"voices": voices}


@app.get("/api/audio/preview/{character}")
async def preview_voice(character: str):
    """
    P2-17: Stream the WAV reference file for a character so the dashboard
    Play button can preview the uploaded voice sample.

    The character name is sanitized to prevent path traversal before the
    file is resolved under character_voices/.
    """
    # Sanitize: allow only alphanumerics and underscores (same policy as upload)
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", character).strip("_")
    if not safe_name:
        return JSONResponse(status_code=400, content={"error": "Invalid character name"})

    voices_dir = Path("character_voices").resolve()
    wav_path = (voices_dir / f"{safe_name}.wav").resolve()

    # Ensure the resolved path stays inside character_voices/
    if not str(wav_path).startswith(str(voices_dir)):
        return JSONResponse(status_code=400, content={"error": "Invalid character name"})

    if not wav_path.exists():
        return JSONResponse(
            status_code=404, content={"error": f"Voice file not found: {safe_name}.wav"}
        )

    return FileResponse(str(wav_path), media_type="audio/wav", filename=f"{safe_name}.wav")


@app.get("/api/config")
async def get_ui_config():
    try:
        config = load_config()
        return {
            "voiceEngine": config.get("tts", {}).get("engine", "omnivoice"),
            "dynamicSubtitles": config.get("subtitles", {}).get("format", "classic") == "tiktok",
            # P3-19: return the real saved value instead of always False
            "uncappedScaling": bool(config.get("script", {}).get("uncapped_scaling", False)),
            "maxImagesPerSegment": config.get("script", {}).get("default_images_per_segment", 6),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.post("/api/config")
async def save_ui_config(
    voice_engine: str = Form(...),
    dynamic_subtitles: str = Form(...),
    uncapped_scaling: str = Form(...),
    max_images_per_segment: int = Form(...),
):
    try:
        # Load, modify, and save config
        config = load_config()
        config.setdefault("tts", {})["engine"] = voice_engine
        config.setdefault("subtitles", {})["format"] = (
            "tiktok" if dynamic_subtitles.lower() == "true" else "classic"
        )
        # P3-19: persist uncapped_scaling as a bool under script section
        uncapped_bool = uncapped_scaling.lower() == "true"
        config.setdefault("script", {})["uncapped_scaling"] = uncapped_bool
        if not uncapped_bool:
            config.setdefault("script", {})["default_images_per_segment"] = max_images_per_segment

        # Save to config.yaml
        config_path = Path("config/config.yaml")
        import yaml

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)

        UIState.add_log(f"Backend: UI configuration saved successfully (engine={voice_engine})")
        return {"status": "success", "message": "Configuration saved successfully."}
    except Exception as e:
        log.error(f"Failed to save UI config: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Failed to save configuration: {e}"},
        )


@app.post("/api/consultation_reply")
async def consultation_reply(reply: str = Form(...)):
    """
    Receives the user's manual creative suggestion or answer to the Director's
    proactive question, unpausing the generation process.
    """
    if UIState.status == "paused":
        UIState.user_reply = reply
        UIState.add_log(f"User response received ({len(reply or '')} chars). Resuming execution...")
        UIState.pause_event.set()
        return {"status": "resumed"}
    return {"status": "ignored", "message": "Engine is not currently paused."}


@app.post("/api/manual_pause")
async def manual_pause():
    """
    Allows user to trigger a manual creative pause mid-run.
    """
    if UIState.status == "running":
        UIState.add_log("User triggered MANUAL PAUSE. Pausing engine...")
        UIState.status = "paused"
        UIState.active_question = (
            "Manual Pause: Feel free to guide the creative direction or select resume."
        )
        UIState.pause_event.clear()
        return {"status": "paused"}
    return {"status": "ignored", "message": "Engine is not currently running."}


@app.post("/api/ab/generate")
async def ab_generate(
    background_tasks: BackgroundTasks,
    segment_num: int = Form(1),
    prompt_a: str = Form(...),
    prompt_b: str = Form(...),
    topic: str = Form("default_topic"),
):
    """
    Start an A/B generation job. Returns immediately with a job_id.
    Poll GET /api/ab/status/{job_id} to check progress.
    """
    try:
        segment_num = _ab_segment_num_validate(segment_num)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Invalid segment_num: {e}"})

    with _ab_jobs_lock:
        if len(_ab_jobs) > 20:
            # Aggressive cleanup for endurance runs
            oldest = list(_ab_jobs.keys())[:5]
            for k in oldest:
                _ab_jobs.pop(k, None)

        job_id = str(uuid.uuid4())[:8]
        _ab_jobs[job_id] = {
            "status": "running",
            "images_a": [],
            "images_b": [],
            "segment_num": segment_num,
            "topic": topic,
            "error": None,
        }

    def _run_ab(job_id: str, pa: str, pb: str):
        with global_scheduler.task("heavy", f"UI-AB:{job_id}"):
            with _ab_jobs_lock:
                job = _ab_jobs.get(job_id)
            if not job:
                return
            try:
                from utils import load_config
                from video.image_gen.image_gen import generate_images

                cfg = load_config()

                # VRAM-protection: mirror the pipeline's rule that only one
                # model should occupy VRAM while SD runs.
                try:
                    from core.segment_runner import evict_ollama_models

                    evict_ollama_models(cfg, reason="UI-AB")
                except Exception:
                    # Best-effort fallback: clear CUDA cache if torch is present.
                    try:
                        import torch

                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except Exception:
                        pass

                out_a = Path("studio_outputs") / "ab_test" / job_id / "variant_a"
                out_b = Path("studio_outputs") / "ab_test" / job_id / "variant_b"
                # Generate variant A
                with _ab_jobs_lock:
                    job["status"] = "generating_a"
                imgs_a = generate_images(pa, out_a, cfg)
                with _ab_jobs_lock:
                    job["images_a"] = [
                        "/studio_outputs/ab_test/" + job_id + "/variant_a/" + p.name for p in imgs_a
                    ]
                # Generate variant B
                with _ab_jobs_lock:
                    job["status"] = "generating_b"
                imgs_b = generate_images(pb, out_b, cfg)
                with _ab_jobs_lock:
                    job["images_b"] = [
                        "/studio_outputs/ab_test/" + job_id + "/variant_b/" + p.name for p in imgs_b
                    ]
                    job["status"] = "ready"
                log.info(f"[A/B] Job {job_id} complete: {len(imgs_a)} + {len(imgs_b)} images")
            except Exception as e:
                with _ab_jobs_lock:
                    job["status"] = "error"
                    job["error"] = str(e)
                log.exception(f"[A/B] Job {job_id} failed: {e}")

    background_tasks.add_task(_run_ab, job_id, prompt_a, prompt_b)
    return JSONResponse(content={"job_id": job_id, "status": "started"})


@app.get("/api/ab/status/{job_id}")
async def ab_status(job_id: str):
    """
    Poll the status of an A/B generation job.
    Returns job_id, status, and image lists once available.
    """
    with _ab_jobs_lock:
        job = _ab_jobs.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": f"Job {job_id} not found"})
    return {
        "job_id": job_id,
        "status": job["status"],
        "images_a": job.get("images_a", []),
        "images_b": job.get("images_b", []),
        "error": job.get("error"),
    }


@app.post("/api/ab/pick")
async def ab_pick(job_id: str = Form(...), choice: str = Form(...), segment_num: int = Form(1)):
    """
    Commit a chosen A/B variant. Copies chosen images to the segment's real output folder.
    choice: 'a' or 'b'
    """
    try:
        segment_num = _ab_segment_num_validate(segment_num)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": f"Invalid segment_num: {exc}"})

    with _ab_jobs_lock:
        job = _ab_jobs.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": f"Job {job_id} not found"})
    if job["status"] != "ready":
        return JSONResponse(status_code=400, content={"error": "Job not ready yet"})
    if choice not in ("a", "b"):
        return JSONResponse(status_code=400, content={"error": "choice must be 'a' or 'b'"})

    # P2-3: validate topic and job_id before using them in path construction
    try:
        safe_topic = _sanitize_path_component(
            job.get("topic", "default_topic").lower().replace(" ", "_")
        )
        safe_job_id = _sanitize_path_component(job_id)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": f"Invalid path component: {exc}"})

    variant_dir = Path("studio_outputs") / "ab_test" / safe_job_id / f"variant_{choice}"
    seg_images_dir = (
        Path("studio_outputs") / safe_topic / "segments" / f"seg_{segment_num:02d}" / "images"
    )

    # Assert both resolved paths stay under the output root (defence-in-depth)
    try:
        variant_dir_resolved = variant_dir.resolve()
        seg_images_dir_resolved = seg_images_dir.resolve()
        if not str(variant_dir_resolved).startswith(str(_AB_OUTPUT_ROOT)):
            raise ValueError(f"variant_dir escapes output root: {variant_dir_resolved}")
        if not str(seg_images_dir_resolved).startswith(str(_AB_OUTPUT_ROOT)):
            raise ValueError(f"seg_images_dir escapes output root: {seg_images_dir_resolved}")
    except ValueError as exc:
        log.warning(f"[A/B] Path traversal attempt blocked: {exc}")
        return JSONResponse(status_code=400, content={"error": "Invalid path"})

    seg_images_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for img_file in sorted(variant_dir.glob("*.png")):
        dest = seg_images_dir / img_file.name
        shutil.copy2(img_file, dest)
        copied.append(str(dest))

    with _ab_jobs_lock:
        if job_id in _ab_jobs:
            _ab_jobs[job_id]["picked"] = choice
    log.info(
        f"[A/B] Job {job_id}: user picked variant {choice.upper()}, {len(copied)} images copied"
    )
    return {
        "status": "committed",
        "choice": choice,
        "copied_to": str(seg_images_dir),
        "images": copied,
    }


if __name__ == "__main__":
    import webbrowser

    import uvicorn

    # Automatically open browser in a separate thread so it doesn't block startup
    def open_browser():
        import time

        time.sleep(3)  # Wait slightly longer for Vite to be ready
        try:
            log.info("Opening browser to new UI at http://localhost:5173 ...")
            webbrowser.open("http://localhost:5173")
        except Exception as e:
            log.debug(f"Failed to open browser automatically: {e}")

    threading.Thread(target=open_browser, daemon=True).start()

    # Enforce strictly local access
    uvicorn.run(app, host="127.0.0.1", port=8000)
