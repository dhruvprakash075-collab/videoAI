"""Pydantic schemas for the Video.AI pipeline.

Replaces loose Dict typing with validated models.
Provides: type safety, schema-aware LLM generation, deterministic serialization.
"""

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, RootModel, field_validator, model_validator

log = logging.getLogger(__name__)


# ── Character ──
class CharacterSpec(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    voice: str = "clear"

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()

    def normalized_key(self) -> str:
        return re.sub(r"[^a-z0-9_]", "", self.name.lower().replace(" ", "_"))


# ── Shot Distribution ──
class ShotDistribution(BaseModel):
    establishing: float = 0.10
    environment: float = 0.20
    character_medium: float = 0.35
    character_closeup: float = 0.20
    emotional_detail: float = 0.10
    action: float = 0.05

    @model_validator(mode="after")
    def normalize(self):
        keys = list(self.__class__.model_fields.keys())
        total = sum(getattr(self, k) for k in keys)
        if total <= 0:
            for k in keys:
                setattr(self, k, ShotDistribution.model_fields[k].default)
        else:
            for k in keys[:-1]:
                setattr(self, k, round(getattr(self, k) / total, 4))
            setattr(
                self,
                keys[-1],
                max(0.0, min(1.0, round(1.0 - sum(getattr(self, k) for k in keys[:-1]), 4))),
            )
        return self


# ── Subtitle ──
class SubtitleConfig(BaseModel):
    format: Literal["classic", "tiktok", "none"] = "classic"
    size: Literal["small", "medium", "large"] = "small"
    color: Literal["white", "yellow", "cyan"] = "white"
    position: Literal["top", "bottom", "center"] = "bottom"


# ── Vision Document ──
class VisionDocument(BaseModel):
    characters: list[CharacterSpec] = []
    visual_style: str = "hybrid 2d anime visual novel style"
    theme: str = "untitled"
    emotions: str = "neutral"
    pacing: str = "moderate"
    shot_distribution: ShotDistribution = ShotDistribution()
    tts_recommendation: Literal["supertonic", "omnivoice"] = "supertonic"
    subtitle_style: SubtitleConfig = SubtitleConfig()
    ambiguity_detected: bool = False
    ambiguity_question: str = ""
    ambiguity_fields: list[str] = []
    recommendations: list[str] = []
    segment_count: int = Field(default=3, ge=1, le=20)
    words_per_segment: int = Field(default=130, ge=50, le=800)
    image_count_per_segment: int = Field(default=6, ge=1, le=30)
    topic: str = ""
    source_hash: str = ""

    @field_validator("characters")
    @classmethod
    def ensure_min_one(cls, v):
        if not v:
            return [CharacterSpec(name="Narrator", description="Omniscient narrator")]
        return v


# ── Writer Breakdown ──
class WriterBreakdown(BaseModel):
    segment_count: int = Field(default=3, ge=1, le=20)
    words_per_segment: int = Field(default=130, ge=50, le=800)
    image_count_per_segment: int = Field(default=6, ge=1, le=30)
    opening_hook_style: str = ""
    pacing_notes: str = ""


# ── User Responses ──
class UserResponses(BaseModel):
    visual_style: str = ""
    subtitle_style: str = ""
    tts_engine: str = ""
    custom_instructions: str = ""
    ambiguity_resolution: str = ""
    extras: dict[str, str] = {}

    @field_validator("custom_instructions")
    @classmethod
    def sanitize(cls, v):
        blocked = ["ignore previous instructions", "system prompt:", "<|im_start|>", "<|im_end|>"]
        for p in blocked:
            if p in v.lower():
                v = v.lower().replace(p, "[FILTERED]")
        return v


# ── Config Overlay (Phase 5 output) ──
class CharacterEntry(BaseModel):
    model_config = {"extra": "forbid"}
    name: str
    description: str = ""
    keywords: list[str] = []
    voice_sample: str = ""


class VisualConfig(BaseModel):
    model_config = {"extra": "forbid"}
    num_scenes: int = Field(default=4, ge=1, le=30)
    style: str = "hybrid 2d anime visual novel style"
    environment_frame_ratio: float = 0.4


class SuperTonicSubConfig(BaseModel):
    model_config = {"extra": "forbid"}
    voice: str = ""
    steps: int = 16
    speed: float = 1.0
    silence_duration: float = 0.1
    max_chunk_length: int = 150


class OmniVoiceSubConfig(BaseModel):
    model_config = {"extra": "forbid"}
    speed: float = 0.5
    num_step: int = 16
    guidance_scale: float = 2.5
    ref_text: str = ""


class DevanagariConfig(BaseModel):
    model_config = {"extra": "forbid"}
    max_latin_ratio: float = 0.1
    max_retranslate_retries: int = 2


class VoiceProfileConfig(BaseModel):
    model_config = {"extra": "forbid"}
    sentence_gap_ms: int = 200


class TTSConfig(BaseModel):
    model_config = {"extra": "forbid"}
    engine: Literal["supertonic", "omnivoice"] = "supertonic"
    lang: str = "hi"
    slow: bool = False
    voice_samples_dir: str = "character_voices"
    voice_profile: VoiceProfileConfig = Field(default_factory=VoiceProfileConfig)
    devanagari: DevanagariConfig = Field(default_factory=DevanagariConfig)
    supertonic: SuperTonicSubConfig = Field(default_factory=SuperTonicSubConfig)
    omnivoice: OmniVoiceSubConfig = Field(default_factory=OmniVoiceSubConfig)
    fish_speech_model_path: str = ""
    fish_speech_temperature: float = 0.7
    fish_speech_top_p: float = 0.9
    fish_speech_repetition_penalty: float = 1.5
    alignment: "AlignmentConfig" = Field(default_factory=lambda: AlignmentConfig())


class ScriptConfig(BaseModel):
    model_config = {"extra": "forbid"}
    words_per_segment: int = Field(default=130, ge=50, le=800)
    min_words: int = Field(default=20, ge=1)
    max_words: int = Field(default=600, ge=1)
    dynamic_image_count: bool = True
    default_images_per_segment: int = Field(default=6, ge=1, le=30)
    max_images_per_segment: int = Field(default=8, ge=1, le=50)
    word_count_tolerance: float = 0.6
    writer_max_tokens: int = 1024
    critic_enabled: bool = True
    critic_threshold: int = 60
    critic_max_rewrites: int = 2
    uncapped_scaling: bool = False


class SubtitleOverlay(BaseModel):
    format: Literal["classic", "tiktok", "none"] = "classic"
    font: str = "Arial"
    size: int = Field(default=24, ge=8, le=72)
    color: str = "&H00FFFFFF&"
    position: str = "bottom"
    language: str = "en"


class PacingConfig(BaseModel):
    style: str = "moderate"
    opening_hook: str = ""
    notes: str = ""


class VideoConfig(BaseModel):
    model_config = {"extra": "forbid"}
    total_duration_min: float = Field(default=10, ge=0.5, le=600)
    segment_duration_min: float = Field(default=2, ge=0.5, le=30)
    fps: int = Field(default=24, ge=1, le=120)
    resolution: str = "1920x1080"
    output_path: str = "studio_outputs/final_video.mp4"
    encoder: str = "h264_nvenc"
    encoder_preset: str = "p5"
    video_bitrate: str = "8M"
    encoder_extra: str = ""
    crossfade_duration: float = 0.3
    ken_burns: str = "light"
    audio_crossfade_ms: int = 200
    generate_thumbnail: bool = True
    motion_engine: str = "none"
    motion_seconds_per_image: int = 3


class ModelsConfig(BaseModel):
    model_config = {"extra": "forbid"}
    director: str = "hermes-director"
    director_max_tokens: int = 2048
    writer: str = "zephyr-writer"
    writer_adapt: str = "zephyr-writer"
    writer_scratch: str = "cra-guided-7b"
    image_engineer: str = "image-engineer"
    translator: str = "sarvam-translate"


class TransitionConfig(BaseModel):
    transition: str = "cross_fade"
    transition_blocks: list[str] = []


class ProductionNotes(BaseModel):
    recommendations: list[str] = []
    custom_instructions: str = ""
    theme: str = ""
    emotions: str = ""
    user_overrides: dict[str, str] = {}


class DirectorVision(BaseModel):
    theme: str = ""
    emotions: str = ""
    pacing: str = ""
    visual_style: str = ""


class UploadConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = False
    platform: Literal["youtube"] = "youtube"
    visibility: Literal["public", "private", "unlisted"] = "private"
    profile_dir: str = "chrome_profile"


class CheckpointConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = True
    dir: str = "studio_checkpoints"
    max_age_hours: float = 0


class MemoryConfig(BaseModel):
    model_config = {"extra": "forbid"}
    memory_file: str = "studio_checkpoints/story_memory.json"
    llm_world_state: bool = True


class OllamaConfig(BaseModel):
    model_config = {"extra": "forbid"}
    host: str = "http://localhost:11434"
    request_timeout: int = 240
    keep_alive: str = "5m"
    breaker_fails: int = 3
    breaker_cooldown_s: int = 30


class PerformanceConfig(BaseModel):
    model_config = {"extra": "forbid"}
    max_workers: int = 1
    checkpoint_interval: int = 1
    whisper_model: str = "tiny"
    whisper_model_final: str = "base"
    max_segment_retries: int = 2
    vram_sd_threshold_gb: float = 4.5
    vram_evict_wait_s: float = 15
    ffmpeg_threads: int = 0
    staged_loop: bool = False
    lookahead_segments: int = 0


class ComfyUIConfig(BaseModel):
    model_config = {"extra": "forbid"}
    server: str = "127.0.0.1"
    host: str = "127.0.0.1"
    port: int = 8188
    root: str = "external/ComfyUI"
    python: str = "python"
    auto_start: bool = True
    open_browser: bool = False
    workflow_path: str = ""
    checkpoint: str = "DreamShaper_8.safetensors"
    width: int = 1024
    height: int = 1024
    steps: int = 20
    cfg: float = 7.0
    sampler_name: str = "euler"
    scheduler: str = "normal"
    timeout_seconds: int = 300
    poll_seconds: float = 1.0
    auto_start_timeout: int = 60
    unload_after_batch: bool = False


class UpscalerConfig(BaseModel):
    model_config = {"extra": "forbid"}
    model: str = "none"
    model_path: str = ""
    scale: int = 4
    target_width: int = 1920
    target_height: int = 1080


class LayeredV3Config(BaseModel):
    model_config = {"extra": "forbid"}
    approval_mode: str = "auto"
    character_threshold: float = 0.3
    closeup_threshold: float = 0.8
    max_characters: int = 2
    fallback_mode: str = "one_pass"
    workflows: dict[str, str] = Field(default_factory=dict)
    character_dir: str = "studio_projects/{project}/characters"


class QwenEditConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = False
    backend: str = "nunchaku"
    workflow_path: str = "config/comfyui/workflows/qwen_image_edit_api.json"
    model_path: str = ""
    lightning_lora: str = ""
    steps: int = 8
    cfg: float = 1.0
    denoise: float = 0.6
    max_resolution: int = 1024
    youtube_aspect: str = "16:9"
    vram_offload: bool = True
    trigger: str = "any_character"
    character_threshold: float = 0.05
    cache_dir: str = ".qwen_edit_cache"
    timeout_seconds: int = 600
    poll_seconds: float = 1.0
    required_custom_nodes: list[str] = Field(default_factory=list)


class ImageGenConfig(BaseModel):
    model_config = {"extra": "forbid"}
    backend: str = "comfyui"
    bonsai_model: str = ""
    sd_model_path: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 12
    guidance_scale: float = 3.5
    cfg: float = 7.0
    ip_adapter_scale: float = 0.8
    lock_seed: bool = True
    preview_steps: int = 12
    oom_recovery: bool = True
    sampler: str = "euler"
    scheduler: str = "normal"
    seed: int = -1
    upscaler: UpscalerConfig = Field(default_factory=UpscalerConfig)
    comfyui: ComfyUIConfig = Field(default_factory=ComfyUIConfig)
    fallback_backend: str = "bonsai"
    composition_mode: str = "one_pass"
    qwen_edit: QwenEditConfig = Field(default_factory=QwenEditConfig)
    layered_v3: LayeredV3Config = Field(default_factory=LayeredV3Config)


class MusicConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = False
    ducking: bool = False
    duck_ratio: float = 0.3
    fade_duration: float = 2.0


class SubtitlesConfig(BaseModel):
    model_config = {"extra": "forbid"}
    format: Literal["classic", "tiktok", "none"] = "classic"
    language: str = "en"
    font: str = "Arial"
    size: int = 24
    color: str = "&H00FFFFFF&"
    position: str = "bottom"


class NarratorConfig(BaseModel):
    model_config = {"extra": "forbid"}
    include_character_descriptions: bool = False


class CacheConfig(BaseModel):
    model_config = {"extra": "forbid"}
    cache_invented_story: bool = True


class AudioFxConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = True
    volume: float = 0.25
    program_loudnorm: bool = True
    loudnorm_two_pass: bool = True
    target_lufs: int = -14


class RvcConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = False


class AlignmentConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = True
    model: str = "base"
    device: str = "cpu"
    compute_type: str = "int8"


class CriticConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = True
    threshold: int = 60
    max_rewrites: int = 2


class ResearchConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = True
    sources: list[str] = Field(default_factory=lambda: ["wikipedia", "wikimedia", "rss"])
    rss_urls: list[str] = Field(default_factory=list)
    user_agent: str = "VideoAI/6.0 (+https://github.com/...)"
    budget: int = 3
    timeout_s: int = 15
    per_source_limit: int = 3


class SEOConfig(BaseModel):
    model_config = {"extra": "forbid"}
    enabled: bool = True
    title_max_chars: int = 100
    description_max_chars: int = 5000
    tags_count: int = 15
    hashtags_count: int = 5
    chapters_max: int = 50
    description_paragraphs: int = 2


class SourceConfig(BaseModel):
    model_config = {"extra": "forbid"}
    allowed_extensions: list[str] = Field(default_factory=lambda: [".txt", ".md", ".pdf", ".docx"])
    max_words: int = 50000
    url_timeout_s: int = 30
    user_agent: str = "VideoAI/6.0 (+https://github.com/...)"


class LanguageConfig(BaseModel):
    model_config = {"extra": "forbid"}
    code: str = Field(
        default="hi", min_length=2, max_length=10, pattern=r"^[a-z]{2}(-[a-z]{2,4})?$"
    )
    tts_engine: str = "supertonic"
    subtitle_language: str = "en"

    @field_validator("code")
    @classmethod
    def normalize(cls, v: str) -> str:
        return v.strip().lower()


class VideoAIConfig(BaseModel):
    model_config = {"extra": "allow"}
    critic: CriticConfig = Field(default_factory=CriticConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    seo: SEOConfig = Field(default_factory=SEOConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)

    @classmethod
    def from_dict(cls, raw: dict) -> "VideoAIConfig":
        try:
            return cls(**{k: raw[k] for k in raw if k in cls.model_fields})
        except Exception:
            return cls()


class ConfigOverlay(BaseModel):
    characters: dict[str, CharacterEntry] = {}
    visual: VisualConfig = VisualConfig()
    tts: TTSConfig = TTSConfig()
    script: ScriptConfig = ScriptConfig()
    subtitles: SubtitleOverlay = SubtitleOverlay()
    pacing: PacingConfig = PacingConfig()
    video: VideoConfig = VideoConfig()
    visualization: TransitionConfig = TransitionConfig()
    production_notes: ProductionNotes = ProductionNotes()
    _director_vision: DirectorVision = DirectorVision()
    upload: UploadConfig = UploadConfig()
    provenance: dict[str, str] = {}


# ── Helpers ──
def validate_or_default(data, schema):
    """Validate dict against schema, return fully-defaulted instance on failure."""
    if not isinstance(data, dict):
        return schema()
    try:
        return schema(**data)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(
            f"Validation failed for {schema.__name__}, using defaults: {e}"
        )
        return schema()


def vision_from_dict(d):
    return validate_or_default(d, VisionDocument)


def breakdown_from_dict(d):
    return validate_or_default(d, WriterBreakdown)


def responses_from_dict(d):
    return validate_or_default(d, UserResponses)


def overlay_from_dict(d):
    return validate_or_default(d, ConfigOverlay)


SECTION_MODELS: dict[str, type[BaseModel]] = {
    "critic": CriticConfig,
    "research": ResearchConfig,
    "seo": SEOConfig,
    "source": SourceConfig,
    "tts": TTSConfig,
    "models": ModelsConfig,
    "visual": VisualConfig,
    "video": VideoConfig,
    "script": ScriptConfig,
    "checkpoint": CheckpointConfig,
    "memory": MemoryConfig,
    "ollama": OllamaConfig,
    "performance": PerformanceConfig,
    "image_gen": ImageGenConfig,
    "music": MusicConfig,
    "subtitles": SubtitlesConfig,
    "upload": UploadConfig,
    "narrator": NarratorConfig,
    "cache": CacheConfig,
    "audio_fx": AudioFxConfig,
    "rvc": RvcConfig,
}


class CharactersConfig(RootModel[dict[str, CharacterEntry]]):
    pass


class SceneTemplatesConfig(RootModel[dict[str, str]]):
    pass


ALLOWED_KEYS = {
    *SECTION_MODELS.keys(),
    "language",
    "project_name",
    "theme",
    "narrator_persona",
    "world_lore",
    "active_plot_threads",
    "characters",
    "sub_characters",
    "scene_templates",
    "visualization",
    "production_notes",
    "_director_vision",
    "provenance",
    "_decision_record",
}


def validate_config(raw_config: dict) -> dict:
    """Strict full-config validation via Pydantic schemas."""
    from pydantic import ValidationError

    from utils.errors import FatalError

    if not isinstance(raw_config, dict):
        raise FatalError("Config must be a dict, got %s" % type(raw_config).__name__)

    validated: dict = {}
    for key, value in raw_config.items():
        if key not in ALLOWED_KEYS:
            raise FatalError("Unknown top-level config section '%s'" % key)

        if key in SECTION_MODELS:
            if isinstance(value, dict):
                try:
                    validated[key] = SECTION_MODELS[key](**value).model_dump()
                except ValidationError as exc:
                    errors = []
                    for error in exc.errors():
                        loc = ".".join(str(x) for x in error.get("loc", []))
                        msg = error.get("msg", "")
                        errors.append(f"field '{loc}' is invalid ({msg})")
                    raise FatalError(
                        "Config section '%s' validation failed: %s" % (key, "; ".join(errors))
                    ) from exc
            else:
                raise FatalError(
                    "Config section '%s' must be a dict, got %s" % (key, type(value).__name__)
                )
        elif key in ("characters", "sub_characters"):
            if isinstance(value, dict):
                try:
                    validated[key] = CharactersConfig(value).root
                    validated[key] = {k: v.model_dump() for k, v in validated[key].items()}
                except ValidationError as exc:
                    errors = []
                    for error in exc.errors():
                        loc = ".".join(str(x) for x in error.get("loc", []))
                        msg = error.get("msg", "")
                        errors.append(f"field '{loc}' is invalid ({msg})")
                    raise FatalError(
                        "Config section '%s' validation failed: %s" % (key, "; ".join(errors))
                    ) from exc
            else:
                raise FatalError(
                    "Config section '%s' must be a dict, got %s" % (key, type(value).__name__)
                )
        elif key == "scene_templates":
            if isinstance(value, dict):
                try:
                    validated[key] = SceneTemplatesConfig(value).root
                except ValidationError as exc:
                    errors = []
                    for error in exc.errors():
                        loc = ".".join(str(x) for x in error.get("loc", []))
                        msg = error.get("msg", "")
                        errors.append(f"field '{loc}' is invalid ({msg})")
                    raise FatalError(
                        "Config section '%s' validation failed: %s" % (key, "; ".join(errors))
                    ) from exc
            else:
                raise FatalError(
                    "Config section '%s' must be a dict, got %s" % (key, type(value).__name__)
                )
        elif key == "language":
            if isinstance(value, dict):
                try:
                    validated[key] = LanguageConfig(**value).model_dump()
                except ValidationError as exc:
                    errors = []
                    for error in exc.errors():
                        loc = ".".join(str(x) for x in error.get("loc", []))
                        msg = error.get("msg", "")
                        errors.append(f"field '{loc}' is invalid ({msg})")
                    raise FatalError(
                        "Config section 'language' validation failed: %s" % ("; ".join(errors))
                    ) from exc
            else:
                validated[key] = value
        else:
            validated[key] = value

    return validated


import logging as _log_dr  # noqa: E402

_dr_log = _log_dr.getLogger(__name__)

DECISION_SCHEMA_VERSION = 1
Provenance = Literal["default", "director", "writer", "user", "cli_flag"]
EndMode = Literal["full", "cliffhanger", "compact"]
RunMode = Literal["project", "one_time"]


class Decision(BaseModel):
    model_config = {"extra": "allow"}
    value: Any
    provenance: Provenance = "default"
    locked: bool = False
    rationale: str = ""


class PerSegmentOverride(BaseModel):
    seg: int = Field(ge=1)
    words: int | None = Field(default=None, ge=50, le=800)
    images: int | None = Field(default=None, ge=1, le=30)
    locked: bool = False


class DecisionConflict(Exception):
    pass


class DecisionRecord(BaseModel):
    model_config = {"extra": "allow"}
    version: int = DECISION_SCHEMA_VERSION
    total_duration_min: Decision = Field(default_factory=lambda: Decision(value=10, provenance="default"))
    segment_count: Decision = Field(default_factory=lambda: Decision(value=5, provenance="default"))
    segment_duration_min: Decision = Field(default_factory=lambda: Decision(value=2, provenance="default"))
    words_per_segment: Decision = Field(default_factory=lambda: Decision(value=130, provenance="default"))
    images_per_segment: Decision = Field(default_factory=lambda: Decision(value=6, provenance="default"))
    per_segment: list[PerSegmentOverride] = Field(default_factory=list)
    end_mode: Decision = Field(default_factory=lambda: Decision(value="full", provenance="default"))
    cliffhanger_point: Decision | None = None
    run_mode: Decision = Field(default_factory=lambda: Decision(value="one_time", provenance="default"))
    project_name: str | None = None
    adjustments: list[dict[str, Any]] = Field(default_factory=list)
    _AUTHORITY: dict[str, int] = {
        "default": 0,
        "director": 1,
        "writer": 2,
        "user": 3,
        "cli_flag": 3,
    }

    def _rank(self, provenance: str) -> int:
        return self._AUTHORITY.get(provenance, 0)

    def set(self, field: str, value: Any, provenance: Provenance, *, lock: bool = False, rationale: str = "") -> bool:
        current: Decision | None = getattr(self, field, None)
        if current is None:
            return False
        incoming_rank = self._rank(provenance)
        can_lock = provenance in ("user", "cli_flag")
        if current.locked and not can_lock:
            return False
        if not current.locked and incoming_rank < self._rank(current.provenance):
            return False
        clamped_value = self._clamp(field, value)
        if clamped_value != value:
            self.adjustments.append({"field": field, "type": "clamp", "from": value, "to": clamped_value, "provenance": provenance})
            value = clamped_value
        object.__setattr__(self, field, Decision(value=value, provenance=provenance, locked=lock if can_lock else False, rationale=rationale))
        return True

    @staticmethod
    def _clamp(field: str, value: Any) -> Any:
        clamps = {
            "total_duration_min": (0.5, 600),
            "segment_count": (1, 200),
            "segment_duration_min": (1, 30),
            "words_per_segment": (50, 800),
            "images_per_segment": (1, 30),
        }
        if field in clamps and isinstance(value, (int, float)):
            lo, hi = clamps[field]
            return max(lo, min(hi, value))
        return value

    def resolve_conflicts(self) -> None:
        sc = self.segment_count
        td = self.total_duration_min
        sdm = self.segment_duration_min
        derived_total = sc.value * sdm.value
        derived_segs = max(1, -(-td.value // sdm.value))
        if abs(derived_total - td.value) <= 1:
            return
        both_locked = sc.locked and td.locked
        if both_locked:
            if not sdm.locked:
                new_seg_duration = round(float(td.value) / max(1, int(sc.value)), 3)
                self.adjustments.append({"field": "segment_duration_min", "type": "conflict_resolved", "rule": "segment_count and total_duration locked → segment duration recomputed", "from": sdm.value, "to": new_seg_duration})
                object.__setattr__(self, "segment_duration_min", Decision(value=new_seg_duration, provenance="cli_flag" if "cli_flag" in (sc.provenance, td.provenance) else td.provenance, locked=False, rationale="recomputed from locked total_duration_min / segment_count"))
                return
            raise DecisionConflict(
                f"Conflict: segment_count={sc.value} × segment_duration={sdm.value}min = {derived_total}min, but total_duration_min is locked to {td.value}min. Please resolve by unlocking one of them."
            )
        if sc.locked:
            new_total = sc.value * sdm.value
            self.adjustments.append({"field": "total_duration_min", "type": "conflict_resolved", "rule": "segment_count locked → total recomputed", "from": td.value, "to": new_total})
            object.__setattr__(self, "total_duration_min", Decision(value=new_total, provenance=sc.provenance, locked=False, rationale="recomputed from locked segment_count × segment_duration_min"))
        elif td.locked:
            self.adjustments.append({"field": "segment_count", "type": "conflict_resolved", "rule": "total_duration_min locked → segment_count recomputed", "from": sc.value, "to": derived_segs})
            object.__setattr__(self, "segment_count", Decision(value=derived_segs, provenance=td.provenance, locked=False, rationale="recomputed from locked total_duration_min / segment_duration_min"))
        else:
            new_total = sc.value * sdm.value
            self.adjustments.append({"field": "total_duration_min", "type": "conflict_resolved", "rule": "neither locked → prefer segment_count", "from": td.value, "to": new_total})
            object.__setattr__(self, "total_duration_min", Decision(value=new_total, provenance="director", rationale="recomputed from segment_count × segment_duration_min"))

    def to_overlay(self) -> dict[str, Any]:
        script_overlay = {"words_per_segment": self.words_per_segment.value, "default_images_per_segment": self.images_per_segment.value}
        if self.images_per_segment.locked:
            script_overlay["dynamic_image_count"] = False
        return {"video": {"total_duration_min": self.total_duration_min.value, "segment_duration_min": self.segment_duration_min.value}, "script": script_overlay, "_decision_record": self.model_dump()}

    def provenance_report(self) -> dict[str, Any]:
        fields = {}
        for fname in ("total_duration_min", "segment_count", "segment_duration_min", "words_per_segment", "images_per_segment", "end_mode", "run_mode"):
            d: Decision = getattr(self, fname)
            fields[fname] = {"value": d.value, "provenance": d.provenance, "locked": d.locked, "rationale": d.rationale}
        return {"schema_version": self.version, "fields": fields, "resolved": {"segment_count": self.segment_count.value, "total_duration_min": self.total_duration_min.value, "words_per_segment": self.words_per_segment.value, "images_per_segment": self.images_per_segment.value}, "adjustments": self.adjustments}


def build_default_decision_record(config: dict) -> "DecisionRecord":
    rec = DecisionRecord()
    video = config.get("video", {})
    script = config.get("script", {})
    rec.set("total_duration_min", video.get("total_duration_min", 10), "default")
    rec.set("segment_count", max(1, -(-video.get("total_duration_min", 10) // max(1, video.get("segment_duration_min", 2)))), "default")
    rec.set("segment_duration_min", video.get("segment_duration_min", 2), "default")
    rec.set("words_per_segment", script.get("words_per_segment", 130), "default")
    rec.set("images_per_segment", script.get("default_images_per_segment", 6), "default")
    return rec


def migrate_decision_record(raw: dict, from_version: int) -> dict:
    if from_version < 1:
        for field in ("total_duration_min", "segment_count", "segment_duration_min", "words_per_segment", "images_per_segment"):
            if field in raw and not isinstance(raw[field], dict):
                raw[field] = {"value": raw[field], "provenance": "default", "locked": False}
        raw["version"] = 1
    return raw


def load_decision_record(raw: dict, config: dict | None = None) -> "DecisionRecord":
    from pydantic import ValidationError

    v = raw.get("version", 0)
    if v < DECISION_SCHEMA_VERSION:
        raw = migrate_decision_record(raw, from_version=v)
    try:
        return DecisionRecord(**raw)
    except (ValidationError, Exception) as e:
        _dr_log.warning(f"[DECISION] Record unmigratable ({e}) — rebuilding from config")
        return build_default_decision_record(config or {})
