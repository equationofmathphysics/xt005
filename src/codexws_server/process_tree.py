import os
import shlex
import signal
import time

from .config import WORKSPACE_AGENT_COMMANDS


def process_state(pid):
    try:
        with open(f"/proc/{int(pid)}/stat", "r", encoding="utf-8") as f:
            stat = f.read()
        return stat.rsplit(") ", 1)[1].split()[0]
    except (OSError, IndexError, TypeError, ValueError):
        return None


def pid_running(pid):
    if not pid:
        return False
    state = process_state(pid)
    if state == "Z":
        return False
    if state:
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def read_ppid(pid):
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def descendant_pids(root_pid):
    try:
        root_pid = int(root_pid)
    except (TypeError, ValueError):
        return []
    children = {}
    try:
        proc_entries = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return []
    for entry in proc_entries:
        pid = int(entry)
        ppid = read_ppid(pid)
        if ppid is not None:
            children.setdefault(ppid, []).append(pid)
    found = []
    stack = [root_pid]
    while stack:
        parent = stack.pop()
        for child in children.get(parent, []):
            found.append(child)
            stack.append(child)
    return found


def process_cmdline(pid):
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            raw = f.read()
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return ""


def process_args(pid):
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            raw = f.read().rstrip(b"\x00")
        if not raw:
            return []
        return [arg.decode("utf-8", errors="replace") for arg in raw.split(b"\x00")]
    except (OSError, ValueError):
        return []


def terminal_process_alive(info):
    proc = info.get("proc")
    if proc and proc.poll() is not None:
        return False
    return pid_running(info.get("pid"))


def terminal_info_usable(info):
    if not info or not info.get("alive") or not terminal_process_alive(info):
        return False
    reader = info.get("reader_thread")
    return not reader or reader.is_alive()


def agent_tokens(workspace_id, command=None):
    if command is None:
        command = WORKSPACE_AGENT_COMMANDS.get(workspace_id)
    if not command:
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def terminal_tree_pids(info):
    pid = info.get("pid")
    if not pid:
        return []
    return [int(pid)] + descendant_pids(pid)


def workspace_agent_processes(workspace_id, info, command=None):
    tokens = agent_tokens(workspace_id, command)
    if not tokens or not info:
        return []
    marker = os.path.basename(tokens[0])
    matches = []
    for pid in terminal_tree_pids(info):
        if not pid_running(pid):
            continue
        cmdline = process_cmdline(pid)
        if marker and marker in cmdline:
            matches.append((pid, cmdline))
    return matches


def workspace_agent_running(workspace_id, info, command=None):
    tokens = agent_tokens(workspace_id, command)
    if not tokens:
        return None
    for _, cmdline in workspace_agent_processes(workspace_id, info, command):
        if all(token in cmdline for token in tokens):
            return True
    return False


def terminate_pids(pids, sig):
    pgids = set()
    for pid in pids:
        try:
            pgid = os.getpgid(int(pid))
            if pgid > 1 and pgid != os.getpgrp():
                pgids.add(pgid)
        except (OSError, TypeError, ValueError):
            pass
    for pgid in pgids:
        try:
            os.killpg(pgid, sig)
        except OSError:
            pass


def terminate_process_tree(root_pid):
    pids = [root_pid] + descendant_pids(root_pid)
    terminate_pids(pids, signal.SIGTERM)
    time.sleep(0.5)
    still_running = [pid for pid in pids if pid_running(pid)]
    if still_running:
        terminate_pids(still_running, signal.SIGKILL)


def terminate_orphaned_session(session_id):
    """Clean remaining jobs after a PTY leader was reaped, without targeting a reused PID."""
    if not session_id or os.path.exists(f"/proc/{int(session_id)}"):
        return
    # Linux reserves a session ID while members remain. Do not infer ownership
    # from a new process which happens to have the old leader's PID.
    members = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            if os.getsid(int(entry)) == int(session_id):
                members.append(int(entry))
        except OSError:
            pass
    if members:
        terminate_pids(members, signal.SIGTERM)
        time.sleep(0.5)
        terminate_pids([pid for pid in members if pid_running(pid)], signal.SIGKILL)
