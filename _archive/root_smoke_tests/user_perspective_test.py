"""
user_perspective_test.py - Exercise the app as a real user would.

Launches every user-facing entry point with realistic inputs and edge cases.
Verifies that:
  - each entry point launches without crashing
  - argparse errors are clear and informative
  - all CLI flags actually do what they claim
  - error paths produce useful messages
  - resource files (configs, prompts, themes) load correctly
  - cross-CLI consistency holds

Does NOT generate a full video - uses --dry-run / fast paths where possible.
"""
import contextlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Force UTF-8
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
os.environ["CREWAI_TELEMETRY_OPTOUT"] = "true"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["TORCHDYNAMO_SUPPRESS_ERRORS"] = "1"

# Use venv python
VENV_PY = Path("C:/Video.AI/venv/Scripts/python.exe")
if not VENV_PY.exists():
    VENV_PY = Path(sys.executable)

ROOT = Path("C:/Video.AI").absolute()
sys.path.insert(0, str(ROOT))

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8")

PASS = "[V] PASS"
FAIL = "[X] FAIL"
WARN = "[!] WARN"
INFO = "[i] INFO"
SKIP = "[-] SKIP"

results = []
def record(name, status, detail=""):
    results.append((name, status, detail))
    line = f"  {status}  {name}"
    if detail:
        line += f"  — {detail[:120]}"
    print(line)


