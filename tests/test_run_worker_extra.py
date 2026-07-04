from unittest.mock import MagicMock, patch

import pytest

from jobs import run_worker


def test_main_once_no_job_and_with_job(tmp_path):
    worker = MagicMock()
    worker.run_once.side_effect = [None, 7]
    worker.store.get_job.return_value = {"status": "succeeded"}

    with (
        patch("sys.argv", ["run_worker.py", "--once", "--db-path", str(tmp_path / "jobs.db")]),
        patch("jobs.run_worker.JobStore") as store_cls,
        patch("jobs.run_worker.Worker", return_value=worker) as worker_cls,
    ):
        run_worker.main()
        run_worker.main()

    store_cls.assert_called_with(db_path=tmp_path / "jobs.db")
    worker_cls.assert_called()
    assert worker.run_once.call_count == 2
    worker.store.get_job.assert_called_once_with(7)


def test_main_once_failure_exits():
    worker = MagicMock()
    worker.run_once.side_effect = RuntimeError("boom")

    with (
        patch("sys.argv", ["run_worker.py", "--once"]),
        patch("jobs.run_worker.Worker", return_value=worker),
        pytest.raises(SystemExit) as exc,
    ):
        run_worker.main()

    assert exc.value.code == 1


def test_main_forever_keyboard_and_unrecoverable_error():
    worker = MagicMock()
    worker.run_forever.side_effect = KeyboardInterrupt
    with (
        patch("sys.argv", ["run_worker.py"]),
        patch("jobs.run_worker.Worker", return_value=worker),
    ):
        run_worker.main()

    worker.run_forever.side_effect = RuntimeError("boom")
    with (
        patch("sys.argv", ["run_worker.py"]),
        patch("jobs.run_worker.Worker", return_value=worker),
        pytest.raises(SystemExit) as exc,
    ):
        run_worker.main()

    assert exc.value.code == 1
