import unittest
from types import SimpleNamespace
from unittest.mock import patch

from codexws_server.context import ApplicationContext


class ApplicationContextTests(unittest.TestCase):
    def runtime(self, calls, fail_on=None):
        def operation(name):
            def run():
                calls.append(name)
                if name == fail_on:
                    raise RuntimeError(name)
            return run

        return SimpleNamespace(
            start_workspace_terminals=operation("start_terminals"),
            start_workspace_supervisor=operation("start_supervisor"),
            start_workspace_tcp_servers=operation("start_tcp"),
            stop_workspace_supervisor=operation("stop_supervisor"),
            close_workspace_tcp_servers=operation("stop_tcp"),
            cleanup_all_terminals=operation("stop_terminals"),
        )

    def test_start_and_stop_are_ordered_and_idempotent(self):
        calls = []
        context = ApplicationContext(self.runtime(calls), workspace_list=lambda: [])

        with patch("codexws_server.context.atexit.register") as register:
            self.assertTrue(context.start())
            self.assertFalse(context.start())
            self.assertTrue(context.started)
            self.assertTrue(context.stop())
            self.assertFalse(context.stop())
            self.assertFalse(context.started)

        self.assertEqual(calls, [
            "start_terminals",
            "start_supervisor",
            "start_tcp",
            "stop_supervisor",
            "stop_tcp",
            "stop_terminals",
        ])
        register.assert_called_once()

    def test_atexit_registration_is_reused_after_restart(self):
        calls = []
        context = ApplicationContext(self.runtime(calls), workspace_list=lambda: [])

        with patch("codexws_server.context.atexit.register") as register:
            context.start()
            context.stop()
            context.start()
            context.stop()

        register.assert_called_once()

    def test_failed_start_rolls_back_every_runtime_component(self):
        calls = []
        context = ApplicationContext(
            self.runtime(calls, fail_on="start_supervisor"),
            workspace_list=lambda: [],
        )

        with patch("codexws_server.context.atexit.register") as register:
            with self.assertRaisesRegex(RuntimeError, "start_supervisor"):
                context.start()

        self.assertFalse(context.started)
        self.assertFalse(context.stop())
        self.assertEqual(calls, [
            "start_terminals",
            "start_supervisor",
            "stop_supervisor",
            "stop_tcp",
            "stop_terminals",
        ])
        register.assert_not_called()


if __name__ == "__main__":
    unittest.main()
