"""translation.py - TranslationMixin: Devanagari + Hinglish narration scripts.

Extracted verbatim from ``agents/director_agent.py`` (WS-4 mixin split).
"""

import logging
import re
from typing import Any

from agents.hinglish_glossary import transliterate_latin_runs
from agents.ui_state import _devanagari_ratio

log = logging.getLogger(__name__)


class TranslationMixin:
    """Translation: Devanagari narration (glossary-protected) + Hinglish scripts."""

    # Provided by the composing DirectorAgent (LlmShimsMixin)
    _call_ollama: Any
    _call_ollama_chat: Any

    def translate_to_devanagari(
        self, english_script: str, segment_plan: dict, context: str = ""
    ) -> str | None:
        """Translate English narration to MODERN spoken Hindi (Devanagari).

        sarvam-translate is a pure translation model -- it translates EVERYTHING
        in the user message. So we send ONLY the raw English script as user
        content and keep all steering in the system message.

        ponytail: the old @@N@@ glossary-token protection (protect_hinglish)
        was removed because sarvam-translate degenerates on placeholder tokens:
        it transliterates English word-for-word into Devanagari ("night ke dead
        mein") instead of translating, and the result passes the script-ratio
        guard. The model already keeps common loanwords (लाइटहाउस) naturally,
        and transliterate_latin_runs below catches any residual Latin runs.
        Returns Devanagari Hindi, or English on failure.
        """

        instruction = (
            "Translate to natural spoken Hindi in Devanagari only. "
            "Output only the translation:"
        )

        def _looks_like_english_echo(text: str) -> bool:
            words = re.findall(r"[A-Za-z]+", text.lower())
            if not words:
                return False
            common_english = {
                "a",
                "all",
                "at",
                "few",
                "is",
                "just",
                "not",
                "the",
                "this",
            }
            return sum(word in common_english for word in words) / len(words) >= 0.35

        def _translate_once(instruction_text: str) -> str:
            prompt = (
                f"{instruction_text}\n\nText to translate:\n{english_script}"
                if instruction_text
                else english_script
            )
            raw = self._call_ollama_chat(
                prompt, model_type="translator", system_msg=""
            )
            if not raw:
                return ""
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"<\|.*?\|>", "", raw).strip()
            if sum(1 for c in raw if "\u0900" <= c <= "\u097f") < 10 and _looks_like_english_echo(raw):
                return raw
            return transliterate_latin_runs(raw)

        try:
            translated = _translate_once(instruction)
            if not translated:
                log.warning("[DIRECTOR] Translation returned empty -- using original English")
                return None

            # Some translation models echo/translate the system prompt before the
            # requested narration. A large expansion is the reliable boundary
            # signal: retry without steering, then fail closed rather than speak
            # instructions to the audience.
            _max_translation_chars = max(len(english_script) + 250, int(len(english_script) * 1.4))

            def _is_oversized_translation(text: str) -> bool:
                return len(text) > _max_translation_chars

            if _is_oversized_translation(translated):
                log.warning(
                    "[DIRECTOR] Translation likely contains instruction leakage "
                    f"({len(translated)} > {_max_translation_chars} chars); retrying clean"
                )
                translated = _translate_once("")
                if not translated or _is_oversized_translation(translated):
                    log.error("[DIRECTOR] Rejecting leaked/oversized translation")
                    return None

            # Validate: at least some Devanagari characters present (U+0900-U+097F)
            devanagari_chars = sum(1 for c in translated if "\u0900" <= c <= "\u097f")
            if devanagari_chars < 10:
                log.warning(
                    f"[DIRECTOR] Translation has only {devanagari_chars} Devanagari chars "
                    "-- using original."
                )
                return None

            # Devanagari-ratio check with bounded re-translation.
            _full_cfg = getattr(self, "llm_config", None) or {}
            if not isinstance(_full_cfg, dict):
                _full_cfg = {}
            _deva_cfg = _full_cfg.get("tts", {}).get("devanagari", {})
            _max_latin = float(_deva_cfg.get("max_latin_ratio", 0.10))
            _max_retries = int(_deva_cfg.get("max_retranslate_retries", 2))
            _min_deva_ratio = 1.0 - _max_latin

            best = translated
            best_ratio = _devanagari_ratio(best)
            attempt = 0

            while best_ratio < _min_deva_ratio and attempt < _max_retries:
                attempt += 1
                log.info(
                    f"[DIRECTOR] Devanagari ratio {best_ratio:.0%} below "
                    f"{_min_deva_ratio:.0%} -- re-translating (attempt {attempt}/{_max_retries})"
                )
                _stricter_instruction = instruction + (
                    " The previous attempt left English (Latin) letters in the output. "
                    "Transliterate EVERY remaining English word phonetically into "
                    "Devanagari. Output ONLY Devanagari."
                )
                try:
                    _candidate = _translate_once(_stricter_instruction)
                    if _candidate:
                        if _is_oversized_translation(_candidate):
                            log.warning("[DIRECTOR] Rejecting oversized re-translation candidate")
                            continue
                        _cand_ratio = _devanagari_ratio(_candidate)
                        if _cand_ratio > best_ratio:
                            best, best_ratio = _candidate, _cand_ratio
                except Exception as _re_err:
                    log.warning(f"[DIRECTOR] Re-translation attempt {attempt} failed ({_re_err})")
                    break

            translated = best
            if best_ratio < _min_deva_ratio:
                log.warning(
                    f"[DIRECTOR] Devanagari ratio {best_ratio:.0%} after {attempt} retries "
                    "-- rejecting translation."
                )
                return None
            log.info(
                f"[DIRECTOR] Devanagari translation complete: {len(translated)} chars, "
                f"ratio {best_ratio:.0%}"
            )
            return translated

        except Exception as e:
            log.exception(f"[DIRECTOR] Translation failed: {e}. Falling back to English.")
            return None

    def generate_hinglish_script(self, segment_plan: dict) -> str:
        """Convert English segment to Hinglish voiceover script."""
        summary = segment_plan.get("summary", "")
        key_event = segment_plan.get("key_event", "")
        mood = segment_plan.get("mood", "mysterious")

        prompt = (
            f"Write a compelling short narration script in natural Romanized Hinglish (Hindi written in English alphabet) "
            f"for a video segment.\n\n"
            f"Segment Summary: {summary}\n"
            f"Key Event: {key_event}\n"
            f"Mood: {mood}\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. Write the ENTIRE script in Romanized Hinglish (e.g., 'Dosto, aaj hum baat karenge...' instead of Devanagari or pure English).\n"
            f"2. Make it highly engaging, cinematic, and emotional.\n"
            f"3. Use dramatic pauses with [pause] and wrap the narration in [narration] ... [/narration] tags.\n"
            f"4. Length should be around 80-120 words.\n"
            f"5. Output ONLY the narration text between [narration] tags, no other labels or commentary."
        )
        try:
            res = self._call_ollama(prompt, model_type="director")
            # Extract content between [narration] tags if present
            match = re.search(r"\[narration\](.*?)\[/narration\]", res, re.DOTALL)
            if match:
                return match.group(1).strip()
            return res.strip()
        except Exception as e:
            log.warning(f"Failed to generate Hinglish script: {e}")
            return f"Aise hi shuru hoti hai kahani. {summary}. Aur phir, {key_event}."
