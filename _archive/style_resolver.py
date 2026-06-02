"""style_resolver.py -- 3-layer visual style resolver."""
import logging
import re
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

class StyleResolver:
    CONFIDENCE_THRESHOLD = 0.45

    def __init__(self, styles_path=None):
        if styles_path is None:
            styles_path = Path(__file__).parent / "styles.yaml"
        self._path = Path(styles_path)
        self._styles = {}
        self._fallback = "hybrid 2d anime visual novel style"
        self._load()

    def _load(self):
        if not self._path.exists():
            log.warning(f"StyleResolver: {self._path} not found -- using fallback")
            return
        with open(self._path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self._styles = data.get("styles", {})
        self._fallback = data.get("fallback_prompt", self._fallback)
        log.info(f"StyleResolver: loaded {len(self._styles)} styles from {self._path}")

    def reload(self):
        self._load()

    def resolve(self, raw_style, llm_expander=None):
        if not raw_style or not raw_style.strip():
            return "fallback", self._fallback
        cleaned = re.sub(r"^keep\s*as-is\s*:\s*", "", raw_style, flags=re.IGNORECASE).strip()
        style_lower = cleaned.lower()
        match = self._exact(style_lower)
        if match:
            return match, self._styles[match].get("prompt", self._fallback)
        fuzzy, conf = self._fuzzy(style_lower)
        if fuzzy and conf >= self.CONFIDENCE_THRESHOLD:
            log.info(f"StyleResolver: fuzzy match '{fuzzy}' (conf {conf:.2f})")
            return fuzzy, self._styles[fuzzy].get("prompt", self._fallback)
        if llm_expander:
            try:
                expanded = llm_expander(cleaned)
                if expanded and len(expanded) > 20:
                    return "llm_expanded", expanded
            except Exception as e:
                log.warning(f"StyleResolver: LLM expansion failed: {e}")
        log.warning(f"StyleResolver: unknown '{raw_style}' -- using fallback")
        return "fallback", self._fallback

    def _exact(self, style_lower):
        padded = f" {style_lower} "
        for key, entry in self._styles.items():
            all_triggers = entry.get("keywords", []) + entry.get("aliases", [])
            for trigger in all_triggers:
                if f" {str(trigger).lower()} " in padded:
                    return key
        return None

    def _fuzzy(self, style_lower):
        input_tokens = set(re.findall(r"[a-z0-9]+", style_lower))
        if not input_tokens:
            return None, 0.0
        best_key, best_score = None, 0.0
        for key, entry in self._styles.items():
            all_triggers = entry.get("keywords", []) + entry.get("aliases", [])
            for trigger in all_triggers:
                trigger_tokens = set(re.findall(r"[a-z0-9]+", str(trigger).lower()))
                if not trigger_tokens:
                    continue
                inter = input_tokens & trigger_tokens
                union = input_tokens | trigger_tokens
                jaccard = len(inter) / len(union) if union else 0
                coverage = len(inter) / len(input_tokens) if input_tokens else 0
                score = jaccard * 0.4 + coverage * 0.6
                if score > best_score:
                    best_score, best_key = score, key
        return best_key, best_score
