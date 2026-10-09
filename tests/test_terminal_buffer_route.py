import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from codexws_server.ids import normalize_terminal_id, terminal_key
from codexws_server.routes.workspaces import register_workspace_routes
from codexws_server.terminal_history import TerminalHistoryBuffer


class TerminalBufferRouteTests(unittest.TestCase):
    def setUp(self):
        history = TerminalHistoryBuffer(100)
        history.append("a😀bc终d")
        self.info = {
            "name": "Main",
            "workspace_id": "alpha",
            "terminal_id": "main",
            "alive": True,
            "codex_active": False,
            "history_buffer": history,
            "history_start": history.start,
            "history_end": history.end,
            "lock": threading.Lock(),
        }
        self.rename_calls = []
        self.ensure_calls = []
        self.write_calls = []
        self.terminals = {terminal_key("alpha", "main"): self.info}

        def rename_terminal_session(workspace_id, terminal_id, name):
            self.rename_calls.append((workspace_id, terminal_id, name))
            return True

        def ensure_terminal(workspace_id, **kwargs):
            self.ensure_calls.append((workspace_id, kwargs))
            terminal_id = kwargs.get("terminal_id") or "main"
            key = terminal_key(workspace_id, terminal_id)
            info = self.terminals.get(key)
            if info is None:
                info = {
                    "name": kwargs.get("name") or terminal_id,
                    "workspace_id": workspace_id,
                    "terminal_id": terminal_id,
                    "thread_id": kwargs.get("thread_id"),
                    "managed": bool(kwargs.get("command")),
                    "alive": True,
                    "codex_active": False,
                    "lock": threading.Lock(),
                }
                self.terminals[key] = info
            elif kwargs.get("thread_id"):
                info["thread_id"] = kwargs["thread_id"]
            return info

        def terminal_summary_for_test(key, info):
            workspace_id, terminal_id = key.split(":", 1)
            return {
                "id": terminal_id,
                "name": info["name"],
                "workspace_id": workspace_id,
                "thread_id": info.get("thread_id"),
                "codex_active": bool(info.get("codex_active")),
                "managed": bool(info.get("managed")),
                "alive": bool(info.get("alive")),
                "usable": bool(info.get("alive")),
            }

        def workspace_terminal_summaries():
            return {
                "alpha": [
                    terminal_summary_for_test(key, info)
                    for key, info in self.terminals.items()
                    if key.startswith("alpha:")
                ],
            }

        def write_terminal(workspace_id, data, **kwargs):
            self.write_calls.append((workspace_id, data, kwargs))
            return True

        self.app = Flask(__name__)
        register_workspace_routes(self.app, SimpleNamespace(
            request_workspace_id=lambda data: data.get("workspace_id") or "alpha",
            workspace_exists=lambda workspace_id: workspace_id == "alpha",
            normalize_terminal_id=normalize_terminal_id,
            terminal_key=terminal_key,
            split_terminal_key=lambda key: tuple(key.split(":", 1)),
            terminals=self.terminals,
            terminals_lock=threading.Lock(),
            ensure_terminal=ensure_terminal,
            rename_terminal_session=rename_terminal_session,
            terminal_summary=terminal_summary_for_test,
            workspace_terminal_summaries=workspace_terminal_summaries,
            write_terminal=write_terminal,
            workspace_item=lambda workspace_id: {
                "id": workspace_id,
                "name": "Alpha",
                "cwd": "/tmp",
                "exists": True,
            },
            terminal_history_page_size=131072,
            terminal_screen_frame=lambda workspace_id, terminal_id: {
                "workspace_id": workspace_id,
                "terminal_id": terminal_id,
                "stream_id": "stream-alpha",
                "frame_seq": 7,
                "frame_end": 6,
                "history_start": 0,
                "history_end": 6,
                "screen_snapshot": "\x1b[2J参考：",
                "screen_snapshot_end": 6,
                "screen_snapshot_cols": 80,
                "screen_snapshot_rows": 24,
                "screen_snapshot_source": "server",
            },
        ))
        self.client = self.app.test_client()

    def test_forward_range_uses_server_code_point_offsets(self):
        response = self.client.get(
            "/api/terminal-buffer?workspace_id=alpha&terminal_id=main&after=1&before=5&limit=4096"
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["buffer"], "😀bc终")
        self.assertEqual((data["start"], data["end"]), (1, 5))
        self.assertFalse(data["has_more_after"])

    def test_screen_route_returns_versioned_authoritative_frame(self):
        response = self.client.get("/api/terminal-screen?workspace_id=alpha&terminal_id=main")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["screen_frame_supported"])
        self.assertEqual((data["stream_id"], data["frame_seq"], data["frame_end"]), ("stream-alpha", 7, 6))
        self.assertEqual(data["screen_snapshot"], "\x1b[2J参考：")

    def test_rename_terminal_updates_runtime_summary(self):
        response = self.client.post(
            "/api/workspace-terminals/rename",
            json={"workspace_id": "alpha", "terminal_id": "main", "name": "开发终端"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.rename_calls, [("alpha", "main", "开发终端")])
        self.assertEqual(self.info["name"], "开发终端")
        self.assertEqual(response.get_json()["terminal"]["name"], "开发终端")

    def test_rename_terminal_rejects_blank_name(self):
        response = self.client.post(
            "/api/workspace-terminals/rename",
            json={"workspace_id": "alpha", "terminal_id": "main", "name": "   "},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rename_calls, [])

    def test_create_terminal_stays_unbound(self):
        response = self.client.post(
            "/api/workspace-terminals/create",
            json={"workspace_id": "alpha", "name": "Build"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.ensure_calls), 1)
        workspace_id, kwargs = self.ensure_calls[0]
        self.assertEqual(workspace_id, "alpha")
        self.assertNotIn("thread_id", kwargs)
        self.assertIsNone(response.get_json()["terminal"]["thread_id"])

    def test_create_terminal_rejects_legacy_history_protocol(self):
        thread_id = "019f83e9-b610-7582-8cc2-49a5a915571d"
        response = self.client.post(
            "/api/workspace-terminals/create",
            json={"workspace_id": "alpha", "name": "VO 前端", "thread_id": thread_id},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.ensure_calls, [])
        self.assertIn("刷新页面", response.get_json()["error"])

    @patch("codexws_server.routes.workspaces.launch_argv", return_value=["fake-cli", "resume", "--no-daemon", "thread"] )
    def test_resume_thread_reuses_terminal_and_launches_once(self, launch):
        thread_id = "019f83e9-b610-7582-8cc2-49a5a915571d"
        first = self.client.post(
            "/api/workspace-terminals/resume",
            json={"workspace_id": "alpha", "name": "VO 前端", "thread_id": thread_id},
        )

        self.assertEqual(first.status_code, 200)
        first_data = first.get_json()
        self.assertTrue(first_data["created"])
        self.assertTrue(first_data["resume_started"])
        self.assertEqual(len(self.ensure_calls), 1)
        workspace_id, kwargs = self.ensure_calls[0]
        self.assertEqual(workspace_id, "alpha")
        self.assertEqual(kwargs["thread_id"], thread_id)
        self.assertEqual(first_data["terminal"]["thread_id"], thread_id)
        self.assertEqual(kwargs["command"], launch.return_value)
        self.assertEqual(self.write_calls, [])

        second = self.client.post(
            "/api/workspace-terminals/resume",
            json={"workspace_id": "alpha", "name": "VO 前端", "thread_id": thread_id},
        )

        self.assertEqual(second.status_code, 200)
        second_data = second.get_json()
        self.assertFalse(second_data["created"])
        self.assertTrue(second_data["reused"])
        self.assertFalse(second_data["resume_started"])
        self.assertEqual(second_data["terminal"]["id"], first_data["terminal"]["id"])
        self.assertEqual(len(self.ensure_calls), 1)
        self.assertEqual(self.write_calls, [])
        launch.assert_called_once_with("/tmp", thread_id)


if __name__ == "__main__":
    unittest.main()
