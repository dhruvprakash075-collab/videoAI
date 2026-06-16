"""
local_ui.py - Local-only FastAPI Backend

Defines the local-only FastAPI backend that serves the HTML/JS frontend.
Provides the "Functional UI" to track all backend processes, verify systems,
and handle the human-in-the-loop Proactive/Manual Pausing.
"""

import json
import logging
import os
import re
import shutil
import threading
import time
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
from utils.ollama_client import get_ollama_client
from utils.path_utils import is_safe_path

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

_LOCAL_ONLY_PREFIXES = ("/studio_outputs", "/studio_projects", "/static")
_LOCALHOST_CLIENTS = ("127.0.0.1", "::1", "localhost")


@app.middleware("http")
async def _restrict_static_to_localhost(request, call_next):
    """Refuse the unauthenticated StaticFiles mounts (/studio_outputs,
    /studio_projects, /static) for any non-loopback client.

    These mounts expose generated artefacts and project assets without auth.
    uvicorn already binds to 127.0.0.1, but this is defence-in-depth in case the
    app is ever placed behind a proxy or bound to a non-local interface.
    """
    if request.url.path.startswith(_LOCAL_ONLY_PREFIXES):
        client_host = request.client.host if request.client else None
        if client_host not in _LOCALHOST_CLIENTS:
            return JSONResponse(
                status_code=403,
                content={"error": "Static assets are only available to local clients."},
            )
    return await call_next(request)


# A/B Director's Chair — in-memory job store
_ab_jobs_lock = threading.Lock()
_ab_jobs: dict = {}  # {job_id: {"status": str, "images_a": [...], "images_b": [...], "error": str}}

# H3 fix: serialize config.yaml read-modify-write saves
_config_save_lock = threading.Lock()

# H5 fix: bounds for in-memory chat sessions
_CHAT_SESSION_TTL_S = 6 * 3600  # evict sessions idle > 6h
_CHAT_MAX_MESSAGES = 60  # cap stored history per session

# Chat sessions v1 — in-memory only
_chat_sessions_lock = threading.Lock()
_chat_sessions: dict = {}  # {session_id: {"messages": [{"role":str, "content":str}], "created_at": float}}

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


def _parse_job_form_bool(value: str | None, field_name: str, default: bool) -> bool:
    if value is None:
        return default

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{field_name} must be a boolean form value, got {value!r}")


_VALID_RUN_MODES = {"project", "one_time"}


def _validate_job_request(req: dict):
    """Validate job request fields, raising ValueError on invalid values."""
    rm = req.get("run_mode")
    if rm is not None and rm not in _VALID_RUN_MODES:
        raise ValueError(
            f"run_mode must be one of {sorted(_VALID_RUN_MODES)}, got {rm!r}"
        )
    for field in ("director_mode", "series"):
        val = req.get(field)
        if val is not None and not isinstance(val, bool):
            raise ValueError(f"{field} must be a boolean, got {type(val).__name__} ({val!r})")


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
    indicators. The value is then reduced to safe characters
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
    safe_value = re.sub(r"[^a-zA-Z0-9_\-]", "_", value)

    # Validate confinement to a root directory (defense-in-depth)
    root_dir = Path().resolve()
    if not is_safe_path(root_dir, safe_value):
        raise ValueError(f"Path component escapes root directory: {safe_value!r}")

    return safe_value


# Mount output directory to serve final videos
if not os.path.exists("studio_outputs"):
    os.makedirs("studio_outputs", exist_ok=True)
app.mount("/studio_outputs", StaticFiles(directory="studio_outputs"), name="studio_outputs")

# Mount projects directory to serve character assets
if not os.path.exists("studio_projects"):
    os.makedirs("studio_projects", exist_ok=True)
