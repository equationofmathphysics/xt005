import os
import pty
import codecs
import re
import select
import socket
import shlex
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import fcntl
import uuid
from collections import deque
from contextlib import contextmanager

from flask import request
from flask_socketio import emit, join_room, leave_room

from .codex_history_store import normalize_thread_id
from .codex_threads import codex_thread_from_processes, codex_thread_title
from .config import (
    DEFAULT_TERMINAL,
    WORKSPACE_AGENT_CHECK_INTERVAL,
    WORKSPACE_AGENT_COMMANDS,
    WORKSPACE_AGENT_RESTART_GRACE,
    WORKSPACE_AGENT_START_DELAY,
    WORKSPACE_PTY_HOST,
    WORKSPACE_PTY_PORTS,
    TERMINAL_BUFFER_LIMIT,
    TERMINAL_HISTORY_LIMIT,
    TERMINAL_SCREEN_SNAPSHOT_LIMIT,
)
from .ids import normalize_terminal_id, split_terminal_key, terminal_key
from .process_tree import (
    pid_running,
    process_args,
    workspace_agent_processes,
    workspace_agent_running,
    terminal_info_usable,
    terminal_tree_pids,
    terminate_pids,
    terminate_process_tree,
    terminate_orphaned_session,
)
from .services import TerminalRuntime
from .socket_protocol import TerminalSocketEvent
from .state import socket_bindings, workspace_tcp_servers, terminals, terminals_lock
from .terminal_history import TerminalHistoryBuffer
from .terminal_screen import TerminalScreenModel


TERMINAL_REPLAY_CONTROL_PATTERNS = (
    re.compile(r"\x1b\[[0-9;?=>]*[cnR]"),
    re.compile(r"\x1b\](?:10|11);[^\x07\x1b]*(?:\x07|\x1b\\)"),
)
TERMINAL_ACTIVITY_TAIL_CHARS = 4096
TERMINAL_ACTIVITY_SAMPLE_LINES = 3
TERMINAL_ACTIVITY_CONTROL_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
TERMINAL_LEADING_CSI_TAIL_RE = re.compile(r"^[0-?]+[ -/]*[@-~]")
TERMINAL_REPLAY_BOUNDARY_SCAN_CHARS = 4096
CODEX_WORKING_RE = re.compile(r"\bWorking\([^)\r\n]{0,120}esc to interrupt\)", re.IGNORECASE)
UTF8_LOCALE_RE = re.compile(r"utf-?8", re.IGNORECASE)
DEFAULT_TERMINAL_UTF8_LOCALE = "C.UTF-8"
CODEX_PROCESS_SCAN_TTL = 4.0
TERMINAL_MIN_COLS = 2
TERMINAL_MAX_COLS = 1000
TERMINAL_MIN_ROWS = 1
TERMINAL_MAX_ROWS = 500
TERMINAL_WRITE_STALL_TIMEOUT = 2.0
TERMINAL_WRITE_POLL_INTERVAL = 0.05
_stream_epoch_lock = threading.Lock()
_last_stream_epoch = 0


def next_terminal_stream_epoch():
    """Return a process-local epoch that remains monotonic across clock changes."""
    global _last_stream_epoch
    candidate = int(time.time() * 1000)
    with _stream_epoch_lock:
        _last_stream_epoch = max(candidate, _last_stream_epoch + 1)
        return _last_stream_epoch


def clamp_terminal_geometry(cols=80, rows=24):
    def clamp(value, default, minimum, maximum):
        if value is None or value == "":
            value = default
        try:
            value = int(value)
        except (TypeError, ValueError, OverflowError):
            value = default
        return max(minimum, min(value, maximum))

    return (
        clamp(cols, 80, TERMINAL_MIN_COLS, TERMINAL_MAX_COLS),
        clamp(rows, 24, TERMINAL_MIN_ROWS, TERMINAL_MAX_ROWS),
    )


def set_terminal_fd_nonblocking(fd):
    """Configure and verify the PTY master as nonblocking."""
    try:
        os.set_blocking(fd, False)
    except (AttributeError, OSError):
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    try:
        blocking = os.get_blocking(fd)
    except AttributeError:
        blocking = not bool(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK)
    if blocking:
        raise OSError("PTY master fd remained blocking")


def sanitize_terminal_replay_buffer(text):
    for pattern in TERMINAL_REPLAY_CONTROL_PATTERNS:
        text = pattern.sub("", text)
    return text


def append_terminal_replay_buffer(buffer, text, limit=TERMINAL_BUFFER_LIMIT):
    clean_text = sanitize_terminal_replay_buffer(text or "")
    if limit <= 0:
        return ""
    if not clean_text:
        return (buffer or "")[-limit:]
    combined = (buffer or "") + clean_text
    if len(combined) <= limit:
        return combined
    text, _ = slice_terminal_replay_tail(combined, limit)
    return text


def trim_leading_partial_terminal_sequence(text):
    match = TERMINAL_LEADING_CSI_TAIL_RE.match(text or "")
    if not match:
        return text or "", 0
    return text[match.end():], match.end()


def slice_terminal_replay_tail(buffer, limit):
    buffer = buffer or ""
    if limit <= 0:
        return "", len(buffer)
    if len(buffer) <= limit:
        text, dropped = trim_leading_partial_terminal_sequence(buffer)
        return text, dropped

    start = len(buffer) - limit
    scan_end = min(len(buffer), start + TERMINAL_REPLAY_BOUNDARY_SCAN_CHARS)
    newline = buffer.find("\n", start, scan_end)
    if newline >= 0:
        start = newline + 1
    else:
        escape = buffer.find("\x1b", start, scan_end)
        if escape >= 0:
            start = escape
    text, dropped = trim_leading_partial_terminal_sequence(buffer[start:])
    return text, start + dropped


def append_terminal_history_buffer(history, history_start, text, limit=TERMINAL_HISTORY_LIMIT):
    clean_text = sanitize_terminal_replay_buffer(text or "")
    history = history or ""
    history_start = int(history_start or 0)
    if limit <= 0:
        history_end = history_start + len(history) + len(clean_text)
        return "", history_end, history_end
    combined = history + clean_text
    if len(combined) <= limit:
        return combined, history_start, history_start + len(combined)
    dropped = len(combined) - limit
    return combined[dropped:], history_start + dropped, history_start + len(combined)


def terminal_activity_sample(info):
    tail = ((info or {}).get("buffer") or "")[-TERMINAL_ACTIVITY_TAIL_CHARS:]
    clean = TERMINAL_ACTIVITY_CONTROL_RE.sub("", tail).replace("\r", "\n")
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    sample_lines = lines[-TERMINAL_ACTIVITY_SAMPLE_LINES:]
    signature = "\n".join(sample_lines)
    working_match = None
    working_line = ""
    for line in reversed(sample_lines):
        working_match = CODEX_WORKING_RE.search(line)
        if working_match:
            working_line = line
            break
    return {
        "lines": sample_lines,
        "signature": signature,
        "working_match": working_match,
        "working_line": working_line,
    }


def codex_activity_from_tail(info, now=None):
    now = time.time() if now is None else now
    sample = terminal_activity_sample(info)
    signature = sample["signature"]
    previous_signature = (info or {}).get("_activity_signature")
    has_previous = previous_signature is not None
    changed = has_previous and signature != previous_signature
    stable = has_previous and signature == previous_signature
    if info is not None:
        info["_activity_signature"] = signature
        info["_activity_sample_lines"] = sample["lines"]
        info["_activity_sampled_at"] = now
    if sample["working_match"]:
        return {
            "state": "working",
            "label": sample["working_match"].group(0)[:160],
            "line": sample["working_line"][-240:],
            "sample_lines": sample["lines"],
            "sample_changed": changed,
        }
    if changed:
        return {
            "state": "active",
            "label": "输出变化",
            "sample_lines": sample["lines"],
            "sample_changed": True,
        }
    return {
        "state": "unknown",
        "label": "状态未知",
        "sample_lines": sample["lines"],
        "sample_stable": stable or not has_previous,
    }


def terminal_run_state(info, codex_active, codex_activity):
    if not terminal_info_usable(info):
        return {"state": "dead", "label": "离线"}
    if (codex_activity or {}).get("state") in ("working", "active"):
        return {"state": "busy", "label": (codex_activity or {}).get("label") or "Working"}
    return {"state": "unknown" if codex_active else "shell", "label": "运行中" if codex_active else "Shell"}


