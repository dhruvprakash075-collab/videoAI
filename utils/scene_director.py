"""scene_director.py - Enrich image prompts with shot directions and camera moves."""

import logging
import re

log = logging.getLogger(__name__)

# Camera direction templates mapped to scene moods
_CAMERA_MOVES = {
    "mysterious": "slow dolly-in, volumetric fog, shallow depth of field",
    "action": "dynamic whip pan, motion blur, dramatic angle",
    "horror": "Dutch angle, claustrophobic close-up, flickering light",
    "dramatic": "hero shot, low angle, dramatic lighting, cinematic",
    "calm": "smooth tracking shot, soft focus, warm golden hour light",
    "epic": "wide establishing shot, sweeping crane move, anamorphic lens",
    "intimate": "close-up, shallow DOF, soft key light, intimate mood",
}

# Curated base negative prompts per mood (fallback when Ollama is unavailable)
_MOOD_NEGATIVE_PROMPTS = {
    "action":     "blurry, soft focus, pastel colors, chibi, deformed hands, static pose, peaceful, serene, "
                  "photorealistic, 3d render, lowres, bad anatomy, watermark",
    "horror":     "bright sunny day, cheerful, colorful, smiling, happy, cute, kawaii, "
                  "photorealistic, 3d render, lowres, bad anatomy, watermark",
    "mysterious": "overexposed, flat lighting, mundane, ordinary, cheerful, bright, "
                  "photorealistic, 3d render, lowres, bad anatomy, watermark",
    "dramatic":   "bland, flat, boring composition, symmetrical, unlit, low contrast, "
                  "photorealistic, 3d render, lowres, bad anatomy, watermark",
    "calm":       "violent, dark, gory, intense, war, battle, explosion, "
                  "photorealistic, 3d render, lowres, bad anatomy, watermark",
    "epic":       "mundane, ordinary, small scale, cramped, indoor only, claustrophobic, "
                  "photorealistic, 3d render, lowres, bad anatomy, watermark",
    "intimate":   "crowd, wide angle, empty space, distant, impersonal, cold lighting, "
                  "photorealistic, 3d render, lowres, bad anatomy, watermark",
}

_OLLAMA_PING_TIMEOUT = 3  # seconds (kept for backward compat; no longer used)


def _cap_tokens(text: str, max_tokens: int = 65) -> str:
    """Cap a comma-separated token string to fit within *max_tokens* CLIP tokens.

    P3-15 fix: uses a word-count estimate (words * 1.3 ≈ CLIP tokens) instead of
    counting comma-phrases.  The old approach of capping at 150 comma-phrases
    produced 300+ CLIP tokens — well above the 77-token CLIP limit — so the
    negatives were always silently truncated.  The new default cap of 65 tokens
    leaves headroom for the positive prompt's shared 77-token budget.
    """
    parts = [t.strip() for t in text.split(",") if t.strip()]
    result = []
    used = 0
    for part in parts:
        cost = max(1, int(len(part.split()) * 1.3))
        if used + cost <= max_tokens:
            result.append(part)
            used += cost
        else:
            break
    return ", ".join(result)


def get_dynamic_negative_prompt(mood: str, script: str, config: dict) -> str:
    """Return a contextually relevant negative prompt using fast static lookup.

    OPT-06 (RTX 4050): Skips the qwen3.5-9b LLM call (~10-15s per segment).
    The static _MOOD_NEGATIVE_PROMPTS table already covers all 7 moods well.
    The image-engineer model handles positive prompt quality separately.

    Args:
        mood:   Detected scene mood string.
        script: Full script text (unused — kept for API compatibility).
        config: Full pipeline config dict.

    Returns:
        Fully assembled negative prompt string.
    """
    base_neg = _MOOD_NEGATIVE_PROMPTS.get(mood, _MOOD_NEGATIVE_PROMPTS["mysterious"])

    # Append global negative prompt from config as final layer
    global_neg = config.get("visual", {}).get("negative_prompt", "")
    if not global_neg:
        global_neg = config.get("image_gen", {}).get("negative_prompt", "")

    parts = [base_neg]
    if global_neg and global_neg.strip():
        parts.append(global_neg.strip())

    combined = ", ".join(parts)
    capped = _cap_tokens(combined, max_tokens=150)
    log.debug(f"Static negative prompt assembled ({len(capped.split(','))} tokens, mood={mood})")
    return capped


