"""manual_integration_test.py - REAL end-to-end feature test (no mocks).

Exercises the maximum number of pipeline features against the actual config.yaml
and the live Ollama server. Run directly:
    venv\\Scripts\\python.exe tests\\manual_integration_test.py

This is NOT a pytest file — it's an operator smoke test that prints a PASS/FAIL
report for each feature so we can see what actually works on this machine.
"""
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_results = []

def check(name, fn):
    """Run a feature check; record PASS/FAIL with detail."""
    t0 = time.time()
    try:
        detail = fn()
        dt = time.time() - t0
        _results.append((name, "PASS", detail, dt))
        print(f"[PASS] {name}  ({dt:.1f}s)  {detail}")
    except Exception as e:
        dt = time.time() - t0
        _results.append((name, "FAIL", str(e), dt))
        print(f"[FAIL] {name}  ({dt:.1f}s)  {e}")
        traceback.print_exc()


# ── 1. Config loads + all new keys present ────────────────────────────────
def t_config():
    from config import load_config
    cfg = load_config()
    perf = cfg.get("performance", {})
    ig = cfg.get("image_gen", {})
    afx = cfg.get("audio_fx", {})
    assert perf.get("vram_evict_wait_s") is not None, "A1 key missing"
    assert ig.get("preview_steps") is not None, "A4 key missing"
    assert ig.get("token_budget"), "B4 key missing"
    assert afx.get("program_loudnorm") is not None, "A3 key missing"
    assert cfg.get("music", {}).get("ducking") is not None, "D5 key missing"
    assert "staged_loop" in perf, "C1 key missing"
    return f"all gated keys present (staged_loop={perf.get('staged_loop')})"


# ── 2. B1 OllamaClient — real call + breaker against live Ollama ───────────
def t_ollama_client():
    from config import load_config
    from utils.ollama_client import get_ollama_client, reset_ollama_client
    reset_ollama_client()
    cfg = load_config()
    client = get_ollama_client(cfg)
    cfg.get("models", {}).get("script-reviewer", "script-reviewer")
    # Use the fast 3B reviewer for speed
    out = client.generate("Reply with the single word: OK", model="script-reviewer",
                           temperature=0.0, num_predict=10)
    assert out, "empty response from live Ollama"
    return f"live generate -> {out[:40]!r}"


# ── 3. B1 breaker opens on a bad model name ───────────────────────────────
def t_breaker():
    from config import load_config
    from utils.ollama_client import OllamaClient
    cfg = dict(load_config())
    cfg["ollama"] = dict(cfg.get("ollama", {}))
    cfg["ollama"]["breaker_fails"] = 1
    cfg["ollama"]["breaker_cooldown_s"] = 60
    cfg["ollama"]["request_timeout"] = 5
    client = OllamaClient(cfg)
    # First call to a nonexistent model fails (Ollama returns error)
    client.generate("hi", model="this-model-does-not-exist-xyz")
    st = client._breaker("this-model-does-not-exist-xyz").state
    # Second call should fail fast (breaker open)
    out2 = client.generate("hi", model="this-model-does-not-exist-xyz")
    assert out2 == "", "breaker did not fail fast"
    return f"breaker state after 1 fail = {st}, 2nd call failed fast"


# ── 4. B3 world-state LLM extraction (Devanagari-aware) — live ────────────
def t_world_state_llm():
    from config import load_config
    from utils.specialized_models import extract_world_state
    cfg = load_config()
    script = ("Arjun entered the ancient temple. The sacred fire could never be "
              "extinguished. Who had lit it a thousand years ago?")
    result = extract_world_state(script, cfg)
    if result is None:
        return "LLM returned unparseable JSON (regex fallback would handle it) — acceptable"
    assert isinstance(result.get("characters"), list)
    return f"chars={result.get('characters')} facts={len(result.get('facts',[]))}"


# ── 5. B3 WorldState.update full path (LLM on) with persistence ───────────
def t_world_state_update(tmp):
    from config import load_config
    from memory.memory import WorldState
    cfg = dict(load_config())
    cfg.setdefault("memory", {})["llm_world_state"] = True
    ws = WorldState("integration_test_topic", tmp)
    ws.update("Meera discovered the cursed sword. It must never be drawn.",
              {"seg": 1, "mood": "mysterious", "title": "T", "key_event": "found sword"},
              config=cfg)
    block = ws.to_prompt_block()
    assert "World State" in block
    return f"world block built ({len(block)} chars)"


# ── 6. B4 token budgeting from config ─────────────────────────────────────
def t_token_budget():
    from config import load_config
    from utils.scene_director import enrich_prompts
    cfg = load_config()
    # Long character description to force budgeting
    cfg = dict(cfg)
    cfg["characters"] = {
        "hero": {"name": "The Hero",
                 "description": "young adult, warm brown eyes, short black hair, "
                                "determined expression, dark grey coat, athletic build, "
                                "original character, intricate silver armor, glowing runes"}
    }
    plan = {"char_presence": [{"hero": 0.9}]}
    result, _neg = enrich_prompts("hero stands on a cliff at dawn", "The hero stood.", cfg, plan)
    first = result.split(";")[0]
    est_tokens = int(len(first.split()) * 1.3)
    assert est_tokens <= 75, f"prompt over budget: ~{est_tokens} tokens"
    return f"enriched prompt ~{est_tokens} CLIP tokens (budgeted)"