app.mount("/studio_projects", StaticFiles(directory="studio_projects"), name="studio_projects")

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
async def upload_script(
    file: UploadFile = File(...),
    topic: str = Form("Narrative"),
    duration: float | None = Form(None),
    dry_run: str | None = Form(None),
    no_resume: str | None = Form(None),
    skip_rvc: str | None = Form(None),
    project: str | None = Form(None),
    series: str | None = Form(None),
    director_mode: str | None = Form(None),
    run_mode: str | None = Form(None),
    eval_models: str | None = Form(None),
    preview: str | None = Form(None),
    skip_preflight: str | None = Form(None),
    preflight_only: str | None = Form(None),
    words_per_segment: int | None = Form(None),
    images_per_segment: int | None = Form(None),
    segment_count: int | None = Form(None),
    yes: str | None = Form(None),
    source: str | None = Form(None),
):
    """
    Endpoint to receive uploaded text files (novels, scripts, stories).

    Accepts all job options as optional form fields. Creates a queued job
    in the job store instead of starting the pipeline directly.
    """
    try:
        content = await file.read()
        script_text = content.decode("utf-8")
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": f"Failed to read script file: {e}"},
        )

    try:
        job_request = {"topic": topic, "content_text": script_text}

        if duration is not None:
            job_request["duration"] = duration
        job_request["dry_run"] = _parse_job_form_bool(dry_run, "dry_run", False)
        job_request["no_resume"] = _parse_job_form_bool(no_resume, "no_resume", True)
        job_request["skip_rvc"] = _parse_job_form_bool(skip_rvc, "skip_rvc", True)
        if project is not None:
            job_request["project"] = project
        if series is not None:
            job_request["series"] = _parse_job_form_bool(series, "series", False)
        if director_mode is not None:
            job_request["director_mode"] = _parse_job_form_bool(
                director_mode, "director_mode", False
            )
        if run_mode is not None:
            job_request["run_mode"] = run_mode
        if eval_models is not None:
            job_request["eval_models"] = _parse_job_form_bool(
                eval_models, "eval_models", False
            )
        if preview is not None:
            job_request["preview"] = _parse_job_form_bool(preview, "preview", False)
        if skip_preflight is not None:
            job_request["skip_preflight"] = _parse_job_form_bool(
                skip_preflight, "skip_preflight", False
            )
        if preflight_only is not None:
            job_request["preflight_only"] = _parse_job_form_bool(
                preflight_only, "preflight_only", False
            )
        if words_per_segment is not None:
            job_request["words_per_segment"] = words_per_segment
        if images_per_segment is not None:
            job_request["images_per_segment"] = images_per_segment
        if segment_count is not None:
            job_request["segment_count"] = segment_count
        if yes is not None:
            job_request["yes"] = _parse_job_form_bool(yes, "yes", False)
        if source is not None:
            job_request["source"] = source

        _validate_job_request(job_request)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})

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

    return JSONResponse(content={"status": "queued", "job_id": job_id, "request": job_request, "message": "Job queued for execution."})


@app.post("/api/jobs")
async def create_job_endpoint(job_request: dict = Body(...)):
    """Create a new job via JSON body.

    Accepts all worker-supported fields:
      topic, duration, dry_run, no_resume, skip_rvc, project, series,
      director_mode, run_mode, eval_models, preview, skip_preflight,
      preflight_only, words_per_segment, images_per_segment, segment_count,
      yes, topics_file, source, file, content_text
    """
    try:
        _validate_job_request(job_request)

        topic = job_request.get("topic")
        image_backend = job_request.get("image_backend")
        comfyui_checkpoint = job_request.get("comfyui_checkpoint")

        # Normalize: store the full payload so the worker gets all flags
        normalized = dict(job_request)
        normalized.setdefault("dry_run", False)
        normalized.setdefault("skip_rvc", True)
        normalized.setdefault("no_resume", True)

        job_id = job_store.create_job(
            normalized,
            topic=topic,
            image_backend=image_backend,
            comfyui_checkpoint=comfyui_checkpoint,
        )
        job_store.append_event(job_id, "created via API", event_type="system")
        return JSONResponse(content={
            "status": "queued",
            "job_id": job_id,
            "request": normalized,
        })
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
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


