# Task 4.6 — Implementation Handoff Plan

**Task (from tasks.md):**
> 4.6 Strengthen `translate_to_devanagari` loanword/number rules; add `_devanagari_ratio`
> post-check with bounded re-translation + warning — _Requirements: 2.6, 2.7_

This is a **completion** task, not a from-scratch build. ~70% already exists. Read this
whole document before editing. Make only the changes in "Work To Do". Do not refactor
unrelated code.

---

## 1. Current state (verified — do NOT redo these)

File: `agents/director_agent.py`, method `translate_to_devanagari(self, english_script, segment_plan, context="")`
starting at approx **line 2337** (match on the method name, line numbers drift).

Already implemented and working:
- **Loanword transliteration rules** — prompt rule 2 (phone → फोन, school → स्कूल, etc.).
- **Number spell-out** — prompt rule 3 (100 → सौ, 3 → तीन).
- **Acronym transliteration** — prompt rule 4.
- **Empty / failed translation fallback** — returns `english_script` if the model
  returns empty or `< 10` Devanagari chars.
- **Tag cleanup** — strips `<think>...</think>` and `<|...|>`.
- **A Latin-ratio post-check (partial)** at approx **line 2417**: it computes
  `latin_ratio = latin_chars / total_alpha` and, when `> 0.1`, logs a `log.warning(...)`.
  **It only warns. It does not retry.**

What is MISSING (this is the entire job for 4.6):
1. A reusable, testable helper `_devanagari_ratio(text) -> float` (or `_latin_ratio`).
2. **Bounded re-translation**: when the Latin ratio is too high, re-run the translation
   up to N times (config-driven, small cap) with a stricter instruction, keep the best
   result, then warn only if still over threshold after the retries.

Requirements mapping:
- **2.6** = loanword/number transliteration rules (already done — just confirm).
- **2.7** = Devanagari-ratio post-check with bounded re-translation + warning (the work).

---

## 2. Config keys to add

File: `config/config.yaml`, under the existing `tts:` section (it already holds
`lang: "hi"`). Add a small sub-block (keep YAML 2-space indent):

```yaml
tts:
  # ... existing keys ...
  devanagari:
    max_latin_ratio: 0.10      # >10% Latin letters triggers re-translation
    max_retranslate_retries: 2 # bounded retries before accepting best result
```

File: `config/config_schema.py` — add a matching optional model so validation doesn't
strip the keys. Find the TTS config model (search `class TTSConfig` or the tts schema).
Add:

```python
class DevanagariConfig(BaseModel):
    max_latin_ratio: float = Field(default=0.10, ge=0.0, le=1.0)
    max_retranslate_retries: int = Field(default=2, ge=0, le=5)
```
Then add `devanagari: DevanagariConfig = DevanagariConfig()` to the TTS config model.
If the TTS schema model uses `extra="allow"`, you may skip the schema edit, but prefer
adding it explicitly for clarity. Verify by loading config (see Verification §5).

> If you cannot quickly locate the TTS schema model, read `config/config_schema.py` top
> to bottom first. Do NOT guess the class name.

---

## 3. Work to do (exact)

### 3.1 Add the helper (module level, near the top of `agents/director_agent.py`)

Place after the imports / existing module-level helpers. Pure function, no LLM calls:

```python
def _devanagari_ratio(text: str) -> float:
    """Return the fraction of alphabetic characters that are Devanagari (U+0900–U+097F).

    Returns 1.0 for text with no alphabetic characters (treated as 'clean' so we don't
    trigger spurious re-translation on punctuation/number-only output).
    """
    total_alpha = sum(1 for c in text if c.isalpha())
    if total_alpha == 0:
        return 1.0
    deva = sum(1 for c in text if "\u0900" <= c <= "\u097F")
    return deva / total_alpha
```

(Keeping a single source of truth: ratio = Devanagari / all-alpha. `latin_ratio ≈ 1 - this`
for hi text since output is Devanagari + Latin only. Use whichever framing reads cleaner,
but use ONE helper.)

### 3.2 Replace the warn-only block with bounded re-translation

Locate the existing block (approx lines 2413–2427) that:
- computes `devanagari_chars`,
- computes `latin_chars` / `total_alpha` / `latin_ratio`,
- and only `log.warning(...)`.

Refactor the **translation call + ratio check** into a small retry loop. Pseudocode —
adapt to the surrounding `try/except` that already exists:

```python
deva_cfg = (self.config.get("tts", {}).get("devanagari", {})
            if hasattr(self, "config") else {})
min_deva_ratio = 1.0 - float(deva_cfg.get("max_latin_ratio", 0.10))
max_retries    = int(deva_cfg.get("max_retranslate_retries", 2))

best = translated                      # first translation (already cleaned + validated)
best_ratio = _devanagari_ratio(best)

attempt = 0
while best_ratio < min_deva_ratio and attempt < max_retries:
    attempt += 1
    log.info(f"[DIRECTOR] Devanagari ratio {best_ratio:.0%} below "
             f"{min_deva_ratio:.0%} — re-translating (attempt {attempt}/{max_retries})")
    stricter = prompt + (
        "\n\nIMPORTANT: The previous attempt left English (Latin) letters in the "
        "output. Re-translate so that EVERY word is in Devanagari. Transliterate all "
        "English loanwords phonetically. Output ONLY Devanagari."
    )
    try:
        candidate = self._call_ollama_chat(
            stricter, model_type="translator",
            system_msg="You are an expert literary translator. Output ONLY Devanagari Hindi.")
        candidate = re.sub(r"<think>.*?</think>", "", candidate or "", flags=re.DOTALL).strip()
        candidate = re.sub(r"<\|.*?\|>", "", candidate).strip()
        cand_ratio = _devanagari_ratio(candidate)
        # keep the best (highest Devanagari ratio) candidate seen
        if candidate and cand_ratio > best_ratio:
            best, best_ratio = candidate, cand_ratio
    except Exception as _re_err:
        log.warning(f"[DIRECTOR] Re-translation attempt {attempt} failed ({_re_err})")
        break

translated = best
if best_ratio < min_deva_ratio:
    log.warning(f"[DIRECTOR] Devanagari ratio still {best_ratio:.0%} after "
                f"{attempt} retries — accepting best result (loanwords may remain).")
```