def enrich_prompts(raw_prompts: str, script: str, config: dict, plan: dict | None = None) -> tuple[str, str]:
    """Add camera directions and visual style to raw image prompts.

    Also generates a dynamic, contextually relevant negative prompt via
    get_dynamic_negative_prompt().

    Args:
        raw_prompts: Semicolon-separated base prompts from build_prompts()
        script:      English script text (used to detect mood keywords)
        config:      Full pipeline config dict
        plan:        Optional segment plan dict containing char_weight_map

    Returns:
        A tuple of:
          - enriched_prompts_str: Semicolon-separated enriched prompts with camera directions
          - negative_prompt_str:  Dynamically generated negative prompt for this scene
    """
    prompts = [p.strip() for p in raw_prompts.split(";") if p.strip()]
    if not prompts:
        log.warning("No prompts to enrich")
        neg = get_dynamic_negative_prompt("mysterious", script, config)
        return raw_prompts, neg

    # Detect mood from script
    mood = _detect_mood(script)
    camera = _CAMERA_MOVES.get(mood, "cinematic shot, professional lighting")

    # Get visual style from config
    style = config.get("visual", {}).get("style") or "Gothic Horror"
    # The Director overlay may set style as a dict {tone:..., elements:[...]} instead of a string.
    # Coerce to a usable string for prompt assembly.
    if isinstance(style, dict):
        _tone = style.get("tone", "")
        _elements = style.get("elements", [])
        style = f"{_tone}, {', '.join(_elements)}" if _elements else (_tone or "Gothic Horror")

    is_anime_style = any(kw in style.lower() for kw in ["anime", "2d", "webtoon", "visual novel", "manga", "drawing"])

    if plan is None:
        plan = {}
    char_presence = plan.get("char_presence")

    # Retrieve all possible character descriptions and names to strip them for environmental frames
    chars = config.get("characters", {})
    char_descriptions = []
    char_names = []
    # P1-12 fix: use the full name only — never split to first token (avoids stripping stop-words like "The")
    _STOP_WORDS = {"the", "a", "an", "of", "in", "at"}
    for c_data in chars.values():
        desc = c_data.get("description", "")
        if desc:
            char_descriptions.append(desc)
        name = c_data.get("name", "")
        if name and name.lower() not in _STOP_WORDS and len(name) > 3:
            char_names.append(name)
    char_names.append("a figure")

    enriched = []
    for i, prompt in enumerate(prompts):
        cp = {}
        if isinstance(char_presence, list) and i < len(char_presence):
            val = char_presence[i]
            if isinstance(val, dict):
                cp = val

        # Calculate max weight in frame to determine overall shot framing
        max_weight = max(cp.values()) if cp else 1.0

        # B4: read token budget from config (falls back to safe defaults)
        _tb = config.get("image_gen", {}).get("token_budget", {})
        _identity_budget = int(_tb.get("identity", 25))
        _style_budget    = int(_tb.get("style", 20))
        _scene_budget    = int(_tb.get("scene", 32))
        # Total budget = identity + style + scene, capped at 70 to leave 7-token headroom.
        # assemble_prompt* internally allocate identity/scene/style shares from this.
        _total_budget = min(70, _identity_budget + _style_budget + _scene_budget)

        # Adjust camera move and framing based on maximum character weight in frame
        if max_weight < 0.3:
            move = f"wide establishing shot of the environment, sweeping landscape, panoramic view, {camera}"
            # Strip ALL character details to ensure pure environmental focus
            for desc in char_descriptions:
                pattern = re.compile(re.escape(desc), re.IGNORECASE)
                prompt = pattern.sub("grand scenery", prompt)
            for name in char_names:
                pattern = re.compile(r'\b' + re.escape(name) + r'\b', re.IGNORECASE)
                prompt = pattern.sub("empty landscape", prompt)
        elif max_weight < 0.7:
            move = f"medium shot, character in environment, {camera}"
        else:
            move = f"medium close-up, detailed character portrait, {camera}"

        # If a specific character's weight is < 0.3 but others are present, strip them so they don't appear
        if max_weight >= 0.3:
            for c_key, c_data in chars.items():
                cw = cp.get(c_key, 0.0)
                if cw < 0.3:
                    name = c_data.get("name", "")
                    # P1-12 fix: skip stop-words/articles; use full name with word-boundary regex only
                    if name and name.lower() not in _STOP_WORDS and len(name) > 3:
                        pattern = re.compile(r'\b' + re.escape(name) + r'\b', re.IGNORECASE)
                        prompt = pattern.sub("someone", prompt)

        # Vary camera slightly for first/last frames if no presence map was provided (backward compatibility)
        if not char_presence:
            if i == 0:
                move = f"establishing shot, {camera}"
            elif i == len(prompts) - 1:
                move = f"closing shot, {camera}"

        if is_anime_style:
            # For anime: style first, then scene, then camera
            _style_part = f"{style}, webtoon art, soft cell shading, high quality"
            if max_weight < 0.3:
                # R3.7: environmental frame — prioritize world/setting, no identity
                _assembled = assemble_prompt(
                    identity_tokens="",
                    scene_tokens=f"{prompt}, {move}",
                    style_tokens=_style_part,
                    budget=_total_budget,
                )
            else:
                # Collect ALL characters with cw >= 0.3 for multi-character interaction frames
                _char_identities = []
                for c_key, cw in sorted(cp.items(), key=lambda x: x[1], reverse=True):
                    if cw >= 0.3:
                        _cd = chars.get(c_key, {}).get("description", "")
                        if _cd:
                            _char_identities.append((_cd, cw))

                if len(_char_identities) >= 2:
                    # Multi-character interaction frame — use budget-shared assembler
                    _assembled = assemble_prompt_multi(
                        identity_list=_char_identities,
                        scene_tokens=f"{prompt}, {move}",
                        style_tokens=_style_part,
                        budget=_total_budget,
                    )
                elif _char_identities:
                    # Single character — use standard assembler
                    _assembled = assemble_prompt(
                        identity_tokens=_char_identities[0][0],
                        scene_tokens=f"{prompt}, {move}",
                        style_tokens=_style_part,
                        budget=_total_budget,
                    )
                else:
                    _assembled = assemble_prompt(
                        identity_tokens="",
                        scene_tokens=f"{prompt}, {move}",
                        style_tokens=_style_part,
                        budget=_total_budget,
                    )
            enriched.append(_assembled)
        else:
            # B6 fix: removed 'photorealistic, masterpiece' — contradicts negative prompt.
            _style_part = f"{style}, cinematic lighting, 8k quality, detailed"
            if max_weight < 0.3:
                # R3.7: environmental frame — world/setting detail, no identity
                _assembled = assemble_prompt(
                    identity_tokens="",
                    scene_tokens=f"{prompt}, {move}",
                    style_tokens=_style_part,
                    budget=_total_budget,
                )
            else:
                # Collect ALL characters with cw >= 0.3 for multi-character interaction frames
                _char_identities = []
                for c_key, cw in sorted(cp.items(), key=lambda x: x[1], reverse=True):
                    if cw >= 0.3:
                        _cd = chars.get(c_key, {}).get("description", "")
                        if _cd:
                            _char_identities.append((_cd, cw))

                if len(_char_identities) >= 2:
                    # Multi-character interaction frame — use budget-shared assembler
                    _assembled = assemble_prompt_multi(
                        identity_list=_char_identities,
                        scene_tokens=f"{prompt}, {move}",
                        style_tokens=_style_part,
                        budget=_total_budget,
                    )
                elif _char_identities:
                    # Single character — use standard assembler
                    _assembled = assemble_prompt(
                        identity_tokens=_char_identities[0][0],
                        scene_tokens=f"{prompt}, {move}",
                        style_tokens=_style_part,
                        budget=_total_budget,
                    )
                else:
                    _assembled = assemble_prompt(
                        identity_tokens="",
                        scene_tokens=f"{prompt}, {move}",
                        style_tokens=_style_part,
                        budget=_total_budget,
                    )
            enriched.append(_assembled)

    result = "; ".join(enriched)
    log.info(f"Enriched {len(enriched)} prompts (mood: {mood}, weights: {char_presence})")

    # Generate dynamic negative prompt for this scene
    neg_prompt = get_dynamic_negative_prompt(mood, script, config)

    return result, neg_prompt