@app.get("/api/jobs/{job_id}/artifacts")
async def get_job_artifacts(job_id: int):
    try:
        artifacts = job_store.get_artifacts(job_id)
        return {"artifacts": artifacts}
    except Exception as e:
        log.exception("Failed to get job artifacts")
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
    if not is_safe_path(voices_dir, str(wav_path.relative_to(voices_dir))):
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
        layered_cfg = image_cfg.get("layered_v3", {}) or {}
        workflows = layered_cfg.get("workflows", {}) or {}
        return {
            "voiceEngine": config.get("tts", {}).get("engine", "omnivoice"),
            "dynamicSubtitles": config.get("subtitles", {}).get("format", "classic") == "tiktok",
            # P3-19: return the real saved value instead of always False
            "uncappedScaling": bool(config.get("script", {}).get("uncapped_scaling", False)),
            "maxImagesPerSegment": config.get("script", {}).get("default_images_per_segment", 6),
            "imageBackend": image_cfg.get("backend", "bonsai"),
            "compositionMode": image_cfg.get("composition_mode", "one_pass"),
            "layeredV3": {
                "approvalMode": layered_cfg.get("approval_mode", "hybrid"),
                "characterThreshold": layered_cfg.get("character_threshold", 0.3),
                "closeupThreshold": layered_cfg.get("closeup_threshold", 0.8),
                "maxCharacters": layered_cfg.get("max_characters", 2),
                "fallbackMode": layered_cfg.get("fallback_mode", "one_pass"),
                "workflows": {
                    "characterSheet": workflows.get("character_sheet", ""),
                    "background": workflows.get("background", ""),
                    "characterPose": workflows.get("character_pose", ""),
                    "compositeRefine": workflows.get("composite_refine", ""),
                },
            },
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
    composition_mode: str | None = Form(None),
    layered_v3_approval_mode: str | None = Form(None),
    layered_v3_character_threshold: float | None = Form(None),
    layered_v3_closeup_threshold: float | None = Form(None),
    layered_v3_max_characters: int | None = Form(None),
    layered_v3_fallback_mode: str | None = Form(None),
    layered_v3_wf_character_sheet: str | None = Form(None),
    layered_v3_wf_background: str | None = Form(None),
    layered_v3_wf_character_pose: str | None = Form(None),
    layered_v3_wf_composite_refine: str | None = Form(None),
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
        from audio.audio_proxy import normalize_tts_engine

        config = load_config()
        config.setdefault("tts", {})["engine"] = normalize_tts_engine(voice_engine)
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

        if composition_mode:
            cm = composition_mode.strip().lower()
            if cm not in ("one_pass", "layered_v3"):
                raise ValueError("composition_mode must be 'one_pass' or 'layered_v3'")
            image_cfg["composition_mode"] = cm

        if any(v is not None for v in (
            layered_v3_approval_mode,
            layered_v3_character_threshold,
            layered_v3_closeup_threshold,
            layered_v3_max_characters,
            layered_v3_fallback_mode,
            layered_v3_wf_character_sheet,
            layered_v3_wf_background,
            layered_v3_wf_character_pose,
            layered_v3_wf_composite_refine,
        )):
            lv3 = image_cfg.setdefault("layered_v3", {})
            if layered_v3_approval_mode is not None:
                am = layered_v3_approval_mode.strip().lower()
                if am not in ("auto", "hybrid", "manual"):
                    raise ValueError("layered_v3_approval_mode must be 'auto', 'hybrid', or 'manual'")
                lv3["approval_mode"] = am
            if layered_v3_character_threshold is not None:
                val = float(layered_v3_character_threshold)
                if val < 0 or val > 1:
                    raise ValueError("character_threshold must be between 0 and 1")
                lv3["character_threshold"] = val
            if layered_v3_closeup_threshold is not None:
                val = float(layered_v3_closeup_threshold)
                if val < 0 or val > 1:
                    raise ValueError("closeup_threshold must be between 0 and 1")
                lv3["closeup_threshold"] = val
            if layered_v3_max_characters is not None:
                val = int(layered_v3_max_characters)
                if val < 1 or val > 10:
                    raise ValueError("max_characters must be between 1 and 10")
                lv3["max_characters"] = val
            if layered_v3_fallback_mode is not None:
                fm = layered_v3_fallback_mode.strip().lower()
                if fm not in ("one_pass", "error"):
                    raise ValueError("fallback_mode must be 'one_pass' or 'error'")
                lv3["fallback_mode"] = fm
            wf = lv3.setdefault("workflows", {})
            if layered_v3_wf_character_sheet is not None:
                wf["character_sheet"] = layered_v3_wf_character_sheet.strip()
            if layered_v3_wf_background is not None:
                wf["background"] = layered_v3_wf_background.strip()
            if layered_v3_wf_character_pose is not None:
                wf["character_pose"] = layered_v3_wf_character_pose.strip()
            if layered_v3_wf_composite_refine is not None:
                wf["composite_refine"] = layered_v3_wf_composite_refine.strip()

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

        # Save to config.yaml atomically (H3 fix): write a temp file and
        # os.replace() it so a crash mid-write can never corrupt the only
        # config. The lock serializes concurrent saves (read-modify-write).
        config_path = Path("config/config.yaml")
        import yaml

        with _config_save_lock:
            tmp_path = config_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    config, f, default_flow_style=False, allow_unicode=True, sort_keys=False
                )
            os.replace(tmp_path, config_path)

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
        # Resolve both paths to absolute
        variant_abs = variant_dir.resolve()
        seg_abs = seg_images_dir.resolve()
        output_abs = _AB_OUTPUT_ROOT.resolve()

        try:
            # Check if variant_abs is under output_abs
            variant_abs.relative_to(output_abs)
            # Check if seg_abs is under output_abs
            seg_abs.relative_to(output_abs)
        except ValueError as exc:
            raise ValueError(f"Path escapes output root: {exc}") from None
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
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


