"""hinglish_glossary.py - Static English->Devanagari glossary for MODERN Hinglish.

Used by DirectorAgent.translate_to_devanagari to make ~25-30% of common English
words appear in the Hindi narration written in Devanagari script
(e.g. "problem" -> प्रॉब्लम), instead of pure/literary Hindi (समस्या).

Mechanism (NO ML dependency):
  1. protect_hinglish() finds glossary words in the English script and swaps them
     for @@N@@ placeholder tokens. These tokens pass through sarvam-translate
     untouched -- verified by the hinglish diagnostic (Test B).
  2. The protected script is translated to Hindi normally; tokens survive.
  3. restore_hinglish() swaps each @@N@@ token back to its Devanagari spelling.

This replaces the originally-planned IndicXlit transliteration pass, which could
not be installed: ai4bharat-transliteration -> fairseq requires Python <= 3.12,
but the target machine runs Python 3.14. A curated static glossary is
deterministic, needs no model, and runs on any Python.

To grow coverage: just add more "english": "देवनागरी" pairs below. Keys MUST be
lowercase. Avoid very short words that collide with common Hindi grammar words
(e.g. "key" -> की collides with the Hindi postposition की).
"""

import re

# Lowercase English -> Devanagari spelling of the English word.
HINGLISH_GLOSSARY: dict[str, str] = {
    # --- modern fillers / everyday ---
    "actually": "एक्चुअली",
    "basically": "बेसिकली",
    "really": "रियली",
    "problem": "प्रॉब्लम",
    "idea": "आइडिया",
    "plan": "प्लान",
    "team": "टीम",
    "story": "स्टोरी",
    "video": "वीडियो",
    "moment": "मोमेंट",
    "time": "टाइम",
    "point": "पॉइंट",
    "level": "लेवल",
    "chance": "चांस",
    "luck": "लक",
    "risk": "रिस्क",
    "reason": "रीजन",
    "answer": "आंसर",
    "question": "क्वेश्चन",
    "mistake": "मिस्टेक",
    "final": "फाइनल",
    "start": "स्टार्ट",
    "focus": "फोकस",
    "target": "टारगेट",
    "goal": "गोल",
    "success": "सक्सेस",
    "speed": "स्पीड",
    # --- story / fantasy nouns ---
    "power": "पावर",
    "energy": "एनर्जी",
    "magic": "मैजिक",
    "secret": "सीक्रेट",
    "mystery": "मिस्ट्री",
    "danger": "डेंजर",
    "enemy": "एनिमी",
    "hero": "हीरो",
    "villain": "विलेन",
    "king": "किंग",
    "queen": "क्वीन",
    "prince": "प्रिंस",
    "princess": "प्रिंसेस",
    "castle": "कैसल",
    "kingdom": "किंगडम",
    "world": "वर्ल्ड",
    "god": "गॉड",
    "death": "डेथ",
    "life": "लाइफ",
    "soul": "सोल",
    "dark": "डार्क",
    "darkness": "डार्कनेस",
    "fire": "फायर",
    "blood": "ब्लड",
    "war": "वॉर",
    "battle": "बैटल",
    "fight": "फाइट",
    "weapon": "वेपन",
    "sword": "सोर्ड",
    "shield": "शील्ड",
    "army": "आर्मी",
    "attack": "अटैक",
    "victory": "विक्ट्री",
    "curse": "कर्स",
    "spell": "स्पेल",
    "portal": "पोर्टल",
    "gate": "गेट",
    "forest": "फॉरेस्ट",
    "mountain": "माउंटेन",
    "river": "रिवर",
    "ocean": "ओशन",
    "sky": "स्काई",
    "star": "स्टार",
    "moon": "मून",
    "shadow": "शैडो",
    "ghost": "गोस्ट",
    "monster": "मॉन्स्टर",
    "demon": "डीमन",
    "dragon": "ड्रैगन",
    "beast": "बीस्ट",
    "creature": "क्रीचर",
    "legend": "लेजेंड",
    "prophecy": "प्रॉफेसी",
    "ritual": "रिचुअल",
    "temple": "टेम्पल",
    "throne": "थ्रोन",
    "crown": "क्राउन",
    "treasure": "ट्रेजर",
    "gold": "गोल्ड",
    "mission": "मिशन",
    "journey": "जर्नी",
    "adventure": "एडवेंचर",
    "quest": "क्वेस्ट",
    "challenge": "चैलेंज",
    "trap": "ट्रैप",
    "escape": "एस्केप",
    "survive": "सर्वाइव",
    "control": "कंट्रोल",
    # --- people / relations ---
    "human": "ह्यूमन",
    "people": "पीपल",
    "friend": "फ्रेंड",
    "family": "फैमिली",
    "brother": "ब्रदर",
    "sister": "सिस्टर",
    "love": "लव",
    "fear": "फियर",
    "hope": "होप",
    "dream": "ड्रीम",
    "promise": "प्रॉमिस",
    "destiny": "डेस्टिनी",
    "fate": "फेट",
    "future": "फ्यूचर",
    "history": "हिस्ट्री",
    "leader": "लीडर",
    "master": "मास्टर",
    "soldier": "सोल्जर",
    "guard": "गार्ड",
    "prisoner": "प्रिज़नर",
    # --- tech / modern world ---
    "system": "सिस्टम",
    "machine": "मशीन",
    "robot": "रोबोट",
    "technology": "टेक्नोलॉजी",
    "science": "साइंस",
    "experiment": "एक्सपेरिमेंट",
    "data": "डेटा",
    "message": "मैसेज",
    "signal": "सिग्नल",
    "code": "कोड",
    "game": "गेम",
    "player": "प्लेयर",
    "character": "कैरेक्टर",
    "camera": "कैमरा",
    "screen": "स्क्रीन",
    "phone": "फोन",
    "market": "मार्केट",
    "money": "मनी",
    "police": "पुलिस",
    "doctor": "डॉक्टर",
    "office": "ऑफिस",
    "school": "स्कूल",
    "result": "रिजल्ट",
    "decision": "डिसीजन",
    "option": "ऑप्शन",
    "proof": "प्रूफ",
    "clue": "क्लू",
}

