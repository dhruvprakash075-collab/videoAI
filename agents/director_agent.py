"""director_agent.py - Director Agent

The Director acts as the creative visionary, analyzing stories and consulting
users on production decisions before the video pipeline runs.

Module map (2026-06-02 refactor — God module split)
----------------------------------------------------
* ``UIState`` and ``_devanagari_ratio`` live in ``agents/ui_state.py``.
  Re-exported here for backward compat.
* LLM client methods (``_call_ollama*``, ``_prewarm_ollama``, ``_resolve_model``,
  ``_ollama_opts``) live in ``agents/llm_client.py`` as the
  ``DirectorLlmClient`` class. ``DirectorAgent.__init__`` constructs one
  in ``self.llm``; thin delegation shims preserve the public method names
  that tests and other modules rely on.
"""

import json
import logging
import re
import sys
import threading
import time
from pathlib import Path

# Ensure project root is in sys.path for top-level imports (style_resolver, utils.*)
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import hashlib
from typing import Any

log = logging.getLogger(__name__)

# Re-exports for backward compat (UIState + Devanagari helper live in ui_state.py).
from utils.utils import extract_json

from .hinglish_glossary import hinglish_ratio, protect_hinglish, restore_hinglish
from .llm_client import DirectorLlmClient
from .ui_state import UIState, _devanagari_ratio

# ── DirectorAgent ──


