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

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agents.director_agent import UIState
from jobs.job_store import JobStore
from utils import load_config
from utils.concurrency import global_scheduler

# Initialize global job store
job_store = JobStore()
# Recover any stale running jobs on startup
try:
    job_store.mark_stale_running_failed()
except Exception as e:
    logging.getLogger(__name__).warning(f"Failed to mark stale jobs as failed on startup: {e}")

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

_COMFYUI_UI_DEFAULTS = {
    "autoStart": True,
    "server": "http://127.0.0.1:8188",
    "host": "127.0.0.1",
    "port": 8188,
    "root": "external/ComfyUI",
    "python": "external/ComfyUI/.venv/Scripts/python.exe",
    "workflowPath": "config/comfyui/workflows/text_to_image_api.json",
    "checkpoint": "DreamShaper_8_pruned.safetensors",
    "width": 1024,
    "height": 1024,
    "steps": 20,
    "cfg": 7.0,
    "samplerName": "euler",
    "scheduler": "normal",
    "timeoutSeconds": 300,
    "pollSeconds": 1,
    "unloadAfterBatch": True,
    "openBrowser": False,
    "fallbackBackend": "bonsai",
}


def _form_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _comfyui_config_for_ui(config: dict) -> dict:
    image_cfg = config.get("image_gen", {}) or {}
    comfy_cfg = image_cfg.get("comfyui", {}) or {}
    return {
        "autoStart": bool(comfy_cfg.get("auto_start", _COMFYUI_UI_DEFAULTS["autoStart"])),
        "server": comfy_cfg.get("server", _COMFYUI_UI_DEFAULTS["server"]),
        "host": comfy_cfg.get("host", _COMFYUI_UI_DEFAULTS["host"]),
        "port": int(comfy_cfg.get("port", _COMFYUI_UI_DEFAULTS["port"])),
        "root": comfy_cfg.get("root", _COMFYUI_UI_DEFAULTS["root"]),
        "python": comfy_cfg.get("python", _COMFYUI_UI_DEFAULTS["python"]),
        "workflowPath": comfy_cfg.get(
            "workflow_path", _COMFYUI_UI_DEFAULTS["workflowPath"]
        ),
        "checkpoint": comfy_cfg.get("checkpoint", _COMFYUI_UI_DEFAULTS["checkpoint"]),
        "width": int(comfy_cfg.get("width", image_cfg.get("width", _COMFYUI_UI_DEFAULTS["width"]))),
        "height": int(
            comfy_cfg.get("height", image_cfg.get("height", _COMFYUI_UI_DEFAULTS["height"]))
        ),
        "steps": int(comfy_cfg.get("steps", _COMFYUI_UI_DEFAULTS["steps"])),
        "cfg": float(comfy_cfg.get("cfg", _COMFYUI_UI_DEFAULTS["cfg"])),
        "samplerName": comfy_cfg.get("sampler_name", _COMFYUI_UI_DEFAULTS["samplerName"]),
        "scheduler": comfy_cfg.get("scheduler", _COMFYUI_UI_DEFAULTS["scheduler"]),
        "timeoutSeconds": int(
            comfy_cfg.get("timeout_seconds", _COMFYUI_UI_DEFAULTS["timeoutSeconds"])
        ),
        "pollSeconds": float(comfy_cfg.get("poll_seconds", _COMFYUI_UI_DEFAULTS["pollSeconds"])),
        "unloadAfterBatch": bool(
            comfy_cfg.get("unload_after_batch", _COMFYUI_UI_DEFAULTS["unloadAfterBatch"])
        ),
        "openBrowser": bool(comfy_cfg.get("open_browser", _COMFYUI_UI_DEFAULTS["openBrowser"])),
        "fallbackBackend": image_cfg.get(
            "fallback_backend", _COMFYUI_UI_DEFAULTS["fallbackBackend"]
        ),
    }


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

    Creates a queued job in the job store instead of starting the pipeline directly.
    """
    try:
        content = await file.read()
        script_text = content.decode("utf-8")
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Failed to read script file: {e}"},
        )

    # Build job request payload
    job_request = {
        "topic": topic,
        "content_text": script_text,
        # preserve sensible defaults for UI-initiated runs
        "dry_run": False,
        "skip_rvc": True,
        "no_resume": True,
    }

    # Try to include image backend info from config
    try:
        cfg = load_config()
        img = cfg.get("image_gen", {}) or {}
        job_request["image_backend"] = img.get("backend")
        cosy = img.get("comfyui", {}) or {}
        job_request["comfyui_checkpoint"] = cosy.get("checkpoint")
    except Exception:
        pass

    job_id = job_store.create_job(job_request, topic=topic, image_backend=job_request.get("image_backend"), comfyui_checkpoint=job_request.get("comfyui_checkpoint"))
    job_store.append_event(job_id, "created via upload_script", event_type="system")

    return JSONResponse(content={"status": "queued", "job_id": job_id, "message": "Job queued for execution."})


@app.post("/api/jobs")
async def create_job_endpoint(job_request: dict = Body(...)):
    """Create a new job via JSON body.

    Example body from UI:
    {
      "topic": "Job Smoke",
      "duration": 1,
      "dry_run": true,
      "skip_rvc": true
    }
    """
    try:
        topic = job_request.get("topic")
        image_backend = job_request.get("image_backend")
        comfyui_checkpoint = job_request.get("comfyui_checkpoint")
        job_id = job_store.create_job(job_request, topic=topic, image_backend=image_backend, comfyui_checkpoint=comfyui_checkpoint)
        job_store.append_event(job_id, "created via API", event_type="system")
        return JSONResponse(content={"status": "queued", "job_id": job_id})
    except Exception as e:
        log.exception("Failed to create job")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


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


# -------------------- Job API Endpoints --------------------
@app.get("/api/jobs")
async def list_jobs(limit: int = 100, offset: int = 0):
    try:
        rows = job_store.list_jobs(limit=limit, offset=offset)
        return {"jobs": rows}
    except Exception as e:
        log.exception("Failed to list jobs")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int):
    job = job_store.get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "job not found"})
    return job


@app.get("/api/jobs/{job_id}/events")
async def get_job_events(job_id: int, limit: int | None = None):
    try:
        events = job_store.get_events(job_id, limit=limit)
        return {"events": events}
    except Exception as e:
        log.exception("Failed to get job events")
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: int):
    ok = job_store.request_cancel(job_id)
    if ok:
        return {"status": "cancel_requested", "job_id": job_id}
    return JSONResponse(status_code=400, content={"status": "error", "message": "Unable to cancel job"})


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: int):
    new_id = job_store.retry_job(job_id)
    if new_id:
        return {"status": "retry_queued", "job_id": new_id}
    return JSONResponse(status_code=400, content={"status": "error", "message": "Unable to retry job"})




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
        image_cfg = config.get("image_gen", {}) or {}
        return {
            "voiceEngine": config.get("tts", {}).get("engine", "omnivoice"),
            "dynamicSubtitles": config.get("subtitles", {}).get("format", "classic") == "tiktok",
            # P3-19: return the real saved value instead of always False
            "uncappedScaling": bool(config.get("script", {}).get("uncapped_scaling", False)),
            "maxImagesPerSegment": config.get("script", {}).get("default_images_per_segment", 6),
            "imageBackend": image_cfg.get("backend", "bonsai"),
            "comfyUiAdvanced": _comfyui_config_for_ui(config),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})


@app.post("/api/config")
async def save_ui_config(
    voice_engine: str = Form(...),
    dynamic_subtitles: str = Form(...),
    uncapped_scaling: str = Form(...),
    max_images_per_segment: int = Form(...),
    image_backend: str | None = Form(None),
    comfyui_auto_start: str | None = Form(None),
    comfyui_server: str | None = Form(None),
    comfyui_host: str | None = Form(None),
    comfyui_port: int | None = Form(None),
    comfyui_root: str | None = Form(None),
    comfyui_python: str | None = Form(None),
    comfyui_workflow_path: str | None = Form(None),
    comfyui_checkpoint: str | None = Form(None),
    comfyui_width: int | None = Form(None),
    comfyui_height: int | None = Form(None),
    comfyui_steps: int | None = Form(None),
    comfyui_cfg: float | None = Form(None),
    comfyui_sampler_name: str | None = Form(None),
    comfyui_scheduler: str | None = Form(None),
    comfyui_timeout_seconds: int | None = Form(None),
    comfyui_poll_seconds: float | None = Form(None),
    comfyui_unload_after_batch: str | None = Form(None),
    comfyui_open_browser: str | None = Form(None),
    comfyui_fallback_backend: str | None = Form(None),
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

        image_cfg = config.setdefault("image_gen", {})
        if image_backend:
            image_backend = image_backend.strip().lower()
            if image_backend not in {"bonsai", "comfyui"}:
                raise ValueError("image_backend must be 'bonsai' or 'comfyui'")
            image_cfg["backend"] = image_backend

        if comfyui_fallback_backend:
            fallback = comfyui_fallback_backend.strip().lower()
            if fallback not in {"bonsai", "none"}:
                raise ValueError("comfyui_fallback_backend must be 'bonsai' or 'none'")
            image_cfg["fallback_backend"] = fallback

        if any(
            value is not None
            for value in (
                comfyui_auto_start,
                comfyui_server,
                comfyui_host,
                comfyui_port,
                comfyui_root,
                comfyui_python,
                comfyui_workflow_path,
                comfyui_checkpoint,
                comfyui_width,
                comfyui_height,
                comfyui_steps,
                comfyui_cfg,
                comfyui_sampler_name,
                comfyui_scheduler,
                comfyui_timeout_seconds,
                comfyui_poll_seconds,
                comfyui_unload_after_batch,
                comfyui_open_browser,
            )
        ):
            comfy_cfg = image_cfg.setdefault("comfyui", {})
            comfy_cfg["auto_start"] = _form_bool(comfyui_auto_start, True)
            if comfyui_server is not None:
                comfy_cfg["server"] = comfyui_server.strip()
            if comfyui_host is not None:
                comfy_cfg["host"] = comfyui_host.strip()
            if comfyui_port is not None:
                comfy_cfg["port"] = max(1, int(comfyui_port))
            if comfyui_root is not None:
                comfy_cfg["root"] = comfyui_root.strip()
            if comfyui_python is not None:
                comfy_cfg["python"] = comfyui_python.strip()
            if comfyui_workflow_path is not None:
                comfy_cfg["workflow_path"] = comfyui_workflow_path.strip()
            if comfyui_checkpoint is not None:
                comfy_cfg["checkpoint"] = comfyui_checkpoint.strip()
            if comfyui_width is not None:
                comfy_cfg["width"] = max(64, int(comfyui_width))
            if comfyui_height is not None:
                comfy_cfg["height"] = max(64, int(comfyui_height))
            if comfyui_steps is not None:
                comfy_cfg["steps"] = max(1, int(comfyui_steps))
            if comfyui_cfg is not None:
                comfy_cfg["cfg"] = max(0.0, float(comfyui_cfg))
            if comfyui_sampler_name is not None:
                comfy_cfg["sampler_name"] = comfyui_sampler_name.strip()
            if comfyui_scheduler is not None:
                comfy_cfg["scheduler"] = comfyui_scheduler.strip()
            if comfyui_timeout_seconds is not None:
                comfy_cfg["timeout_seconds"] = max(1, int(comfyui_timeout_seconds))
            if comfyui_poll_seconds is not None:
                comfy_cfg["poll_seconds"] = max(0.1, float(comfyui_poll_seconds))
            comfy_cfg["unload_after_batch"] = _form_bool(comfyui_unload_after_batch, True)
            comfy_cfg["open_browser"] = _form_bool(comfyui_open_browser, False)

        # Save to config.yaml
        config_path = Path("config/config.yaml")
        import yaml

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

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