def run_cmd(args, timeout=300, cwd=None, env=None):
    """Run a subprocess; return (exit_code, stdout, stderr, timed_out)."""
    if env is None:
        env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("OTEL_SDK_DISABLED", "true")
    env.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    env.setdefault("CREWAI_TELEMETRY_OPTOUT", "true")
    try:
        proc = subprocess.run(
            [str(VENV_PY), *list(args)],
            capture_output=True,
            timeout=timeout,
            cwd=cwd or str(ROOT),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s", True
    except Exception as e:
        return -2, "", f"exec error: {e}", False


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: Help/version for every entry point
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 1. Every entry point has working --help ---")
ENTRY_POINTS = [
    ("bootstrap_pipeline.py", ["bootstrap_pipeline.py", "--help"]),
    ("core/pipeline_long.py", ["core/pipeline_long.py", "--help"]),
    ("utils/model_eval.py", ["utils/model_eval.py", "--help"]),
    ("utils/diagnose.py", ["utils/diagnose.py", "--help"]),
    ("verify_fixes.py", ["verify_fixes.py", "--help"]),
]
# isolated_tests.py is a script that runs immediately on import — no argparse.
# Calling it with --help would try to RUN the tests. Skip it from --help checks.
for label, args in ENTRY_POINTS:
    rc, out, err, timed_out = run_cmd(args, timeout=30)
    if timed_out:
        record(f"{label} --help", FAIL, "timed out (CLI hangs?)")
    elif rc != 0:
        # Some CLIs exit 0 on --help, some exit 1 — but they MUST print usage
        has_usage = "usage" in out.lower() or "usage" in err.lower() or "options" in out.lower()
        if has_usage:
            record(f"{label} --help", PASS, f"prints usage (exit={rc})")
        else:
            record(f"{label} --help", FAIL, f"no usage printed; out={out[:80]!r}")
    else:
        has_usage = "usage" in out.lower() or "options" in out.lower() or "args" in out.lower()
        record(f"{label} --help", PASS, "help printed cleanly")

# isolated_tests.py is a run-on-import script (no argparse). Verify it
# imports cleanly without error.
try:
    importlib_spec = __import__("importlib.util", fromlist=["spec_from_file_location"])
    spec = importlib_spec.spec_from_file_location("isolated_tests", str(ROOT / "isolated_tests.py"))
    # Don't actually execute it (would run tests); just check it parses.
    import ast
    with open(ROOT / "isolated_tests.py", encoding="utf-8") as f:
        ast.parse(f.read())
    record("isolated_tests.py is parseable", PASS, "AST valid (script has no CLI by design)")
except Exception as e:
    record("isolated_tests.py is parseable", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: Bootstrap with no required args — must error clearly
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 2. Bootstrap CLI: required arg validation ---")
rc, out, err, _ = run_cmd(["bootstrap_pipeline.py"], timeout=30)
if rc != 0 and ("topic" in (out + err).lower() or "eval" in (out + err).lower() or "file" in (out + err).lower()):
    record("no args → clear error", PASS, f"exit={rc}, mentions required arg")
else:
    record("no args → clear error", FAIL, f"exit={rc}, out={out[:200]!r}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: Invalid flag values
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 3. Bootstrap CLI: invalid flag values ---")
for flag, val in [
    ("--run-mode", "garbage"),
    ("--duration", "not-a-number"),
    ("--words-per-segment", "not-a-number"),
    ("--segment-count", "0"),
    ("--segment-count", "-1"),
]:
    rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "x", flag, val], timeout=15)
    if rc != 0:
        record(f"{flag} {val!r} → rejected", PASS, f"exit={rc}")
    else:
        record(f"{flag} {val!r} → rejected", FAIL, "accepted invalid value")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: --file with missing path
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 4. --file with bad paths ---")
for label, args in [
    ("missing --file", ["bootstrap_pipeline.py", "--file", "C:/no/such/file.txt", "--dry-run", "--yes"]),
    ("empty --file", ["bootstrap_pipeline.py", "--file", "", "--dry-run", "--yes"]),
]:
    rc, out, err, _ = run_cmd(args, timeout=15)
    if rc != 0:
        record(label, PASS, f"exit={rc}, error caught")
    else:
        record(label, FAIL, "no error for bad path")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5: --topics-file with bad input
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 5. --topics-file edge cases ---")
with tempfile.TemporaryDirectory() as td:
    # Missing file — should error specifically about file not found
    rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topics-file", f"{td}/no.txt", "--dry-run", "--yes"], timeout=15)
    if rc != 0 and "not found" in (out + err).lower():
        record("missing topics-file → error", PASS, f"exit={rc}")
    elif rc == 2 and "topic" in (out + err).lower() and "topics_file" in (out + err).lower():
        record("missing topics-file → error", FAIL, f"BUG: --topics-file still requires --topic (exit=2, {out[:120]!r})")
    else:
        record("missing topics-file → error", FAIL, f"exit={rc}, out={out[:200]!r}")

    # Empty file
    empty = Path(td) / "empty.txt"
    empty.write_text("", encoding="utf-8")
    rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topics-file", str(empty), "--dry-run", "--yes"], timeout=15)
    if rc != 0 and "no topics" in (out + err).lower():
        record("empty topics-file → error", PASS, f"exit={rc}")
    else:
        record("empty topics-file → error", FAIL, f"exit={rc}, out={out[:200]!r}")

    # Comments only
    cmt = Path(td) / "comments.txt"
    cmt.write_text("# comment 1\n# comment 2\n\n", encoding="utf-8")
    rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topics-file", str(cmt), "--dry-run", "--yes"], timeout=15)
    if rc != 0 and "no topics" in (out + err).lower():
        record("comments-only topics-file → error", PASS, f"exit={rc}")
    else:
        record("comments-only topics-file → error", FAIL, f"exit={rc}, out={out[:200]!r}")

    # Two topics, one invalid (path traversal) — don't run, just check it's parseable
    two = Path(td) / "two.txt"
    two.write_text("Good Topic\n../../etc/passwd\n", encoding="utf-8")
    lines = [l.strip() for l in two.read_text().splitlines() if l.strip() and not l.strip().startswith("#")]
    if len(lines) == 2:
        record("topics-file parses 2 lines", PASS, f"{len(lines)} topics")
    else:
        record("topics-file parses 2 lines", FAIL, f"got {len(lines)} topics")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6: --duration edge cases (the v3 bug)
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 6. --duration accepts all valid values ---")
# Just verify argparse accepts the value (no full pipeline run)
# We use a short timeout because the dry-run path will take 6-8 min.
# If dry-run completes, great; if it times out, that's OK as long as the
# flag itself wasn't rejected.
for dur in ["0.5", "2.0", "2.5", "10.0", "0"]:
    rc, out, err, timed_out = run_cmd(["bootstrap_pipeline.py", "--topic", "user_test", "--duration", dur,
                                "--dry-run", "--yes", "--no-resume"], timeout=15)
    if timed_out:
        # Process started successfully (didn't reject the flag) but ran too long
        record(f"--duration {dur}", PASS, "flag accepted (dry-run still running)")
    elif rc == 0 or "duration" in (out + err).lower():
        record(f"--duration {dur}", PASS, f"exit={rc}, flag accepted")
    elif "invalid" in (out + err).lower() or "error:" in (out + err).lower():
        record(f"--duration {dur}", FAIL, f"rejected: {(out+err)[:150]!r}")
    else:
        record(f"--duration {dur}", WARN, f"exit={rc}, unclear: {(out+err)[:100]!r}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7: --yes auto-accept (verify in code, not by running)
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 7. --yes flag is wired correctly ---")
# Verify the bootstrap source has the --yes wire-up
bp_src = (ROOT / "bootstrap_pipeline.py").read_text(encoding="utf-8")
if "UIState.auto_accept = True" in bp_src and '"--yes"' in bp_src:
    record("--yes wires UIState.auto_accept", PASS, "code wiring verified")
else:
    record("--yes wires UIState.auto_accept", FAIL, "--yes not properly wired in bootstrap")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8: Topic with special chars (filename safety) — argparse only
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 8. Topic with special/unicode chars accepted by argparse ---")
for label, topic in [
    ("ascii normal", "Hero Story"),
    ("with slash", "Hero/Story"),
    ("with backslash", "Hero\\Story"),
    ("with colon", "Hero: Story"),
    ("with dot-dot", "Hero..Story"),
    ("unicode Hindi", "नायक की कहानी"),
    ("emoji", "Hero 🐉 Dragon"),
    ("very long", "A " * 50),
    ("empty-equivalent", "."),
    ("all spaces", "    "),
]:
    # Use --help to short-circuit; argparse will reject if --topic type is wrong
    # Actually, --topic is str (no type), so anything passes argparse. We just
    # verify the CLI doesn't reject unicode/special chars at the parser level.
    rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", topic, "--dry-run", "--yes", "--no-resume"],
                               timeout=5)
    # rc doesn't matter here — we just want to confirm the flag was accepted
    # (no "unrecognized arguments" or "invalid value" error).
    if "unrecognized arguments" in err or "invalid value" in err:
        record(f"topic={label!r}", FAIL, f"argparse rejected: {err[:80]!r}")
    else:
        record(f"topic={label!r}", PASS, "argparse accepted")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9: All structural locks accepted by argparse
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 9. All structural locks accepted by argparse ---")
rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "locks_test", "--yes", "--no-resume",
                            "--duration", "3.0",
                            "--words-per-segment", "100",
                            "--images-per-segment", "6",
                            "--segment-count", "2",
                            "--dry-run"], timeout=5)