class DirectorAgent:
    """Creative Director LLM Agent.



    Orchestrates story analysis, user consultation, writer collaboration,

    and runtime config generation for the narrative video engine.

    """

    _prompts = {}  # class-level cache for loaded YAML prompts

    def __init__(self, llm_config: dict, memory=None):

        self.llm_config = llm_config

        self.memory = memory

        # LLM transport lives in DirectorLlmClient (agents/llm_client.py).
        # Internal ``self._call_ollama*`` calls route through this object via
        # the thin delegation shims below. The 14 internal call sites and the
        # ``test_director_call_ollama_*`` tests keep working unchanged.
        self.llm = DirectorLlmClient(llm_config)

        self._last_estimated_minutes = 10
        self._last_segment_count = 0
        # P2-12: set True by callers (e.g. run_pre_production) to bypass a stale
        # cached vision doc. Declared here so it's a known instance attribute.
        self._force_refresh = False

        if not DirectorAgent._prompts:
            self._load_prompts()

    # ── LLM Interface (delegation shims → DirectorLlmClient) ────────────────
    # The actual implementations live in agents/llm_client.py. These shims
    # preserve the existing ``self._call_ollama(...)`` API for the 14 internal
    # call sites and the ``test_director_call_ollama_*`` tests.

    def _resolve_model(self, model_type: str = "director") -> str:
        return self.llm._resolve_model(model_type)

    def _ollama_opts(self) -> tuple:
        return self.llm._ollama_opts()

    def _call_ollama(
        self,
        prompt: str,
        model_type: str = "director",
        format_json: bool = False,
        seed: int | None = None,
    ) -> str:
        return self.llm._call_ollama(
            prompt, model_type=model_type, format_json=format_json, seed=seed
        )

    def _call_ollama_chat(
        self,
        prompt: str,
        model_type: str = "translator",
        system_msg: str = "You are a professional translator. "
        "Translate the given text to Hindi (Devanagari script). "
        "Output only the translation.",
    ) -> str:
        return self.llm._call_ollama_chat(prompt, model_type=model_type, system_msg=system_msg)

    def _call_ollama_streaming(self, prompt: str, label: str = "") -> str:
        return self.llm._call_ollama_streaming(prompt, label=label)

    def _prewarm_ollama(self) -> None:
        self.llm._prewarm_ollama()

    def _parse_json(self, text: str, fallback: dict | None = None) -> dict:
        """Extract JSON from LLM response. Returns fallback on failure."""

        if not text:
            return fallback or {}

        try:
            result = extract_json(text)
            if isinstance(result, dict):
                return result
        except Exception:
            log.debug("JSON parse failed, using fallback")

        return fallback or {}

    # ── User Consultation ──

    def consult_user(
        self, question: str, options: list[str] | None = None, allow_custom: bool = True
    ) -> str:
        """Consult user via web UI or CLI fallback."""

        # A6: --yes flag — return default without prompting
        if getattr(UIState, "auto_accept", False):
            _default = options[0] if options else "Proceed as planned."
            log.info(f"[DIRECTOR] --yes flag: auto-accepting default for: {question[:60]}")
            return _default

        if hasattr(UIState, "is_ui_mode") and UIState.is_ui_mode:
            UIState.add_log(f"[DIRECTOR PAUSE] {question}")

            UIState.active_question = question

            UIState.status = "paused"

            UIState.pause_event.clear()

            if not UIState.pause_event.wait(timeout=300):
                log.warning("[DIRECTOR] Web UI timeout after 300s — proceeding with default")
                UIState.add_degradation(0, "consult_user", "Web UI timeout after 300s — proceeding with default")

                UIState.status = "running"

                UIState.active_question = None

                return options[0] if options else "Proceed as planned."

            UIState.status = "running"

            UIState.active_question = None

            reply = UIState.user_reply

            UIState.user_reply = None

            return reply or "Proceed as planned."

        def _safe_input(prompt=""):
            try:
                if not sys.stdin.isatty():
                    return None
            except Exception:
                pass

            try:
                return input(prompt)

            except (EOFError, KeyboardInterrupt):
                print()

                return None

            except Exception:
                # Broken/redirected stdin (e.g. background process on Windows can
                # raise OSError [Errno 22] instead of EOFError). Treat as no input.
                return None

        # Non-interactive run (background process, piped/redirected stdin, no TTY):
        # auto-proceed with the default instead of printing a menu nobody can answer.
        # This prevents the consultation prompts from spinning when run unattended.
        _interactive = True
        try:
            _interactive = sys.stdin.isatty()
        except Exception:
            _interactive = False
        if not _interactive:
            _default = options[0] if options else "Proceed as planned."
            log.info(f"[DIRECTOR] Non-interactive — auto-selecting default for: {question[:60]}")
            return _default

        sep = "=" * 60

        print(f"\n{sep}")

        print("  DIRECTOR CONSULTATION")

        print(sep)

        print(f"\n  {question}\n")

        if options:
            shown = options[:12]  # Show first 12, paginate beyond

            for idx, opt in enumerate(shown, 1):
                print(f"  [{idx}] {opt}")

            if len(options) > 12:
                remaining = len(options) - 12

                print(
                    f"  [{len(shown) + 1}] Show {remaining} more option{'s' if remaining > 1 else ''}..."
                )

            if allow_custom:
                print("  [0] Custom (type your own)")

            print()

            _attempts = 0
            while True:
                _attempts += 1
                if _attempts > 50:
                    log.warning("[DIRECTOR] Too many invalid inputs — using default choice.")
                    return options[0] if options else "Proceed as planned."
                try:
                    choice = _safe_input("  Your choice: ")
                    if choice is None:
                        choice = ""

                    choice = choice.strip()

                    if not choice:
                        return options[0] if options else "Proceed as planned."

                    if choice == "0" and allow_custom:
                        custom_input = _safe_input("  Custom input: ")
                        return custom_input.strip() if custom_input else ""

                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(options):
                            return options[idx]
                        print(
                            f"  Invalid choice. Please enter a number between 1 and {len(options)}, or 0 for custom input."
                        )
                    except ValueError:
                        print("  Invalid input. Please enter a number.")
                except Exception as e:
                    log.warning(f"Input error: {e}. Using default choice.")
                    return options[0] if options else "Proceed as planned."

        else:
            reply = _safe_input("  Your response: ")
            if reply is None:
                return "Proceed with default settings."
            reply = reply.strip()

            return reply if reply else "Proceed as planned."

    def consult_fields(self, fields, vision_summary="", timeout=0, allow_regenerate=False):
        """Present multiple choice fields as a single form.



        Each field dict: {"key": str, "label": str, "current": str, "options": [str], "impact": int}

        User answers with "1:2 3:5" format (field_num:choice_num).

        Empty line = accept all defaults.  'r' = regenerate.

        timeout: if >0, auto-proceed after N seconds (headless/CI).

        """

        # A6: --yes flag — return all defaults without prompting
        if getattr(UIState, "auto_accept", False):
            log.info("[DIRECTOR] --yes flag: auto-accepting all field defaults")
            results = {}
            for f in fields:
                opts = f.get("options", [])
                results[f["key"]] = opts[0] if opts else f.get("current", "")
            return results

        if hasattr(UIState, "is_ui_mode") and UIState.is_ui_mode:
            batch_text = "\n".join(
                "[%d] %s (current: %s)" % (i + 1, f["label"], f["current"])
                for i, f in enumerate(fields)
            )

            UIState.active_question = "Multiple decisions needed:\n" + batch_text

            UIState.status = "paused"

            UIState.pause_event.clear()

            if not UIState.pause_event.wait(timeout=300):
                log.warning("[DIRECTOR] Web UI timeout after 300s — proceeding with default")
                UIState.add_degradation(0, "consult_fields", "Web UI timeout after 300s — proceeding with default")

                UIState.status = "running"

                UIState.active_question = None

                return {}

            UIState.status = "running"

            reply = UIState.user_reply or ""

            UIState.user_reply = None

            results = {}

            for line in reply.strip().split("\n"):
                for part in line.replace(",", " ").split():
                    if ":" in part:
                        try:
                            fi, ci = part.split(":", 1)

                            fi, ci = int(fi) - 1, int(ci) - 1

                            if 0 <= fi < len(fields) and 0 <= ci < len(
                                fields[fi].get("options", [])
                            ):
                                results[fields[fi]["key"]] = fields[fi]["options"][ci]

                        except (ValueError, IndexError):
                            pass

            return results

        def _safe_input(prompt=""):
            try:
                if not sys.stdin.isatty():
                    return None
            except Exception:
                pass

            try:
                return input(prompt)

            except (EOFError, KeyboardInterrupt):
                print()

                return ""

        sep = "=" * 60

        print("\n" + sep)

        print("  DIRECTOR CONFIGURATION")

        print(sep)

        if vision_summary:
            print(vision_summary)

            print("  " + sep)

        fields = sorted(fields, key=lambda f: f.get("impact", 0), reverse=True)

        for idx, f in enumerate(fields, 1):
            print("\n  [%d] %s" % (idx, f["label"]))

            print("      Director's pick: " + f["current"])

            opts = f.get("options", [])

            if opts:
                for oi, opt in enumerate(opts, 1):
                    print("      %d. %s" % (oi, opt))

            print("      0. Skip (keep default)")

            if allow_regenerate:
                print("      r. Regenerate suggestions")

        print("\n  Quick mode: type '%d' to accept ALL defaults\n" % (len(fields) + 1))

        if timeout > 0:
            user_input: list = [None]

            def _timer():

                time.sleep(timeout)

                if user_input[0] is None:
                    print("\n  [Timeout] Accepting all defaults.")

                    user_input[0] = str(len(fields) + 1)

            t = threading.Thread(target=_timer, daemon=True)

            t.start()

            try:
                line = (
                    _safe_input(
                        "\n  Format: field:choice (e.g. '1:2 3:5') or Enter for all defaults: "
                    )
                    or ""
                ).strip()

            finally:
                user_input[0] = "done"

        else:
            line = (
                _safe_input("\n  Format: field:choice (e.g. '1:2 3:5') or Enter for all defaults: ")
                or ""
            ).strip()

        results = {}

        if not line or line == str(len(fields) + 1):
            for f in fields:
                opts = f.get("options", [])

                results[f["key"]] = opts[0] if opts else f.get("current", "")

            return results

        if line.lower() == "r" and allow_regenerate:
            return {"_regenerate": True}

        for part in line.replace(",", " ").split():
            part = part.strip()

            if not part or ":" not in part:
                continue

            try:
                fi_str, ci_str = part.split(":", 1)

                fi, ci = int(fi_str) - 1, int(ci_str) - 1

                if 0 <= fi < len(fields):
                    opts = fields[fi].get("options", [])

                    if ci == -1:
                        results[fields[fi]["key"]] = (
                            opts[0] if opts else fields[fi].get("current", "")
                        )

                    elif 0 <= ci < len(opts):
                        results[fields[fi]["key"]] = opts[ci]

            except (ValueError, IndexError):
                continue

        for f in fields:
            if f["key"] not in results:
                opts = f.get("options", [])

                results[f["key"]] = opts[0] if opts else f.get("current", "")

        return results

    # ── Insert consult_on_config body here ──

    def consult_user_stream(
        self, question: str, options: list[str] | None = None, allow_custom: bool = True
    ) -> str:
        """Streaming variant: options appear progressively."""
        if hasattr(UIState, "is_ui_mode") and UIState.is_ui_mode:
            import time as _ts

            UIState.add_log(f"[STREAM] {question}")
            if options:
                for i, opt in enumerate(options[:12]):
                    UIState.add_log(f"[OPTION {i + 1}] {opt}")
                    _ts.sleep(0.1)
        return self.consult_user(question, options, allow_custom)

    @classmethod
    def _load_prompts(cls):
        """Load prompt templates from prompts.yaml."""

        if cls._prompts:
            return

        import yaml  # type: ignore[import-untyped]

        try:
            # prompts.yaml lives at the repo root, not in agents/
            prompts_path = Path(__file__).parent.parent / "prompts.yaml"
            if not prompts_path.exists():
                # Fallback: legacy location alongside this module
                prompts_path = Path(__file__).parent / "prompts.yaml"

            if prompts_path.exists():
                with open(prompts_path, encoding="utf-8") as f:
                    cls._prompts = yaml.safe_load(f) or {}

                log.info(
                    f"[DIRECTOR] Loaded {len(cls._prompts)} prompt templates from {prompts_path}"
                )

        except Exception as e:
            log.warning(f"[DIRECTOR] Failed to load prompts: {e}")

            cls._prompts = {}

    def review_important_image(
        self, image_path: str, prompt: str, char_presence: dict | None, project_id: str
    ) -> dict:
        """Review an identity-critical image and decide how to store it.

        Reads the image file and passes it as base64 to a vision-capable model
        if available, otherwise falls back to text-only analysis with metadata.
        """
        log.info(f"[DIRECTOR] Reviewing important image: {image_path}")

        import base64
        from pathlib import Path

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
            dom_char = max(char_presence, key=char_presence.get)
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
        import json
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
        with _ur.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        raw = body.get("message", {}).get("content", "")
        return self._parse_json(raw, {"decision": "approve", "reason": "vision_reviewed", "locked": False})

    def review_segment_memory(
        self,
        segment_script: str,
        image_plan: dict,
        generated_prompts: list[str],
        current_memory: dict,
        world_state: str,
        generated_images: list[str] | None = None
    ) -> dict:
        """Review the segment and extract long-term memory items."""
        log.info("[DIRECTOR] Performing segment memory review...")

        images_block = ""
        if generated_images:
            images_block = "\nGenerated Images:\n" + "\n".join(f"  - {p}" for p in generated_images) + "\n"

        prompt_text = (
            f"You are the Creative Director. Analyze the segment to extract important visual and story memory.\n"
            f"Segment Script: {segment_script}\n"
            f"Image Plan: {image_plan}\n"
            f"Generated Prompts: {generated_prompts}\n"
            f"{images_block}"
            f"Current Memory: {current_memory}\n"
            f"World State: {world_state}\n\n"
            f"Identify important faces, outfits, jewelry, weapons, lore objects, locations, and story-impacting details.\n"
            f"Return a JSON object with 'memory_items' list. Each item must have:\n"
            f"{{'type': 'costume|face_reference|weapon|jewelry|lore_object|location|symbol_motif|relationship|timeline_change|negative_memory|character_identity|temporary_scene_detail',\n"
            f" 'name': '...', 'owner': '...', 'importance': 'core|high|medium', 'scope': 'project|story',\n"
            f" 'description': '...', 'visual_rules': [], 'negative_rules': [], 'lora_candidate': bool, 'reason': '...'}}"
        )

        res = self._call_ollama(prompt_text, format_json=True)
        return self._parse_json(res, {"memory_items": []})

    def _prompt(self, key: str, **kwargs) -> str:
        """Get a formatted prompt template by key."""

        template = DirectorAgent._prompts.get(key, "")

        if not template:
            return ""

        try:
            safe_kwargs = {}

            for k, v in kwargs.items():
                if isinstance(v, str):
                    safe_kwargs[k] = v.replace("{", "{{").replace("}", "}}")
                else:
                    safe_kwargs[k] = v

            return template.format(**safe_kwargs)

        except KeyError:
            return template

    def _research_cache_path(self, topic: str) -> "Path":
        """Path for cached research results."""

        cdir = (
            self.llm_config.get("cache_dir", "cache")
            if isinstance(self.llm_config, dict)
            else "cache"
        )

        cache_dir = Path(cdir)

        cache_dir.mkdir(parents=True, exist_ok=True)

        return cache_dir / f"research_{re.sub(r'[^a-z0-9_]', '_', topic.strip().lower())[:60]}.json"

    # ── Research & Analysis ──

    def research_story(self, topic: str) -> dict:
        """Search the web for background on this topic."""

        log.info(f"[DIRECTOR] Phase 1/5: Researching '{topic}'...")

        try:
            from utils.web_search import search_story_web

            result = search_story_web(topic)

            summary = result.get("combined_summary", topic)

            raw = result.get("wikipedia_results", []) + result.get("ddg_results", [])

            return {
                "topic": topic,
                "combined_summary": summary,
                "result_count": len(raw),
                "raw_results": raw,
            }

        except ImportError:
            log.warning("[DIRECTOR] web_search module not available, using empty research")

            return {"topic": topic, "combined_summary": topic, "result_count": 0}

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

        except Exception:
            pass

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

    def consult_on_config(self, vision_doc: dict):
        """Phase 3: Present config decisions to user as a single form."""

        log.info("[DIRECTOR] Phase 3/5: Consulting user...")

        # Vision summary header (S8)

        chars = vision_doc.get("characters", [])
        if isinstance(chars, dict):
            chars_list = []
            for name, details in chars.items():
                if isinstance(details, dict):
                    c = details.copy()
                    c.setdefault("name", name)
                else:
                    c = {"name": name, "description": str(details)}
                chars_list.append(c)
            chars = chars_list
        if isinstance(chars, str):
            chars = [{"name": chars}]
        if chars and isinstance(chars[0], str):
            chars = [{"name": c} for c in chars]

        char_names = ", ".join(c.get("name", "?") for c in chars[:4])

        if len(chars) > 4:
            char_names += " +%d more" % (len(chars) - 4)

        vision_summary = (
            "\n  Story: {}\n  Style: {}  |  Pacing: {}  |  Emotions: {}\n  Characters: {}"
        ).format(
            vision_doc.get("theme", "Untitled"),
            vision_doc.get("visual_style", "?"),
            vision_doc.get("pacing", "?"),
            vision_doc.get("emotions", "?"),
            char_names,
        )

        # Ambiguity check

        ambiguity_q = vision_doc.get("ambiguity_question", "")

        if vision_doc.get("ambiguity_detected") and ambiguity_q:
            reply = self.consult_user(
                f"Ambiguity detected: {ambiguity_q}",
                allow_custom=True,
            )

            user_responses = {}

            if reply is not None and reply.strip():
                user_responses["ambiguity_resolution"] = reply.strip()

                log.info(f"[DIRECTOR] User resolved ambiguity: {reply:.80}...")

        else:
            user_responses = {}

        # Questionnaire

        uncertain_fields = vision_doc.get("ambiguity_fields", [])

        q_data = {"fields": {}, "breakdown": {}}

        if uncertain_fields:
            chars_text = "\n".join(
                "  {}: {}".format(c.get("name", "?"), c.get("description", "")) for c in chars[:5]
            )

            current_vals = {f: str(vision_doc.get(f, "not set")) for f in uncertain_fields}

            questionnaire_prompt = self._prompt(
                "consultation_questionnaire",
                theme=vision_doc.get("theme", "?"),
                visual_style=vision_doc.get("visual_style", "?"),
                pacing=vision_doc.get("pacing", "?"),
                emotions=vision_doc.get("emotions", "?"),
                chars_text=chars_text,
                fields_list=", ".join(uncertain_fields),
                current_values=json.dumps(current_vals),
            ) or (
                "You are the Director of a video production.\n"
                "Uncertain about: {}.\n"
                "Current values: {}\n"
                "Output JSON with 'fields' key containing per-field options.\n"
                'Example: {{"fields": {{"visual_style": {{"options": ["gothic", "watercolor", '
                '"bright shonen"]}}}}, "pacing": {{"options": ["slow", "moderate", "fast"]}}}}}}}}\n'
            ).format(", ".join(uncertain_fields), json.dumps(current_vals))

            questionnaire_prompt += (
                "\n\nAlso provide a creative screenwriter breakdown as a 'breakdown' key:\n"
                '{"breakdown": {"segment_count": <int 3-8>, "words_per_segment": <int 100-400>, '
                '"image_count_per_segment": <int 5-12>, "opening_hook_style": "...", "pacing_notes": "..."}}\n'
            )

            # S10: Impact ranking for progressive disclosure

            impact_order = {
                "visual_style": 10,
                "pacing": 9,
                "subtitle_style": 8,
                "tts_engine": 7,
                "narrator_voice": 5,
                "color_palette": 4,
                "music_style": 3,
                "shot_distribution": 2,
                "transition_style": 1,
            }

            max_regenerations = 2

            for regen_attempt in range(max_regenerations + 1):
                q_raw = self._call_ollama(
                    questionnaire_prompt,
                    format_json=True,
                    seed=int(hashlib.sha256(questionnaire_prompt.encode()).hexdigest()[:8], 16),
                )

                q_data = self._parse_json(q_raw, {"fields": {}, "breakdown": {}})

                q_fields = (
                    q_data.get("fields", {}) if isinstance(q_data.get("fields"), dict) else {}
                )

                q_fields_lower = {k.lower(): v for k, v in q_fields.items()}

                field_forms = []

                for field in uncertain_fields:
                    field_key = field.strip().lower()

                    fdata = q_fields_lower.get(field_key, {})

                    options = fdata.get("options", []) if isinstance(fdata, dict) else []

                    if not isinstance(options, list) or len(options) < 2:
                        options = [
                            "Keep as-is: {}".format(vision_doc.get(field, "current setting")),
                            "Something different",
                        ]

                    else:
                        # S4: validate relevance

                        vision_value = str(vision_doc.get(field, "")).lower()

                        vision_words = set(vision_value.split()) if vision_value else set()

                        if vision_words:
                            any_relevant = any(
                                bool(vision_words & set(str(o).lower().split()))
                                for o in options[:3]
                            )

                            if not any_relevant:
                                options = [
                                    "Keep as-is: {}".format(
                                        vision_doc.get(field, "current setting")
                                    ),
                                    *options,
                                ]

                    field_forms.append(
                        {
                            "key": field_key,
                            "label": field.replace("_", " ").title(),
                            "current": str(vision_doc.get(field, "not set")),
                            "options": options,
                            "impact": impact_order.get(field_key, 5),
                        }
                    )

                # S6: Single form. S7: Enter=default. S9: regenerate. S13: timeout.

                import os

                try:
                    timeout = int(os.environ.get("DIRECTOR_TIMEOUT", "0"))

                    timeout = max(0, timeout)  # clamp negative

                except (ValueError, TypeError):
                    timeout = 0

                field_results = self.consult_fields(
                    field_forms,
                    vision_summary=vision_summary,
                    timeout=timeout,
                    allow_regenerate=(regen_attempt < max_regenerations),
                )

                if field_results.get("_regenerate"):
                    log.info(
                        "[DIRECTOR] Regenerating options (attempt %d/%d)"
                        % (regen_attempt + 1, max_regenerations)
                    )

                    continue

                # S12: skip storing fields identical to vision_doc default

                for f_meta in field_forms:
                    k = str(f_meta["key"])

                    choice = str(field_results.get(k, ""))

                    default_val = str(vision_doc.get(k, "not set"))

                    stripped_default = f"Keep as-is: {default_val}"

                    if (
                        choice
                        and choice not in (stripped_default, default_val)
                        and not choice.startswith("Keep as-is")
                    ):
                        user_responses[k] = choice

                        log.info(f"[DIRECTOR] '{k}' = {choice[:60]}")

                    else:
                        log.info(f"[DIRECTOR] '{k}' kept default: {default_val[:60]}")

                break

            log.info(
                "[DIRECTOR] User consulted on %d fields (of %d offered)"
                % (len(user_responses), len(uncertain_fields))
            )

        # Custom instructions

        ci_options_prompt = self._prompt(
            "custom_instructions_options",
            theme=vision_doc.get("theme", "?"),
            visual_style=vision_doc.get("visual_style", "?"),
            pacing=vision_doc.get("pacing", "?"),
            emotions=vision_doc.get("emotions", "?"),
        )

        if not ci_options_prompt:
            ci_options_prompt = "Suggest 3-5 production tweaks. Include No additional instructions as option 1. Output one per line."

        ci_options_prompt += (
            '\n\nOutput JSON: {"options": ["option 1", "option 2", ...]}. '
            "Include exactly 3-5 options."
        )

        ci_raw = self._call_ollama(
            ci_options_prompt,
            format_json=True,
            seed=int(hashlib.sha256(ci_options_prompt.encode()).hexdigest()[:8], 16),
        )

        ci_options = []

        if ci_raw:
            ci_parsed = self._parse_json(ci_raw, {"options": []})

            if isinstance(ci_parsed, dict) and "options" in ci_parsed:
                ci_options = [
                    o for o in ci_parsed["options"] if isinstance(o, str) and len(o.strip()) > 3
                ]

            if not ci_options:
                ci_options = [
                    o.strip().strip(".-") for o in ci_raw.splitlines() if o.strip().strip(".-")
                ]

                ci_options = [o for o in ci_options if len(o) > 5]

        if not ci_options or len(ci_options) < 2:
            ci_options = [
                "No additional instructions \u2014 proceed with Director's plan",
                "Add my own custom instructions",
            ]

        ci_reply = self.consult_user(
            "Any additional instructions for the production?",
            options=ci_options,
            allow_custom=True,
        )

        if (
            ci_reply
            and ci_reply != ci_options[0]
            and "proceed with director" not in ci_reply.lower()
        ):
            # Sanitize: block common prompt injection patterns

            sanitized = ci_reply

            for bad_phrase in [
                "ignore previous instructions",
                "ignore all previous",
                "system prompt",
                "you are now",
                "new instructions:",
            ]:
                if bad_phrase in sanitized.lower():
                    log.warning(
                        f"[DIRECTOR] Prompt injection detected in custom_instructions: '{sanitized[:60]}'"
                    )

                    sanitized = sanitized.replace(bad_phrase, "[FILTERED]")

            user_responses["custom_instructions"] = sanitized

            log.info(f"[DIRECTOR] Custom instructions: {ci_reply:.60}")

        else:
            log.info("[DIRECTOR] No custom instructions")

        # Extract writer breakdown from combined response

        writer_input = (
            q_data.get("breakdown", {}) if isinstance(q_data.get("breakdown"), dict) else {}
        )

        if (
            writer_input
            and "segment_count" in writer_input
            and isinstance(writer_input["segment_count"], (int, float))
        ):
            self._last_segment_count = int(writer_input["segment_count"])

        log.info(
            "[DIRECTOR] Phase 3 complete: %d user changes, %s segments from writer"
            % (len(user_responses), writer_input.get("segment_count", "?"))
        )

        return user_responses, writer_input

    def _validate_vision_doc(self, vision: dict) -> dict:
        """Validate and normalise vision document fields.

        Coerces every field to its expected Python type so downstream code
        never receives a boolean where it expects a string, a dict where it
        expects a list, etc.  This is the single boundary that absorbs all
        LLM type-drift.
        """
        if not isinstance(vision, dict):
            vision = {}

        # ── Ensure required fields exist ──────────────────────────────────
        defaults = {
            "characters": [],
            "visual_style": "anime",
            "theme": "untitled",
            "emotions": "neutral",
            "pacing": "moderate",
            "shot_distribution": {},
            "tts_recommendation": "supertonic",
            "subtitle_style": {},
            "ambiguity_detected": False,
            "ambiguity_question": "",
            "ambiguity_fields": [],
            "recommendations": [],
        }
        for k, v in defaults.items():
            if k not in vision:
                vision[k] = v

        # ── Type coercion — LLM can return wrong types for any field ──────
        # visual_style: must be a plain string (LLM sometimes returns a dict
        # like {tone:..., elements:[...]} which crashes scene_director.py)
        vs = vision.get("visual_style")
        if isinstance(vs, dict):
            _tone = vs.get("tone", "")
            _elems = vs.get("elements", [])
            vision["visual_style"] = (
                f"{_tone}, {', '.join(str(e) for e in _elems)}" if _elems else (_tone or "anime")
            )
        elif not isinstance(vs, str):
            vision["visual_style"] = str(vs) if vs else "anime"

        # tts_recommendation: must be a valid engine ID
        tts_rec = vision.get("tts_recommendation")
        if not isinstance(tts_rec, str):
            vision["tts_recommendation"] = "supertonic"
        else:
            try:
                from audio.audio_proxy import normalize_tts_engine
                vision["tts_recommendation"] = normalize_tts_engine(tts_rec)
            except Exception:
                pass

        # theme / emotions / pacing: must be strings
        for _str_field in ("theme", "emotions", "pacing"):
            val = vision.get(_str_field)
            if not isinstance(val, str):
                vision[_str_field] = str(val) if val else defaults[_str_field]

        # characters: must be a list
        chars = vision.get("characters")
        if not isinstance(chars, list):
            vision["characters"] = [chars] if isinstance(chars, dict) else []

        # ambiguity_detected: must be bool
        ad = vision.get("ambiguity_detected")
        if not isinstance(ad, bool):
            vision["ambiguity_detected"] = bool(ad)

        # ambiguity_fields / recommendations: must be lists
        for _list_field in ("ambiguity_fields", "recommendations"):
            val = vision.get(_list_field)
            if not isinstance(val, list):
                vision[_list_field] = [val] if val else []

        # ── Normalise shot distribution to sum to 1.0 ─────────────────────
        sdist = vision.get("shot_distribution", {})
        if sdist and isinstance(sdist, dict):
            total = sum(v for v in sdist.values() if isinstance(v, (int, float)))
            if total == 0:
                vision["shot_distribution"] = {
                    "establishing": 0.10,
                    "environment": 0.20,
                    "character_medium": 0.35,
                    "character_closeup": 0.20,
                    "emotional_detail": 0.10,
                    "action": 0.05,
                }
            elif abs(total - 1.0) > 0.01:
                for k in sdist:
                    if isinstance(sdist[k], (int, float)):
                        sdist[k] = round(sdist[k] / total, 4)

        return vision

    def consult_with_writer(self, vision_doc: dict, user_responses: dict) -> dict:
        """Phase 4: Collaborate with the LLM Writer for production guidance."""

        log.info("[DIRECTOR] Phase 4/5: Collaborating with Writer...")

        chars = vision_doc.get("characters", [])
        if isinstance(chars, dict):
            chars_list = []
            for name, details in chars.items():
                if isinstance(details, dict):
                    c = details.copy()
                    c.setdefault("name", name)
                else:
                    c = {"name": name, "description": str(details)}
                chars_list.append(c)
            chars = chars_list

        chars_text = "\n".join(
            f"  {c.get('name', '?')}: {c.get('description', '')}" for c in chars[:5]
        )

        user_str = "\n".join(
            f"  {k}: {v}"
            for k, v in user_responses.items()
            if v and str(v).strip() and k != "ambiguity_resolution"
        )

        recommendations = "\n".join(f"  - {r}" for r in vision_doc.get("recommendations", []))

        prompt = self._prompt(
            "writer_breakdown",
            visual_style=vision_doc.get("visual_style", "?"),
            theme=vision_doc.get("theme", "?"),
            emotions=vision_doc.get("emotions", "?"),
            pacing=vision_doc.get("pacing", "?"),
            chars_text=chars_text if chars_text.strip() else "No character details available.",
            user_str=user_str if user_str.strip() else "No user preferences provided.",
            recommendations=recommendations if recommendations.strip() else "No recommendations.",
        )

        if not prompt:
            log.warning("[DIRECTOR] writer_breakdown prompt missing, using fallback")

            import json

            vkeys = ("theme", "visual_style", "pacing", "emotions")

            prompt = (
                "You are the Creative Screenwriter.\n"
                f"Based on vision and user input, suggest scene breakdown.\n"
                f"Vision: {json.dumps({k: v for k, v in vision_doc.items() if k in vkeys})}\n"
                f"User: {json.dumps(user_responses)}\n"
                'Output JSON: {"segment_count": int, "words_per_segment": int, '
                '"image_count_per_segment": int, "opening_hook_style": "...", "pacing_notes": "..."}'
            )

        raw = self._call_ollama(
            prompt, format_json=True, seed=int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)
        )

        writer_input = self._parse_json(
            raw,
            {
                "segment_count": 3,
                "words_per_segment": 390,
                "image_count_per_segment": 6,
                "opening_hook_style": "",
                "pacing_notes": "",
            },
        )

        if "segment_count" in writer_input and isinstance(
            writer_input["segment_count"], (int, float)
        ):
            self._last_segment_count = int(writer_input["segment_count"])

        log.info(
            f"[DIRECTOR] Writer suggests: {writer_input.get('segment_count')} segments, "
            f"{writer_input.get('words_per_segment')} words/seg, "
            f"{writer_input.get('image_count_per_segment')} images/seg"
        )

        return writer_input

    @staticmethod
    def _normalize_shot_distribution(sdist: dict) -> dict:
        """Normalize shot distribution to sum exactly 1.0."""

        defaults = {
            "establishing": 0.10,
            "environment": 0.20,
            "character_medium": 0.35,
            "character_closeup": 0.20,
            "emotional_detail": 0.10,
            "action": 0.05,
        }

        if not sdist or not isinstance(sdist, dict):
            return dict(defaults)

        total = sum(float(v) for v in sdist.values() if isinstance(v, (int, float)))

        if total <= 0:
            return dict(defaults)

        result = {k: round(float(v) / total, 4) for k, v in sdist.items()}

        # Fix rounding: adjust last key to make sum exactly 1.0

        keys = list(result.keys())

        if keys:
            result[keys[-1]] = round(1.0 - sum(result[k] for k in keys[:-1]), 4)

        return result

    def produce_runtime_config(
        self, vision_doc: dict, user_responses: dict, writer_input: dict, mode: str = "full"
    ) -> dict:
        """Phase 5: Merge vision, user, and writer input into config overlay.

        mode: "full" (default), "video-only" (no audio), "voice-only" (no visuals).
        """
        if not isinstance(vision_doc, dict):
            log.error("[DIRECTOR] vision_doc is not a dict -- using empty fallback")
            vision_doc = {}
        if not isinstance(user_responses, dict):
            user_responses = {}
        if not isinstance(writer_input, dict):
            writer_input = {}

        _mode = mode.lower()

        log.info(f"[DIRECTOR] Phase 5/5: Building config overlay (mode={_mode})...")

        # -- Characters --
        characters = vision_doc.get("characters", [])
        if isinstance(characters, dict):
            chars_list = []
            for name, details in characters.items():
                if isinstance(details, dict):
                    c = details.copy()
                    c.setdefault("name", name)
                else:
                    c = {"name": name, "description": str(details)}
                chars_list.append(c)
            characters = chars_list
        if not characters or not isinstance(characters, list):
            characters = [{"name": "Narrator", "description": "Omniscient narrator voice"}]
            log.warning("[DIRECTOR] vision_doc has no characters -- using default Narrator")

        chars_dict = {}
        for c in characters:
            if not c or not isinstance(c, dict):
                continue
            raw_name = str(c.get("name", "")).strip()
            key = re.sub(r"[^a-z0-9_]", "", raw_name.lower().replace(" ", "_"))
            if not key or len(key.replace("_", "").strip()) < 1:
                log.warning(f"[DIRECTOR] Skipping character with empty/whitespace name: {c}")
                continue
            if key in chars_dict:
                log.info(f"[DIRECTOR] Near-duplicate character key '{key}' -- suffixing")
                suffix = 2
                while f"{key}_{suffix}" in chars_dict:
                    suffix += 1
                key = f"{key}_{suffix}"
            chars_dict[key] = {
                "name": c.get("name", key),
                "description": c.get("description", ""),
                "keywords": [],
                "voice_sample": c.get("voice", ""),
            }

        # -- Clamp integers from writer_input --
        seg_count = max(1, min(20, int((writer_input.get("segment_count") or 3) or 3)))
        img_per_seg = max(1, min(30, int((writer_input.get("image_count_per_segment") or 6) or 6)))
        words_per = max(50, min(800, int((writer_input.get("words_per_segment") or 390) or 390)))

        # -- Visual Style (skip for voice-only) --
        if _mode != "voice-only":
            style_response = user_responses.get("visual_style", "")
            if (
                style_response
                and style_response.lower() != str(vision_doc.get("visual_style", "")).lower()
            ):
                from style_resolver import StyleResolver

                _styler = StyleResolver(
                    styles_path=str(Path(__file__).resolve().parent.parent / "styles.yaml")
                )
                _rname, _rprompt = _styler.resolve(style_response)
                style_response = _rprompt
            final_style = style_response or vision_doc.get("visual_style", "")
            if not final_style:
                final_style = "hybrid 2d anime visual novel style"
                log.warning("[DIRECTOR] No visual style set -- falling back to default")
            visual = {"num_scenes": img_per_seg, "style": final_style}
        else:
            visual = {"num_scenes": 0, "style": "n/a"}
            final_style = "n/a"

        # -- Narrator voice mapping --
        narrator_voice = (
            user_responses.get("narrator_voice", "").lower() if _mode != "video-only" else ""
        )
        voice_map = {
            "deep": "deep_male_narrator",
            "dramatic": "ras_dramatic_narrator",
            "news": "news_anchor_clear",
            "calm": "calm_female_smooth",
            "storyteller": "storyteller_warm",
        }
        if narrator_voice:
            mapped = False
            for k, v in voice_map.items():
                if k in narrator_voice:
                    narrator_voice = v
                    mapped = True
                    break
            if not mapped:
                narrator_voice = "storyteller_warm"
        else:
            narrator_voice = "storyteller_warm" if _mode != "video-only" else "none"

        # -- TTS (skip for video-only) --
        if _mode != "video-only":
            tts_response = str(user_responses.get("tts_engine", "")).lower()
            engine = vision_doc.get("tts_recommendation", "supertonic") or "supertonic"
            if tts_response:
                try:
                    from audio.audio_proxy import normalize_tts_engine
                    engine = normalize_tts_engine(tts_response)
                except Exception:
                    pass
            else:
                try:
                    from audio.audio_proxy import normalize_tts_engine
                    engine = normalize_tts_engine(engine)
                except Exception:
                    pass
            tts_lang = (
                self.llm_config.get("tts", {}).get("lang", "hi")
                if isinstance(self.llm_config, dict)
                else "hi"
            )
            tts = {
                "engine": engine,
                "lang": tts_lang,
                "narrator_voice": narrator_voice,
                "omnivoice": {"speed": 0.85, "num_step": 40, "guidance_scale": 2.5},
            }
        else:
            tts = {"engine": "none", "lang": "n/a", "narrator_voice": "none"}

        # -- Script --
        script = {
            "words_per_segment": words_per,
            "dynamic_image_count": True,
            "default_images_per_segment": img_per_seg,
            "shot_distribution": self._normalize_shot_distribution(
                vision_doc.get("shot_distribution", {})
            ),
        }

        # -- Subtitles (skip for voice-only) --
        if _mode != "voice-only":
            sub_response = user_responses.get("subtitle_style", "")
            sub_config = json.loads(json.dumps(vision_doc.get("subtitle_style", {})))
            if sub_response:
                sl = sub_response.lower()
                if "yellow" in sl:
                    sub_config["color"] = "yellow"
                elif "white" in sl or "classic" in sl:
                    sub_config["color"] = "white"
                    sub_config["format"] = "classic"
                if "tiktok" in sl or "centered" in sl:
                    sub_config["format"] = "tiktok"
                if "bottom" in sl:
                    sub_config["position"] = "bottom"
                if "none" in sl or "no subtitles" in sl:
                    sub_config = {"format": "none"}
            if isinstance(sub_config, str):
                sub_config = {"format": sub_config}
            subtitles = {
                "format": sub_config.get("format", "classic")
                if isinstance(sub_config, dict)
                else "classic",
                "font": "Arial",
                "size": {"small": 20, "medium": 28, "large": 38}.get(
                    sub_config.get("size", "small"), 24
                ),
                "color": {
                    "white": "&H00FFFFFF&",
                    "yellow": "&H0000FFFF&",
                    "cyan": "&H00FFFF00&",
                }.get(sub_config.get("color", "white"), "&H00FFFFFF&"),
                "position": sub_config.get("position", "bottom"),
            }
        else:
            subtitles = {
                "format": "none",
                "font": "n/a",
                "size": 0,
                "color": "n/a",
                "position": "n/a",
            }

        # -- Pacing --
        pacing = {
            "style": vision_doc.get("pacing", "moderate"),
            "opening_hook": str(writer_input.get("opening_hook_style") or ""),
            "notes": str(writer_input.get("pacing_notes") or ""),
        }

        # -- Transitions --
        _mood_to_transition = {
            "mysterious": "domain_warp_dissolve",
            "horror": "glitch",
            "action": "light_leak",
            "dramatic": "chromatic_radial_split",
            "epic": "gravitational_lens",
            "calm": "cross_fade",
            "intimate": "cross_fade",
        }
        emotions_text = str(vision_doc.get("emotions", "")).lower()
        pacing_text = str(vision_doc.get("pacing", "")).lower()
        transition = "cross_fade"
        for mood, t in _mood_to_transition.items():
            if mood in emotions_text:
                transition = t
                break
        if transition == "cross_fade":
            transition = _mood_to_transition.get(pacing_text, "cross_fade")
        visualization = {
            "transition": transition if _mode != "voice-only" else "none",
            "transition_blocks": list(set(_mood_to_transition.values())),
        }

        # -- Video --
        seg_dur_min = (
            self.llm_config.get("video", {}).get("segment_duration_min", 2)
            if isinstance(self.llm_config, dict)
            else 2
        )
        # P4-22 fix: use the clamped seg_count (not the potentially stale
        # _last_segment_count from a previous call) to compute est_duration.
        est_duration = seg_count * seg_dur_min
        video = {"total_duration_min": est_duration, "segment_duration_min": seg_dur_min}

        # -- Music style from emotions --
        music_map = {
            "horror": "ambient_dark",
            "tension": "ambient_cinematic",
            "action": "orchestral_heroic",
            "epic": "orchestral_epic",
            "mysterious": "ambient_mystery",
            "calm": "ambient_peaceful",
            "dramatic": "orchestral_dramatic",
            "romantic": "ambient_warm",
        }
        music_style = "ambient_cinematic"
        for e, genre in music_map.items():
            if e in emotions_text:
                music_style = genre
                break
        duck_ratio = 0.3  # Music volume during narration: 30% music, 100% voice

        # -- Production Notes --
        production_notes = {
            "recommendations": vision_doc.get("recommendations", []),
            "custom_instructions": user_responses.get("custom_instructions", ""),
            "theme": vision_doc.get("theme", ""),
            "emotions": vision_doc.get("emotions", ""),
            "music_style": music_style,
            "duck_ratio": duck_ratio,
            "output_mode": _mode,
        }

        known_keys = {
            "visual_style",
            "subtitle_style",
            "tts_engine",
            "ambiguity_resolution",
            "custom_instructions",
            "narrator_voice",
            "music_style",
        }
        user_overrides = {}
        for k, v in user_responses.items():
            if k not in known_keys and v and str(v).strip():
                user_overrides[k] = str(v)
        if user_overrides:
            production_notes["user_overrides"] = user_overrides

        # -- Provenance --
        _provenance = {
            "characters": "vision_doc",
            "visual": "vision+user",
            "tts": "vision+user",
            "script": "writer+vision",
            "subtitles": "vision+user",
            "pacing": "vision+writer",
            "video": "writer+estimate",
            "visualization": "vision",
            "production_notes": "vision+user",
            "music_style": "emotions_map",
            "narrator_voice": "user_response",
        }

        # -- Final Overlay --
        overlay = {
            "_provenance": _provenance,
            "characters": chars_dict,
            "visual": visual,
            "tts": tts,
            "script": script,
            "subtitles": subtitles,
            "pacing": pacing,
            "video": video,
            "visualization": visualization,
            "production_notes": production_notes,
            "_director_vision": {
                "theme": vision_doc.get("theme", ""),
                "emotions": vision_doc.get("emotions", ""),
                "pacing": vision_doc.get("pacing", ""),
                "visual_style": vision_doc.get("visual_style", ""),
            },
        }

        log.info(
            f"[DIRECTOR] Config overlay built: {len(chars_dict)} chars, "
            f"style={visual.get('style', '?')}, "
            f"segments={seg_count}, engine={tts.get('engine', 'none')}, mode={_mode}"
        )
        return overlay

    def consult_on_duration(self, auto_minutes: int) -> dict:
        """Ask user whether to keep, reduce, or adjust video duration."""

        if auto_minutes <= 5:
            return {"accepted": True, "target_minutes": auto_minutes, "action": "keep"}

        h = auto_minutes // 60

        m = auto_minutes % 60

        dur_str = f"{h}h {m}min" if h > 0 else f"{m}min"

        choice = self.consult_user(
            f"Content analysis estimates ~{dur_str} ({auto_minutes} minutes) of video. Would you like to control the duration?",
            options=["Keep estimated duration (Recommended)", "Reduce or adjust the duration"],
        )

        if "keep" in choice.lower() or "recommended" in choice.lower():
            return {"accepted": True, "target_minutes": auto_minutes, "action": "keep"}

        action = self.consult_user("Target duration in minutes?", allow_custom=True)

        try:
            # Guard against non-str / non-numeric types (e.g. empty dict {} on UI timeout)
            if not isinstance(action, (str, int, float)):
                raise TypeError(f"Unexpected action type: {type(action).__name__}")
            target = int(action)

            return {"accepted": True, "target_minutes": target, "action": "adjusted"}

        except (ValueError, TypeError):
            log.warning(
                f"[DURATION] consult_on_duration: could not parse action {action!r} "
                f"(type={type(action).__name__}) — defaulting to 'keep'"
            )
            return {"accepted": True, "target_minutes": auto_minutes, "action": "keep"}

    def suggest_cliffhangers(self, content: str, current_minutes: int) -> list:
        """Suggest 2–3 high-note end points for a cliffhanger-style video cut.

        Returns a list of dicts: [{point: float(0-100), outcome: str, reason: str}]
        One pre-production LLM call, only when the user chooses cliffhanger mode.
        """
        if not content or len(content) < 200:
            log.warning("[DIRECTOR] suggest_cliffhangers: content too short, returning defaults")
            return [
                {
                    "point": 50,
                    "outcome": "Story reaches its midpoint climax",
                    "reason": "Natural midpoint",
                },
                {
                    "point": 75,
                    "outcome": "Story reaches a dramatic turning point",
                    "reason": "Three-quarter climax",
                },
            ]

        prompt = (
            f"You are a creative director analyzing a story for a video production.\n"
            f"The full video would be approximately {current_minutes} minutes.\n"
            f"Identify 2-3 dramatic high-note moments in the story where the video could end "
            f"on a cliffhanger — leaving the audience wanting more.\n\n"
            f"Story excerpt (first 3000 chars):\n{content[:3000]}\n\n"
            f"For each cliffhanger point output JSON:\n"
            f'{{"cliffhangers": [{{"point": <0-100 percent through story>, '
            f'"outcome": "<one sentence describing the dramatic moment>", '
            f'"reason": "<why this is a good cliffhanger>"}}]}}\n'
            f"Output ONLY the JSON. Provide exactly 2-3 options."
        )

        try:
            raw = self._call_ollama(prompt, format_json=True)
            parsed = self._parse_json(raw, {"cliffhangers": []})
            cliffs = parsed.get("cliffhangers", [])
            # Validate and clean
            result = []
            for c in cliffs:
                if isinstance(c, dict) and "point" in c and "outcome" in c:
                    point = max(10, min(95, float(c["point"])))
                    result.append(
                        {
                            "point": point,
                            "outcome": str(c.get("outcome", "Dramatic moment"))[:120],
                            "reason": str(c.get("reason", ""))[:120],
                        }
                    )
            if len(result) >= 2:
                log.info(f"[DIRECTOR] Cliffhanger options: {len(result)} points")
                return sorted(result, key=lambda x: x["point"])
        except Exception as e:
            log.warning(f"[DIRECTOR] suggest_cliffhangers LLM call failed: {e}")

        # Fallback
        return [
            {
                "point": 50,
                "outcome": "Story reaches its midpoint climax",
                "reason": "Natural midpoint",
            },
            {
                "point": 75,
                "outcome": "Story reaches a dramatic turning point",
                "reason": "Three-quarter climax",
            },
        ]

    def compact_story(self, content: str, target_minutes: int, original_minutes: int) -> str:
        """Condense story text to fit a target video duration.

        Uses the Director model to intelligently compress the narrative while
        preserving key characters, plot points, and emotional beats.
        One pre-production LLM call, only when the user chooses compact mode.
        """
        if not content or len(content) < 100:
            return content

        if target_minutes >= original_minutes:
            log.info("[DIRECTOR] compact_story: target >= original, no compaction needed")
            return content

        ratio = target_minutes / max(1, original_minutes)
        target_words = int(len(content.split()) * ratio)

        log.info(
            f"[DIRECTOR] Compacting story: {original_minutes}min → {target_minutes}min "
            f"(ratio={ratio:.2f}, target ~{target_words} words)"
        )

        prompt = (
            f"You are a skilled story editor condensing a narrative for video production.\n"
            f"The original story is approximately {original_minutes} minutes of video.\n"
            f"Condense it to fit {target_minutes} minutes while:\n"
            f"  - Preserving all main characters and their key traits\n"
            f"  - Keeping the core plot arc and emotional journey\n"
            f"  - Maintaining the most dramatic and impactful moments\n"
            f"  - Removing subplots, repetition, and minor details\n"
            f"  - Targeting approximately {target_words} words\n\n"
            f"ORIGINAL STORY:\n{content[:8000]}\n\n"
            f"Output ONLY the condensed story text, no commentary or labels."
        )

        try:
            compacted = self._call_ollama(prompt, model_type="director")
            if compacted and len(compacted.split()) > 50:
                log.info(
                    f"[DIRECTOR] Story compacted: {len(content.split())} → "
                    f"{len(compacted.split())} words"
                )
                return compacted
            log.warning("[DIRECTOR] compact_story returned empty/short result — using original")
            return content
        except Exception as e:
            log.warning(f"[DIRECTOR] compact_story LLM call failed: {e} — using original")
            return content

    # ── User Decision Prompts ──

    def ask_cache_ttl(self) -> None:
        """Ask user for cache TTL preference."""

        pass

    def ask_search_online(self) -> bool:
        """Ask user whether to search online for research."""

        choice = self.consult_user(
            "Search online for story context?", options=["No, use story only", "Yes, search online"]
        )

        return "yes" in choice.lower()

    def ask_create_from_scratch(self, topic: str) -> tuple:
        """Ask user if they want to create a story from scratch."""

        choice = self.consult_user(
            f"Create original story for '{topic}'?",
            options=["No, I have a story", "Yes, create from scratch"],
        )

        if "yes" in choice.lower():
            notes = self.consult_user("Any notes for the story?", allow_custom=True)

            return True, notes

        return False, ""

    # ── Story Generation ──

    def _sync_memory_to_worldstate(self, topic: str, config: dict) -> None:
        """Sync character/lore to world state for continuity."""
        from pathlib import Path

        from memory.memory import WorldState

        ck_dir = Path(config.get("checkpoint", {}).get("dir", "studio_checkpoints"))
        ws = WorldState(topic=topic, checkpoint_dir=ck_dir)

        # Add config characters
        for _c_key, c_data in config.get("characters", {}).items():
            name = c_data.get("name", "")
            desc = c_data.get("description", "")
            if name:
                ws._data.setdefault("characters", {})
                ws._data["characters"][name] = {
                    "first_seen_seg": 0,
                    "moods_seen": [],
                    "status": "active",
                    "description": desc,
                }
                fact = f"{name}: {desc[:150]}" if desc else f"Character: {name}"
                if fact not in ws._data.get("world_facts", []):
                    ws._data.setdefault("world_facts", []).append(fact)

        # Add production notes/recommendations
        p_notes = config.get("production_notes", {})
        if isinstance(p_notes, dict):
            for rec in p_notes.get("recommendations", []):
                if rec and rec not in ws._data.get("world_facts", []):
                    ws._data.setdefault("world_facts", []).append(f"[Director] {rec}")

        ws._save()

    def invent_story(self, topic: str, user_notes: str, force_refresh: bool = False) -> str:
        """Generate an original story from scratch.

        A5: Caches the invented story to cache/story_{topic_hash}.json so the same
        topic doesn't pay the LLM cost twice. Pass force_refresh=True or use
        --no-resume to bypass the cache.
        """
        import hashlib as _hs
        import json as _js

        # A5: check cache first
        _cache_enabled = False
        try:
            _cfg = self.llm_config if isinstance(self.llm_config, dict) else {}
            _cache_enabled = _cfg.get("cache", {}).get("cache_invented_story", True)
        except Exception:
            pass

        if _cache_enabled and not force_refresh:
            _topic_hash = _hs.sha256(topic.strip().lower().encode()).hexdigest()[:12]
            _cache_dir = Path(
                self.llm_config.get("cache_dir", "cache")
                if isinstance(self.llm_config, dict)
                else "cache"
            )
            _cache_dir.mkdir(parents=True, exist_ok=True)
            _cache_path = _cache_dir / f"story_{_topic_hash}.json"
            try:
                _cache_path.resolve().relative_to(_cache_dir.resolve())
            except ValueError:
                log.warning(f"[DIRECTOR] A5: cache path escapes cache dir: {_cache_path}")
                _cache_path = None
            if _cache_path and _cache_path.exists():
                try:
                    _cached = _js.loads(_cache_path.read_text(encoding="utf-8"))
                    _story = _cached.get("story", "")
                    if _story:
                        log.info(
                            f"[DIRECTOR] A5: story cache hit for '{topic[:40]}' ({len(_story.split())} words)"
                        )
                        return _story
                except Exception as _ce:
                    log.debug(f"[DIRECTOR] A5: cache read failed ({_ce}), regenerating")

        prompt = self._prompt("invent_story", topic=topic, notes=user_notes) or (
            f"Create a short dramatic story about: {topic}. {user_notes}\n"
            f"Length: ~500 words. Include 2-3 characters and a clear arc."
        )

        res = self._call_ollama(prompt)

        log.info(f"[DIRECTOR] Story invented: {len(res.split())} words")

        # A5: write to cache
        if _cache_enabled and res:
            try:
                _topic_hash = _hs.sha256(topic.strip().lower().encode()).hexdigest()[:12]
                _cache_dir = Path(
                    self.llm_config.get("cache_dir", "cache")
                    if isinstance(self.llm_config, dict)
                    else "cache"
                )
                _cache_dir.mkdir(parents=True, exist_ok=True)
                _cache_path = _cache_dir / f"story_{_topic_hash}.json"
                try:
                    _cache_path.resolve().relative_to(_cache_dir.resolve())
                except ValueError:
                    log.warning(f"[DIRECTOR] A5: cache write path escapes cache dir: {_cache_path}")
                    raise
                _cache_path.write_text(
                    _js.dumps({"topic": topic, "story": res}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                log.info(f"[DIRECTOR] A5: story cached to {_cache_path.name}")
            except Exception as _cwe:
                log.debug(f"[DIRECTOR] A5: cache write failed ({_cwe})")

        return res

    def read_story(self, full_script: str) -> dict[str, Any]:
        """Parse a full story script into structured segments."""
        if not full_script:
            return {"segments": [], "total_words": 0}

        # Try to find splitters like [Segment 1], Segment 1:, Part 1:, ## Part 1, etc.
        pattern = r"(?:\[Segment\s+\d+\]|Segment\s+\d+:|\[Part\s+\d+\]|Part\s+\d+:|##\s+(?:Part|Segment)\s+\d+)"
        splits = re.split(pattern, full_script, flags=re.IGNORECASE)
        headers = re.findall(pattern, full_script, flags=re.IGNORECASE)

        segments = []
        if len(splits) > 1:
            # The first part before any header might be empty or preamble
            splits[0].strip()
            for idx, part in enumerate(splits[1:]):
                header = headers[idx] if idx < len(headers) else f"Part {idx + 1}"
                text = part.strip()
                if text:
                    segments.append(
                        {"header": header, "text": text, "word_count": len(text.split())}
                    )
        else:
            # Split by double newlines (paragraphs)
            paragraphs = [p.strip() for p in full_script.split("\n\n") if p.strip()]
            current_seg = []
            current_word_count = 0
            seg_idx = 1
            for p in paragraphs:
                p_words = len(p.split())
                if current_word_count + p_words > 250 and current_seg:
                    text = "\n\n".join(current_seg)
                    segments.append(
                        {
                            "header": f"Segment {seg_idx}",
                            "text": text,
                            "word_count": current_word_count,
                        }
                    )
                    seg_idx += 1
                    current_seg = [p]
                    current_word_count = p_words
                else:
                    current_seg.append(p)
                    current_word_count += p_words
            if current_seg:
                text = "\n\n".join(current_seg)
                segments.append(
                    {"header": f"Segment {seg_idx}", "text": text, "word_count": current_word_count}
                )

        # Fallback if no segments resolved
        if not segments:
            segments = [
                {"header": "Segment 1", "text": full_script, "word_count": len(full_script.split())}
            ]

        total_words = sum(int(s["word_count"]) for s in segments)

        # Estimate last estimated minutes based on segment count
        self._last_estimated_minutes = len(segments)

        return {"segments": segments, "total_words": total_words, "theme": "Untitled Story"}

    def define_pacing_and_length(self, vision_doc: dict) -> int:
        """Determine pacing and target length from vision doc."""

        return self._last_estimated_minutes

    def translate_to_devanagari(
        self, english_script: str, segment_plan: dict, context: str = ""
    ) -> str | None:
        """Translate English narration to MODERN spoken Hindi (Devanagari),
        with ~25-30% common English words kept as Hinglish (English written in
        Devanagari, e.g. problem -> प्रॉब्लम) via a static glossary.

        sarvam-translate is a pure translation model -- it translates EVERYTHING
        in the user message. So we send ONLY the (token-protected) English script
        as user content and keep all steering in the system message.

        Pipeline: protect glossary words as @@N@@ tokens -> translate -> restore
        tokens to their Devanagari spellings. Tokens survive sarvam untouched
        (verified by diagnostic). Returns Devanagari Hindi, or English on failure.
        """
        mood = segment_plan.get("mood", "mysterious")

        system_msg = (
            "You are a translator who writes MODERN, casual, everyday spoken Hindi "
            "for YouTube narration -- the way young people actually talk today. "
            "Use simple common words, NOT literary, Sanskritized, or archaic Hindi. "
            "For names and common English terms, use pronunciation-friendly phonetic "
            "Devanagari so a Hindi TTS voice says the English word naturally; never replace "
            "a familiar English term with an awkward formal Hindi translation. "
            "Write everything in Devanagari script. Preserve dramatic punctuation "
            "(... ! ? --). "
            "Keep any token like @@0@@ EXACTLY as-is -- do not translate, renumber, "
            "add spaces inside, or alter these tokens in any way. "
            "Output ONLY the translation, no commentary, no labels."
        )

        # Protect glossary words BEFORE translation so they return as
        # English-in-Devanagari (Hinglish) rather than literary Hindi.
        protected, token_map = protect_hinglish(english_script)
        log.info(
            f"[DIRECTOR] Translating segment to Devanagari "
            f"(mood={mood}, {len(english_script)} chars, "
            f"{len(token_map)} Hinglish words ~{hinglish_ratio(english_script, token_map):.0%})..."
        )

        def _translate_once(sys_msg: str) -> str:
            raw = self._call_ollama_chat(
                protected, model_type="translator", system_msg=sys_msg
            )
            if not raw:
                return ""
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"<\|.*?\|>", "", raw).strip()
            return restore_hinglish(raw, token_map)

        try:
            translated = _translate_once(system_msg)
            if not translated:
                log.warning("[DIRECTOR] Translation returned empty -- using original English")
                return None

            # Some translation models echo/translate the system prompt before the
            # requested narration. A large expansion is the reliable boundary
            # signal: retry without steering, then fail closed rather than speak
            # instructions to the audience.
            _max_translation_chars = max(len(english_script) + 250, int(len(english_script) * 1.4))
            if len(translated) > _max_translation_chars:
                log.warning(
                    "[DIRECTOR] Translation likely contains instruction leakage "
                    f"({len(translated)} > {_max_translation_chars} chars); retrying clean"
                )
                translated = _translate_once("")
                if not translated or len(translated) > _max_translation_chars:
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
                _stricter_sys = system_msg + (
                    " The previous attempt left English (Latin) letters in the output. "
                    "Transliterate EVERY remaining English word phonetically into "
                    "Devanagari, but STILL keep the @@N@@ tokens exactly as-is. "
                    "Output ONLY Devanagari and the tokens."
                )
                try:
                    _candidate = _translate_once(_stricter_sys)
                    if _candidate:
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
                    "-- accepting best result."
                )
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
