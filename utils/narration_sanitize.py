"""Narration sanitization shared by pre_production and segment_runner."""
from __future__ import annotations


def _normalize_hindi_for_tts(text: str) -> str:
    """Normalize unsupported Hindi characters before TTS synthesis.

    Supertonic ONNX model may not support: ऋ, ॠ, ऌ, and the English-loanword
    "candra" vowels ॉ/ऑ (candra-O) and ॅ/ऍ (candra-E). Replace with the closest
    standard, pronounceable equivalents so Hinglish words still speak correctly.
    """
    _map = {
        '\u090b': '\u0930\u093f',  # ऋ → रि
        '\u0960': '\u0930\u0940',  # ॠ → री
        '\u090c': '\u0932\u093f',  # ऌ → लि
        '\u0949': '\u094b',        # ॉ (candra-O sign) → ो   (प्रॉब्लम → प्रोब्लम)
        '\u0911': '\u0913',        # ऑ (candra-O independent) → ओ  (ऑफिस → ओफिस)
        '\u0945': '\u0947',        # ॅ (candra-E sign) → े
        '\u090d': '\u090f',        # ऍ (candra-E independent) → ए
    }
    for _old, _new in _map.items():
        text = text.replace(_old, _new)
    return text


def _reject_unsafe_narration(text: str) -> str | None:
    """Reject narration that contains leftover JSON, schema, or meta-commentary.

    Returns None if the text is unsafe, otherwise the cleaned text.
    """
    import re as _re_safe
    if not text or len(text) < 10:
        return None
    _unsafe_patterns = [
        r'\{"narration":', r'"narration"', r'"segment"',
        r'\{[\s\S]*?\}',  # JSON-like braces
        r'\[/?[a-z_]+\]',  # remaining tags
        r'<\|[^>]+\|>',
        r'```',
    ]
    for _pat in _unsafe_patterns:
        if _re_safe.search(_pat, text):
            return None
    return text


def _sanitize_narration(script: str) -> str:
    """Strip all non-spoken artifacts from a script before TTS/translation.

    Removes:
      - Story-structure tags: [narration], [/narration], [section], [pause], [scene]
      - LLM XML-ish tags: <answer>, </answer>, <think>...</think>, <|...|>
      - Markdown code fences and headers
      - Parenthetical stage directions: (softly), (whispering), [SFX: ...]
      - Leading labels like "Narration:", "Script:", "Segment 1:"
      - W3: Meta-commentary sentences from the LLM about its own writing
      - W3: Bold markers (**text**), HTML comments, [END_OF_TEXT] tokens
    Returns clean spoken text only.
    """
    import re as _re

    if not script:
        return ""
    s = script
    s = _re.sub(r"([A-Za-z\u0900-\u097F])-\s+([A-Za-z\u0900-\u097F])", r"\1\2", s)
    s = _re.sub(r"<think>.*?</think>", "", s, flags=_re.DOTALL | _re.IGNORECASE)
    s = _re.sub(r"</?[a-zA-Z][a-zA-Z0-9_]*(?:\s[^>]*)?\s*/?>", "", s)
    s = _re.sub(r"<!--.*?-->", "", s, flags=_re.DOTALL)
    s = _re.sub(r"<\|.*?\|>", "", s)
    s = _re.sub(r"```[a-zA-Z]*", "", s)
    s = _re.sub(r"\[END_OF_TEXT\]|\[END\]|\[STOP\]", "", s, flags=_re.IGNORECASE)
    s = _re.sub(r"\*\*([^*]*)\*\*", r"\1", s)
    s = _re.sub(r"\*([^*]*)\*", r"\1", s)
    _meta_patterns = [
        r"\bIn response to (?:your|the) (?:critique|feedback|instructions)\b[^.!?।]{0,150}[.!?।]",
        r"\bThe changes reflect\b[^.!?।]{0,150}[.!?।]",
        r"\bThis version (?:aims|is|reflects)\b[^.!?।]{0,150}[.!?।]",
        r"\bRevised Script\s*:?",
        r"\bHere'?s? (?:is )?the (?:revised|rewritten|updated)\b[^.!?।]{0,150}?(?:script|version|text|story|narration)\b[^.!?।]{0,80}?\s*[:\-]",
        r"\bHere'?s? (?:is )?the (?:revised|rewritten|updated)\b[^.!?।]{0,100}[.!?।]",
        r"\bNow,? each (?:detail|layer)\b[^.!?।]{0,150}[.!?।]",
        r"\bI have (?:revised|rewritten|updated|incorporated)\b[^.!?।]{0,150}[.!?।]",
        r"\bAs (?:requested|instructed|per your)\b[^.!?।]{0,150}?(?:script|version|text|story|narration)\b[^.!?।]{0,80}?\s*[:\-]",
        r"\bAs (?:requested|instructed|per your)\b[^.!?।]{0,100}[.!?।]",
        r"\b(?:Below|Here) (?:is|are) the (?:revised|updated|rewritten)\b[^.!?।]{0,150}[.!?।]",
        r"\bOutput plain text only[^.!?।]{0,150}[.!?।]",
        r"\b(?:They'?re|They are) not actually in the text\b[^.!?।]{0,220}[.!?।]",
        r"\byou'?ve introduced\b[^.!?।]{0,220}[.!?।]",
        r"\bI (?:can'?t|cannot|won'?t|will not)\b[^.!?।]{0,220}[.!?।]",
        r"\b(?:the|your) (?:provided )?(?:input|source text|text I have to work with)\b[^.!?।]{0,180}[.!?।]",
        r"^\s*(?:IMPORTANT|CRITICAL|SYSTEM|USER|ASSISTANT|Current Task|Instructions?|Task)\s*:\s*.*$",
    ]
    for pat in _meta_patterns:
        s = _re.sub(pat, "", s, flags=_re.IGNORECASE | _re.MULTILINE)
    s = _re.sub(
        r"\[/?(?:narration|section|pause|scene|sfx|music|cut|fade)[^\]]*\]",
        "",
        s,
        flags=_re.IGNORECASE,
    )
    s = _re.sub(r"\[[^\]]{0,60}\]", "", s)
    s = _re.sub(
        r"^\s*(?:narration|script|segment\s*\d*|title|hook|insight|escalation)\s*:\s*",
        "",
        s,
        flags=_re.IGNORECASE | _re.MULTILINE,
    )
    s = _re.sub(r"^\s*[a-zA-Z_ -]{1,30}\s*#{2,}\d*\s*:\s*", "", s, flags=_re.MULTILINE)
    s = _re.sub(r"\s+", " ", s).strip()
    return s
