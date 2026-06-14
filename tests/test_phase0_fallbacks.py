"""test_phase0_fallbacks.py - Unit/regression tests for Phase 0 loud fallbacks and timeouts."""

import contextlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.main as cm
from agents.director_agent import DirectorAgent
from agents.ui_state import UIState


def test_consult_user_timeout_records_degradation():
    UIState.reset_run("test")
    UIState.is_ui_mode = True
    UIState.user_reply = None

    agent = DirectorAgent({"models": {"director": "test"}})

    # Force timeout by patching pause_event.wait to return False immediately
    with patch.object(UIState.pause_event, "wait", return_value=False):
        res = agent.consult_user("Is this a test?", options=["Option A", "Option B"])

    assert res == "Option A"
    assert len(UIState.degradations) == 1
    assert UIState.degradations[0]["stage"] == "consult_user"
    assert "timeout" in UIState.degradations[0]["reason"].lower()
    UIState.is_ui_mode = False


def test_consult_fields_timeout_records_degradation():
    UIState.reset_run("test")
    UIState.is_ui_mode = True

    agent = DirectorAgent({"models": {"director": "test"}})
    fields = [{"key": "f1", "label": "Field 1", "current": "val1", "options": ["val1", "val2"]}]

    with patch.object(UIState.pause_event, "wait", return_value=False):
        res = agent.consult_fields(fields)

    assert res == {}
    assert len(UIState.degradations) == 1
    assert UIState.degradations[0]["stage"] == "consult_fields"
    assert "timeout" in UIState.degradations[0]["reason"].lower()
    UIState.is_ui_mode = False


def test_create_writer_fallback_records_degradation(monkeypatch):
    UIState.reset_run("test")

    # Mock LLM creation to bypass Agent instantiation crash
    def _fake_create_llm(model_name, host="http://localhost:11434", timeout=240, max_tokens=2048):
        return f"ollama/{model_name}"

    monkeypatch.setattr(cm, "_create_ollama_llm", _fake_create_llm)
    # Force _ollama_model_available to return False so fallback triggers
    monkeypatch.setattr(cm, "_ollama_model_available", lambda *a, **kw: False)

    cfg = {
        "models": {
            "director": "hermes-director",
            "writer": "zephyr-writer",
        },
        "ollama": {"host": "http://localhost:11434", "request_timeout": 240},
    }

    with contextlib.suppress(Exception):
        cm.create_writer(cfg)

    assert len(UIState.degradations) == 1
    assert UIState.degradations[0]["stage"] == "create_writer"
    assert "zephyr-writer" in UIState.degradations[0]["reason"]
    assert "hermes-director" in UIState.degradations[0]["reason"]


def test_translate_node_fallback_records_degradation(monkeypatch):
    UIState.reset_run("test")

    import threading

    from core.segment_runner import make_process_segment
    from utils.source_splitter import SegmentChunk

    # Create dummy dependencies for make_process_segment
    mock_director = MagicMock()
    # Force translate_to_devanagari to raise exception
    mock_director.translate_to_devanagari.side_effect = RuntimeError("Translation model crashed")

    mp4s = [None]
    counter = [0]
    cfg = {
        "critic": {"threshold": 60},
        "script": {"word_count_tolerance": 0.25},
        "tts": {"lang": "hi"}
    }
    outline = [{"title": "Intro"}]

    process_seg = make_process_segment(
        topic="test",
        config=cfg,
        outline=outline,
        n_segs=1,
        out_base=Path("C:/tmp"),
        tts_cfg={"lang": "hi"},
        cp_mgr=MagicMock(),
        world_state=MagicMock(),
        mem=MagicMock(),
        ctx_mgr=MagicMock(),
        director_agent_instance=mock_director,
        writer_agent=MagicMock(),
        resume=False,
        dry_run=True,
        director_mode=False,
        preview_mode=False,
        skip_rvc=True,
        words_per_seg=100,
        seg_min=2,
        shared_prompt_executor=MagicMock(),
        global_scheduler=MagicMock(),
        _crewai_lock=threading.RLock(),
        crewai_lock=threading.RLock(),
        completed_segs_counter_holder=counter,
        completed_segs_lock=threading.Lock(),
        mp4s=mp4s,
        mp4s_lock=threading.Lock(),
        run_start_ts=0.0,
        source_chunks=[
            SegmentChunk(
                index=1,
                text="This is a very long and safe and clean script for testing the translation node fallback without getting filtered by the safety sanitization block.",
                source_chapter="Chapter 1"
            )
        ]
    )

    with patch("crewai.Crew"), patch("crewai.Task"):
        with contextlib.suppress(Exception):
            process_seg(1)

    # Verification: should have recorded translation degradation in UIState
    assert len(UIState.degradations) > 0
    stages = [d["stage"] for d in UIState.degradations]
    assert "translate_node" in stages