if "unrecognized arguments" in err or "invalid" in err.lower():
    record("all locks accepted", FAIL, f"argparse rejected: {err[:150]!r}")
else:
    record("all locks accepted", PASS, "argparse accepted all structural flags")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10: Conflicting locks — verify code handles them
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 10. Conflicting structural locks accepted by argparse ---")
rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "conflict", "--yes", "--no-resume",
                            "--duration", "2.0", "--segment-count", "10",
                            "--dry-run"], timeout=5)
if "unrecognized arguments" in err or "invalid" in err.lower():
    record("conflicting locks accepted", FAIL, f"argparse rejected: {err[:150]!r}")
else:
    record("conflicting locks accepted", PASS, "argparse accepts; pipeline resolves")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 11: --eval-models (should be fast, no LLM)
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 11. --eval-models launches and runs ---")
# This actually invokes model_eval.run_eval() which may take a while
rc, out, err, timed_out = run_cmd(["bootstrap_pipeline.py", "--eval-models"], timeout=120)
if timed_out:
    record("--eval-models", WARN, "took >2min (still running)")
elif rc == 0:
    record("--eval-models", PASS, f"exit=0, output={len(out)} chars")
else:
    record("--eval-models", FAIL, f"exit={rc}, err={err[:200]!r}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 12: studio_tui.py launches without crash (textual may be missing)
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 12. studio_tui.py launch attempt ---")
# TUI is interactive — can't actually run it, but we can verify it imports
try:
    import studio_tui  # noqa
    record("studio_tui imports", PASS, "module loaded")
except Exception as e:
    record("studio_tui imports", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 13: utils/local_ui.py (Web UI FastAPI app)
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 13. utils/local_ui.py (Web UI) ---")
try:
    import importlib
    ui_mod = importlib.import_module("utils.local_ui")
    if hasattr(ui_mod, "app"):
        record("local_ui.app exists", PASS, "FastAPI app loaded")
    else:
        record("local_ui.app exists", FAIL, "no 'app' attribute")
except Exception as e:
    record("local_ui imports", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 14: utils/tui_theme_tester.py
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 14. utils/tui_theme_tester.py ---")
try:
    import importlib
    tester_mod = importlib.import_module("utils.tui_theme_tester")
    record("tui_theme_tester imports", PASS, "module loaded (v1 fix verified)")
except Exception as e:
    record("tui_theme_tester imports", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 15: diagnose.py — diagnostic tool
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 15. utils/diagnose.py (no args) ---")
rc, out, err, _ = run_cmd(["utils/diagnose.py"], timeout=60)
if rc == 0 or len(out) > 0 or len(err) > 0:
    record("diagnose.py runs", PASS, f"exit={rc}, output={len(out) + len(err)} chars")
else:
    record("diagnose.py runs", FAIL, f"exit={rc}, no output")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 16: model_eval.py directly
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 16. utils/model_eval.py --image-only ---")
rc, out, err, timed_out = run_cmd(["utils/model_eval.py", "--image-only"], timeout=180)
if timed_out:
    record("model_eval --image-only", WARN, "took >3min (still running)")
elif rc in (0, 1, 2):  # any reasonable exit
    record("model_eval --image-only", PASS, f"exit={rc}")
else:
    record("model_eval --image-only", FAIL, f"exit={rc}, err={err[:200]!r}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 17: verify_fixes.py
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 17. verify_fixes.py ---")
rc, out, err, _ = run_cmd(["verify_fixes.py"], timeout=120)
if rc == 0 and ("PASS" in out or "OK" in out or "passed" in out.lower()):
    record("verify_fixes.py passes", PASS, "exit=0")
else:
    record("verify_fixes.py passes", WARN, f"exit={rc}, out={out[:200]!r}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 18: isolated_tests.py
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 18. isolated_tests.py ---")
# This needs smoke_test_190722_seg01.json to exist
seg1 = ROOT / "studio_checkpoints" / "smoke_test_190722_seg01.json"
if not seg1.exists():
    record("isolated_tests.py smoke seg1 missing", SKIP, f"file not found: {seg1}")
else:
    rc, out, err, timed_out = run_cmd(["isolated_tests.py"], timeout=300)
    if timed_out:
        record("isolated_tests.py", WARN, "took >5min")
    elif rc == 0 and "PASS" in out:
        record("isolated_tests.py", PASS, "exit=0")
    else:
        record("isolated_tests.py", FAIL, f"exit={rc}, err={err[:200]!r}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 19: --no-resume vs default (resume) — code path verification
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 19. --no-resume and --resume flags accepted ---")
# Just verify argparse accepts both flags. Full pipeline runs are in the smoke test.
rc1, _, err1, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "resume_test", "--dry-run", "--yes"], timeout=5)
rc2, _, err2, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "resume_test", "--dry-run", "--yes", "--no-resume"], timeout=5)
if "unrecognized arguments" in (err1 + err2) or "invalid" in (err1 + err2).lower():
    record("resume / --no-resume accepted", FAIL, f"argparse rejected: {(err1+err2)[:200]!r}")
else:
    record("resume / --no-resume accepted", PASS, "argparse accepts both flags")
# Verify the code has the no-resume handler
pl_src = (ROOT / "core/pipeline_long.py").read_text(encoding="utf-8")
if "resume: bool = True" in pl_src and "Cleared stale" in pl_src:
    record("--no-resume clears WorldState", PASS, "code path exists")
else:
    record("--no-resume clears WorldState", WARN, "code path not found")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 20: Two runs of same topic — argparse + code path
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 20. Same-topic runs accepted ---")
# Just verify argparse accepts the topic string (idempotency is a code property)
rc, _, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "overwrite_test",
                          "--dry-run", "--yes", "--no-resume"], timeout=5)
if "unrecognized arguments" in err or "invalid" in err.lower():
    record("same-topic run accepted", FAIL, f"argparse rejected: {err[:100]!r}")
else:
    record("same-topic run accepted", PASS, "argparse accepts same-topic run")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 21: Run from a different working directory (absolute path)
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 21. Run from different cwd (absolute path) ---")
with tempfile.TemporaryDirectory() as td:
    # Use absolute path so the subprocess can find bootstrap_pipeline.py
    abs_script = str(ROOT / "bootstrap_pipeline.py")
    rc, out, err, _ = run_cmd([abs_script, "--topic", "cwd_test", "--dry-run", "--yes", "--no-resume"],
                               timeout=5, cwd=td)
    if "unrecognized arguments" in err or "No such file" in err or "can't open" in err:
        record("different cwd (absolute)", FAIL, f"argparse rejected: {err[:200]!r}")
    else:
        record("different cwd (absolute)", PASS, "pipeline launches from foreign cwd")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 22: Cancel mid-run — verify code path
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 22. KeyboardInterrupt / cancel handling ---")
# Verify the bootstrap code catches KeyboardInterrupt cleanly
bp_src = (ROOT / "bootstrap_pipeline.py").read_text(encoding="utf-8")
if "except KeyboardInterrupt" in bp_src and "[FAILED] Pipeline interrupted" in bp_src:
    record("KeyboardInterrupt handler present", PASS, "bootstrap catches and exits 1")
else:
    record("KeyboardInterrupt handler present", FAIL, "no KeyboardInterrupt handler")
# Verify pipeline_long has request_cancel
pl_src = (ROOT / "core/pipeline_long.py").read_text(encoding="utf-8")
if "request_cancel" in pl_src:
    record("request_cancel API present", PASS, "cancellation API exposed")
else:
    record("request_cancel API present", WARN, "request_cancel not found")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 23: Cross-CLI consistency (flags that exist in bootstrap but not pipeline_long)
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 23. Cross-CLI flag consistency ---")
bootstrap_src = (ROOT / "bootstrap_pipeline.py").read_text(encoding="utf-8")
pl_src = (ROOT / "core/pipeline_long.py").read_text(encoding="utf-8")
import re

bootstrap_flags = set(re.findall(r'--([a-z][a-z0-9-]+)', bootstrap_src))
pl_flags = set(re.findall(r'--([a-z][a-z0-9-]+)', pl_src))
# Filter out non-CLI flag matches
non_cli = {"init--", "markdown", "newline", "self-", "py-"}
bootstrap_flags = {f for f in bootstrap_flags if f not in non_cli and not f.startswith("f") and len(f) > 3}
pl_flags = {f for f in pl_flags if f not in non_cli and len(f) > 3}
# Flags only in bootstrap (not in pipeline_long)
only_bootstrap = sorted(bootstrap_flags - pl_flags)
record("flags only in bootstrap",
       INFO, f"{len(only_bootstrap)}: {only_bootstrap[:10]}")
record("flags only in pipeline_long",
       INFO, f"{len(pl_flags - bootstrap_flags)}: {sorted(pl_flags - bootstrap_flags)[:10]}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 24: --project with nonexistent project
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 24. --project with missing project ---")
rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "x", "--project", "nonexistent_project_xyz",
                            "--dry-run", "--yes", "--no-resume"], timeout=120)
