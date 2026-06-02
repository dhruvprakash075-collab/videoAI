"""Tests for utils.shutdown."""

import signal

import pytest

from utils import shutdown


class TestIsShuttingDown:
    def test_false_initially(self):
        shutdown._reset_for_tests()
        assert shutdown.is_shutting_down() is False


class TestCleanupHooks:
    def test_hooks_run_in_order(self):
        shutdown._reset_for_tests()
        order: list[str] = []
        shutdown.register_cleanup_hook(lambda: order.append("a"))
        shutdown.register_cleanup_hook(lambda: order.append("b"))
        shutdown.register_cleanup_hook(lambda: order.append("c"))
        shutdown._run_cleanup_hooks()
        assert order == ["a", "b", "c"]

    def test_hook_exception_does_not_stop_chain(self):
        shutdown._reset_for_tests()
        order: list[str] = []
        shutdown.register_cleanup_hook(lambda: order.append("a"))
        def boom():
            order.append("b")
            raise RuntimeError("kaboom")
        shutdown.register_cleanup_hook(boom)
        shutdown.register_cleanup_hook(lambda: order.append("c"))
        shutdown._run_cleanup_hooks()
        assert order == ["a", "b", "c"]  # c ran despite b raising

    def test_hook_with_name_used_in_logs(self, caplog):
        shutdown._reset_for_tests()
        def named_hook():
            pass
        named_hook.__name__ = "my_named_hook"
        shutdown.register_cleanup_hook(named_hook)
        with caplog.at_level("INFO", logger="utils.shutdown"):
            shutdown._run_cleanup_hooks()
        assert "my_named_hook" in caplog.text


class TestRegisterShutdownHandlers:
    def test_registers_once(self):
        shutdown._reset_for_tests()
        assert shutdown.register_shutdown_handlers() is True
        # Second call still returns True (idempotent)
        assert shutdown.register_shutdown_handlers() is True

    def test_signal_handler_can_be_invoked(self):
        """Simulate Ctrl-C by calling the handler directly. It calls
        sys.exit, so we patch sys.exit to capture."""
        shutdown._reset_for_tests()
        shutdown.register_cleanup_hook(lambda: None)
        with pytest.raises(SystemExit) as exc:
            shutdown._handle_signal(signal.SIGINT, None)
        assert exc.value.code == 128 + signal.SIGINT.value
        assert shutdown.is_shutting_down() is True

    def test_second_signal_exits_with_same_code(self):
        shutdown._reset_for_tests()
        shutdown.register_shutdown_handlers()
        with pytest.raises(SystemExit):
            shutdown._handle_signal(signal.SIGINT, None)
        # After first signal, _shutting_down is set. Second call should also exit.
        with pytest.raises(SystemExit) as exc:
            shutdown._handle_signal(signal.SIGINT, None)
        assert exc.value.code == 128 + signal.SIGINT.value