# @@N@@ token style verified to survive sarvam-translate (diagnostic Test B).
# Restore is tolerant: accept Devanagari digits (०-९) and stray spaces that a
# translator might introduce inside a token, so a token can never silently leak.
_TOKEN_RE = re.compile(r"@@\s*([0-9\u0966-\u096f]+)\s*@@")
_TOKEN_ASCII_DIGITS = str.maketrans("\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f", "0123456789")
# Match runs of latin letters (allow internal apostrophe/hyphen).
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def _lookup(word: str) -> str | None:
    """Return Devanagari spelling for an English word, handling simple plurals."""
    w = word.lower().strip("'-")
    if w in HINGLISH_GLOSSARY:
        return HINGLISH_GLOSSARY[w]
    if w.endswith("'s") and w[:-2] in HINGLISH_GLOSSARY:
        return HINGLISH_GLOSSARY[w[:-2]]
    if w.endswith("es") and w[:-2] in HINGLISH_GLOSSARY:
        return HINGLISH_GLOSSARY[w[:-2]]
    if w.endswith("s") and w[:-1] in HINGLISH_GLOSSARY:
        return HINGLISH_GLOSSARY[w[:-1]]
    return None


def protect_hinglish(text: str) -> tuple[str, dict[str, str]]:
    """Swap glossary words for @@N@@ tokens.

    Returns (protected_text, token_map) where token_map maps each token string
    to its Devanagari replacement.
    """
    token_map: dict[str, str] = {}
    counter = {"n": 0}

    def _sub(m: re.Match) -> str:
        word = m.group(0)
        deva = _lookup(word)
        if deva is None:
            return word
        tok = f"@@{counter['n']}@@"
        counter["n"] += 1
        token_map[tok] = deva
        return tok

    return _WORD_RE.sub(_sub, text), token_map


def restore_hinglish(text: str, token_map: dict[str, str]) -> str:
    """Swap @@N@@ tokens back to their Devanagari spellings.

    Tolerates Devanagari digits / inner spaces that a translator might inject;
    the digits captured by _TOKEN_RE are normalized back to ASCII before lookup
    so a token can never be left unrendered in the final narration.
    """
    if not token_map:
        return text

    def _sub(m: re.Match) -> str:
        n = m.group(1).translate(_TOKEN_ASCII_DIGITS)
        return token_map.get(f"@@{n}@@") or m.group(0)

    return _TOKEN_RE.sub(_sub, text)


_CONSONANTS = {
    "kh": "ख",
    "gh": "घ",
    "ch": "च",
    "jh": "झ",
    "th": "थ",
    "dh": "ध",
    "ph": "फ",
    "bh": "भ",
    "sh": "श",
    "k": "क",
    "g": "ग",
    "c": "क",
    "j": "ज",
    "t": "ट",
    "d": "ड",
    "n": "न",
    "p": "प",
    "b": "ब",
    "m": "म",
    "y": "य",
    "r": "र",
    "l": "ल",
    "v": "व",
    "w": "व",
    "s": "स",
    "h": "ह",
    "f": "फ",
    "z": "ज़",
    "q": "क",
    "x": "क्स",
}
_VOWELS = {
    "aa": ("आ", "ा"),
    "ee": ("ई", "ी"),
    "ii": ("ई", "ी"),
    "oo": ("ऊ", "ू"),
    "uu": ("ऊ", "ू"),
    "ai": ("ऐ", "ै"),
    "au": ("औ", "ौ"),
    "a": ("अ", ""),
    "i": ("इ", "ि"),
    "e": ("ए", "े"),
    "u": ("उ", "ु"),
    "o": ("ओ", "ो"),
}
_VOWEL_KEYS = sorted(_VOWELS, key=len, reverse=True)
_CONSONANT_KEYS = sorted(_CONSONANTS, key=len, reverse=True)


def _take(chunk: str, keys: list[str]) -> str:
    for key in keys:
        if chunk.startswith(key):
            return key
    return ""


def _roman_word_to_devanagari(word: str) -> str:
    glossary = _lookup(word)
    if glossary:
        return glossary

    src = word.lower().strip("'-")
    out: list[str] = []
    i = 0
    while i < len(src):
        chunk = src[i:]
        vowel = _take(chunk, _VOWEL_KEYS)
        if vowel:
            out.append(_VOWELS[vowel][0])
            i += len(vowel)
            continue

        cons = _take(chunk, _CONSONANT_KEYS)
        if not cons:
            i += 1
            continue
        base = _CONSONANTS[cons]
        i += len(cons)

        vowel = _take(src[i:], _VOWEL_KEYS)
        if vowel:
            out.append(base + _VOWELS[vowel][1])
            i += len(vowel)
        else:
            out.append(base if i >= len(src) else base + "्")

    return "".join(out) or word


def transliterate_latin_runs(text: str) -> str:
    """Convert leftover Roman Hindi/Hinglish words to Devanagari for Hindi TTS."""
    return _WORD_RE.sub(lambda m: _roman_word_to_devanagari(m.group(0)), text)


def hinglish_ratio(original_english: str, token_map: dict[str, str]) -> float:
    """Fraction of English words that were converted to Hinglish."""
    total = len(_WORD_RE.findall(original_english)) or 1
    return len(token_map) / total