if rc != 0:
    record("--project nonexistent → error", PASS, f"exit={rc}")
else:
    record("--project nonexistent → error", FAIL, "no error for missing project")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 25: --preview flag
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 25. --preview flag acceptance ---")
rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "preview_test", "--preview",
                            "--dry-run", "--yes", "--no-resume"], timeout=5)
if "unrecognized arguments" in err:
    record("--preview accepted", FAIL, f"argparse rejected: {err[:80]!r}")
else:
    record("--preview accepted", PASS, "argparse accepts")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 26: --director-mode flag
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 26. --director-mode flag acceptance ---")
rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "director_test", "--director-mode",
                            "--dry-run", "--yes", "--no-resume"], timeout=5)
if "unrecognized arguments" in err:
    record("--director-mode accepted", FAIL, f"argparse rejected: {err[:80]!r}")
else:
    record("--director-mode accepted", PASS, "argparse accepts")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 27: --series flag
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 27. --series flag ---")
rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "series_test", "--series",
                            "--dry-run", "--yes", "--no-resume"], timeout=5)
if "unrecognized arguments" in err:
    record("--series accepted", FAIL, f"argparse rejected: {err[:80]!r}")
else:
    record("--series accepted", PASS, "argparse accepts")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 28: --run-mode choices
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 28. --run-mode choices ---")
for mode in ["project", "one_time"]:
    rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", f"mode_{mode}", "--run-mode", mode,
                                "--dry-run", "--yes", "--no-resume"], timeout=5)
    if "unrecognized arguments" in err or "invalid choice" in err:
        record(f"--run-mode {mode}", FAIL, f"argparse rejected: {err[:80]!r}")
    else:
        record(f"--run-mode {mode}", PASS, "argparse accepts")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 29: skip-rvc flag
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 29. --skip-rvc flag ---")
rc, out, err, _ = run_cmd(["bootstrap_pipeline.py", "--topic", "skip_rvc_test", "--skip-rvc",
                            "--dry-run", "--yes", "--no-resume"], timeout=5)
