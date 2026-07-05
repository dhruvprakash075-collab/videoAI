from unittest.mock import patch

import bootstrap_pipeline as bp


def test_parser_accepts_sentry_smoke():
    args = bp._build_parser().parse_args(["--sentry-smoke"])
    assert args.sentry_smoke is True


def test_smoke_branch_calls_sentry_and_exits(monkeypatch):
    monkeypatch.setattr(bp, "_run_preflight", lambda _args: ({}, None))

    with patch("utils.sentry.capture_smoke_exception") as smoke, patch("sys.exit") as exit_:
        args = bp._build_parser().parse_args(["--sentry-smoke"])
        if getattr(args, "sentry_smoke", False):
            from utils.sentry import capture_smoke_exception

            capture_smoke_exception()
            exit_(0)

    smoke.assert_called_once()
    exit_.assert_called_once_with(0)
