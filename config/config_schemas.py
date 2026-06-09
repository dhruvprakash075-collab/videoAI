"""Pydantic schemas for the Video.AI pipeline.

Replaces loose Dict typing with validated models.
Provides: type safety, schema-aware LLM generation, deterministic serialization.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    tts_recommendation: Literal["supertonic", "omnivoice", "f5", "edge", "indicf5"] = "supertonic"
    subtitle_style: SubtitleConfig = SubtitleConfig()
    ambiguity_detected: bool = False
    ambiguity_question: str = ""
    ambiguity_fields: list[str] = []
    recommendations: list[str] = []
    segment_count: int = Field(default=3, ge=1, le=20)
    words_per_segment: int = Field(default=130, ge=50, le=800)  # aligned with config.yaml
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
    words_per_segment: int = Field(default=130, ge=50, le=800)  # aligned with config.yaml
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
    name: str
    description: str = ""
    keywords: list[str] = []
    voice_sample: str = ""


class VisualConfig(BaseModel):
    num_scenes: int = Field(default=4, ge=1, le=30)
    style: str = "hybrid 2d anime visual novel style"


class TTSConfig(BaseModel):
    engine: Literal["supertonic", "omnivoice", "f5", "edge", "indicf5"] = "supertonic"
    lang: str = "hi"
    # Fish Speech specific settings
    fish_speech_model_path: str = "C:/models/s2-pro-q5_k_m.gguf"
    fish_speech_temperature: float = 0.7
    fish_speech_top_p: float = 0.9
    fish_speech_repetition_penalty: float = 1.5
    alignment: "AlignmentConfig" = Field(default_factory=lambda: AlignmentConfig())


class ScriptConfig(BaseModel):
    words_per_segment: int = Field(default=130, ge=50, le=800)  # aligned with config.yaml
    dynamic_image_count: bool = True
    default_images_per_segment: int = Field(default=6, ge=1, le=30)
    shot_distribution: dict[str, float] = {}


class SubtitleOverlay(BaseModel):
    format: Literal["classic", "tiktok", "none"] = "classic"
    font: str = "Arial"
    size: int = Field(default=24, ge=8, le=72)
    color: str = "&H00FFFFFF&"
    position: str = "bottom"
    language: str = "en"  # 2026-06-02: force English-only subtitle text; whisper `task=translate` if audio is non-English


class PacingConfig(BaseModel):
    style: str = "moderate"
    opening_hook: str = ""
    notes: str = ""


class VideoConfig(BaseModel):
    # P4-23 fix: float allows fractional minutes. Decision record still
    # stores value as `Decision(value: Any)` so int inputs keep working.
    total_duration_min: float = Field(default=10, ge=0.5, le=600)
    segment_duration_min: float = Field(default=2, ge=0.5, le=30)


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
    enabled: bool = False
    platform: Literal["youtube"] = "youtube"
    visibility: Literal["public", "private", "unlisted"] = "private"
    profile_dir: str = "chrome_profile"


# ── Phase 0: New top-level config sections ─────────────────────────
class AlignmentConfig(BaseModel):
    enabled: bool = True
    model: str = "base"
    device: str = "cpu"
    compute_type: str = "int8"


class CriticConfig(BaseModel):
    enabled: bool = True
    threshold: int = 60
    max_rewrites: int = 2


class ResearchConfig(BaseModel):
    enabled: bool = True
    sources: list[str] = Field(default_factory=lambda: ["wikipedia", "wikimedia", "rss"])
    rss_urls: list[str] = Field(default_factory=list)
    user_agent: str = "VideoAI/6.0 (+https://github.com/...)"
    budget: int = 3
    timeout_s: int = 15
    per_source_limit: int = 3


class SEOConfig(BaseModel):
    enabled: bool = True
    title_max_chars: int = 100
    description_max_chars: int = 5000
    tags_count: int = 15
    hashtags_count: int = 5
    chapters_max: int = 50
    description_paragraphs: int = 2


class SourceConfig(BaseModel):
    allowed_extensions: list[str] = Field(default_factory=lambda: [".txt", ".md", ".pdf", ".docx"])
    max_words: int = 50000
    url_timeout_s: int = 30
    user_agent: str = "VideoAI/6.0 (+https://github.com/...)"


class VideoAIConfig(BaseModel):
    """Top-level config mirror for the Phase 0 verification check.

    Loads the 4 new top-level sections (critic, research, seo, source) plus
    an aligned TTS sub-config. The raw YAML is still loaded permissively by
    `config.load_config()`; this class is for typed access during tests and
    for code that wants schema validation.
    """

    model_config = {"extra": "allow"}
    critic: CriticConfig = Field(default_factory=CriticConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)
    seo: SEOConfig = Field(default_factory=SEOConfig)
    source: SourceConfig = Field(default_factory=SourceConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)

    @classmethod
    def from_dict(cls, raw: dict) -> "VideoAIConfig":
        """Build a VideoAIConfig from a raw config dict (skip-validation)."""
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


def validate_config(raw_config: dict) -> dict:
    """Best-effort full-config validation. Returns sanitized dict.

    Unlike `validate_or_default` (per-section), this validates the top-level
    config. Currently permissive — returns raw config on any validation
    failure. Use the per-section `validate_or_default` / `*_from_dict`
    helpers for stricter checks. The bool/int/float keys round-trip
    unchanged so callers can rely on `cfg.get(...)` lookups post-validate.
    """
    if not isinstance(raw_config, dict):
        return {}
    return raw_config


# ══════════════════════════════════════════════════════════════════════════════
# ── Decision Record (single source of truth for structural decisions) ─────────
# ══════════════════════════════════════════════════════════════════════════════

import logging as _log_dr  # noqa: E402  (placed after the schema section for readability)

_dr_log = _log_dr.getLogger(__name__)

DECISION_SCHEMA_VERSION = 1

Provenance = Literal["default", "director", "writer", "user", "cli_flag"]

EndMode = Literal["full", "cliffhanger", "compact"]
RunMode = Literal["project", "one_time"]


class Decision(BaseModel):
    """A single structural value with its origin and lock state."""

    model_config = {"extra": "allow"}
    value: Any
    provenance: Provenance = "default"
    locked: bool = False  # True → downstream MUST NOT change
    rationale: str = ""  # optional, e.g. Writer's reason for adjustment


class PerSegmentOverride(BaseModel):
    """Optional per-segment word/image override (data model present in v1; CLI not yet exposed)."""

    seg: int = Field(ge=1)
    words: int | None = Field(default=None, ge=50, le=800)
    images: int | None = Field(default=None, ge=1, le=30)
    locked: bool = False


class DecisionConflict(Exception):
    """Raised when two locked structural values are mutually inconsistent."""

    pass


class DecisionRecord(BaseModel):
    """Versioned, provenance-tracked record of all structural decisions for a run.

    Authority order (lowest → highest):
        default < director < writer < user / cli_flag

    Only user / cli_flag provenance may lock a field.
    """

    model_config = {"extra": "allow"}

    version: int = DECISION_SCHEMA_VERSION

    # ── Core structural decisions ──────────────────────────────────────────
    total_duration_min: Decision = Field(
        default_factory=lambda: Decision(value=10, provenance="default")
    )
    segment_count: Decision = Field(default_factory=lambda: Decision(value=5, provenance="default"))
    segment_duration_min: Decision = Field(
        default_factory=lambda: Decision(value=2, provenance="default")
    )
    words_per_segment: Decision = Field(
        default_factory=lambda: Decision(value=130, provenance="default")
    )
    images_per_segment: Decision = Field(
        default_factory=lambda: Decision(value=6, provenance="default")
    )

    # ── Optional per-segment fine control ─────────────────────────────────
    per_segment: list[PerSegmentOverride] = Field(default_factory=list)

    # ── Length-control outcome ─────────────────────────────────────────────
    end_mode: Decision = Field(default_factory=lambda: Decision(value="full", provenance="default"))
    cliffhanger_point: Decision | None = None  # % through story (0–100)

    # ── Run mode ──────────────────────────────────────────────────────────
    run_mode: Decision = Field(
        default_factory=lambda: Decision(value="one_time", provenance="default")
    )
    project_name: str | None = None

    # ── Audit trail ───────────────────────────────────────────────────────
    adjustments: list[dict[str, Any]] = Field(default_factory=list)

    # ── Authority ranking ─────────────────────────────────────────────────
    _AUTHORITY: dict[str, int] = {
        "default": 0,
        "director": 1,
        "writer": 2,
        "user": 3,
        "cli_flag": 3,
    }

    def _rank(self, provenance: str) -> int:
        return self._AUTHORITY.get(provenance, 0)

    def set(
        self,
        field: str,
        value: Any,
        provenance: Provenance,
        *,
        lock: bool = False,
        rationale: str = "",
    ) -> bool:
        """Set a structural field respecting lock immutability.

        Returns True if the value was applied, False if blocked by a lock.
        Only user/cli_flag provenance may lock or overwrite a locked field.
        """
        current: Decision | None = getattr(self, field, None)
        if current is None:
            return False

        incoming_rank = self._rank(provenance)
        can_lock = provenance in ("user", "cli_flag")

        # Blocked: current is locked and incoming is not user/cli_flag
        if current.locked and not can_lock:
            _dr_log.debug(
                f"[DECISION] '{field}' locked by {current.provenance} — "
                f"ignoring {provenance} proposal (value={value})"
            )
            return False

        # Blocked: lower-authority write on an unlocked field
        if not current.locked and incoming_rank < self._rank(current.provenance):
            _dr_log.debug(
                f"[DECISION] '{field}' already set by higher authority "
                f"({current.provenance} > {provenance}) — ignoring"
            )
            return False

        # Apply clamp for known numeric fields
        clamped_value = self._clamp(field, value)
        if clamped_value != value:
            self.adjustments.append(
                {
                    "field": field,
                    "type": "clamp",
                    "from": value,
                    "to": clamped_value,
                    "provenance": provenance,
                }
            )
            value = clamped_value

        object.__setattr__(
            self,
            field,
            Decision(
                value=value,
                provenance=provenance,
                locked=lock if can_lock else False,
                rationale=rationale,
            ),
        )
        _dr_log.debug(
            f"[DECISION] '{field}' = {value} (provenance={provenance}, locked={lock and can_lock})"
        )
        return True

    @staticmethod
    def _clamp(field: str, value: Any) -> Any:
        """Clamp numeric structural fields to safe ranges."""
        clamps = {
            "total_duration_min": (1, 600),
            "segment_count": (1, 200),
            "segment_duration_min": (1, 30),
            "words_per_segment": (50, 800),
            "images_per_segment": (1, 30),
        }
        if field in clamps and isinstance(value, (int, float)):
            lo, hi = clamps[field]
            # P4-23 fix: preserve float type so fractional minutes survive the clamp.
            return max(lo, min(hi, value))
        return value

    def resolve_conflicts(self) -> None:
        """Reconcile segment_count vs total_duration_min / segment_duration_min.

        Rules (per Req 3.3, 4.2):
        - Both locked and inconsistent → raise DecisionConflict (never silent).
        - One locked → locked one wins; recompute the other.
        - Neither locked → prefer segment_count; recompute total_duration_min.
        """
        sc = self.segment_count
        td = self.total_duration_min
        sdm = self.segment_duration_min

        derived_total = sc.value * sdm.value
        derived_segs = max(1, -(-td.value // sdm.value))  # ceiling div

        if abs(derived_total - td.value) <= 1:
            return  # consistent enough

        both_locked = sc.locked and td.locked
        if both_locked:
            raise DecisionConflict(
                f"Conflict: segment_count={sc.value} × segment_duration={sdm.value}min "
                f"= {derived_total}min, but total_duration_min is locked to {td.value}min. "
                f"Please resolve by unlocking one of them."
            )

        if sc.locked:
            # segment_count wins → recompute total_duration_min
            new_total = sc.value * sdm.value
            self.adjustments.append(
                {
                    "field": "total_duration_min",
                    "type": "conflict_resolved",
                    "rule": "segment_count locked → total recomputed",
                    "from": td.value,
                    "to": new_total,
                }
            )
            object.__setattr__(
                self,
                "total_duration_min",
                Decision(
                    value=new_total,
                    provenance=sc.provenance,
                    locked=False,
                    rationale="recomputed from locked segment_count × segment_duration_min",
                ),
            )
            _dr_log.info(
                f"[DECISION] Conflict resolved: segment_count locked ({sc.value}) → "
                f"total_duration_min recomputed to {new_total}min"
            )
        elif td.locked:
            # total_duration_min wins → recompute segment_count
            self.adjustments.append(
                {
                    "field": "segment_count",
                    "type": "conflict_resolved",
                    "rule": "total_duration_min locked → segment_count recomputed",
                    "from": sc.value,
                    "to": derived_segs,
                }
            )
            object.__setattr__(
                self,
                "segment_count",
                Decision(
                    value=derived_segs,
                    provenance=td.provenance,
                    locked=False,
                    rationale="recomputed from locked total_duration_min / segment_duration_min",
                ),
            )
            _dr_log.info(
                f"[DECISION] Conflict resolved: total_duration_min locked ({td.value}min) → "
                f"segment_count recomputed to {derived_segs}"
            )
        else:
            # Neither locked → prefer segment_count, recompute total
            new_total = sc.value * sdm.value
            self.adjustments.append(
                {
                    "field": "total_duration_min",
                    "type": "conflict_resolved",
                    "rule": "neither locked → prefer segment_count",
                    "from": td.value,
                    "to": new_total,
                }
            )
            object.__setattr__(
                self,
                "total_duration_min",
                Decision(
                    value=new_total,
                    provenance="director",
                    rationale="recomputed from segment_count × segment_duration_min",
                ),
            )
            _dr_log.info(
                f"[DECISION] Conflict resolved (prefer segment_count={sc.value}) → "
                f"total_duration_min={new_total}min"
            )

    def to_overlay(self) -> dict[str, Any]:
        """Flatten to the existing config-overlay shape for _deep_merge compatibility."""
        script_overlay = {
            "words_per_segment": self.words_per_segment.value,
            "default_images_per_segment": self.images_per_segment.value,
        }
        # When the image count is explicitly locked (user/CLI), force a fixed
        # image count so the Director's per-segment num_images can't override it.
        if self.images_per_segment.locked:
            script_overlay["dynamic_image_count"] = False
        return {
            "video": {
                "total_duration_min": self.total_duration_min.value,
                "segment_duration_min": self.segment_duration_min.value,
            },
            "script": script_overlay,
            "_decision_record": self.model_dump(),
        }

    def provenance_report(self) -> dict[str, Any]:
        """Return a manifest-ready dict of all fields with provenance."""
        fields = {}
        for fname in (
            "total_duration_min",
            "segment_count",
            "segment_duration_min",
            "words_per_segment",
            "images_per_segment",
            "end_mode",
            "run_mode",
        ):
            d: Decision = getattr(self, fname)
            fields[fname] = {
                "value": d.value,
                "provenance": d.provenance,
                "locked": d.locked,
                "rationale": d.rationale,
            }
        return {
            "schema_version": self.version,
            "fields": fields,
            "resolved": {
                "segment_count": self.segment_count.value,
                "total_duration_min": self.total_duration_min.value,
                "words_per_segment": self.words_per_segment.value,
                "images_per_segment": self.images_per_segment.value,
            },
            "adjustments": self.adjustments,
        }


# ── Schema versioning & migration ─────────────────────────────────────────────


def build_default_decision_record(config: dict) -> "DecisionRecord":
    """Build a DecisionRecord from an existing config dict (provenance=default/cli_flag)."""
    rec = DecisionRecord()
    video = config.get("video", {})
    script = config.get("script", {})
    rec.set("total_duration_min", video.get("total_duration_min", 10), "default")
    rec.set(
        "segment_count",
        max(
            1,
            -(-video.get("total_duration_min", 10) // max(1, video.get("segment_duration_min", 2))),
        ),
        "default",
    )
    rec.set("segment_duration_min", video.get("segment_duration_min", 2), "default")
    rec.set("words_per_segment", script.get("words_per_segment", 130), "default")
    rec.set("images_per_segment", script.get("default_images_per_segment", 6), "default")
    return rec


def migrate_decision_record(raw: dict, from_version: int) -> dict:
    """Apply ordered, idempotent migration steps from_version → current."""
    # v0 → v1: wrap bare numeric fields into Decision dicts
    if from_version < 1:
        for field in (
            "total_duration_min",
            "segment_count",
            "segment_duration_min",
            "words_per_segment",
            "images_per_segment",
        ):
            if field in raw and not isinstance(raw[field], dict):
                raw[field] = {"value": raw[field], "provenance": "default", "locked": False}
        raw["version"] = 1
    return raw


def load_decision_record(raw: dict, config: dict | None = None) -> "DecisionRecord":
    """Load a DecisionRecord from a raw dict, migrating if needed.

    Falls back to building from config on any unrecoverable error.
    """
    from pydantic import ValidationError

    v = raw.get("version", 0)
    if v < DECISION_SCHEMA_VERSION:
        raw = migrate_decision_record(raw, from_version=v)
    try:
        return DecisionRecord(**raw)
    except (ValidationError, Exception) as e:
        _dr_log.warning(f"[DECISION] Record unmigratable ({e}) — rebuilding from config")
        return build_default_decision_record(config or {})
