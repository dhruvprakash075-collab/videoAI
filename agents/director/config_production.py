"""config_production.py - ConfigProductionMixin: config consultation + runtime overlay.

Extracted verbatim from ``agents/director_agent.py`` (WS-4 mixin split).
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ConfigProductionMixin:
    """Config production: consult on vision doc, writer collaboration, runtime overlay."""

    # Provided by the composing DirectorAgent (facade __init__ + sibling mixins)
    llm_config: Any
    consult_user: Any
    consult_fields: Any
    _prompt: Any
    _call_ollama: Any
    _parse_json: Any

    def consult_on_config(self, vision_doc: dict):
        """Phase 3: Present config decisions to user as a single form."""

        log.info("[DIRECTOR] Phase 3/5: Consulting user...")

        # Vision summary header (S8)

        chars: list[dict[str, Any]] | dict[str, Any] | str = vision_doc.get("characters", [])
        if isinstance(chars, dict):
            chars_list: list[dict[str, Any]] = []
            for name, details in chars.items():
                if isinstance(details, dict):
                    c = {k: str(v) for k, v in details.items()}
                    c.setdefault("name", name)
                else:
                    c = {"name": name, "description": str(details)}
                chars_list.append(c)
            chars = chars_list
        if isinstance(chars, str):
            chars = [{"name": chars}]
        if chars and isinstance(chars[0], str):
            chars = [{"name": c} for c in chars]

        char_names = ", ".join(str(c.get("name", "?")) for c in chars[:4])

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

        q_data: dict[str, Any] = {"fields": {}, "breakdown": {}}

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
            except Exception as exc:
                log.debug(f"[DIRECTOR] TTS recommendation normalization skipped: {exc}")

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
            if key == "mickey_mouse":
                log.warning("[DIRECTOR] Dropping hallucinated character 'Mickey Mouse'")
                continue
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
        words_per = max(100, min(400, int((writer_input.get("words_per_segment") or 390) or 390)))

        # -- Visual Style (skip for voice-only) --
        if _mode != "voice-only":
            style_response = user_responses.get("visual_style", "")
            if (
                style_response
                and style_response.lower() != str(vision_doc.get("visual_style", "")).lower()
            ):
                from style_resolver import StyleResolver

                _styler = StyleResolver(
                    styles_path=str(Path(__file__).resolve().parent.parent.parent / "styles.yaml")
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
            _base_engine = (
                self.llm_config.get("tts", {}).get("engine", "")
                if isinstance(self.llm_config, dict)
                else ""
            )
            if tts_response:
                try:
                    from audio.audio_proxy import normalize_tts_engine
                    engine = normalize_tts_engine(tts_response)
                except Exception as exc:
                    log.debug(f"[DIRECTOR] TTS response normalization skipped: {exc}")
                    engine = _base_engine or "indicf5"
            elif _base_engine:
                engine = _base_engine
            else:
                engine = vision_doc.get("tts_recommendation", "indicf5") or "indicf5"
                try:
                    from audio.audio_proxy import normalize_tts_engine
                    engine = normalize_tts_engine(engine)
                except Exception as exc:
                    log.debug(f"[DIRECTOR] TTS default normalization skipped: {exc}")
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
