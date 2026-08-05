from unittest.mock import patch

import pytest

import bootstrap_pipeline as bp


def test_parser_accepts_sentry_smoke():
    args = bp._build_parser().parse_args(["--sentry-smoke"])
    assert args.sentry_smoke is True


def test_smoke_branch_calls_sentry_and_exits(monkeypatch):
    monkeypatch.setattr("sys.argv", ["bootstrap_pipeline.py", "--sentry-smoke"])
    calls = []
    monkeypatch.setattr(
        "utils.sentry.capture_smoke_exception", lambda: calls.append("sent")
    )
    with pytest.raises(SystemExit) as exc:
        bp.run_pipeline_with_args()
    assert exc.value.code == 0
    assert len(calls) == 1


def test_smoke_branch_failure_exits_nonzero(monkeypatch):
    monkeypatch.setattr("sys.argv", ["bootstrap_pipeline.py", "--sentry-smoke"])
    monkeypatch.setattr(
        "utils.sentry.capture_smoke_exception",
        lambda: (_ for _ in ()).throw(RuntimeError("sentry down")),
    )
    with patch("sys.stdout"):
        with pytest.raises(SystemExit) as exc:
            bp.run_pipeline_with_args()
    assert exc.value.code == 1