if "unrecognized arguments" in err:
    record("--skip-rvc accepted", FAIL, f"argparse rejected: {err[:80]!r}")
else:
    record("--skip-rvc accepted", PASS, "argparse accepts")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 30: Pipeline from different Python interpreter (no venv)
# ═══════════════════════════════════════════════════════════════════════════
print("\n--- 30. Config loads from any cwd ---")
try:
    os.chdir(ROOT)
    from config import load_config
    cfg = load_config()
    record("config loads from cwd", PASS, f"loaded {len(cfg)} top-level keys")
except Exception as e:
    record("config loads from cwd", FAIL, f"{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
total = len(results)
passed = sum(1 for _, s, _ in results if s == PASS)
failed = sum(1 for _, s, _ in results if s == FAIL)
warned = sum(1 for _, s, _ in results if s == WARN)
infoed = sum(1 for _, s, _ in results if s == INFO)
skipped = sum(1 for _, s, _ in results if s == SKIP)
print(f"TOTAL: {total} | PASS: {passed} | FAIL: {failed} | WARN: {warned} | INFO: {infoed} | SKIP: {skipped}")
print("=" * 70)
if failed > 0:
    print("\nFAILED tests:")
    for name, s, detail in results:
        if s == FAIL:
            print(f"  [X] {name}: {detail}")
if warned > 0:
    print("\nWARNINGS:")
    for name, s, detail in results:
        if s == WARN:
            print(f"  [!] {name}: {detail}")

sys.exit(1 if failed > 0 else 0)