def assemble_prompt_multi(identity_list: list, scene_tokens: str,
                          style_tokens: str, budget: int = 70) -> str:
    """Build a CLIP-safe prompt supporting MULTIPLE characters in interaction scenes.

    When a frame has 2-3 characters with cw >= 0.3, all their identity tokens must
    fit. This function budgets identity tokens proportionally by weight, trimming
    each character's description to fit the shared identity budget.

    Args:
        identity_list: List of (description, weight) tuples sorted by weight descending.
                       Each description is the character's canonical visual identity string.
        scene_tokens:  Scene/action/mood description.
        style_tokens:  Visual style, camera, lighting (trimmed first if over budget).
        budget:        Approximate CLIP token budget (default 70, leaving headroom).

    Returns:
        A single comma-separated prompt string within the token budget.
    """
    def _count(text: str) -> int:
        """Fast word-based token approximation (1 word ≈ 1.3 tokens)."""
        return max(1, int(len(text.split()) * 1.3)) if text.strip() else 0

    def _trim_to_budget(text: str, max_tokens: int) -> str:
        """Trim a comma-separated description to fit within max_tokens."""
        if not text.strip():
            return ""
        parts = [t.strip() for t in text.split(",") if t.strip()]
        result = []
        used = 0
        for part in parts:
            cost = _count(part)
            if used + cost <= max_tokens:
                result.append(part)
                used += cost
            else:
                break
        return ", ".join(result)

    parts = []
    used = 0

    # 0. Style anchor (guaranteed minimum — never trimmed, ensures consistent look)
    _style_anchor = ""
    if style_tokens.strip():
        _anchor_parts = [t.strip() for t in style_tokens.split(",") if t.strip()][:4]
        _style_anchor = ", ".join(_anchor_parts)
        _anchor_cost = _count(_style_anchor)
        if _anchor_cost <= budget * 0.25:
            parts.append(_style_anchor)
            used += _anchor_cost

    # 1. Multi-character identity tokens (highest priority, budget-shared)
    # Allocate up to 45% of total budget for identity (leaves room for scene + style)
    identity_budget = int(budget * 0.45)
    valid_identities = [(desc, w) for desc, w in identity_list if desc and desc.strip()]

    if valid_identities:
        total_weight = sum(w for _, w in valid_identities) or 1.0
        for desc, weight in valid_identities:
            # Proportional budget allocation by character weight
            char_budget = max(5, int(identity_budget * (weight / total_weight)))
            trimmed = _trim_to_budget(desc, char_budget)
            if trimmed:
                cost = _count(trimmed)
                if used + cost <= budget:
                    parts.append(trimmed)
                    used += cost
                else:
                    # Try a minimal version (first 3 tokens only)
                    minimal = _trim_to_budget(desc, 4)
                    if minimal and used + _count(minimal) <= budget:
                        parts.append(minimal)
                        used += _count(minimal)

    # 2. Scene tokens
    if scene_tokens.strip():
        sc_cost = _count(scene_tokens)
        if used + sc_cost <= budget:
            parts.append(scene_tokens.strip())
            used += sc_cost
        else:
            words = scene_tokens.split()
            remaining = max(0, budget - used)
            trimmed = " ".join(words[:int(remaining / 1.3)])
            if trimmed:
                parts.append(trimmed)
                used += _count(trimmed)

    # 3. Style tokens (lowest priority — trimmed first; skip anchor already added)
    if style_tokens.strip() and used < budget:
        words = style_tokens.split(",")
        for token in words:
            token = token.strip()
            if not token:
                continue
            if _style_anchor and token in _style_anchor:
                continue
            cost = _count(token)
            if used + cost <= budget:
                parts.append(token)
                used += cost
            else:
                break

    return ", ".join(p for p in parts if p)