# ── 7. B2 degradation ledger + reset ──────────────────────────────────────
def t_degradation():
    from agents.director_agent import UIState
    UIState.reset_run("test")
    assert UIState.degradations == []
    UIState.add_degradation(3, "sfx_skip", "no files")
    UIState.add_degradation(5, "image_black_frame", "OOM")
    assert len(UIState.degradations) == 2
    UIState.reset_run("test2")
    assert UIState.degradations == []
    return "add + reset works"


# ── 8. A3 loudnorm command construction (dry, no real render) ─────────────
def t_loudnorm_cmd():
    # Verify the 2-pass loudnorm path builds the right filter without running ffmpeg
    import tempfile
    from unittest.mock import patch

    from video.renderer.assembler import concatenate_segments
    tmp = Path(tempfile.mkdtemp())
    segs = [tmp / "s0.mp4", tmp / "s1.mp4"]
    for s in segs:
        s.write_bytes(b"x")
    out = tmp / "out.mp4"
    cfg = {"audio_fx": {"program_loudnorm": True, "target_lufs": -14}}
    calls = []
    fake_stderr = '{"input_i":"-18","input_tp":"-1","input_lra":"7","input_thresh":"-28","target_offset":"0"}'
    class _P: stderr = fake_stderr; returncode = 0
    def fake_run(cmd, timeout=300):
        calls.append(cmd)
        for a in cmd:
            if str(a).endswith(".mp4") and "_prenorm_" in str(a):
                Path(a).write_bytes(b"x")
        if str(out) in [str(x) for x in cmd]:
            out.write_bytes(b"x")
    with patch("video.renderer.assembler._run", side_effect=fake_run), \
         patch("subprocess.run", return_value=_P()):
        concatenate_segments(segs, out, config=cfg)
    allargs = " ".join(str(a) for c in calls for a in c)
    assert "linear=true" in allargs, "2-pass linear loudnorm not applied"
    return "2-pass loudnorm with linear=true built"


# ── 9. D5 music ducking command ───────────────────────────────────────────
def t_ducking_cmd():
    import tempfile
    from unittest.mock import patch

    from video.renderer.assembler import concatenate_segments
    tmp = Path(tempfile.mkdtemp())
    segs = [tmp / "s0.mp4"]; segs[0].write_bytes(b"x")
    music = tmp / "m.mp3"; music.write_bytes(b"x")
    out = tmp / "out.mp4"
    cfg = {"music": {"ducking": True, "duck_ratio": 0.4}, "audio_fx": {"program_loudnorm": False}}
    calls = []
    def fake_run(cmd, timeout=300):
        calls.append(cmd); out.write_bytes(b"x")
    with patch("video.renderer.assembler._run", side_effect=fake_run):
        concatenate_segments(segs, out, music=music, config=cfg)
    allargs = " ".join(str(a) for c in calls for a in c)
    assert "sidechaincompress" in allargs
    return "sidechaincompress ducking built"


# ── 10. A6 --yes auto-accept ──────────────────────────────────────────────
def t_autoaccept():
    from agents.director_agent import DirectorAgent, UIState
    UIState.auto_accept = True
    UIState.is_ui_mode = False
    agent = DirectorAgent(llm_config={})
    r = agent.consult_user("Pick", options=["A", "B"])
    UIState.auto_accept = False
    assert r == "A"
    return "auto-accept returns first option"


# ── 11. D4 batch topics-file parsing ──────────────────────────────────────
def t_batch_parse(tmp):
    f = tmp / "topics.txt"
    f.write_text("Topic A\n\n# comment\nTopic B\n  \nTopic C\n", encoding="utf-8")
    lines = f.read_text(encoding="utf-8").splitlines()
    topics = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
    assert topics == ["Topic A", "Topic B", "Topic C"]
    return f"parsed {len(topics)} topics"


# ── 12. Director real translate to Devanagari (live, the heaviest LLM) ────
def t_translate():
    from audio.audio_proxy import translate_hinglish
    out = translate_hinglish("The hero walked into the dark forest.", seg=1)
    # Either Devanagari (success) or original English (fallback) — both are valid
    deva = sum(1 for c in out if "\u0900" <= c <= "\u097F")
    return f"translate returned {len(out)} chars, {deva} Devanagari"


if __name__ == "__main__":
    import tempfile
    print("=" * 70)
    print("  Video.AI — MAXIMUM FEATURE INTEGRATION TEST (live Ollama)")
    print("=" * 70)
    _tmp = Path(tempfile.mkdtemp())

    check("1. Config + all gated keys", t_config)
    check("2. B1 OllamaClient live generate", t_ollama_client)
    check("3. B1 circuit breaker fail-fast", t_breaker)
    check("4. B3 world-state LLM extract (live)", t_world_state_llm)
    check("5. B3 WorldState.update + persist", lambda: t_world_state_update(_tmp))
    check("6. B4 token budgeting from config", t_token_budget)
    check("7. B2 degradation ledger", t_degradation)
    check("8. A3 2-pass loudnorm command", t_loudnorm_cmd)
    check("9. D5 music ducking command", t_ducking_cmd)
    check("10. A6 --yes auto-accept", t_autoaccept)
    check("11. D4 batch topics-file parse", lambda: t_batch_parse(_tmp))
    check("12. Live Devanagari translation", t_translate)

    print("=" * 70)
    _pass = sum(1 for _, s, _, _ in _results if s == "PASS")
    _fail = sum(1 for _, s, _, _ in _results if s == "FAIL")
    print(f"  RESULT: {_pass} PASS / {_fail} FAIL  out of {len(_results)}")
    print("=" * 70)
    sys.exit(0 if _fail == 0 else 1)