Constraints:
- **Reuse the existing `prompt` variable** already built in the method (do not rebuild
  from scratch). The stricter retry just appends an instruction.
- Keep the existing empty/`< 10` Devanagari-char early-return BEFORE the loop.
- Keep the outer `try/except` that returns `english_script` on hard failure.
- Do not change the method signature or return type (still returns a `str`).

### 3.3 Confirm `self.config` exists

`translate_to_devanagari` reads config via `self`. Verify the `DirectorAgent` stores the
full config (search `self.config` in `agents/director_agent.py`). If the agent only
stores `self.models`, fall back to module-level `load_config()`:

```python
try:
    from config import load_config
    _full_cfg = getattr(self, "config", None) or load_config()
except Exception:
    _full_cfg = {}
deva_cfg = _full_cfg.get("tts", {}).get("devanagari", {})
```

Pick whichever matches the existing pattern in the file. Do NOT introduce a new config
load path if `self.config` already exists.

---

## 4. Tests to add

File: `tests/test_devanagari_translation.py` (new). Tests MUST mock the LLM — **no real
Ollama calls**. Follow the mocking style already used in `tests/` (check
`tests/conftest.py` and existing `test_decision_engine.py` for the pattern).

Required cases:
1. **`test_devanagari_ratio_pure`** — `_devanagari_ratio("नमस्ते दोस्तों")` == 1.0.
2. **`test_devanagari_ratio_mixed`** — `_devanagari_ratio("नमस्ते phone")` is between
   0 and 1 and < 1.0 (Latin present).
3. **`test_devanagari_ratio_no_alpha`** — `_devanagari_ratio("123 ... !")` == 1.0
   (no false re-translation trigger).
4. **`test_retranslate_triggers_on_latin_heavy`** — mock `_call_ollama_chat` to return a
   Latin-heavy first result, then a clean Devanagari result; assert the method returns
   the clean one and that the mock was called > 1 time (retry happened).
5. **`test_no_retranslate_when_clean`** — mock returns clean Devanagari first try;
   assert `_call_ollama_chat` called exactly once (no wasted retries).
6. **`test_retry_cap_respected`** — mock always returns Latin-heavy; assert call count
   == `1 + max_retries` and the method still returns a string (best-effort, no crash).
7. **`test_latin_path_unchanged`** (Req sanity) — confirm the English/Latin pipeline is
   untouched: calling with a non-hi scenario or asserting `inject_emotion` Latin path
   still works is OUT OF SCOPE here; only assert this method returns Devanagari text.

Run: `venv\Scripts\python.exe -m pytest tests/test_devanagari_translation.py -q`

---

## 5. Verification checklist (run all, in order)

```powershell
# 1. Diagnostics clean on edited files
#    (use the IDE getDiagnostics tool on:)
#    agents/director_agent.py, config/config_schema.py, config/config.yaml

# 2. Config loads with new keys present
venv\Scripts\python.exe -c "import bootstrap_pipeline as b; b.bootstrap(); from config import load_config; c=load_config(); print(c['tts'].get('devanagari'))"
#    Expect: {'max_latin_ratio': 0.1, 'max_retranslate_retries': 2}

# 3. New tests pass
venv\Scripts\python.exe -m pytest tests/test_devanagari_translation.py -q

# 4. Full suite still green (no regressions)
venv\Scripts\python.exe -m pytest tests/ -q

# 5. Module still imports
venv\Scripts\python.exe -c "import bootstrap_pipeline as b; b.bootstrap(); import agents.director_agent; print('OK')"
```

All five must pass. If config keys come back `None` in step 2, the schema model
(§2) is stripping them — fix the schema, do not delete the YAML keys.

---

## 6. Scope guardrails (do NOT do these)

- Do NOT change the prompt's existing loanword/number rules — they satisfy 2.6 already.
- Do NOT touch `generate_hinglish_script` (separate romanized path, B38 — out of scope).
- Do NOT add real network/Ollama calls to tests.
- Do NOT change `translate_to_devanagari`'s signature or callers in `pipeline_long.py`.
- Keep retries SMALL and config-capped — this runs once per segment; an unbounded loop
  on a 90-segment video is a hang risk (see B13 lesson in BUGS.md).
- After done, tick `4.6` in `.kiro/specs/production-quality-fixes/tasks.md`.

---

## 7. Definition of done

- `_devanagari_ratio` helper exists and is unit-tested.
- High-Latin output triggers bounded, config-capped re-translation; best result kept;
  single warning only if still over threshold after retries.
- New config keys present and schema-validated.
- New test file passes; full suite stays green; module imports clean.
- Task 4.6 checkbox ticked in tasks.md.