def assemble_prompt(identity_tokens: str, scene_tokens: str,
                    style_tokens: str, budget: int = 70) -> str:
    """Build a CLIP-safe prompt with identity tokens first (B4 fix).

    Places character identity tokens at the start so they survive the ~77-token
    CLIP truncation limit. Trims style/camera boilerplate before identity.

    For multi-character frames, use assemble_prompt_multi() instead.

    Args:
        identity_tokens: Character description (most important — placed first).
        scene_tokens:    Scene/action/mood description.
        style_tokens:    Visual style, camera, lighting (trimmed first if over budget).
        budget:          Approximate CLIP token budget (default 70, leaving headroom).

    Returns:
        A single comma-separated prompt string within the token budget.
    """
    def _count(text: str) -> int:
        """Fast word-based token approximation (1 word ≈ 1.3 tokens)."""
        return max(1, int(len(text.split()) * 1.3)) if text.strip() else 0

    # Build parts in priority order: identity > scene > style
    parts = []
    used = 0

    # 0. Style anchor (guaranteed minimum — never trimmed, ensures consistent look)
    # Take the first ~6 tokens of style as a non-negotiable anchor.
    _style_anchor = ""
    if style_tokens.strip():
        _anchor_parts = [t.strip() for t in style_tokens.split(",") if t.strip()][:4]
        _style_anchor = ", ".join(_anchor_parts)
        _anchor_cost = _count(_style_anchor)
        if _anchor_cost <= budget * 0.25:  # max 25% of budget for anchor
            parts.append(_style_anchor)
            used += _anchor_cost

    # 1. Identity tokens first (highest priority)
    if identity_tokens.strip():
        id_cost = _count(identity_tokens)
        if used + id_cost <= budget:
            parts.append(identity_tokens.strip())
            used += id_cost

    # 2. Scene tokens
    if scene_tokens.strip():
        sc_cost = _count(scene_tokens)
        if used + sc_cost <= budget:
            parts.append(scene_tokens.strip())
            used += sc_cost
        else:
            # Trim scene tokens to fit
            words = scene_tokens.split()
            remaining = max(0, budget - used)
            trimmed = " ".join(words[:int(remaining / 1.3)])
            if trimmed:
                parts.append(trimmed)
                used += _count(trimmed)

    # 3. Style tokens (lowest priority — trimmed first; skip anchor tokens already added)
    if style_tokens.strip() and used < budget:
        words = style_tokens.split(",")
        for token in words:
            token = token.strip()
            if not token:
                continue
            # Skip tokens already in the style anchor
            if _style_anchor and token in _style_anchor:
                continue
            cost = _count(token)
            if used + cost <= budget:
                parts.append(token)
                used += cost
            else:
                break  # stop adding style tokens once budget is full

    return ", ".join(p for p in parts if p)


