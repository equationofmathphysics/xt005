import importlib
import struct
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask, request


terminal_runtime = importlib.import_module("codexws_server.terminal_runtime")
from codexws_server.ids import terminal_key


class FakeSocketIO:
    def __init__(self):
        self.handlers = {}
        self.emitted = []

    def on_event(self, event, handler):
        self.handlers[event] = handler

    def emit(self, event, data, **kwargs):
        self.emitted.append((event, data, kwargs))


class DummyProcess:
    pid = None

    def poll(self):
        return None


class DummyThread:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def start(self):
        return None

    def is_alive(self):
        return True


class TerminalGeometryPureFunctionTests(unittest.TestCase):
    def test_clamp_terminal_geometry_accepts_valid_dimensions(self):
        self.assertEqual(terminal_runtime.clamp_terminal_geometry("132", 43), (132, 43))

    def test_clamp_terminal_geometry_bounds_and_defaults_invalid_dimensions(self):
        self.assertEqual(terminal_runtime.clamp_terminal_geometry(-10, 9999), (2, 500))
        self.assertEqual(terminal_runtime.clamp_terminal_geometry(0, 0), (2, 1))
        self.assertEqual(terminal_runtime.clamp_terminal_geometry("wide", None), (80, 24))

    def test_nonblocking_configuration_rejects_a_still_blocking_fd(self):
        with (
            patch.object(terminal_runtime.os, "set_blocking"),
            patch.object(terminal_runtime.os, "get_blocking", return_value=True),
            self.assertRaisesRegex(OSError, "remained blocking"),
        ):
            terminal_runtime.set_terminal_fd_nonblocking(10)


class TerminalGeometryEventTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.socketio = FakeSocketIO()
        self.live_terminals = {}
        self.bindings = {}
        self.patchers = [
            patch.object(terminal_runtime, "terminals", self.live_terminals),
            patch.object(terminal_runtime, "socket_bindings", self.bindings),
            patch.object(
                terminal_runtime,
                "terminal_info_usable",
                side_effect=lambda info: bool(info and info.get("alive")),
            ),
            patch.object(terminal_runtime, "emit"),
            patch.object(terminal_runtime, "join_room"),
            patch.object(terminal_runtime, "leave_room"),
            patch.object(terminal_runtime, "set_terminal_fd_nonblocking"),
            patch.object(terminal_runtime.fcntl, "ioctl"),
        ]
        started = [patcher.start() for patcher in self.patchers]
        self.addCleanup(self._stop_patchers)
        self.ioctl = started[-1]
        self.set_nonblocking = started[-2]

        self.deps = SimpleNamespace(
            default_workspace_id=lambda: "alpha",
            forget_terminal_session=Mock(return_value=True),
            record_terminal_session=Mock(return_value=True),
            record_terminal_thread=Mock(return_value=True),
            record_terminal_output_observation=Mock(),
            workspace_path=lambda workspace_id: "/tmp" if workspace_id in {"alpha", "beta"} else None,
            terminal_observation_summary=lambda key: {},
            terminal_sessions_loaded=lambda: True,
            workspace_exists=lambda workspace_id: workspace_id in {"alpha", "beta"},
            workspace_item=lambda workspace_id: {
                "id": workspace_id,
                "cwd": "/tmp",
                "exists": workspace_id in {"alpha", "beta"},
            },
            workspace_terminal_session_specs=lambda: {},
        )
        self.runtime = terminal_runtime.create_terminal_runtime(self.socketio, self.deps)
        self.runtime.register_socket_handlers()

    def _stop_patchers(self):
        for patcher in reversed(self.patchers):
            patcher.stop()

    def terminal_info(self, workspace_id, terminal_id, fd, alive=True):
        info = {
            "key": terminal_key(workspace_id, terminal_id),
            "workspace_id": workspace_id,
            "terminal_id": terminal_id,
            "name": terminal_id,
            "fd": fd,
            "pid": None,
            "buffer": "",
            "history_buffer": "",
            "history_start": 0,
            "history_end": 0,
            "alive": alive,
            "lock": threading.Lock(),
            "write_lock": threading.Lock(),
            "delivery_lock": threading.RLock(),
            "cwd": "/tmp",
        }
        self.live_terminals[info["key"]] = info
        return info

    def captured_runtime_terminal(self, workspace_id="alpha", terminal_id="main", master_fd=301):
        with (
            patch.object(terminal_runtime.pty, "openpty", return_value=(master_fd, master_fd + 1)),
            patch.object(terminal_runtime.subprocess, "Popen", return_value=DummyProcess()),
            patch.object(terminal_runtime.threading, "Thread", DummyThread),
            patch.object(terminal_runtime.os, "close"),
            patch.object(terminal_runtime, "WORKSPACE_AGENT_COMMANDS", {}),
        ):
            return self.runtime.ensure_terminal(
                workspace_id,
                terminal_id,
                reopen=True,
            )

    def bind(self, socket_id, workspace_id="alpha", terminal_id="main", view_only=False, attach_seq=1):
        self.bindings[socket_id] = {
            "workspace_id": workspace_id,
            "terminal_id": terminal_id,
            "key": terminal_key(workspace_id, terminal_id),
            "view_only": view_only,
            "attach_seq": attach_seq,
        }

    def invoke(self, event, socket_id, data=None):
        with self.app.test_request_context("/"):
            request.sid = socket_id
            handler = self.socketio.handlers[event]
            if event == "disconnect":
                return handler()
            return handler(data)

    def resized_geometries(self):
        return [
            (call.args[0],) + struct.unpack("HHHH", call.args[2])[:2]
            for call in self.ioctl.call_args_list
        ]

    def test_workspace_resize_updates_every_live_pty_in_bound_workspace(self):
        main = self.terminal_info("alpha", "main", 101)
        aux = self.terminal_info("alpha", "aux", 102)
        self.terminal_info("alpha", "dead", 103, alive=False)
        beta = self.terminal_info("beta", "main", 201)
        self.bind("socket-a")

        self.invoke("terminal_resize_workspace", "socket-a", {"cols": 5000, "rows": -5})

        self.assertCountEqual(self.resized_geometries(), [(101, 1, 1000), (102, 1, 1000)])
        self.assertEqual((main["cols"], main["rows"]), (1000, 1))
        self.assertEqual((aux["cols"], aux["rows"]), (1000, 1))
        self.assertNotIn("cols", beta)

    def test_workspace_resize_skips_unchanged_ptys(self):
        main = self.terminal_info("alpha", "main", 101)
        aux = self.terminal_info("alpha", "aux", 102)
        main.update(cols=132, rows=43)
        aux.update(cols=132, rows=43)
        self.bind("socket-a")

        self.invoke("terminal_resize_workspace", "socket-a", {"cols": 132, "rows": 43})

        self.assertEqual(self.ioctl.call_count, 0)

    def test_terminal_summary_does_not_expose_history_binding(self):
        info = self.terminal_info("alpha", "main", 101)

        summary = self.runtime.terminal_summary(terminal_key("alpha", "main"), info)

        self.assertNotIn("codex_session_id", summary)

    def test_terminal_summary_discovers_canonical_thread_from_foreground_codex(self):
        info = self.terminal_info("alpha", "main", 101)
        info["pid"] = 100
        descriptor = {
            "thread_id": "019f83e9-b610-7582-8cc2-49a5a915571d",
            "title": "VO 前端结构",
        }
        with (
            patch.object(terminal_runtime, "terminal_tree_pids", return_value=[100, 200]),
            patch.object(terminal_runtime, "pid_running", return_value=True),
            patch.object(terminal_runtime, "process_args", return_value=["codex", "--yolo"]),
            patch.object(terminal_runtime.os, "tcgetpgrp", return_value=44),
            patch.object(terminal_runtime.os, "getpgid", return_value=44),
            patch.object(terminal_runtime, "codex_thread_from_processes", return_value=descriptor) as detect,
        ):
            summary = self.runtime.terminal_summary(terminal_key("alpha", "main"), info)

        self.assertEqual(summary["thread_id"], descriptor["thread_id"])
        self.assertEqual(summary["thread_title"], "VO 前端结构")
        detect.assert_called_once()
        self.deps.record_terminal_thread.assert_called_once_with(
            "alpha", "main", descriptor["thread_id"]
        )

    def test_terminal_summary_refreshes_mapped_title_without_active_codex(self):
        info = self.terminal_info("alpha", "main", 101)
        thread_id = "019f83e9-b610-7582-8cc2-49a5a915571d"
        info["thread_id"] = thread_id
        info["thread_title"] = "旧标题"
        with patch.object(
            terminal_runtime,
            "codex_thread_title",
            return_value="跨客户端更新后的标题",
        ) as resolve_title:
            summary = self.runtime.terminal_summary(terminal_key("alpha", "main"), info)

        self.assertFalse(summary["codex_active"])
        self.assertEqual(summary["thread_id"], thread_id)
        self.assertEqual(summary["thread_title"], "跨客户端更新后的标题")
        resolve_title.assert_called_once_with(thread_id, "旧标题")

    def test_latest_claim_wins_and_disconnect_releases_owner(self):
        self.terminal_info("alpha", "main", 101)
        self.terminal_info("alpha", "aux", 102)
        self.bind("socket-a", terminal_id="main")
        self.bind("socket-b", terminal_id="aux")

        self.invoke(
            "terminal_resize_workspace",
            "socket-a",
            {"cols": 100, "rows": 30, "claim_geometry": True},
        )
        self.ioctl.reset_mock()
        self.invoke("terminal_resize_workspace", "socket-b", {"cols": 110, "rows": 31})
        self.assertEqual(self.ioctl.call_count, 0)

        self.invoke(
            "terminal_resize_workspace",
            "socket-b",
            {"cols": 120, "rows": 32, "claim_geometry": True},
        )
        self.assertEqual(self.ioctl.call_count, 2)
        self.ioctl.reset_mock()
        self.invoke("terminal_resize_workspace", "socket-a", {"cols": 130, "rows": 33})
        self.assertEqual(self.ioctl.call_count, 0)

        self.invoke("disconnect", "socket-b")
        self.invoke("terminal_resize_workspace", "socket-a", {"cols": 140, "rows": 34})
        self.assertEqual(self.ioctl.call_count, 2)

    def test_attach_keeps_legacy_single_pty_behavior_and_supports_workspace_sync(self):
        self.terminal_info("alpha", "main", 101)
        self.terminal_info("alpha", "aux", 102)

        self.invoke(
            "terminal_attach",
            "legacy",
            {"workspace_id": "alpha", "terminal_id": "main", "cols": 90, "rows": 28},
        )
        self.assertEqual(self.resized_geometries(), [(101, 28, 90)])

        self.ioctl.reset_mock()
        self.invoke(
            "terminal_attach",
            "modern",
            {
                "workspace_id": "alpha",
                "terminal_id": "aux",
                "cols": 132,
                "rows": 43,
                "claim_geometry": True,
                "sync_workspace_geometry": True,
            },
        )
        self.assertCountEqual(self.resized_geometries(), [(101, 43, 132), (102, 43, 132)])

        self.ioctl.reset_mock()
        self.invoke(
            "terminal_attach",
            "legacy-2",
            {"workspace_id": "alpha", "terminal_id": "main", "cols": 80, "rows": 24},
        )
        self.assertEqual(self.resized_geometries(), [(101, 24, 80)])

    def test_view_only_socket_cannot_claim_geometry(self):
        self.terminal_info("alpha", "main", 101)
        self.bind("viewer", view_only=True)
        self.bind("interactive")

        self.invoke(
            "terminal_resize_workspace",
            "viewer",
            {"cols": 200, "rows": 50, "claim_geometry": True},
        )
        self.assertEqual(self.ioctl.call_count, 0)
        self.invoke("terminal_resize_workspace", "interactive", {"cols": 100, "rows": 25})
        self.assertEqual(self.resized_geometries(), [(101, 25, 100)])

    def test_terminal_geometry_owners_are_independent_within_workspace(self):
        self.terminal_info("alpha", "main", 101)
        self.terminal_info("alpha", "aux", 102)
        self.bind("socket-a", terminal_id="main")
        self.bind("socket-b", terminal_id="aux")

        self.invoke("terminal_resize", "socket-a", {
            "cols": 100,
            "rows": 30,
            "claim_geometry": True,
        })
        self.invoke("terminal_resize", "socket-b", {
            "cols": 120,
            "rows": 32,
            "claim_geometry": True,
        })

        self.assertEqual(self.resized_geometries(), [(101, 30, 100), (102, 32, 120)])

    def test_resize_is_an_ordered_zero_length_stream_revision(self):
        info = self.terminal_info("alpha", "main", 101)
        info.update(
            cols=80,
            rows=24,
            history_start=10,
            history_end=42,
            frame_seq=7,
            stream_id="stream-alpha",
            stream_epoch=1234,
            screen_model=SimpleNamespace(resize=Mock()),
        )
        self.bind("socket-a")
        self.socketio.emitted.clear()

        self.invoke("terminal_resize", "socket-a", {"cols": 100, "rows": 30})

        self.assertEqual(info["frame_seq"], 8)
        self.assertEqual(len(self.socketio.emitted), 1)
        event, payload, kwargs = self.socketio.emitted[0]
        self.assertEqual(event, "terminal_output")
        self.assertEqual(kwargs["room"], "terminal:alpha:main")
        self.assertEqual(payload["event_type"], "resize")
        self.assertEqual(payload["data"], "")
        self.assertEqual((payload["output_start"], payload["output_end"]), (42, 42))
        self.assertEqual((payload["frame_seq"], payload["frame_end"]), (8, 42))
        self.assertEqual((payload["cols"], payload["rows"]), (100, 30))
        self.assertEqual((payload["stream_id"], payload["stream_epoch"]), ("stream-alpha", 1234))

        self.invoke("terminal_resize", "socket-a", {"cols": 100, "rows": 30})
        self.assertEqual(len(self.socketio.emitted), 1)

    def test_terminal_snapshot_is_stored_at_valid_history_offset(self):
        info = self.terminal_info("alpha", "main", 101)
        info["history_end"] = 42
        self.bind("socket-a", terminal_id="main")

        response = self.invoke("terminal_snapshot", "socket-a", {
            "snapshot": "\x1b[2Jrestored screen",
            "snapshot_end": 42,
            "cols": 120,
            "rows": 35,
        })

        self.assertTrue(response["ok"])
        self.assertEqual(info["screen_snapshot"], "\x1b[2Jrestored screen")
        self.assertEqual(info["screen_snapshot_end"], 42)
        self.assertEqual((info["screen_snapshot_cols"], info["screen_snapshot_rows"]), (120, 35))

    def test_terminal_snapshot_rejects_offset_outside_history(self):
        info = self.terminal_info("alpha", "main", 101)
        info.update(history_start=20, history_end=42)
        self.bind("socket-a", terminal_id="main")

        response = self.invoke("terminal_snapshot", "socket-a", {
            "snapshot": "screen",
            "snapshot_end": 10,
            "cols": 80,
            "rows": 24,
        })

        self.assertFalse(response["ok"])
        self.assertNotIn("screen_snapshot", info)

    def test_terminal_snapshot_does_not_replace_newer_checkpoint(self):
        info = self.terminal_info("alpha", "main", 101)
        info.update(
            history_end=42,
            screen_snapshot="new screen",
            screen_snapshot_end=40,
        )
        self.bind("socket-a", terminal_id="main")

        response = self.invoke("terminal_snapshot", "socket-a", {
            "snapshot": "stale screen",
            "snapshot_end": 30,
            "cols": 80,
            "rows": 24,
        })

        self.assertFalse(response["ok"])
        self.assertEqual(info["screen_snapshot"], "new screen")
        self.assertEqual(info["screen_snapshot_end"], 40)

    def test_stale_attach_events_cannot_write_snapshot_or_resize_current_binding(self):
        info = self.terminal_info("alpha", "main", 101)
        info["history_end"] = 42
        self.bind("socket-a", attach_seq=2)

        with patch.object(terminal_runtime.os, "write", side_effect=lambda fd, data: len(data)) as write:
            self.invoke("terminal_input", "socket-a", {"attach_seq": 1, "data": "stale"})
            self.invoke("terminal_input", "socket-a", {"attach_seq": 2, "data": "current"})

        stale_snapshot = self.invoke("terminal_snapshot", "socket-a", {
            "attach_seq": 1,
            "snapshot": "stale screen",
            "snapshot_end": 42,
        })
        self.invoke("terminal_resize", "socket-a", {
            "attach_seq": 1,
            "cols": 120,
            "rows": 40,
        })

        self.assertEqual(write.call_count, 1)
        self.assertEqual(bytes(write.call_args.args[1]), b"current")
        self.assertFalse(stale_snapshot["ok"])
        self.assertNotIn("screen_snapshot", info)
        self.ioctl.assert_not_called()

    def test_terminal_write_does_not_hold_screen_state_lock(self):
        info = self.terminal_info("alpha", "main", 101)
        completed = threading.Event()

        def write():
            self.runtime.write_terminal("alpha", b"paste", expected_info=info)
            completed.set()

        with patch.object(terminal_runtime.os, "write", side_effect=lambda fd, data: len(data)):
            info["lock"].acquire()
            try:
                thread = threading.Thread(target=write)
                thread.start()
                self.assertTrue(completed.wait(0.5), "PTY write blocked on the screen-state lock")
            finally:
                info["lock"].release()
            thread.join(1)

    def test_terminal_write_stops_after_bounded_eagain_stall(self):
        info = self.terminal_info("alpha", "main", 101)

        with (
            patch.object(terminal_runtime.os, "write", side_effect=BlockingIOError()) as write,
            patch.object(terminal_runtime.select, "select", return_value=([], [], [])) as select_call,
            patch.object(terminal_runtime.time, "monotonic", side_effect=[0.0, 0.0, 3.0]),
        ):
            result = self.runtime.write_terminal("alpha", b"blocked", expected_info=info)

        self.assertFalse(result)
        write.assert_called_once()
        self.assertLessEqual(
            select_call.call_args.args[3],
            terminal_runtime.TERMINAL_WRITE_POLL_INTERVAL,
        )

    def test_blocked_terminal_input_does_not_hold_socket_attach_lock(self):
        self.terminal_info("alpha", "main", 101)
        self.terminal_info("beta", "main", 201)
        self.bind("socket-a", attach_seq=1)
        write_started = threading.Event()
        release_write = threading.Event()
        input_completed = threading.Event()
        attach_completed = threading.Event()

        def blocked_write(fd, data):
            write_started.set()
            release_write.wait(1)
            raise BlockingIOError()

        def send_input():
            self.invoke("terminal_input", "socket-a", {"attach_seq": 1, "data": "paste"})
            input_completed.set()

        def switch_terminal():
            self.invoke("terminal_attach", "socket-a", {
                "workspace_id": "beta",
                "terminal_id": "main",
                "view_only": True,
                "attach_seq": 2,
            })
            attach_completed.set()

        with (
            patch.object(terminal_runtime.os, "write", side_effect=blocked_write),
            patch.object(terminal_runtime.select, "select", return_value=([], [], [101])),
        ):
            input_thread = threading.Thread(target=send_input)
            attach_thread = threading.Thread(target=switch_terminal)
            input_thread.start()
            self.assertTrue(write_started.wait(0.5))
            attach_thread.start()
            try:
                self.assertTrue(
                    attach_completed.wait(0.5),
                    "attach was blocked by PTY input backpressure",
                )
                self.assertEqual(self.bindings["socket-a"]["workspace_id"], "beta")
            finally:
                release_write.set()
            self.assertTrue(input_completed.wait(1))
            input_thread.join(1)
            attach_thread.join(1)

    def test_same_sid_inputs_are_enqueued_in_validation_order(self):
        info = self.terminal_info("alpha", "main", 101)
        info["input_condition"] = threading.Condition()
        info["input_queue"] = terminal_runtime.deque()
        self.bind("socket-a", attach_seq=1)
        first_in_encode = threading.Event()
        release_first = threading.Event()
        second_completed = threading.Event()

        class BlockingText:
            def encode(self, encoding):
                first_in_encode.set()
                release_first.wait(1)
                return b"A"

        def send_first():
            self.invoke("terminal_input", "socket-a", {
                "attach_seq": 1,
                "data": BlockingText(),
            })

        def send_second():
            self.invoke("terminal_input", "socket-a", {
                "attach_seq": 1,
                "data": "B",
            })
            second_completed.set()

        first_thread = threading.Thread(target=send_first)
        second_thread = threading.Thread(target=send_second)
        first_thread.start()
        self.assertTrue(first_in_encode.wait(0.5))
        second_thread.start()
        self.assertFalse(second_completed.wait(0.05))
        release_first.set()
        first_thread.join(1)
        second_thread.join(1)

        self.assertEqual(list(info["input_queue"]), [b"A", b"B"])
        self.assertEqual(info["input_queue_size"], 2)

    def test_single_writer_queue_completes_partial_write_after_eagain(self):
        info = self.captured_runtime_terminal()
        key = terminal_key("alpha", "main")
        self.assertTrue(self.runtime.write_terminal("alpha", b"abcdef", expected_info=info))
        attempts = []
        write_completed = threading.Event()

        def partial_write(fd, data):
            attempts.append(bytes(data))
            if len(attempts) == 1:
                return 2
            if len(attempts) == 2:
                raise BlockingIOError()
            write_completed.set()
            return len(data)

        writer = threading.Thread(
            target=info["writer_thread"].kwargs["target"],
            args=info["writer_thread"].kwargs["args"],
        )
        with (
            patch.object(terminal_runtime.os, "write", side_effect=partial_write),
            patch.object(terminal_runtime.select, "select", return_value=([], [301], [])),
        ):
            writer.start()
            self.assertTrue(write_completed.wait(1))
            with info["input_condition"]:
                info["closing"] = True
                info["input_condition"].notify_all()
            writer.join(1)

        self.assertFalse(writer.is_alive())
        self.assertEqual(attempts, [b"abcdef", b"cdef", b"cdef"])
        self.assertEqual(list(info["input_queue"]), [])
        self.assertIs(self.live_terminals[key], info)

    def test_close_cancels_a_nonblocking_pty_write_that_cannot_progress(self):
        info = self.terminal_info("alpha", "main", 101)
        write_started = threading.Event()
        write_completed = threading.Event()
        close_completed = threading.Event()
        write_result = []

        def blocked_write(fd, data):
            write_started.set()
            raise BlockingIOError()

        def write():
            write_result.append(self.runtime.write_terminal("alpha", b"large paste", expected_info=info))
            write_completed.set()

        def close():
            self.runtime.close_terminal("alpha", "main", manual=False, expected_info=info)
            close_completed.set()

        with (
            patch.object(terminal_runtime.os, "write", side_effect=blocked_write),
            patch.object(terminal_runtime.select, "select", return_value=([], [101], [])),
            patch.object(terminal_runtime, "terminate_process_tree"),
            patch.object(terminal_runtime.os, "close"),
        ):
            writer = threading.Thread(target=write)
            writer.start()
            self.assertTrue(write_started.wait(0.5))
            closer = threading.Thread(target=close)
            closer.start()
            self.assertTrue(write_completed.wait(1))
            self.assertTrue(close_completed.wait(1))
            writer.join(1)
            closer.join(1)

        self.assertEqual(write_result, [False])
        self.assertNotIn(terminal_key("alpha", "main"), self.live_terminals)

    def test_close_waits_for_delivery_boundary_before_detaching_instance(self):
        info = self.terminal_info("alpha", "main", 101)
        started = threading.Event()
        completed = threading.Event()

        def close():
            started.set()
            self.runtime.close_terminal("alpha", "main", manual=False, expected_info=info)
            completed.set()

        info["delivery_lock"].acquire()
        try:
            with (
                patch.object(terminal_runtime, "terminate_process_tree"),
                patch.object(terminal_runtime.os, "close"),
            ):
                thread = threading.Thread(target=close)
                thread.start()
                self.assertTrue(started.wait(0.5))
                self.assertFalse(completed.wait(0.05))
                self.assertIs(self.live_terminals[terminal_key("alpha", "main")], info)
                info["delivery_lock"].release()
                self.assertTrue(completed.wait(1))
                thread.join(1)
        finally:
            try:
                info["delivery_lock"].release()
            except RuntimeError:
                pass

        self.assertNotIn(terminal_key("alpha", "main"), self.live_terminals)
        self.assertFalse(info["alive"])

    def test_close_releases_terminal_history_and_screen_state(self):
        info = self.terminal_info("alpha", "main", 101)
        replay = terminal_runtime.TerminalHistoryBuffer(1024)
        history = terminal_runtime.TerminalHistoryBuffer(4096)
        replay.append("replay data")
        history.append("history data")
        info.update({
            "buffer": replay,
            "history_buffer": history,
            "history_end": len("history data"),
            "screen_model": object(),
            "screen_snapshot": "saved screen",
            "screen_snapshot_end": len("history data"),
            "screen_snapshot_cols": 120,
            "screen_snapshot_rows": 40,
            "input_condition": threading.Condition(),
            "input_queue": terminal_runtime.deque([b"queued input"]),
            "input_queue_size": len(b"queued input"),
            "tcp_clients": set(),
        })

        with (
            patch.object(terminal_runtime, "terminate_process_tree"),
            patch.object(terminal_runtime.os, "close"),
        ):
            self.assertTrue(self.runtime.close_terminal("alpha", "main", manual=False))

        self.assertEqual(info["buffer"], "")
        self.assertEqual(info["history_buffer"], "")
        self.assertEqual(info["history_start"], info["history_end"])
        self.assertIsNone(info["screen_model"])
        self.assertEqual(info["screen_snapshot"], "")
        self.assertEqual(list(info["input_queue"]), [])
        self.assertEqual(info["input_queue_size"], 0)

    def test_terminal_summary_reads_chunked_buffers_under_state_lock(self):
        info = self.terminal_info("alpha", "main", 101)

        class GuardedBuffer:
            def __len__(self):
                self._assert_locked()
                return 5

            def __getitem__(self, key):
                self._assert_locked()
                return "ready"[key]

            @staticmethod
            def _assert_locked():
                if not info["lock"].locked():
                    raise RuntimeError("buffer read outside terminal state lock")

        info["buffer"] = GuardedBuffer()
        info["history_buffer"] = GuardedBuffer()

        summary = self.runtime.terminal_summary(terminal_key("alpha", "main"), info)

        self.assertEqual(summary["buffer_size"], 5)
        self.assertEqual(summary["history_size"], 5)

    def test_attach_returns_saved_terminal_snapshot(self):
        info = self.terminal_info("alpha", "main", 101)
        info.update(
            history_end=42,
            screen_snapshot="\x1b[2Jsaved screen",
            screen_snapshot_end=42,
            screen_snapshot_cols=120,
            screen_snapshot_rows=35,
        )
        terminal_runtime.emit.reset_mock()

        self.invoke("terminal_attach", "socket-a", {
            "workspace_id": "alpha",
            "terminal_id": "main",
            "cols": 100,
            "rows": 30,
        })

        ready = next(
            call.args[1]
            for call in terminal_runtime.emit.call_args_list
            if call.args and call.args[0] == "terminal_ready"
        )
        self.assertTrue(ready["checkpoint_supported"])
        self.assertEqual(ready["screen_snapshot"], "\x1b[2Jsaved screen")
        self.assertEqual(ready["screen_snapshot_end"], 42)
        self.assertEqual((ready["screen_snapshot_cols"], ready["screen_snapshot_rows"]), (120, 35))

    def test_attach_prefers_authoritative_server_screen_model(self):
        info = self.terminal_info("alpha", "main", 101)
        screen_model = SimpleNamespace(
            cols=100,
            rows=30,
            active_buffer="alternate",
            delta_safe=True,
            serialize_bounded=Mock(return_value="\x1b[2Jserver screen"),
            resize=Mock(),
        )
        info.update(
            cols=100,
            rows=30,
            history_end=42,
            screen_model=screen_model,
            screen_snapshot="client screen",
            screen_snapshot_end=30,
        )
        terminal_runtime.emit.reset_mock()

        self.invoke("terminal_attach", "socket-a", {
            "workspace_id": "alpha",
            "terminal_id": "main",
            "cols": 100,
            "rows": 30,
        })

        ready = next(
            call.args[1]
            for call in terminal_runtime.emit.call_args_list
            if call.args and call.args[0] == "terminal_ready"
        )
        self.assertEqual(ready["screen_snapshot"], "\x1b[2Jserver screen")
        self.assertEqual(ready["screen_snapshot_end"], 42)
        self.assertEqual(ready["screen_snapshot_source"], "server")
        self.assertTrue(ready["screen_frame_supported"])
        self.assertEqual((ready["frame_seq"], ready["frame_end"]), (0, 42))
        self.assertEqual(ready["screen_snapshot_active_buffer"], "alternate")
        self.assertTrue(ready["screen_snapshot_delta_safe"])

    def test_stale_attach_sequence_cannot_rebind_socket(self):
        self.terminal_info("alpha", "main", 101)
        self.terminal_info("beta", "main", 201)
        terminal_runtime.emit.reset_mock()

        self.invoke("terminal_attach", "socket-a", {
            "workspace_id": "alpha",
            "terminal_id": "main",
            "view_only": True,
            "attach_seq": 2,
        })
        self.invoke("terminal_attach", "socket-a", {
            "workspace_id": "beta",
            "terminal_id": "main",
            "view_only": True,
            "attach_seq": 1,
        })

        self.assertEqual(self.bindings["socket-a"]["workspace_id"], "alpha")
        ready_events = [
            call for call in terminal_runtime.emit.call_args_list
            if call.args and call.args[0] == "terminal_ready"
        ]
        self.assertEqual(len(ready_events), 1)
        self.assertEqual(ready_events[0].args[1]["attach_seq"], 2)

    def test_attach_aborts_cleanly_after_socket_disconnect(self):
        self.terminal_info("alpha", "main", 101)
        self.socketio.server = SimpleNamespace(
            manager=SimpleNamespace(is_connected=lambda socket_id, namespace: False)
        )
        terminal_runtime.join_room.reset_mock()

        self.invoke("terminal_attach", "socket-gone", {
            "workspace_id": "alpha",
            "terminal_id": "main",
            "view_only": True,
            "attach_seq": 1,
        })

        self.assertNotIn("socket-gone", self.bindings)
        terminal_runtime.join_room.assert_not_called()

    def test_disconnected_queued_writable_attach_cannot_create_ghost_pty(self):
        self.terminal_info("alpha", "main", 101)
        self.bind("socket-gone", attach_seq=1)
        self.socketio.server = SimpleNamespace(
            manager=SimpleNamespace(is_connected=lambda socket_id, namespace: False)
        )
        self.invoke("disconnect", "socket-gone")

        with patch.object(
            terminal_runtime.pty,
            "openpty",
            side_effect=AssertionError("disconnected attach created a PTY"),
        ) as openpty:
            self.invoke("terminal_attach", "socket-gone", {
                "workspace_id": "beta",
                "terminal_id": "ghost",
                "attach_seq": 2,
            })

        openpty.assert_not_called()
        self.assertNotIn("socket-gone", self.bindings)
        self.ioctl.assert_not_called()

    def test_stale_terminal_instance_cannot_close_replacement(self):
        current = self.terminal_info("alpha", "main", 101)
        stale = {**current, "fd": 99}

        closed = self.runtime.close_terminal(
            "alpha",
            "main",
            manual=False,
            expected_info=stale,
        )

        self.assertFalse(closed)
        self.assertIs(self.live_terminals[terminal_key("alpha", "main")], current)

    def test_manual_close_blocks_automatic_ensure_until_explicit_reopen(self):
        info = self.terminal_info("alpha", "main", 101)
        key = terminal_key("alpha", "main")

        with (
            patch.object(terminal_runtime, "terminate_process_tree"),
            patch.object(terminal_runtime.os, "close"),
        ):
            self.assertTrue(self.runtime.close_terminal("alpha", "main", expected_info=info))

        with patch.object(terminal_runtime.pty, "openpty") as openpty:
            self.assertIsNone(self.runtime.ensure_terminal("alpha", "main"))
        openpty.assert_not_called()

        popen = Mock(return_value=DummyProcess())
        with (
            patch.object(terminal_runtime.pty, "openpty", return_value=(301, 302)),
            patch.object(terminal_runtime.subprocess, "Popen", popen),
            patch.object(terminal_runtime.threading, "Thread", DummyThread),
            patch.object(terminal_runtime.os, "close"),
            patch.object(terminal_runtime, "WORKSPACE_AGENT_COMMANDS", {}),
        ):
            reopened = self.runtime.ensure_terminal("alpha", "main", reopen=True)

        self.assertIs(self.live_terminals[key], reopened)
        self.assertEqual(reopened["terminal_generation"], 1)

    def test_workspace_close_serializes_ensure_and_requires_new_generation(self):
        info = self.terminal_info("alpha", "main", 101)
        key = terminal_key("alpha", "main")
        cleanup_started = threading.Event()
        release_cleanup = threading.Event()
        close_completed = threading.Event()
        ensure_completed = threading.Event()
        ensure_results = []

        def blocking_terminate(pid):
            cleanup_started.set()
            release_cleanup.wait(1)

        def close_workspace():
            self.runtime.close_workspace_terminals("alpha")
            close_completed.set()

        def ensure_workspace():
            ensure_results.append(self.runtime.ensure_terminal("alpha", "main"))
            ensure_completed.set()

        with (
            patch.object(terminal_runtime, "terminate_process_tree", side_effect=blocking_terminate),
            patch.object(terminal_runtime.os, "close"),
        ):
            close_thread = threading.Thread(target=close_workspace)
            ensure_thread = threading.Thread(target=ensure_workspace)
            close_thread.start()
            self.assertTrue(cleanup_started.wait(0.5))
            ensure_thread.start()
            self.assertFalse(ensure_completed.wait(0.05))
            release_cleanup.set()
            self.assertTrue(close_completed.wait(1))
            self.assertTrue(ensure_completed.wait(1))
            close_thread.join(1)
            ensure_thread.join(1)

        self.assertEqual(ensure_results, [None])
        self.assertNotIn(key, self.live_terminals)

        popen = Mock(return_value=DummyProcess())
        with (
            patch.object(terminal_runtime.pty, "openpty", return_value=(401, 402)),
            patch.object(terminal_runtime.subprocess, "Popen", popen),
            patch.object(terminal_runtime.threading, "Thread", DummyThread),
            patch.object(terminal_runtime.os, "close"),
            patch.object(terminal_runtime, "WORKSPACE_AGENT_COMMANDS", {}),
        ):
            reopened = self.runtime.ensure_terminal(
                "alpha",
                "main",
                reopen_workspace=True,
            )

        self.assertIs(self.live_terminals[key], reopened)
        self.assertEqual(reopened["workspace_generation"], 1)
        self.assertEqual(reopened["terminal_generation"], 1)
        self.assertIsNone(self.runtime.ensure_terminal(
            "alpha",
            "main",
            expected_workspace_generation=0,
            expected_terminal_generation=0,
        ))
        self.assertIs(self.live_terminals[key], reopened)

    def test_attach_ready_failure_restores_previous_binding_and_room(self):
        self.terminal_info("alpha", "main", 101)
        self.terminal_info("beta", "main", 201)
        self.bind("socket-a", attach_seq=1)

        def emit_or_fail(event, *args, **kwargs):
            if event == "terminal_ready":
                raise RuntimeError("client disconnected during ready")

        terminal_runtime.emit.side_effect = emit_or_fail
        try:
            self.invoke("terminal_attach", "socket-a", {
                "workspace_id": "beta",
                "terminal_id": "main",
                "view_only": True,
                "attach_seq": 2,
            })
        finally:
            terminal_runtime.emit.side_effect = None

        self.assertEqual(self.bindings["socket-a"]["workspace_id"], "alpha")
        self.assertEqual(
            [call.args[0] for call in terminal_runtime.join_room.call_args_list],
            ["terminal:beta:main", "terminal:alpha:main"],
        )
        self.assertEqual(
            [call.args[0] for call in terminal_runtime.leave_room.call_args_list],
            ["terminal:alpha:main", "terminal:beta:main"],
        )

    def test_disconnect_cannot_create_per_sid_lock_aba_with_waiting_attach(self):
        self.terminal_info("alpha", "main", 101)
        self.terminal_info("beta", "main", 201)
        self.bind("socket-race", attach_seq=1)
        real_rlock = threading.RLock
        lock_contended = threading.Event()
        disconnect_in_leave = threading.Event()
        release_disconnect = threading.Event()
        second_in_join = threading.Event()
        release_second = threading.Event()
        second_completed = threading.Event()
        third_completed = threading.Event()

        class TrackedRLock:
            def __init__(self):
                self.lock = real_rlock()

            def acquire(self, blocking=True, timeout=-1):
                if self.lock.acquire(False):
                    return True
                lock_contended.set()
                if not blocking:
                    return False
                if timeout is None or timeout < 0:
                    return self.lock.acquire(True)
                return self.lock.acquire(True, timeout)

            def release(self):
                self.lock.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, exc_type, exc, tb):
                self.release()

        def leave_room_side_effect(room):
            if threading.current_thread().name == "disconnect-race":
                disconnect_in_leave.set()
                release_disconnect.wait(1)

        def join_room_side_effect(room):
            if threading.current_thread().name == "attach-second":
                second_in_join.set()
                release_second.wait(1)

        def disconnect():
            self.invoke("disconnect", "socket-race")

        def attach_second():
            self.invoke("terminal_attach", "socket-race", {
                "workspace_id": "beta",
                "terminal_id": "main",
                "view_only": True,
                "attach_seq": 2,
            })
            second_completed.set()

        def attach_third():
            self.invoke("terminal_attach", "socket-race", {
                "workspace_id": "alpha",
                "terminal_id": "main",
                "view_only": True,
                "attach_seq": 3,
            })
            third_completed.set()

        terminal_runtime.leave_room.side_effect = leave_room_side_effect
        terminal_runtime.join_room.side_effect = join_room_side_effect
        try:
            with patch.object(terminal_runtime.threading, "RLock", side_effect=TrackedRLock):
                disconnect_thread = threading.Thread(target=disconnect, name="disconnect-race")
                second_thread = threading.Thread(target=attach_second, name="attach-second")
                third_thread = threading.Thread(target=attach_third, name="attach-third")
                disconnect_thread.start()
                self.assertTrue(disconnect_in_leave.wait(0.5))
                second_thread.start()
                self.assertTrue(lock_contended.wait(0.5), "second attach never waited on SID lock")
                release_disconnect.set()
                disconnect_thread.join(1)
                self.assertTrue(second_in_join.wait(0.5))

                third_thread.start()
                self.assertFalse(
                    third_completed.wait(0.05),
                    "third attach bypassed a waiter through a replacement SID lock",
                )
                release_second.set()
                self.assertTrue(second_completed.wait(1))
                self.assertTrue(third_completed.wait(1))
                second_thread.join(1)
                third_thread.join(1)
        finally:
            release_disconnect.set()
            release_second.set()
            terminal_runtime.leave_room.side_effect = None
            terminal_runtime.join_room.side_effect = None

        self.assertEqual(self.bindings["socket-race"]["workspace_id"], "alpha")
        self.assertEqual(self.bindings["socket-race"]["attach_seq"], 3)

    def test_tcp_client_is_not_registered_on_replaced_terminal_info(self):
        info = self.terminal_info("alpha", "main", 101)
        info["buffer"] = "replay"
        key = terminal_key("alpha", "main")
        replacement = {
            **info,
            "fd": 202,
            "lock": threading.Lock(),
            "write_lock": threading.Lock(),
            "delivery_lock": threading.RLock(),
            "tcp_clients": set(),
        }

        class FakeClient:
            def __init__(client_self):
                client_self.closed = False
                client_self.sent = []
                client_self.recv_calls = 0

            def settimeout(client_self, timeout):
                return None

            def sendall(client_self, data):
                client_self.sent.append(data)
                self.live_terminals[key] = replacement

            def recv(client_self, size):
                client_self.recv_calls += 1
                return b""

            def close(client_self):
                client_self.closed = True

        client = FakeClient()

        class FakeServer:
            def __init__(server_self):
                server_self.accepted = False

            def setsockopt(server_self, *args):
                return None

            def bind(server_self, address):
                return None

            def listen(server_self, backlog):
                return None

            def accept(server_self):
                if server_self.accepted:
                    raise OSError("server closed")
                server_self.accepted = True
                return client, ("127.0.0.1", 12345)

            def close(server_self):
                return None

        server = FakeServer()

        class ImmediateThread:
            def __init__(thread_self, target=None, args=(), **kwargs):
                thread_self.target = target
                thread_self.args = args

            def start(thread_self):
                thread_self.target(*thread_self.args)

        with (
            patch.object(terminal_runtime, "WORKSPACE_PTY_PORTS", {"alpha": 1234}),
            patch.object(terminal_runtime.socket, "socket", return_value=server),
            patch.object(terminal_runtime.threading, "Thread", ImmediateThread),
            patch.object(terminal_runtime, "workspace_tcp_servers", {}),
        ):
            self.runtime.start_workspace_tcp_servers()

        self.assertEqual(client.sent, [b"replay"])
        self.assertTrue(client.closed)
        self.assertEqual(client.recv_calls, 0)
        self.assertNotIn(client, info.get("tcp_clients", set()))
        self.assertNotIn(client, replacement["tcp_clients"])

    def test_reader_continues_after_observation_and_socket_emit_exceptions(self):
        info = self.captured_runtime_terminal()
        key = terminal_key("alpha", "main")
        second_emit = threading.Event()
        stop_select = threading.Event()
        select_calls = []
        output_calls = []

        def select_side_effect(readable, writable, exceptional, timeout):
            select_calls.append(True)
            if len(select_calls) <= 2:
                return ([301], [], [])
            stop_select.wait(1)
            return ([], [], [])

        def emit_side_effect(event, payload, **kwargs):
            if event != "terminal_output":
                return None
            output_calls.append(payload["data"])
            if len(output_calls) == 1:
                raise RuntimeError("broadcast unavailable")
            second_emit.set()
            return None

        self.socketio.emit = Mock(side_effect=emit_side_effect)
        self.deps.record_terminal_output_observation.side_effect = [
            RuntimeError("observation unavailable"),
            None,
        ]
        reader = threading.Thread(
            target=info["reader_thread"].kwargs["target"],
            args=info["reader_thread"].kwargs["args"],
        )
        with (
            patch.object(terminal_runtime.select, "select", side_effect=select_side_effect),
            patch.object(terminal_runtime.os, "read", side_effect=[b"one", b"two"]),
        ):
            reader.start()
            self.assertTrue(second_emit.wait(1))
            with terminal_runtime.terminals_lock:
                self.live_terminals.pop(key, None)
            stop_select.set()
            reader.join(1)

        self.assertFalse(reader.is_alive())
        self.assertEqual(output_calls, ["one", "two"])
        self.assertEqual(info["history_buffer"].to_string(), "onetwo")
        self.assertEqual(info["frame_seq"], 2)
        self.assertIn("RuntimeError", info["observation_error"])
        self.assertIn("RuntimeError", info["socket_emit_error"])

    def test_tcp_replay_and_live_output_are_exactly_once_across_registration(self):
        info = self.captured_runtime_terminal()
        key = terminal_key("alpha", "main")
        underlying_delivery_lock = threading.RLock()
        delivery_released = threading.Event()
        tcp_registered = threading.Event()
        release_client = threading.Event()
        reader_after_release = threading.Event()
        stop_reader = threading.Event()

        class PauseReaderAfterReleaseLock:
            def __init__(lock_self):
                lock_self.paused = False

            def acquire(lock_self, blocking=True, timeout=-1):
                if timeout is None or timeout < 0:
                    return underlying_delivery_lock.acquire(blocking)
                return underlying_delivery_lock.acquire(blocking, timeout)

            def release(lock_self):
                underlying_delivery_lock.release()
                if (
                    threading.current_thread().name == "reader-exactly-once"
                    and not lock_self.paused
                ):
                    lock_self.paused = True
                    delivery_released.set()
                    tcp_registered.wait(1)

            def __enter__(lock_self):
                lock_self.acquire()
                return lock_self

            def __exit__(lock_self, exc_type, exc, tb):
                lock_self.release()

        info["delivery_lock"] = PauseReaderAfterReleaseLock()
        select_calls = []

        def select_side_effect(readable, writable, exceptional, timeout):
            select_calls.append(True)
            if len(select_calls) == 1:
                return ([301], [], [])
            reader_after_release.set()
            stop_reader.wait(1)
            return ([], [], [])

        class FakeClient:
            def __init__(client_self):
                client_self.sent = []
                client_self.closed = False

            def settimeout(client_self, timeout):
                return None

            def sendall(client_self, data):
                client_self.sent.append(data)

            def recv(client_self, size):
                tcp_registered.set()
                release_client.wait(1)
                return b""

            def close(client_self):
                client_self.closed = True

        client = FakeClient()

        class FakeServer:
            def __init__(server_self):
                server_self.accepted = False

            def setsockopt(server_self, *args):
                return None

            def bind(server_self, address):
                return None

            def listen(server_self, backlog):
                return None

            def accept(server_self):
                if server_self.accepted:
                    raise OSError("server closed")
                server_self.accepted = True
                return client, ("127.0.0.1", 12345)

            def close(server_self):
                return None

        class ImmediateThread:
            def __init__(thread_self, target=None, args=(), **kwargs):
                thread_self.target = target
                thread_self.args = args

            def start(thread_self):
                thread_self.target(*thread_self.args)

        server = FakeServer()
        reader = threading.Thread(
            target=info["reader_thread"].kwargs["target"],
            args=info["reader_thread"].kwargs["args"],
            name="reader-exactly-once",
        )
        tcp_launcher = threading.Thread(target=self.runtime.start_workspace_tcp_servers)

        with (
            patch.object(terminal_runtime.select, "select", side_effect=select_side_effect),
            patch.object(terminal_runtime.os, "read", return_value=b"delta"),
            patch.object(terminal_runtime, "WORKSPACE_PTY_PORTS", {"alpha": 1234}),
            patch.object(terminal_runtime.socket, "socket", return_value=server),
            patch.object(terminal_runtime.threading, "Thread", ImmediateThread),
            patch.object(terminal_runtime, "workspace_tcp_servers", {}),
        ):
            reader.start()
            self.assertTrue(delivery_released.wait(1))
            tcp_launcher.start()
            self.assertTrue(tcp_registered.wait(1))
            self.assertTrue(reader_after_release.wait(1))
            self.assertEqual(client.sent, [b"delta"])
            release_client.set()
            tcp_launcher.join(1)
            with terminal_runtime.terminals_lock:
                self.live_terminals.pop(key, None)
            stop_reader.set()
            reader.join(1)

        self.assertFalse(reader.is_alive())
        self.assertFalse(tcp_launcher.is_alive())
        self.assertTrue(client.closed)

    def test_attach_creates_new_pty_with_requested_geometry(self):
        popen = Mock(return_value=DummyProcess())
        with (
            patch.object(terminal_runtime.pty, "openpty", return_value=(301, 302)),
            patch.object(terminal_runtime.subprocess, "Popen", popen),
            patch.object(terminal_runtime.threading, "Thread", DummyThread),
            patch.object(terminal_runtime.os, "close"),
            patch.object(terminal_runtime, "WORKSPACE_AGENT_COMMANDS", {}),
        ):
            self.invoke(
                "terminal_attach",
                "creator",
                {"workspace_id": "alpha", "terminal_id": "new", "cols": 144, "rows": 48},
            )

        created = self.live_terminals[terminal_key("alpha", "new")]
        self.assertEqual((created["cols"], created["rows"]), (144, 48))
        self.assertEqual(popen.call_args.kwargs["env"]["COLUMNS"], "144")
        self.assertEqual(popen.call_args.kwargs["env"]["LINES"], "48")
        self.assertEqual(self.resized_geometries()[0], (302, 48, 144))

    def test_terminal_creation_fails_and_closes_both_fds_when_nonblocking_is_unavailable(self):
        self.set_nonblocking.side_effect = OSError("cannot configure O_NONBLOCK")
        popen = Mock(return_value=DummyProcess())

        with (
            patch.object(terminal_runtime.pty, "openpty", return_value=(301, 302)),
            patch.object(terminal_runtime.subprocess, "Popen", popen),
            patch.object(terminal_runtime.os, "close") as close,
            self.assertRaisesRegex(OSError, "O_NONBLOCK"),
        ):
            self.runtime.ensure_terminal("alpha", "new")

        popen.assert_not_called()
        self.assertEqual([call.args[0] for call in close.call_args_list], [301, 302])
        self.assertNotIn(terminal_key("alpha", "new"), self.live_terminals)


if __name__ == "__main__":
    unittest.main()