def locale_is_utf8(value):
    return bool(value and UTF8_LOCALE_RE.search(str(value)))


def ensure_terminal_utf8_locale(env):
    active_locale = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or ""
    if locale_is_utf8(active_locale):
        return env

    target = env.get("TERMINAL_UTF8_LOCALE") or os.environ.get("TERMINAL_UTF8_LOCALE") or DEFAULT_TERMINAL_UTF8_LOCALE
    for key in ("LANG", "LC_CTYPE"):
        if not locale_is_utf8(env.get(key)):
            env[key] = target
    if env.get("LC_ALL") and not locale_is_utf8(env.get("LC_ALL")):
        env["LC_ALL"] = target
    return env


def build_terminal_env(base_env, cols, rows, workspace_id="", terminal_id=""):
    env = dict(base_env)
    for key in list(env):
        if key == "PROMPT_COMMAND" or key.startswith("VSCODE_") or key.startswith("__vsc_"):
            env.pop(key, None)
    env.update({
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "CLICOLOR": "1",
        "TERM_PROGRAM": "codexws",
        "TERM_PROGRAM_VERSION": "xterm.js-5.3.0",
        "COLUMNS": str(cols),
        "LINES": str(rows),
    })
    if workspace_id and terminal_id:
        env["CODEXWS_WORKSPACE_ID"] = str(workspace_id)
        env["CODEXWS_TERMINAL_ID"] = str(terminal_id)
    return ensure_terminal_utf8_locale(env)