def _detect_mood(script: str) -> str:
    """Detect narrative mood from script text using keyword matching."""
    script_lower = script.lower()

    mood_keywords = {
        "horror": ["dark", "shadow", "fear", "terror", "nightmare", "monster",
                   "scream", "blood", "death", "haunt", "ghost"],
        "action": ["battle", "fight", "chase", "explosion", "attack", "rush",
                   "charge", "strike", "pursue"],
        "mysterious": ["mystery", "unknown", "secret", "strange", "puzzle",
                       "enigma", "curious", "unseen"],
        "dramatic": ["reveal", "betrayal", "discover", "confront", "intense",
                     "powerful", "transform"],
        "calm": ["peace", "quiet", "serene", "gentle", "soft", "warm",
                 "tranquil", "still"],
        "epic": ["legend", "destiny", "ancient", "prophecy", "epic",
                 "mighty", "vast", "eternal"],
        "intimate": ["whisper", "embrace", "touch", "close", "personal",
                     "private", "gentle"],
    }

    scores = {}
    for mood, keywords in mood_keywords.items():
        scores[mood] = sum(1 for kw in keywords if re.search(r'\b' + re.escape(kw) + r'\b', script_lower))

    if not scores or max(scores.values()) == 0:
        return "mysterious"

    best_mood = max(scores, key=lambda k: scores[k])
    log.debug(f"Detected mood: {best_mood} (score: {scores[best_mood]})")
    return best_mood
