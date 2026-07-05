import os

import utils.sentry as sentry_mod


def test_sentry_helpers_are_noops_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    sentry_mod.init_sentry()
    sentry_mod.capture_smoke_exception()

    assert "SENTRY_DSN" not in os.environ