def create_terminal_runtime(socketio, deps):
    manually_closed_terminal_keys = set()
    manually_closed_workspace_ids = set()
    terminal_lifecycle_generations = {}
    workspace_lifecycle_generations = {}
    terminal_geometry_owners = {}
    workspace_geometry_owners = {}
    workspace_geometry_lock = threading.RLock()
    terminal_lifecycle_locks = {}
    terminal_lifecycle_locks_guard = threading.Lock()
    workspace_lifecycle_locks = {}
    workspace_lifecycle_locks_guard = threading.Lock()
    socket_attach_locks = {}
    socket_attach_locks_guard = threading.Lock()
    socket_attach_sequences = {}
    workspace_supervisor_lock = threading.Lock()
    workspace_supervisor_stop = threading.Event()
    runtime_stopping = threading.Event()
    workspace_supervisor_thread = {"value": None}

    def keyed_lock(key, locks, guard):
        with guard:
            return locks.setdefault(key, threading.RLock())

    def terminal_lifecycle_lock(key):
        return keyed_lock(key, terminal_lifecycle_locks, terminal_lifecycle_locks_guard)

    def workspace_lifecycle_lock(workspace_id):
        return keyed_lock(workspace_id, workspace_lifecycle_locks, workspace_lifecycle_locks_guard)

    @contextmanager
    def socket_attach_guard(socket_id):
        # Count holders and waiters before acquiring the per-SID lock. This
        # prevents disconnect cleanup from removing a lock while another event
        # still references it and creating two independent locks for one SID.
        with socket_attach_locks_guard:
            entry = socket_attach_locks.get(socket_id)
            if entry is None:
                entry = {"lock": threading.RLock(), "users": 0}
                socket_attach_locks[socket_id] = entry
            entry["users"] += 1
        try:
            with entry["lock"]:
                yield
        finally:
            with socket_attach_locks_guard:
                entry["users"] -= 1
                if (
                    entry["users"] == 0
                    and socket_id not in socket_bindings
                    and socket_attach_locks.get(socket_id) is entry
                ):
                    socket_attach_locks.pop(socket_id, None)

    def terminal_delivery_lock(info):
        lock = info.get("delivery_lock")
        if lock is not None:
            return lock
        with terminals_lock:
            return info.setdefault("delivery_lock", threading.RLock())

    def terminal_write_lock(info):
        lock = info.get("write_lock")
        if lock is not None:
            return lock
        with terminals_lock:
            return info.setdefault("write_lock", threading.Lock())

    def event_matches_terminal_binding(binding, data):
        if not binding:
            return False
        raw_sequence = (data or {}).get("attach_seq")
        if raw_sequence is None:
            return True
        try:
            return int(raw_sequence) == int(binding.get("attach_seq"))
        except (TypeError, ValueError, OverflowError):
            return False

    def socket_connection_alive(socket_id):
        manager = getattr(getattr(socketio, "server", None), "manager", None)
        if manager is None:
            return True
        try:
            return bool(manager.is_connected(socket_id, "/"))
        except Exception:
            return False

    def terminal_geometry_resize_allowed(key, socket_id, claim_geometry=False):
        with workspace_geometry_lock:
            if claim_geometry:
                terminal_geometry_owners[key] = socket_id
                return True
            owner = terminal_geometry_owners.get(key)
            return owner is None or owner == socket_id

    def release_terminal_geometry_owner(socket_id, key=None):
        with workspace_geometry_lock:
            if key is not None:
                if terminal_geometry_owners.get(key) == socket_id:
                    terminal_geometry_owners.pop(key, None)
                return
            owned_keys = [
                terminal_key_value
                for terminal_key_value, owner in terminal_geometry_owners.items()
                if owner == socket_id
            ]
            for terminal_key_value in owned_keys:
                terminal_geometry_owners.pop(terminal_key_value, None)

    def workspace_geometry_resize_allowed(workspace_id, socket_id, claim_geometry=False):
        with workspace_geometry_lock:
            if claim_geometry:
                workspace_geometry_owners[workspace_id] = socket_id
                return True
            owner = workspace_geometry_owners.get(workspace_id)
            return owner is None or owner == socket_id

    def release_workspace_geometry_owner(socket_id, workspace_id=None):
        with workspace_geometry_lock:
            if workspace_id is not None:
                if workspace_geometry_owners.get(workspace_id) == socket_id:
                    workspace_geometry_owners.pop(workspace_id, None)
                return
            owned_workspaces = [
                workspace_id
                for workspace_id, owner in workspace_geometry_owners.items()
                if owner == socket_id
            ]
            for workspace_id in owned_workspaces:
                workspace_geometry_owners.pop(workspace_id, None)

    def process_looks_like_codex(args):
        if not args:
            return False
        argv0 = os.path.basename(args[0]).lower()
        if argv0 == "codex" or argv0.startswith("codex-"):
            return True
        joined = " ".join(args).lower()
        return "@openai/codex" in joined or "/codex/bin/" in joined

    def terminal_foreground_pgrp(info):
        try:
            return os.tcgetpgrp(info["fd"])
        except (OSError, KeyError):
            return None

    def request_terminal_redraw_later(key, delay=0.12):
        def request_redraw():
            time.sleep(delay)
            with terminals_lock:
                current = terminals.get(key)
            if not terminal_info_usable(current):
                return
            foreground_pgrp = terminal_foreground_pgrp(current)
            if foreground_pgrp is None:
                return
            try:
                os.killpg(foreground_pgrp, signal.SIGWINCH)
            except OSError:
                pass

        threading.Thread(target=request_redraw, daemon=True).start()

    def terminal_has_codex_process(info):
        return bool(foreground_codex_processes(info)[1])

    def foreground_codex_processes(info):
        if not terminal_info_usable(info):
            return None, []
        root_pid = info.get("pid")
        foreground_pgrp = terminal_foreground_pgrp(info)
        processes = []
        for pid in terminal_tree_pids(info):
            if (pid == root_pid and not info.get("managed_command")) or not pid_running(pid):
                continue
            if foreground_pgrp is not None:
                try:
                    if os.getpgid(pid) != foreground_pgrp:
                        continue
                except OSError:
                    continue
            args = process_args(pid)
            if process_looks_like_codex(args):
                processes.append((pid, args))
        return foreground_pgrp, processes

    def refresh_terminal_codex_identity(info, now=None):
        if not terminal_info_usable(info):
            return False
        now = time.time() if now is None else now
        last_scan = float(info.get("_codex_process_scan_at") or 0)
        if now - last_scan < CODEX_PROCESS_SCAN_TTL:
            return bool(info.get("_codex_process_active"))

        foreground_pgrp, processes = foreground_codex_processes(info)
        active = bool(processes)
        info["_codex_process_scan_at"] = now
        info["_codex_process_active"] = active
        if not active:
            with info["lock"]:
                mapped_thread_id = normalize_thread_id(info.get("thread_id"))
                previous_title = str(info.get("thread_title") or "").strip()
            if mapped_thread_id:
                refreshed_title = codex_thread_title(mapped_thread_id, previous_title)
                with info["lock"]:
                    if normalize_thread_id(info.get("thread_id")) == mapped_thread_id:
                        info["thread_title"] = refreshed_title
            return False

        with info["lock"]:
            preferred_thread_id = normalize_thread_id(info.get("thread_id"))
            screen_model = info.get("screen_model")
            terminal_title = str(getattr(screen_model, "title", "") or "")
        descriptor = codex_thread_from_processes(
            processes,
            preferred_thread_id=preferred_thread_id,
            terminal_title=terminal_title,
        )
        if not descriptor:
            return True

        thread_id = descriptor["thread_id"]
        thread_title = str(descriptor.get("title") or "").strip()
        with info["lock"]:
            changed = info.get("thread_id") != thread_id
            info["thread_id"] = thread_id
            info["thread_title"] = thread_title
            info["thread_process_pgrp"] = foreground_pgrp
        if changed:
            deps.record_terminal_thread(
                info.get("workspace_id"),
                info.get("terminal_id") or DEFAULT_TERMINAL,
                thread_id,
            )
        return True

    def cached_terminal_has_codex_process(info, now=None):
        return refresh_terminal_codex_identity(info, now=now)

    def get_terminal_info(workspace_id, terminal_id=DEFAULT_TERMINAL):
        key = terminal_key(workspace_id, terminal_id)
        with terminals_lock:
            return terminals.get(key)

    def terminal_summary(key, info):
        workspace_id, terminal_id = split_terminal_key(key)
        if info:
            workspace_id = info.get("workspace_id") or workspace_id
            terminal_id = info.get("terminal_id") or terminal_id
        observation = deps.terminal_observation_summary(key)
        codex_active = cached_terminal_has_codex_process(info) if info else False
        state_lock = (info or {}).get("lock") or threading.RLock()
        with state_lock:
            codex_activity = codex_activity_from_tail(info) if info else {"state": "idle"}
            state = {
                "name": (info or {}).get("name") or terminal_id,
                "pid": (info or {}).get("pid"),
                "alive": bool((info or {}).get("alive")),
                "buffer_size": len((info or {}).get("buffer", "")),
                "history_size": len((info or {}).get("history_buffer", "")),
                "cols": (info or {}).get("cols"),
                "rows": (info or {}).get("rows"),
                "cwd": (info or {}).get("cwd") or deps.workspace_path(workspace_id),
                "reader_alive": bool(
                    info and info.get("reader_thread") and info["reader_thread"].is_alive()
                ),
                "thread_id": normalize_thread_id((info or {}).get("thread_id")),
                "thread_title": str((info or {}).get("thread_title") or "").strip(),
            }
        run_state = terminal_run_state(info, codex_active, codex_activity)
        return {
            "id": terminal_id,
            "name": state["name"],
            "workspace_id": workspace_id,
            "pid": state["pid"],
            "alive": state["alive"],
            "usable": terminal_info_usable(info) if info else False,
            "codex_active": codex_active,
            "has_codex_process": codex_active,
            "thread_id": state["thread_id"] or None,
            "thread_title": state["thread_title"] or None,
            "codex_activity": codex_activity,
            "run_state": run_state,
            "process_state": "running" if terminal_info_usable(info) else "exited",
            "agent_state": "unknown",
            "managed": bool((info or {}).get("managed_command")),
            "exit_code": (info or {}).get("exit_code"),
            "buffer_size": state["buffer_size"],
            "history_size": state["history_size"],
            "cols": state["cols"],
            "rows": state["rows"],
            "cwd": state["cwd"],
            "last_output_at": observation.get("last_output_at") or 0,
            "reader_alive": state["reader_alive"],
            "observation": observation,
        }

    def workspace_terminal_summaries():
        grouped = {}
        with terminals_lock:
            snapshot = list(terminals.items())
        for key, info in snapshot:
            workspace_id, _ = split_terminal_key(key)
            grouped.setdefault(workspace_id, []).append(terminal_summary(key, info))
        for items in grouped.values():
            items.sort(key=lambda item: (item["id"] != DEFAULT_TERMINAL, item["name"]))
        return grouped

    def append_terminal_buffer(info, text):
        clean_text = sanitize_terminal_replay_buffer(text or "")
        replay = info.get("buffer")
        if isinstance(replay, TerminalHistoryBuffer):
            replay.append(clean_text)
        else:
            info["buffer"] = append_terminal_replay_buffer(replay or "", clean_text)
        history = info.get("history_buffer")
        if isinstance(history, TerminalHistoryBuffer):
            history.append(clean_text)
            info["history_start"] = history.start
            info["history_end"] = history.end
            return
        history, history_start, history_end = append_terminal_history_buffer(
            history or "",
            info.get("history_start", 0),
            text,
        )
        info["history_buffer"] = history
        info["history_start"] = history_start
        info["history_end"] = history_end

    def terminal_frame_locked(info):
        screen_model = info.get("screen_model")
        if screen_model is None:
            return None
        try:
            snapshot = screen_model.serialize_bounded(TERMINAL_SCREEN_SNAPSHOT_LIMIT)
        except Exception as exc:
            info["screen_model_error"] = f"serialize: {type(exc).__name__}: {exc}"
            return None
        if not snapshot:
            info["screen_model_error"] = "serialize: authoritative frame exceeded the configured limit"
            return None
        info.pop("screen_model_error", None)
        if not info.get("stream_id"):
            info["stream_id"] = uuid.uuid4().hex
        if not info.get("stream_epoch"):
            info["stream_epoch"] = next_terminal_stream_epoch()
        history_start = int(info.get("history_start") or 0)
        history_end = int(info.get("history_end") or 0)
        return {
            "stream_id": str(info.get("stream_id") or ""),
            "stream_epoch": int(info.get("stream_epoch") or 0),
            "frame_seq": int(info.get("frame_seq") or 0),
            "frame_end": history_end,
            "history_start": history_start,
            "history_end": history_end,
            "screen_snapshot": snapshot,
            "screen_snapshot_end": history_end,
            "screen_snapshot_cols": screen_model.cols,
            "screen_snapshot_rows": screen_model.rows,
            "screen_snapshot_source": "server",
            "screen_snapshot_active_buffer": getattr(screen_model, "active_buffer", "normal"),
            "screen_snapshot_delta_safe": bool(getattr(screen_model, "delta_safe", False)),
        }

    def terminal_screen_frame(workspace_id, terminal_id=DEFAULT_TERMINAL):
        terminal_id = normalize_terminal_id(terminal_id)
        key = terminal_key(workspace_id, terminal_id)
        with terminals_lock:
            info = terminals.get(key)
        if not terminal_info_usable(info):
            return None
        with terminal_delivery_lock(info):
            with terminals_lock:
                if terminals.get(key) is not info:
                    return None
            with info["lock"]:
                frame = terminal_frame_locked(info)
        if frame is None:
            return None
        return {
            "workspace_id": workspace_id,
            "terminal_id": terminal_id,
            **frame,
        }

    def rebuild_terminal_screen_locked(info, text):
        history = info.get("history_buffer")
        if int(info.get("history_start") or 0) != 0:
            return False
        raw_history = history.to_string() if isinstance(history, TerminalHistoryBuffer) else str(history or "")
        try:
            replacement = TerminalScreenModel(info.get("cols") or 80, info.get("rows") or 24)
            replacement.feed(raw_history)
            replacement.feed(text)
        except Exception as exc:
            info["screen_model_error"] = f"rebuild: {type(exc).__name__}: {exc}"
            return False
        info["screen_model"] = replacement
        info.pop("screen_model_error", None)
        return True

    def terminal_tcp_clients_snapshot(info):
        with info["lock"]:
            return list(info.get("tcp_clients", set()))

    def send_to_tcp_clients(info, data, clients=None):
        if clients is None:
            clients = terminal_tcp_clients_snapshot(info)
        for client in clients:
            try:
                client.settimeout(0.2)
                client.sendall(data)
            except Exception:
                remove_tcp_client(info, client)
            else:
                try:
                    client.settimeout(0.5)
                except Exception:
                    remove_tcp_client(info, client)

    def remove_tcp_client(info, client):
        try:
            with info["lock"]:
                info.get("tcp_clients", set()).discard(client)
        except Exception:
            pass
        try:
            client.close()
        except OSError:
            pass

    def write_terminal_bytes(term_key, info, data, stall_timeout=None):
        deadline = None
        if stall_timeout is not None:
            deadline = time.monotonic() + max(0.0, float(stall_timeout))
        try:
            with terminal_write_lock(info):
                view = memoryview(data)
                while view:
                    with terminals_lock:
                        if (
                            terminals.get(term_key) is not info
                            or info.get("closing")
                            or not terminal_info_usable(info)
                        ):
                            return False
                    try:
                        written = os.write(info["fd"], view)
                    except BlockingIOError:
                        timeout = TERMINAL_WRITE_POLL_INTERVAL
                        if deadline is not None:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                return False
                            timeout = min(timeout, remaining)
                        try:
                            _, writable, exceptional = select.select(
                                [],
                                [info["fd"]],
                                [info["fd"]],
                                timeout,
                            )
                        except (OSError, ValueError):
                            return False
                        if exceptional:
                            return False
                        if (
                            deadline is not None
                            and not writable
                            and time.monotonic() >= deadline
                        ):
                            return False
                        continue
                    if written <= 0:
                        return False
                    view = view[written:]
                    if stall_timeout is not None:
                        deadline = time.monotonic() + max(0.0, float(stall_timeout))
            return True
        except OSError:
            return False

    def enqueue_terminal_write(term_key, info, data):
        condition = info.get("input_condition")
        queue = info.get("input_queue")
        if condition is None or queue is None:
            return None
        with condition:
            with terminals_lock:
                if (
                    terminals.get(term_key) is not info
                    or info.get("closing")
                    or not terminal_info_usable(info)
                ):
                    return False
            queued_bytes = int(info.get("input_queue_size") or 0)
            queue.append(bytes(data))
            info["input_queue_size"] = queued_bytes + len(data)
            condition.notify()
        return True

    def pty_writer(term_key, info):
        condition = info["input_condition"]
        queue = info["input_queue"]
        while True:
            with condition:
                condition.wait_for(lambda: queue or info.get("closing"))
                if info.get("closing"):
                    queue.clear()
                    info["input_queue_size"] = 0
                    return
                data = queue.popleft()
                info["input_queue_size"] = max(
                    0,
                    int(info.get("input_queue_size") or 0) - len(data),
                )

            if write_terminal_bytes(term_key, info, data):
                continue
            with terminals_lock:
                active = terminals.get(term_key) is info and not info.get("closing")
            if not active:
                return
            with info["lock"]:
                info["writer_error"] = "PTY input writer stopped before completing queued input"
            workspace_id, terminal_id = split_terminal_key(term_key)
            if close_terminal(workspace_id, terminal_id, manual=False, expected_info=info):
                threading.Thread(
                    target=restart_terminal_later,
                    args=(
                        workspace_id,
                        terminal_id,
                        info.get("workspace_generation"),
                        info.get("terminal_generation"),
                    ),
                    daemon=True,
                ).start()
            return

    def pty_reader(term_key, info):
        workspace_id, terminal_id = split_terminal_key(term_key)
        room = f"terminal:{term_key}"
        fd = info["fd"]
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            with terminals_lock:
                if terminals.get(term_key) is not info or not info.get("alive"):
                    return
            try:
                r, _, _ = select.select([fd], [], [], 0.2)
                if not r:
                    continue
                try:
                    data = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not data:
                    break
                text = decoder.decode(data)
                if not text:
                    continue
                try:
                    deps.record_terminal_output_observation(term_key, text)
                except Exception as exc:
                    with info["lock"]:
                        info["observation_error"] = f"{type(exc).__name__}: {exc}"
                with terminal_delivery_lock(info):
                    with terminals_lock:
                        if terminals.get(term_key) is not info or not info.get("alive"):
                            return
                    with info["lock"]:
                        output_start = int(info.get("history_end") or 0)
                        screen_model = info.get("screen_model")
                        if screen_model is not None:
                            try:
                                screen_model.feed(text)
                            except Exception as exc:
                                info["screen_model_error"] = f"feed: {type(exc).__name__}: {exc}"
                                rebuild_terminal_screen_locked(info, text)
                        append_terminal_buffer(info, text)
                        info["frame_seq"] = int(info.get("frame_seq") or 0) + 1
                        output_end = int(info.get("history_end") or output_start)
                        history_start = int(info.get("history_start") or 0)
                    try:
                        socketio.emit(TerminalSocketEvent.OUTPUT, {
                            "workspace_id": workspace_id,
                            "terminal_id": terminal_id,
                            "stream_id": info.get("stream_id"),
                            "stream_epoch": info.get("stream_epoch"),
                            "frame_seq": info.get("frame_seq"),
                            "data": text,
                            "output_start": output_start,
                            "output_end": output_end,
                            "history_start": history_start,
                        }, room=room)
                    except Exception as exc:
                        with info["lock"]:
                            info["socket_emit_error"] = f"{type(exc).__name__}: {exc}"
                    # Snapshot membership at the same revision as history. A
                    # client registering afterwards replays this revision and
                    # is intentionally absent from this live-send snapshot.
                    tcp_clients = terminal_tcp_clients_snapshot(info)
                send_to_tcp_clients(
                    info,
                    text.encode("utf-8", errors="replace"),
                    tcp_clients,
                )
            except Exception as exc:
                with info["lock"]:
                    info["reader_error"] = f"{type(exc).__name__}: {exc}"
                break

        with terminals_lock:
            if terminals.get(term_key) is not info:
                return
            info["alive"] = False
        with terminal_delivery_lock(info):
            with terminals_lock:
                if terminals.get(term_key) is not info:
                    return
            try:
                socketio.emit(TerminalSocketEvent.EXIT, {
                    "workspace_id": workspace_id,
                    "terminal_id": terminal_id,
                    "stream_id": info.get("stream_id"),
                    "stream_epoch": info.get("stream_epoch"),
                }, room=room)
            except Exception as exc:
                with info["lock"]:
                    info["socket_emit_error"] = f"{type(exc).__name__}: {exc}"
        if info.get("managed_command"):
            info["closing"] = True
            with info["input_condition"]:
                info["input_condition"].notify_all()
            proc = info.get("proc")
            if proc is not None:
                try:
                    info["exit_code"] = proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            return
        if not close_terminal(workspace_id, terminal_id, manual=False, expected_info=info):
            return
        threading.Thread(
            target=restart_terminal_later,
            args=(
                workspace_id,
                terminal_id,
                info.get("workspace_generation"),
                info.get("terminal_generation"),
            ),
            daemon=True,
        ).start()

    def terminal_env(cols, rows, workspace_id="", terminal_id=""):
        return build_terminal_env(os.environ, cols, rows, workspace_id, terminal_id)

    def start_workspace_agent_later(workspace_id, delay=WORKSPACE_AGENT_START_DELAY):
        if workspace_id not in WORKSPACE_AGENT_COMMANDS:
            return
        threading.Thread(target=delayed_workspace_agent_start, args=(workspace_id, delay), daemon=True).start()

    def delayed_workspace_agent_start(workspace_id, delay):
        time.sleep(delay)
        ensure_workspace_agent(workspace_id)

    def ensure_workspace_agent(workspace_id, command_override=None, force_restart=False):
        command = command_override or WORKSPACE_AGENT_COMMANDS.get(workspace_id)
        if not command:
            return
        info = ensure_terminal(workspace_id, reopen=force_restart)
        if not info:
            return
        now = time.time()
        if not force_restart and now - info.get("last_agent_start", 0) < WORKSPACE_AGENT_RESTART_GRACE:
            return
        if not force_restart and workspace_agent_running(workspace_id, info, command):
            return

        agent_pids = [pid for pid, _ in workspace_agent_processes(workspace_id, info, command)]
        if agent_pids:
            terminate_pids(agent_pids, signal.SIGTERM)
            time.sleep(0.5)
            remaining = [pid for pid in agent_pids if pid_running(pid)]
            if remaining:
                terminate_pids(remaining, signal.SIGKILL)

        info["last_agent_start"] = now
        cwd = shlex.quote(info.get("cwd") or deps.workspace_path(workspace_id))
        write_terminal(
            workspace_id,
            f"cd {cwd} && {command}\r".encode("utf-8"),
            expected_info=info,
        )

    def cleanup_terminal_info(info):
        if not info:
            return
        with info["lock"]:
            clients = list(info.get("tcp_clients", set()))
        for client in clients:
            remove_tcp_client(info, client)
        proc = info.get("proc")
        # An already reaped leader PID may have been reused by another process.
        if proc is None or proc.poll() is None:
            terminate_process_tree(info.get("pid"))
        else:
            terminate_orphaned_session(info.get("pid"))
        if proc is not None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        try:
            os.close(info["fd"])
        except OSError:
            pass
        input_condition = info.get("input_condition")
        if input_condition is not None:
            with input_condition:
                queue = info.get("input_queue")
                if queue is not None:
                    queue.clear()
                info["input_queue_size"] = 0
                input_condition.notify_all()
        with info["lock"]:
            info["buffer"] = ""
            info["history_buffer"] = ""
            info["history_start"] = int(info.get("history_end") or 0)
            info["screen_model"] = None
            info["screen_snapshot"] = ""
            info["screen_snapshot_end"] = 0
            info["screen_snapshot_cols"] = 0
            info["screen_snapshot_rows"] = 0
            info["tcp_clients"] = set()
            info["proc"] = None
            info["reader_thread"] = None
            info["writer_thread"] = None

    def cleanup_terminal_info_async(info):
        if info:
            threading.Thread(target=cleanup_terminal_info, args=(info,), daemon=True).start()

    def detach_terminal_instance(key, info):
        if not info:
            return None
        with terminals_lock:
            if terminals.get(key) is not info:
                return None
            info["closing"] = True
        input_condition = info.get("input_condition")
        if input_condition is not None:
            with input_condition:
                input_condition.notify_all()
        with terminal_write_lock(info):
            with terminal_delivery_lock(info):
                with terminals_lock:
                    if terminals.get(key) is not info:
                        return None
                    terminals.pop(key, None)
                with info["lock"]:
                    info["alive"] = False
        return info

    def close_terminal(workspace_id, terminal_id=DEFAULT_TERMINAL, manual=True, expected_info=None):
        key = terminal_key(workspace_id, terminal_id)
        with terminal_lifecycle_lock(key):
            with terminals_lock:
                current = terminals.get(key)
                if expected_info is not None and current is not expected_info:
                    return False
                if manual:
                    manually_closed_terminal_keys.add(key)
                    terminal_lifecycle_generations[key] = (
                        int(terminal_lifecycle_generations.get(key) or 0) + 1
                    )
            info = detach_terminal_instance(key, current)
            if manual:
                # Keep session metadata in the same lifecycle boundary. A
                # concurrent explicit reopen must record after this forget.
                deps.forget_terminal_session(workspace_id, terminal_id)
        cleanup_terminal_info(info)
        return True

    def close_workspace_terminals(workspace_id):
        with workspace_lifecycle_lock(workspace_id):
            manually_closed_workspace_ids.add(workspace_id)
            workspace_lifecycle_generations[workspace_id] = (
                int(workspace_lifecycle_generations.get(workspace_id) or 0) + 1
            )
            with terminals_lock:
                keys = [key for key in terminals if split_terminal_key(key)[0] == workspace_id]
            for key in keys:
                _, terminal_id = split_terminal_key(key)
                close_terminal(workspace_id, terminal_id, manual=True)

    def restart_terminal_later(
        workspace_id,
        terminal_id=DEFAULT_TERMINAL,
        expected_workspace_generation=None,
        expected_terminal_generation=None,
    ):
        time.sleep(1)
        if runtime_stopping.is_set():
            return
        ensure_terminal(
            workspace_id,
            terminal_id=terminal_id,
            expected_workspace_generation=expected_workspace_generation,
            expected_terminal_generation=expected_terminal_generation,
        )

    def ensure_terminal(
        workspace_id,
        terminal_id=DEFAULT_TERMINAL,
        cols=80,
        rows=24,
        name=None,
        thread_id=None,
        reopen=False,
        reopen_workspace=False,
        expected_workspace_generation=None,
        expected_terminal_generation=None,
        command=None,
    ):
        if runtime_stopping.is_set():
            return None
        cols, rows = clamp_terminal_geometry(cols, rows)
        terminal_id = normalize_terminal_id(terminal_id)
        thread_id = normalize_thread_id(thread_id)
        key = terminal_key(workspace_id, terminal_id)
        with workspace_lifecycle_lock(workspace_id):
            workspace_generation = int(workspace_lifecycle_generations.get(workspace_id) or 0)
            if (
                expected_workspace_generation is not None
                and int(expected_workspace_generation) != workspace_generation
            ):
                return None
            workspace_was_closed = workspace_id in manually_closed_workspace_ids
            if workspace_was_closed and not reopen_workspace:
                return None
            if not deps.workspace_exists(workspace_id):
                return None
            cwd = deps.workspace_path(workspace_id)
            if not cwd:
                return None

            with terminal_lifecycle_lock(key):
                if runtime_stopping.is_set():
                    return None
                terminal_generation = int(terminal_lifecycle_generations.get(key) or 0)
                if (
                    expected_terminal_generation is not None
                    and int(expected_terminal_generation) != terminal_generation
                ):
                    return None
                terminal_was_closed = key in manually_closed_terminal_keys
                if terminal_was_closed and not (reopen or reopen_workspace):
                    return None
                with terminals_lock:
                    existing = terminals.get(key)
                if terminal_info_usable(existing):
                    if thread_id:
                        with existing["lock"]:
                            existing["thread_id"] = thread_id
                            existing["thread_title"] = codex_thread_title(thread_id)
                        deps.record_terminal_thread(workspace_id, terminal_id, thread_id)
                    return existing
                stale = detach_terminal_instance(key, existing)
                cleanup_terminal_info(stale)

                master_fd, slave_fd = pty.openpty()
                try:
                    set_terminal_fd_nonblocking(master_fd)
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
                    proc = subprocess.Popen(
                        [sys.executable, os.path.join(os.path.dirname(__file__), "pty_child.py"), *(command or ["bash", "-i"])],
                        stdin=slave_fd,
                        stdout=slave_fd,
                        stderr=slave_fd,
                        start_new_session=True,
                        cwd=cwd,
                        env=terminal_env(cols, rows, workspace_id, terminal_id),
                    )
                except Exception:
                    try:
                        os.close(master_fd)
                    except OSError:
                        pass
                    raise
                finally:
                    try:
                        os.close(slave_fd)
                    except OSError:
                        pass

                input_condition = threading.Condition()
                info = {
                    "key": key,
                    "workspace_id": workspace_id,
                    "workspace_generation": workspace_generation,
                    "terminal_id": terminal_id,
                    "terminal_generation": terminal_generation,
                    "name": name or terminal_id,
                    "thread_id": thread_id,
                    "thread_title": codex_thread_title(thread_id) if thread_id else "",
                    "fd": master_fd,
                    "pid": proc.pid,
                    "proc": proc,
                    "managed_command": list(command) if command else None,
                    "buffer": TerminalHistoryBuffer(TERMINAL_BUFFER_LIMIT),
                    "history_buffer": TerminalHistoryBuffer(TERMINAL_HISTORY_LIMIT),
                    "history_start": 0,
                    "history_end": 0,
                    "screen_model": TerminalScreenModel(cols, rows),
                    "stream_id": uuid.uuid4().hex,
                    "stream_epoch": next_terminal_stream_epoch(),
                    "frame_seq": 0,
                    "cols": cols,
                    "rows": rows,
                    "alive": True,
                    "closing": False,
                    "lock": threading.Lock(),
                    "write_lock": threading.Lock(),
                    "input_condition": input_condition,
                    "input_queue": deque(),
                    "input_queue_size": 0,
                    "delivery_lock": threading.RLock(),
                    "cwd": cwd,
                    "tcp_clients": set(),
                    "last_agent_start": 0,
                }
                with terminals_lock:
                    terminals[key] = info
                manually_closed_terminal_keys.discard(key)
                if workspace_was_closed:
                    manually_closed_workspace_ids.discard(workspace_id)

                writer = threading.Thread(target=pty_writer, args=(key, info), daemon=True)
                info["writer_thread"] = writer
                writer.start()
                reader = threading.Thread(target=pty_reader, args=(key, info), daemon=True)
                info["reader_thread"] = reader
                reader.start()
                deps.record_terminal_session(workspace_id, terminal_id, info["name"])
                if thread_id:
                    deps.record_terminal_thread(workspace_id, terminal_id, thread_id)
        start_workspace_agent_later(workspace_id)
        return info

    def resize_terminal_info(info, cols, rows):
        if not info:
            return False
        cols, rows = clamp_terminal_geometry(cols, rows)
        if info.get("cols") == cols and info.get("rows") == rows:
            return True
        with terminal_delivery_lock(info):
            with terminals_lock:
                if terminals.get(info.get("key")) is not info or not info.get("alive"):
                    return False
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(info["fd"], termios.TIOCSWINSZ, winsize)
            except (KeyError, OSError):
                return False
            info["cols"] = cols
            info["rows"] = rows
            with info["lock"]:
                screen_model = info.get("screen_model")
                if screen_model is not None:
                    try:
                        screen_model.resize(cols, rows)
                    except Exception as exc:
                        info["screen_model_error"] = f"resize: {type(exc).__name__}: {exc}"
                info["frame_seq"] = int(info.get("frame_seq") or 0) + 1
                history_start = int(info.get("history_start") or 0)
                history_end = int(info.get("history_end") or 0)
                resize_event = {
                    "workspace_id": info.get("workspace_id"),
                    "terminal_id": info.get("terminal_id") or DEFAULT_TERMINAL,
                    "stream_id": info.get("stream_id"),
                    "stream_epoch": info.get("stream_epoch"),
                    "frame_seq": info.get("frame_seq"),
                    "frame_end": history_end,
                    "event_type": "resize",
                    "data": "",
                    "output_start": history_end,
                    "output_end": history_end,
                    "history_start": history_start,
                    "cols": cols,
                    "rows": rows,
                }
            socketio.emit(
                TerminalSocketEvent.OUTPUT,
                resize_event,
                room=f"terminal:{info.get('key')}",
            )
        return True

    def resize_terminal(workspace_id, cols, rows, terminal_id=DEFAULT_TERMINAL):
        key = terminal_key(workspace_id, terminal_id)
        with terminals_lock:
            info = terminals.get(key)
        return resize_terminal_info(info, cols, rows)

    def resize_workspace_terminals(workspace_id, cols, rows):
        cols, rows = clamp_terminal_geometry(cols, rows)
        with terminals_lock:
            workspace_infos = [
                info
                for key, info in terminals.items()
                if (info.get("workspace_id") or split_terminal_key(key)[0]) == workspace_id
            ]
        resized = 0
        for info in workspace_infos:
            if terminal_info_usable(info) and resize_terminal_info(info, cols, rows):
                resized += 1
        return resized

    def write_terminal(workspace_id, data, terminal_id=DEFAULT_TERMINAL, expected_info=None):
        data = data.replace(b"\x1b[I", b"").replace(b"\x1b[O", b"")
        if not data:
            return True
        key = terminal_key(workspace_id, terminal_id)
        info = expected_info or ensure_terminal(workspace_id, terminal_id=terminal_id)
        if not info:
            return False
        queued = enqueue_terminal_write(key, info, data)
        if queued is not None:
            return queued
        # Compatibility for externally supplied legacy info records. Runtime-
        # created terminals always use the lossless single-writer queue.
        return write_terminal_bytes(key, info, data, TERMINAL_WRITE_STALL_TIMEOUT)

    def handle_workspace_tcp_client(workspace_id, client, address):
        terminal_id = DEFAULT_TERMINAL
        key = terminal_key(workspace_id, terminal_id)
        info = ensure_terminal(workspace_id, terminal_id=terminal_id)
        if not info:
            try:
                client.close()
            except OSError:
                pass
            return
        try:
            client.settimeout(0.5)
        except OSError:
            remove_tcp_client(info, client)
            return

        # Replay and client registration share the terminal delivery boundary.
        # The client is added only after replay succeeds, so live output cannot
        # overtake replay and a replacement cannot inherit a stale client.
        with terminal_delivery_lock(info):
            with terminals_lock:
                current = terminals.get(key)
                active = current is info and info.get("alive") and not info.get("closing")
            if not active:
                remove_tcp_client(info, client)
                return
            with info["lock"]:
                replay = info.get("buffer", "")
                buffer = replay.to_string() if isinstance(replay, TerminalHistoryBuffer) else replay
            if buffer:
                try:
                    client.settimeout(0.2)
                    client.sendall(buffer.encode("utf-8", errors="replace"))
                except (OSError, socket.timeout):
                    remove_tcp_client(info, client)
                    return
            try:
                client.settimeout(0.5)
            except OSError:
                remove_tcp_client(info, client)
                return
            with terminals_lock:
                current = terminals.get(key)
                active = current is info and info.get("alive") and not info.get("closing")
            if not active:
                remove_tcp_client(info, client)
                return
            with info["lock"]:
                info.setdefault("tcp_clients", set()).add(client)

        try:
            while True:
                try:
                    data = client.recv(4096)
                except socket.timeout:
                    with terminals_lock:
                        active = (
                            terminals.get(key) is info
                            and info.get("alive")
                            and not info.get("closing")
                        )
                    if not active:
                        break
                    continue
                if not data:
                    break
                if not write_terminal(workspace_id, data, terminal_id=terminal_id, expected_info=info):
                    break
        except OSError:
            pass
        finally:
            remove_tcp_client(info, client)

    def workspace_tcp_server(workspace_id, host, port):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((host, port))
            srv.listen(20)
        except OSError as e:
            print(f"workspace PTY port disabled for {workspace_id}: {host}:{port} ({e})")
            try:
                srv.close()
            except OSError:
                pass
            return
        workspace_tcp_servers[workspace_id] = srv
        print(f"workspace PTY port listening: {workspace_id} {host}:{port}")
        while True:
            try:
                client, address = srv.accept()
            except OSError:
                break
            t = threading.Thread(target=handle_workspace_tcp_client, args=(workspace_id, client, address), daemon=True)
            t.start()

    def start_workspace_tcp_servers():
        for workspace_id, port in list(WORKSPACE_PTY_PORTS.items()):
            t = threading.Thread(target=workspace_tcp_server, args=(workspace_id, WORKSPACE_PTY_HOST, port), daemon=True)
            t.start()

    def close_workspace_tcp_servers():
        for srv in list(workspace_tcp_servers.values()):
            try:
                srv.close()
            except OSError:
                pass
        workspace_tcp_servers.clear()

    def handle_terminal_attach(data):
        data = data or {}
        workspace_id = str(data.get("workspace_id") or "").strip()
        terminal_id = normalize_terminal_id(data.get("terminal_id") or DEFAULT_TERMINAL)
        attach_seq = data.get("attach_seq")
        view_only = bool(data.get("view_only") or data.get("readonly"))
        claim_geometry = bool(data.get("claim_geometry"))
        sync_workspace_geometry = bool(data.get("sync_workspace_geometry"))
        if not workspace_id:
            emit(TerminalSocketEvent.ERROR, {"error": "缺少 workspace_id"})
            return
        if not deps.workspace_exists(workspace_id):
            emit(TerminalSocketEvent.ERROR, {"error": "未知工作区"})
            return
        item = deps.workspace_item(workspace_id)
        if not item or not item.get("exists"):
            emit(TerminalSocketEvent.ERROR, {"error": f"工作区目录不存在: {(item or {}).get('cwd') or workspace_id}"})
            return

        previous = socket_bindings.get(request.sid)

        cols, rows = clamp_terminal_geometry(data.get("cols", 80), data.get("rows", 24))
        key = terminal_key(workspace_id, terminal_id)
        if view_only:
            with terminals_lock:
                info = terminals.get(key)
            if not terminal_info_usable(info):
                emit(TerminalSocketEvent.ERROR, {"error": "终端不可用"})
                return
        else:
            with terminals_lock:
                info = terminals.get(key)
            if not (info and info.get("managed_command")):
                info = ensure_terminal(
                    workspace_id, terminal_id=terminal_id, cols=cols, rows=rows, reopen=True,
                )
        if not info:
            emit(TerminalSocketEvent.ERROR, {"error": "终端启动失败"})
            return

        if not view_only:
            with workspace_geometry_lock:
                if sync_workspace_geometry:
                    resize_allowed = workspace_geometry_resize_allowed(
                        workspace_id,
                        request.sid,
                        claim_geometry=claim_geometry,
                    )
                    if resize_allowed:
                        resize_workspace_terminals(workspace_id, cols, rows)
                else:
                    resize_allowed = terminal_geometry_resize_allowed(
                        key,
                        request.sid,
                        claim_geometry=claim_geometry,
                    )
                    if resize_allowed:
                        resize_terminal(workspace_id, cols, rows, terminal_id=terminal_id)
        deps.record_terminal_session(workspace_id, terminal_id, info.get("name") or terminal_id)
        replay_limit = int(data.get("replay_limit", 0) or 0)
        if replay_limit < 0:
            replay_limit = 0
        if replay_limit:
            replay_limit = min(replay_limit, TERMINAL_BUFFER_LIMIT)
        terminal_payload = terminal_summary(key, info)
        workspace_payload = deps.workspace_item(workspace_id)
        with terminal_delivery_lock(info):
            with terminals_lock:
                if terminals.get(key) is not info:
                    emit(TerminalSocketEvent.ERROR, {"error": "终端已被替换，请重试"})
                    return
            if not socket_connection_alive(request.sid):
                return
            with info["lock"]:
                replay = info.get("buffer", "")
                buffer = replay.to_string() if isinstance(replay, TerminalHistoryBuffer) else replay
                history = info.get("history_buffer", "")
                history_start = int(info.get("history_start") or 0)
                history_end = int(info.get("history_end") or (history_start + len(history or buffer)))
                buffer_end = history_end
                buffer_start = max(history_start, buffer_end - len(buffer))
                if replay_limit and len(buffer) > replay_limit:
                    buffer, relative_start = slice_terminal_replay_tail(buffer, replay_limit)
                    buffer_start = max(history_start, buffer_end - len(replay) + relative_start)
                frame = terminal_frame_locked(info)
                screen_snapshot = ""
                screen_snapshot_end = 0
                screen_snapshot_cols = 0
                screen_snapshot_rows = 0
                screen_snapshot_source = ""
                screen_snapshot_active_buffer = "unknown"
                screen_snapshot_delta_safe = False
                if frame is not None:
                    screen_snapshot = frame["screen_snapshot"]
                    screen_snapshot_end = frame["screen_snapshot_end"]
                    screen_snapshot_cols = frame["screen_snapshot_cols"]
                    screen_snapshot_rows = frame["screen_snapshot_rows"]
                    screen_snapshot_source = "server"
                    screen_snapshot_active_buffer = frame["screen_snapshot_active_buffer"]
                    screen_snapshot_delta_safe = frame["screen_snapshot_delta_safe"]
                else:
                    screen_snapshot = str(info.get("screen_snapshot") or "")
                    screen_snapshot_end = int(info.get("screen_snapshot_end") or 0)
                    if screen_snapshot and history_start <= screen_snapshot_end <= history_end:
                        screen_snapshot_cols = int(info.get("screen_snapshot_cols") or 0)
                        screen_snapshot_rows = int(info.get("screen_snapshot_rows") or 0)
                        screen_snapshot_source = "client"
                    else:
                        screen_snapshot = ""
                        screen_snapshot_end = 0

            ready_payload = {
                "attach_seq": attach_seq,
                "workspace_id": workspace_id,
                "terminal_id": terminal_id,
                "terminal": terminal_payload,
                "workspace": workspace_payload,
                "pid": info.get("pid"),
                "cwd": info.get("cwd"),
                "buffer": buffer,
                "buffer_start": buffer_start,
                "buffer_end": buffer_end,
                "history_start": history_start,
                "history_end": history_end,
                "has_older_buffer": buffer_start > history_start,
                "checkpoint_supported": True,
                "screen_frame_supported": frame is not None,
                "stream_id": info.get("stream_id") or "",
                "stream_epoch": int(info.get("stream_epoch") or 0),
                "frame_seq": int(info.get("frame_seq") or 0),
                "frame_end": screen_snapshot_end,
                "screen_snapshot": screen_snapshot,
                "screen_snapshot_end": screen_snapshot_end,
                "screen_snapshot_cols": screen_snapshot_cols,
                "screen_snapshot_rows": screen_snapshot_rows,
                "screen_snapshot_source": screen_snapshot_source,
                "screen_snapshot_active_buffer": screen_snapshot_active_buffer,
                "screen_snapshot_delta_safe": screen_snapshot_delta_safe,
            }
            new_binding = {
                "workspace_id": workspace_id,
                "terminal_id": terminal_id,
                "key": key,
                "view_only": view_only,
                "attach_seq": attach_seq,
            }
            previous_room = f"terminal:{previous['key']}" if previous else ""
            target_room = f"terminal:{key}"
            room_changed = not previous or previous.get("key") != key
            joined_target = False
            left_previous = False
            try:
                # Join the target while its delivery lock is held, then leave
                # the old room. Target deltas therefore cannot overtake ready,
                # and a failed switch can restore the previous subscription.
                if room_changed:
                    joined_target = True
                    join_room(target_room)
                    if previous:
                        left_previous = True
                        leave_room(previous_room)
                socket_bindings[request.sid] = new_binding
                emit(TerminalSocketEvent.READY, ready_payload)
            except Exception:
                if previous:
                    socket_bindings[request.sid] = previous
                else:
                    socket_bindings.pop(request.sid, None)
                if left_previous:
                    try:
                        join_room(previous_room)
                    except Exception:
                        pass
                if joined_target:
                    try:
                        leave_room(target_room)
                    except Exception:
                        pass
                return
        if previous and (previous.get("key") != key or view_only):
            release_terminal_geometry_owner(request.sid, previous.get("key"))
        if previous and (previous.get("workspace_id") != workspace_id or view_only):
            release_workspace_geometry_owner(request.sid, previous.get("workspace_id"))
        if not screen_snapshot_delta_safe and not view_only and terminal_has_codex_process(info):
            request_terminal_redraw_later(key)

    def on_terminal_attach(data):
        socket_id = request.sid
        with socket_attach_guard(socket_id):
            if not socket_connection_alive(socket_id):
                return
            payload = dict(data or {})
            last_seq = int(socket_attach_sequences.get(socket_id) or 0)
            try:
                attach_seq = int(payload.get("attach_seq"))
            except (TypeError, ValueError, OverflowError):
                attach_seq = last_seq + 1
            if attach_seq <= last_seq:
                return
            socket_attach_sequences[socket_id] = attach_seq
            payload["attach_seq"] = attach_seq
            return handle_terminal_attach(payload)

    def on_terminal_snapshot(data):
        data = data or {}
        socket_id = request.sid
        with socket_attach_guard(socket_id):
            binding = socket_bindings.get(socket_id)
            if not binding or binding.get("view_only"):
                return {"ok": False, "error": "terminal is not writable"}
            if not event_matches_terminal_binding(binding, data):
                return {"ok": False, "error": "stale terminal attachment"}
            with terminals_lock:
                info = terminals.get(binding["key"])
            if not terminal_info_usable(info):
                return {"ok": False, "error": "terminal is unavailable"}

            snapshot = data.get("snapshot")
            if not isinstance(snapshot, str) or not snapshot:
                return {"ok": False, "error": "snapshot is empty"}
            if len(snapshot) > TERMINAL_SCREEN_SNAPSHOT_LIMIT:
                return {"ok": False, "error": "snapshot is too large"}
            try:
                snapshot_end = int(data.get("snapshot_end"))
            except (TypeError, ValueError, OverflowError):
                return {"ok": False, "error": "snapshot offset is invalid"}
            cols, rows = clamp_terminal_geometry(data.get("cols"), data.get("rows"))

            with terminal_delivery_lock(info):
                with terminals_lock:
                    if terminals.get(binding["key"]) is not info:
                        return {"ok": False, "error": "terminal is unavailable"}
                with info["lock"]:
                    history_start = int(info.get("history_start") or 0)
                    history_end = int(info.get("history_end") or 0)
                    if not history_start <= snapshot_end <= history_end:
                        return {"ok": False, "error": "snapshot offset is outside terminal history"}
                    current_snapshot_end = int(info.get("screen_snapshot_end") or 0)
                    if info.get("screen_snapshot") and snapshot_end < current_snapshot_end:
                        return {"ok": False, "error": "snapshot is older than the saved checkpoint"}
                    info["screen_snapshot"] = snapshot
                    info["screen_snapshot_end"] = snapshot_end
                    info["screen_snapshot_cols"] = cols
                    info["screen_snapshot_rows"] = rows
            return {
                "ok": True,
                "snapshot_end": snapshot_end,
                "history_end": history_end,
            }

    def on_terminal_input(data):
        data = data or {}
        socket_id = request.sid
        with socket_attach_guard(socket_id):
            binding = socket_bindings.get(socket_id)
            if not binding or binding.get("view_only") or not event_matches_terminal_binding(binding, data):
                return
            workspace_id = binding["workspace_id"]
            terminal_id = binding["terminal_id"]
            if not deps.workspace_exists(workspace_id):
                return
            with terminals_lock:
                info = terminals.get(binding["key"])
            if not terminal_info_usable(info):
                return
            text = data.get("data", "")
            encoded = text.encode("utf-8")
            queued = enqueue_terminal_write(binding["key"], info, encoded)
            if queued is not None:
                return queued
        # PTY backpressure must not prevent this SID from attaching elsewhere
        # or disconnecting. expected_info keeps the accepted input tied to the
        # terminal instance that was current at its validation point.
        return write_terminal(
            workspace_id,
            encoded,
            terminal_id=terminal_id,
            expected_info=info,
        )

    def on_terminal_resize(data):
        data = data or {}
        socket_id = request.sid
        with socket_attach_guard(socket_id):
            binding = socket_bindings.get(socket_id)
            if not binding or binding.get("view_only") or not event_matches_terminal_binding(binding, data):
                return
            workspace_id = binding["workspace_id"]
            terminal_id = binding["terminal_id"]
            if not deps.workspace_exists(workspace_id):
                return
            cols, rows = clamp_terminal_geometry(data.get("cols", 80), data.get("rows", 24))
            with workspace_geometry_lock:
                if not terminal_geometry_resize_allowed(
                    binding["key"],
                    socket_id,
                    claim_geometry=bool(data.get("claim_geometry")),
                ):
                    return
                resize_terminal(workspace_id, cols, rows, terminal_id=terminal_id)

    def on_terminal_resize_workspace(data):
        data = data or {}
        socket_id = request.sid
        with socket_attach_guard(socket_id):
            binding = socket_bindings.get(socket_id)
            if not binding or binding.get("view_only") or not event_matches_terminal_binding(binding, data):
                return
            workspace_id = binding["workspace_id"]
            if not deps.workspace_exists(workspace_id):
                return
            cols, rows = clamp_terminal_geometry(data.get("cols", 80), data.get("rows", 24))
            with workspace_geometry_lock:
                if not workspace_geometry_resize_allowed(
                    workspace_id,
                    socket_id,
                    claim_geometry=bool(data.get("claim_geometry")),
                ):
                    return
                resize_workspace_terminals(workspace_id, cols, rows)

    def on_disconnect():
        socket_id = request.sid
        with socket_attach_guard(socket_id):
            binding = socket_bindings.pop(socket_id, None)
            socket_attach_sequences.pop(socket_id, None)
            release_terminal_geometry_owner(socket_id)
            release_workspace_geometry_owner(socket_id)
            if binding:
                try:
                    leave_room(f"terminal:{binding['key']}")
                except KeyError:
                    pass

    def register_socket_handlers():
        socketio.on_event(TerminalSocketEvent.ATTACH, on_terminal_attach)
        socketio.on_event(TerminalSocketEvent.INPUT, on_terminal_input)
        socketio.on_event(TerminalSocketEvent.SNAPSHOT, on_terminal_snapshot)
        socketio.on_event(TerminalSocketEvent.RESIZE, on_terminal_resize)
        socketio.on_event(TerminalSocketEvent.RESIZE_WORKSPACE, on_terminal_resize_workspace)
        socketio.on_event("disconnect", on_disconnect)

    def start_workspace_terminals():
        runtime_stopping.clear()
        restored = False
        for workspace_id, sessions in deps.workspace_terminal_session_specs().items():
            if not deps.workspace_exists(workspace_id):
                continue
            for session in sessions:
                if session.get("thread_id"):
                    continue  # Stored conversations are only awakened on explicit request.
                terminal_id = normalize_terminal_id(session.get("id") or DEFAULT_TERMINAL)
                info = ensure_terminal(
                    workspace_id,
                    terminal_id=terminal_id,
                    name=session.get("name") or terminal_id,
                    thread_id=session.get("thread_id"),
                )
                restored = bool(info) or restored
        if restored or deps.terminal_sessions_loaded():
            return
        workspace_id = deps.default_workspace_id()
        if workspace_id:
            ensure_terminal(workspace_id, terminal_id=DEFAULT_TERMINAL)

    def workspace_supervisor_loop():
        while not workspace_supervisor_stop.wait(WORKSPACE_AGENT_CHECK_INTERVAL):
            with terminals_lock:
                active_keys = list(terminals)
            if not active_keys:
                if deps.terminal_sessions_loaded():
                    continue
                default_id = deps.default_workspace_id()
                active_keys = [terminal_key(default_id, DEFAULT_TERMINAL)] if default_id else []
            for key in active_keys:
                workspace_id, terminal_id = split_terminal_key(key)
                try:
                    existing = get_terminal_info(workspace_id, terminal_id)
                    if existing and existing.get("managed_command"):
                        continue
                    info = ensure_terminal(workspace_id, terminal_id=terminal_id)
                    if info and not terminal_info_usable(info):
                        close_terminal(
                            workspace_id,
                            terminal_id,
                            manual=False,
                            expected_info=info,
                        )
                        info = ensure_terminal(workspace_id, terminal_id=terminal_id)
                    if info:
                        ensure_workspace_agent(workspace_id)
                except Exception as exc:
                    print(f"terminal supervisor error for {key}: {exc}")

    def start_workspace_supervisor():
        with workspace_supervisor_lock:
            current = workspace_supervisor_thread["value"]
            if current is not None and getattr(current, "is_alive", lambda: False)():
                return current
            workspace_supervisor_stop.clear()
            thread = threading.Thread(target=workspace_supervisor_loop, daemon=True)
            workspace_supervisor_thread["value"] = thread
            thread.start()
            return thread

    def stop_workspace_supervisor():
        runtime_stopping.set()
        with workspace_supervisor_lock:
            thread = workspace_supervisor_thread["value"]
            workspace_supervisor_thread["value"] = None
            workspace_supervisor_stop.set()
        if (
            thread is not None
            and thread is not threading.current_thread()
            and hasattr(thread, "join")
        ):
            thread.join(timeout=max(1.0, WORKSPACE_AGENT_CHECK_INTERVAL + 0.5))

    def cleanup_all_terminals():
        runtime_stopping.set()
        with terminals_lock:
            workspace_ids = {split_terminal_key(key)[0] for key in terminals}
        workspace_ids.update(deps.workspace_ids())
        for workspace_id in sorted(workspace_ids):
            # Wait for any in-flight spawn before taking its cleanup snapshot.
            with workspace_lifecycle_lock(workspace_id):
                with terminals_lock:
                    keys = [key for key in terminals if split_terminal_key(key)[0] == workspace_id]
                for key in keys:
                    _, terminal_id = split_terminal_key(key)
                    close_terminal(workspace_id, terminal_id, manual=False)

    return TerminalRuntime(
        cleanup_all_terminals=cleanup_all_terminals,
        close_workspace_tcp_servers=close_workspace_tcp_servers,
        close_terminal=close_terminal,
        close_workspace_terminals=close_workspace_terminals,
        ensure_workspace_agent=ensure_workspace_agent,
        ensure_terminal=ensure_terminal,
        get_terminal_info=get_terminal_info,
        register_socket_handlers=register_socket_handlers,
        resize_terminal=resize_terminal,
        start_workspace_supervisor=start_workspace_supervisor,
        start_workspace_tcp_servers=start_workspace_tcp_servers,
        start_workspace_terminals=start_workspace_terminals,
        stop_workspace_supervisor=stop_workspace_supervisor,
        terminal_has_codex_process=terminal_has_codex_process,
        terminal_screen_frame=terminal_screen_frame,
        terminal_summary=terminal_summary,
        workspace_terminal_summaries=workspace_terminal_summaries,
        write_terminal=write_terminal,
    )