# -------------------- Chat Endpoints --------------------
@app.post("/api/chat")
async def chat(body: dict = Body(...)):
    message = (body.get("message") or "").strip()
    session_id = body.get("session_id") or ""
    if not message:
        return JSONResponse(status_code=400, content={"error": "message is required"})

    if not session_id:
        session_id = uuid.uuid4().hex[:12]

    now = time.time()
    with _chat_sessions_lock:
        # H5 fix: evict sessions idle past the TTL so memory is bounded.
        expired = [
            sid
            for sid, s in _chat_sessions.items()
            if now - s.get("last_used", s.get("created_at", now)) > _CHAT_SESSION_TTL_S
        ]
        for sid in expired:
            _chat_sessions.pop(sid, None)
        if session_id not in _chat_sessions:
            _chat_sessions[session_id] = {"messages": [], "created_at": now}
        session = _chat_sessions[session_id]
        session["last_used"] = now

    try:
        cfg = load_config()
        director_model = cfg.get("models", {}).get("director", "llama3.1")
        ollama = get_ollama_client(cfg)

        context_parts = []
        status = getattr(UIState, "status", "idle")
        context_parts.append(f"Backend status: {status}")
        latest_job = job_store.list_jobs(limit=1)
        if latest_job:
            j = latest_job[0]
            context_parts.append(f"Latest job: #{j.get('id')} — {j.get('topic', '')} — {j.get('status', '')}")
        context_parts.append(f"Config: director_model={director_model}")

        system_msg = (
            "You are the Video.AI Assistant, embedded in the local dashboard. "
            "You help the user understand system status, jobs, configuration, and next steps. "
            "Be concise and practical. Current context:\n" + "\n".join(context_parts)
        )

        messages = [{"role": "system", "content": system_msg}]
        for msg in session["messages"][-20:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": message})

        reply = ollama.chat(messages=messages, model=director_model, temperature=0.3)

        if not reply:
            return JSONResponse(status_code=500, content={
                "error": "Ollama returned an empty response. Check that the director model is running and reachable.",
                "session_id": session_id,
                "reply": "",
            })

        # H5 fix: append under the lock (race fix) and cap stored history so
        # neither memory nor the response payload grows without bound.
        with _chat_sessions_lock:
            session["messages"].append({"role": "user", "content": message})
            session["messages"].append({"role": "assistant", "content": reply})
            if len(session["messages"]) > _CHAT_MAX_MESSAGES:
                session["messages"] = session["messages"][-_CHAT_MAX_MESSAGES:]
            messages_out = list(session["messages"])

        return JSONResponse(content={
            "session_id": session_id,
            "reply": reply,
            "messages": messages_out,
        })
    except Exception as e:
        log.exception("Chat error")
        return JSONResponse(status_code=500, content={
            "error": f"Chat failed: {e}",
            "session_id": session_id,
            "reply": "Sorry, I encountered an error. Please check that Ollama is running and the director model is available.",
        })


@app.get("/api/chat/sessions/{session_id}")
async def get_chat_session(session_id: str):
    with _chat_sessions_lock:
        session = _chat_sessions.get(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return {"session_id": session_id, "messages": session["messages"]}


@app.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    with _chat_sessions_lock:
        existed = session_id in _chat_sessions
        _chat_sessions.pop(session_id, None)
    if not existed:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return {"status": "deleted", "session_id": session_id}


# -------------------- Preflight Endpoints --------------------
@app.get("/api/preflight")
async def run_preflight_endpoint():
    try:
        from utils.preflight import run_preflight
        cfg = load_config()
        result = run_preflight(cfg, fail_fast=False, quiet=True)
        checks = []
        for c in result.checks:
            checks.append({
                "name": c.name,
                "status": c.status,
                "detail": c.message,
            })
        return {"all_ok": result.all_ok, "checks": checks}
    except Exception as e:
        log.exception("Preflight error")
        return JSONResponse(status_code=500, content={"error": f"Preflight failed: {e}"})


# -------------------- Artifacts Endpoints --------------------
@app.get("/api/artifacts")
async def list_artifacts():
    output_root = Path("studio_outputs").resolve()
    if not output_root.exists():
        return {"artifacts": []}

    artifacts = []
    try:
        for child in sorted(output_root.iterdir()):
            if child.is_dir() and child.name != "ab_test":
                run = {"run_id": child.name, "path": str(child.name)}
                video_files = list(child.glob("*.mp4")) + list(child.glob("*.webm"))
                run["video"] = f"/studio_outputs/{child.name}/{video_files[0].name}" if video_files else None
                thumb_files = list(child.glob("thumb*.png")) + list(child.glob("*.jpg"))
                run["thumbnail"] = f"/studio_outputs/{child.name}/{thumb_files[0].name}" if thumb_files else None
                run["has_manifest"] = (child / "run_manifest.json").exists()
                run["has_chapters"] = (child / "chapters.txt").exists()
                artifacts.append(run)

        # Also scan for root-level _final_video.mp4 files
        for fpath in sorted(output_root.glob("*_final_video.mp4")):
            run_id = fpath.stem  # e.g. "Narrative_final_video"
            artifacts.append({
                "run_id": run_id,
                "path": str(fpath.name),
                "video": f"/studio_outputs/{fpath.name}",
                "thumbnail": None,
                "has_manifest": False,
                "has_chapters": False,
            })
    except Exception:
        log.exception("Error listing artifacts")

    return {"artifacts": artifacts}


@app.get("/api/artifacts/{run_id:path}")
async def get_artifact_detail(run_id: str):
    # H4 fix: the :path converter accepts '/', and the sanitizer raises on
    # separators/'..' — catch it so malformed ids are a 400, not a 500.
    try:
        safe = _sanitize_path_component(run_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid run id"})
    run_dir = Path("studio_outputs") / safe
    if not run_dir.exists() or not run_dir.is_dir():
        return JSONResponse(status_code=404, content={"error": "Run not found"})

    result = {"run_id": safe}
    manifest_path = run_dir / "run_manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, encoding="utf-8") as f:
                result["manifest"] = json.load(f)
        except Exception:
            result["manifest"] = None

    chapters_path = run_dir / "chapters.txt"
    if chapters_path.exists():
        try:
            result["chapters"] = chapters_path.read_text(encoding="utf-8")
        except Exception:
            result["chapters"] = None

    video_files = list(run_dir.glob("*.mp4")) + list(run_dir.glob("*.webm"))
    if video_files:
        result["video"] = f"/studio_outputs/{safe}/{video_files[0].name}"

    thumb_files = list(run_dir.glob("thumb*.png")) + list(run_dir.glob("*.jpg"))
    if thumb_files:
        result["thumbnail"] = f"/studio_outputs/{safe}/{thumb_files[0].name}"

    segments_dir = run_dir / "segments"
    if segments_dir.exists():
        segments = []
        for seg_dir in sorted(segments_dir.iterdir()):
            if seg_dir.is_dir():
                images = [f"/studio_outputs/{safe}/segments/{seg_dir.name}/images/{p.name}" for p in (seg_dir / "images").glob("*.png")] if (seg_dir / "images").exists() else []
                segments.append({"name": seg_dir.name, "images": images})
        result["segments"] = segments

    return result


# -------------------- Memory Endpoints --------------------
@app.get("/api/memory")
async def list_memory():
    memory_items = []
    projects_dir = Path("studio_projects")
    if projects_dir.exists():
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir() or proj_dir.name == "jobs":
                continue
            project_json = proj_dir / "project.json"
            if project_json.exists():
                try:
                    with open(project_json, encoding="utf-8") as f:
                        data = json.load(f)
                    for key, item in data.get("memory_items", {}).items():
                        item["key"] = key
                        item["project"] = proj_dir.name
                        memory_items.append(item)
                    for key, item in data.get("characters", {}).items():
                        memory_items.append({
                            "key": key,
                            "name": item.get("name", key),
                            "type": "character",
                            "project": proj_dir.name,
                            "scope": "project",
                        })
                    for key, item in data.get("world_lore", {}).items():
                        memory_items.append({
                            "key": key,
                            "name": item.get("name", key),
                            "type": "world_lore",
                            "project": proj_dir.name,
                            "scope": "project",
                        })
                    for key, item in data.get("visual_locks", {}).items():
                        memory_items.append({
                            "key": key,
                            "name": item.get("name", key),
                            "type": "visual_lock",
                            "project": proj_dir.name,
                            "scope": "project",
                        })
                    for key, item in data.get("motifs", {}).items():
                        memory_items.append({
                            "key": key,
                            "name": item.get("name", key),
                            "type": "motif",
                            "project": proj_dir.name,
                            "scope": "project",
                        })
                except Exception:
                    pass

    checkpoints_dir = Path("studio_checkpoints")
    if checkpoints_dir.exists():
        for f in checkpoints_dir.rglob("permanent_memory.json"):
            try:
                with open(f, encoding="utf-8") as mf:
                    data = json.load(mf)
                if isinstance(data, dict):
                    for key, item in data.items():
                        if isinstance(item, dict):
                            item["key"] = key
                            item["source"] = str(f.relative_to(checkpoints_dir.parent))
                            memory_items.append(item)
            except Exception:
                pass

    # Also scan nested story.json files under studio_projects/*/stories/
    sp_dir = Path("studio_projects")
    if sp_dir.exists():
        for f in sp_dir.rglob("story.json"):
            try:
                with open(f, encoding="utf-8") as sf:
                    data = json.load(sf)
                if isinstance(data, dict):
                    item = {"key": "story.json", "type": "story", "source": str(f.relative_to(sp_dir.parent))}
                    item.update(data)
                    memory_items.append(item)
            except Exception:
                pass

    return {"memory": memory_items}


# -------------------- Characters Endpoints --------------------
@app.get("/api/characters")
async def list_characters():
    characters = []
    projects_dir = Path("studio_projects")
    if projects_dir.exists():
        for proj_dir in projects_dir.iterdir():
            if not proj_dir.is_dir() or proj_dir.name == "jobs":
                continue
            chars_dir = proj_dir / "characters"
            if chars_dir.exists():
                for char_dir in chars_dir.iterdir():
                    if not char_dir.is_dir():
                        continue
                    char = {
                        "name": char_dir.name,
                        "project": proj_dir.name,
                    }
                    master = char_dir / "master.png"
                    if master.exists():
                        char["master_portrait"] = f"/studio_projects/{proj_dir.name}/characters/{char_dir.name}/master.png"
                    # Also check legacy name
                    if not master.exists():
                        master_legacy = char_dir / "master_portrait.png"
                        if master_legacy.exists():
                            char["master_portrait"] = f"/studio_projects/{proj_dir.name}/characters/{char_dir.name}/master_portrait.png"
                    fullbody = char_dir / "full_body_ref.png"
                    if fullbody.exists():
                        char["full_body_ref"] = f"/studio_projects/{proj_dir.name}/characters/{char_dir.name}/full_body_ref.png"
                    approved = list((char_dir / "approved").glob("*.png")) if (char_dir / "approved").exists() else []
                    char["approved_count"] = len(approved)
                    rejected = list((char_dir / "rejected").glob("*.png")) if (char_dir / "rejected").exists() else []
                    char["rejected_count"] = len(rejected)
                    identity = char_dir / "identity_hash.txt"
                    if identity.exists():
                        char["identity_hash"] = identity.read_text(encoding="utf-8").strip()
                    ipa_dir = char_dir / "ip_adapter"
                    if ipa_dir.exists():
                        char["ip_adapter_refs"] = [str(p.relative_to(projects_dir.parent)) for p in ipa_dir.glob("*")]
                    lora_dir = char_dir / "lora"
                    if lora_dir.exists():
                        lora_files = list(lora_dir.glob("*.safetensors"))
                        char["lora_candidates"] = [f.name for f in lora_files]
                    characters.append(char)

    return {"characters": characters}


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
