"""Optional Sentry initialization.

Sentry stays inert unless SENTRY_DSN is set. This keeps local runs offline by
default while letting operators opt into runtime crash capture.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def init_sentry() -> None:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    try:
        import sentry_sdk
    except ImportError:
        log.warning("Sentry DSN is set but sentry-sdk is not installed")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "local"),
        release=os.environ.get("SENTRY_RELEASE"),
        send_default_pii=False,
        enable_logs=True,
    )


def capture_smoke_exception() -> None:
    """Send a tiny test event when Sentry is configured.

    This is meant for a one-shot smoke check, not regular runtime use.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return

    try:
        import sentry_sdk
    except ImportError:
        return

    try:
        raise RuntimeError("Video.AI Sentry smoke test")
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)
