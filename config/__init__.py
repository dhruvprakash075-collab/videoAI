"""config - Configuration management for Video.AI pipeline."""

from .config import _safe_filename, get_character, load_config
from .config_schemas import (
    DECISION_SCHEMA_VERSION,
    CharacterSpec,
    ConfigOverlay,
    Decision,
    DecisionConflict,
    # Decision Record
    DecisionRecord,
    PerSegmentOverride,
    Provenance,
    UserResponses,
    VisionDocument,
    WriterBreakdown,
    breakdown_from_dict,
    build_default_decision_record,
    load_decision_record,
    migrate_decision_record,
    overlay_from_dict,
    responses_from_dict,
    vision_from_dict,
)
