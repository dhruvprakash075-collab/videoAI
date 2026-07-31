"""vision.py - VisionMixin: image review + vision-doc analysis with caching.

Extracted verbatim from ``agents/director_agent.py`` (WS-4 mixin split).
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class VisionMixin:
    """Vision: identity-critical image review, vision cache, story analysis."""

    # Provided by the composing DirectorAgent (facade __init__ + sibling mixins)
    llm: Any
    llm_config: Any
    _call_ollama: Any
    _parse_json: Any
    _resolve_model: Any
    _prompt: Any
    _validate_vision_doc: Any

    def review_important_image(
        self, image_path: str, prompt: str, char_presence: dict | None, project_id: str
    ) -> dict:
        """Review an identity-critical image and decide how to store it.

        Reads the image file and passes it as base64 to a vision-capable model
        if available, otherwise falls back to text-only analysis with metadata.
        """
        log.info(f"[DIRECTOR] Reviewing important image: {image_path}")

        import base64

        img_file = Path(image_path)
        if not img_file.exists():
            log.warning(f"[DIRECTOR] Image not found: {image_path} — auto-approving")
            return {"decision": "approve", "reason": "file_not_found", "locked": False}

        try:
            img_file.resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            log.warning(f"[DIRECTOR] Image path escapes project root: {image_path}")
            return {"decision": "approve", "reason": "path_escape", "locked": False}

        try:
            from PIL import Image as _PILImage
            with _PILImage.open(img_file) as pil_img:
                width, height = pil_img.size
                fmt = pil_img.format or "PNG"
        except Exception:
            width, height, fmt = 0, 0, "unknown"

        try:
            image_b64 = base64.b64encode(img_file.read_bytes()).decode("utf-8")
        except Exception as e:
            log.warning(f"[DIRECTOR] Could not read image for vision: {e}")
            image_b64 = ""

        # Try vision-capable chat endpoint first
        if image_b64 and self._is_vision_model():
            try:
                return self._review_with_vision(image_b64, prompt, char_presence, width, height, fmt)
            except Exception as e:
                log.warning(f"[DIRECTOR] Vision review failed ({e}) — falling back to text")
                return {"decision": "approve", "reason": f"vision_fallback: {e}", "locked": False}

        # Text-only fallback with image metadata
        meta = f"File: {img_file.name}, Format: {fmt}, Size: {width}x{height}"
        if char_presence:
            dom_char = max(char_presence, key=lambda k: char_presence.get(k, 0) or 0)
            meta += f", Dominant char: {dom_char}"
        prompt_text = (
            f"You are the Creative Director. Review this image for character consistency.\n"
            f"Image: {meta}\n"
            f"Generated Text Prompt: {prompt}\n"
            f"Characters Present: {char_presence}\n"
            f"Project: {project_id}\n\n"
            f"Decide if this asset should be: approved, rejected, stored as LoRA candidate, "
            f"or used as an IP-Adapter reference. Return JSON: "
            f'{{"decision": "approve|reject|lora_candidate|ip_ref", "reason": "...", "locked": bool}}'
        )
        res = self._call_ollama(prompt_text, format_json=True)
        return self._parse_json(res, {"decision": "approve", "reason": "Auto-approved (text fallback)", "locked": False})

    def _is_vision_model(self) -> bool:
        """Check if the director model supports vision (based on model name heuristic)."""
        model = self._resolve_model("director").lower()
        vision_indicators = ("llava", "bakllava", "minicpm", "cogvlm", "internvl", "qwen2-vl", "gpt-4o", "gpt-4-vision", "gemini", "claude")
        return any(ind in model for ind in vision_indicators)

    def _review_with_vision(
        self, image_b64: str, prompt: str, char_presence: dict | None,
        width: int, height: int, fmt: str
    ) -> dict:
        """Call the Ollama chat API with an embedded base64 image. Classification: local service URL."""
        import urllib.request as _ur
        host, timeout, _ = self.llm._ollama_opts()
        # SSRF: validate local service URL before constructing request
        from utils.url_security import build_validated_url, validate_service_base_url
        validated_host = validate_service_base_url(host)
        model = self._resolve_model("director")
        url = build_validated_url(validated_host, "/api/chat")
        payload = json.dumps({
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Review this character image ({width}x{height}, {fmt}) for visual consistency.\n"
                        f"Generated Prompt: {prompt}\n"
                        f"Characters Present: {char_presence}\n"
                        f"Decide: approve, reject, lora_candidate (perfect face/body reference), "
                        f"or ip_ref (useful for IP-Adapter). "
                        f"Set locked=true if this outfit/identity must never change.\n"
                        f"Return JSON: {{\"decision\": \"...\", \"reason\": \"...\", \"locked\": bool}}"
                    ),
                    "images": [image_b64],
                }
            ],
            "stream": False,
            "options": {"temperature": 0.2},
        }).encode()
        req = _ur.Request(url, data=payload, headers={"Content-Type": "application/json"})
        from utils.url_security import open_validated_url

        with open_validated_url(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        raw = body.get("message", {}).get("content", "")
        return self._parse_json(raw, {"decision": "approve", "reason": "vision_reviewed", "locked": False})

    def _vision_cache_path(self) -> "Path":
        """Path to the vision analysis cache file."""

        cdir = (
            self.llm_config.get("cache_dir", "cache")
            if isinstance(self.llm_config, dict)
            else "cache"
        )

        cache_dir = Path(cdir)

        cache_dir.mkdir(parents=True, exist_ok=True)

        return cache_dir / "vision_cache.json"

    def _load_vision_cache(self) -> dict:
        """Load cached vision analyses, keyed by normalized topic."""

        cp = self._vision_cache_path()

        try:
            if cp.exists():
                return json.loads(cp.read_text(encoding="utf-8"))

        except Exception as exc:
            log.debug(f"[DIRECTOR] Ignoring unreadable vision cache {cp}: {exc}")

        return {}

    def _save_vision_cache(self, cache: dict) -> None:
        """Persist vision cache to disk."""

        try:
            self._vision_cache_path().write_text(json.dumps(cache, indent=2), encoding="utf-8")

        except Exception as e:
            log.warning(f"[DIRECTOR] Failed to persist vision cache: {e}")

    def _topic_key(self, topic: str) -> str:
        """Normalise a topic into a cache key."""
        return re.sub(r"[^a-z0-9_]", "_", topic.strip().lower())[:80]

    def analyze_with_research(
        self,
        topic: str,
        research: dict,
        target_duration_min: int = 10,
        content_text: str | None = None,
    ) -> dict:
        """Phase 2: Director analyzes story + research. Returns vision doc."""

        # Reset the duration estimate for this run

        self._last_estimated_minutes = 0

        # Check cache first
        from utils.vision_cache import VisionCache

        cache = VisionCache(
            cache_dir=str(
                Path(self.llm_config.get("cache_dir", "cache"))
                if isinstance(self.llm_config, dict)
                else "cache"
            ),
            # P2-12 fix: thread force_refresh so the caller can bypass a stale vision doc.
            force_refresh=getattr(self, "_force_refresh", False),
        )
        cached = cache.get(topic, content_text=content_text or "")
        if cached is not None:
            return cached

        log.info("[DIRECTOR] Phase 2/5: Analyzing story...")

        research_text = research.get("combined_summary", "")

        content_text = content_text or ""

        # Auto-calculate video duration from uploaded content density

        if content_text and len(content_text) > 500:
            total_words = len(content_text.split())

            estimated_minutes = max(5, int((total_words / 150) * 1.15))

            self._last_estimated_minutes = estimated_minutes

            log.info(
                f"[DIRECTOR] Content analysis: {total_words} words -> approx {estimated_minutes} min"
            )

            content_block = (
                f"The following story is present:\n{content_text[:3000]}\n"
                f"Word count: {total_words} words.\n"
                f"You MUST include a 'recommended_duration_min' field in your JSON output.\n"
                f"Decide the optimal video duration based on:\n"
                f"  - Content length and complexity\n"
                f"  - Number of characters and story arcs\n"
                f"  - Pacing needs (slow lore vs fast action)\n"
                f"  - Emotional beats and dramatic structure\n"
                f"  - Whether the story has natural break points\n"
                f"Rules:\n"
                f"  - Minimum: 5 minutes\n"
                f"  - Maximum: 180 minutes (3 hours)\n"
                f"  - For short stories (<1000 words): 5-15 min\n"
                f"  - For medium stories (1000-5000 words): 15-45 min\n"
                f"  - For long stories (5000-15000 words): 45-90 min\n"
                f"  - For epic stories (15000+ words): 90-180 min\n"
                f"  - Prioritize story completeness over arbitrary length\n"
                f"Estimated from word count: ~{estimated_minutes} min (use as reference, not hard rule)."
            )

        else:
            content_block = ""

        research_block_parts = []
        if research_text:
            research_block_parts.append(f"Research:\n{research_text[:1000]}")
        if content_block:
            research_block_parts.append(content_block)
        research_block = "\n\n".join(research_block_parts)

        prompt = self._prompt(
            "vision_document",
            topic=topic,
            target_duration=target_duration_min,
            research_block=research_block,
        ) or (
            f"You are the Creative Director for a narrative video production.\n"
            f"Analyze this story topic: {topic}\n"
            f"Research: {research_text[:1000]}\n"
            f"{content_block}\n"
            f"Output JSON with: characters, visual_style, theme, emotions, pacing, "
            f"shot_distribution, tts_recommendation, subtitle_style, "
            f"ambiguity_detected, ambiguity_question, ambiguity_fields, recommendations, "
            f"recommended_duration_min.\n"
            f"recommended_duration_min: the optimal video length in minutes based on the content analysis.\n"
            f"Output ONLY the JSON."
        )

        res = self._call_ollama(
            prompt, format_json=True, seed=int(hashlib.sha256(topic.encode()).hexdigest()[:8], 16)
        )

        vision_doc = self._validate_vision_doc(
            self._parse_json(
                res,
                {
                    "characters": [
                        {
                            "name": "Protagonist",
                            "description": "The central character",
                            "voice": "clear",
                        }
                    ],
                    "visual_style": "hybrid 2d anime visual novel style",
                    "theme": topic,
                    "emotions": "tension, curiosity",
                    "pacing": "moderate",
                    "shot_distribution": {
                        "establishing": 0.10,
                        "environment": 0.20,
                        "character_medium": 0.35,
                        "character_closeup": 0.20,
                        "emotional_detail": 0.10,
                        "action": 0.05,
                    },
                    "tts_recommendation": "supertonic",
                    "subtitle_style": {
                        "format": "classic",
                        "size": "small",
                        "color": "white",
                        "position": "bottom",
                    },
                    "ambiguity_detected": False,
                    "ambiguity_question": "",
                    "ambiguity_fields": [],
                    "recommendations": [],
                    "recommended_duration_min": 10,
                    "topic": topic,
                },
            )
        )

        # Cache the result

        _input_hash = hashlib.sha256(
            (
                topic
                + json.dumps(vision_doc if isinstance(vision_doc, dict) else {}, sort_keys=True)
            ).encode()
        ).hexdigest()[:12]
        vision_doc["source_hash"] = _input_hash
        cache.set(topic, vision_doc, content_text=content_text or "")

        log.info(
            f"[DIRECTOR] Vision doc: {len(vision_doc.get('characters', []))} character(s), "
            f"style={vision_doc.get('visual_style')}, pacing={vision_doc.get('pacing')}"
        )

        return vision_doc
