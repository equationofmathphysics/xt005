"""Persistent workspace registry."""

import json
import os
import time

from .config import DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACES, WORKSPACES, WORKSPACES_FILE
from .ids import normalize_workspace_id, normalize_terminal_id
from .config import DEFAULT_TERMINAL, WORKSPACE_AGENT_COMMANDS
from .codex_history_store import normalize_thread_id
from .state import workspace_terminal_sessions, workspace_terminal_sessions_loaded
from .state import (
    closed_default_workspaces,
    custom_workspaces,
    workspace_lock,
    workspace_meta,
    workspace_order,
    workspace_pins,
)


def expand_workspace_path(path):
    return os.path.realpath(os.path.expanduser(os.path.expandvars(str(path or "").strip())))


def workspace_exists(workspace_id):
    with workspace_lock:
        return workspace_id in WORKSPACES


def workspace_ids():
    with workspace_lock:
        return list(WORKSPACES)


def _append_unique(items, value):
    if value and value not in items:
        items.append(value)


def normalize_workspace_order(raw_order):
    order = []
    if not isinstance(raw_order, list):
        return order
    for item in raw_order:
        _append_unique(order, normalize_workspace_id(item))
    return order


def rebuild_workspace_maps_locked():
    WORKSPACES.clear()
    workspace_meta.clear()
    available = {}
    fallback_order = []
    for workspace_id, path in DEFAULT_WORKSPACES.items():
        if workspace_id in closed_default_workspaces:
            continue
        fallback_order.append(workspace_id)
        available[workspace_id] = {
            "id": workspace_id,
            "name": workspace_id,
            "path": path,
            "source": "default",
        }
    for workspace_id, entry in custom_workspaces.items():
        fallback_order.append(workspace_id)
        available[workspace_id] = dict(entry, source="registered")

    pinned_ids = []
    for workspace_id in workspace_pins:
        if workspace_id in available:
            _append_unique(pinned_ids, workspace_id)
    workspace_pins[:] = pinned_ids

    ordered_ids = list(pinned_ids)
    for workspace_id in workspace_order:
        if workspace_id in available:
            _append_unique(ordered_ids, workspace_id)
    for workspace_id in fallback_order:
        _append_unique(ordered_ids, workspace_id)
    workspace_order[:] = ordered_ids
    for workspace_id in ordered_ids:
        entry = available[workspace_id]
        WORKSPACES[workspace_id] = entry["path"]
        workspace_meta[workspace_id] = entry


def load_workspaces():
    loaded = {}
    closed_defaults = set()
    loaded_order = []
    loaded_pins = []
    loaded_sessions = {}
    sessions_loaded = False
    try:
        with open(WORKSPACES_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        raw = []
    if isinstance(raw, dict):
        closed_raw = raw.get("closed_default_workspaces", raw.get("hidden_defaults", []))
        if isinstance(closed_raw, list):
            for workspace_id in closed_raw:
                normalized = normalize_workspace_id(workspace_id)
                if normalized in DEFAULT_WORKSPACES:
                    closed_defaults.add(normalized)
        loaded_order = normalize_workspace_order(raw.get("workspace_order") or raw.get("order"))
        loaded_pins = normalize_workspace_order(raw.get("pinned_workspaces") or raw.get("pins"))
        loaded_sessions = normalize_terminal_sessions(raw.get("terminal_sessions", {}))
        sessions_loaded = "terminal_sessions" in raw
        raw = raw.get("workspaces", [])
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            workspace_id = normalize_workspace_id(entry.get("id"))
            path = expand_workspace_path(entry.get("path"))
            if not workspace_id or not path or workspace_id in DEFAULT_WORKSPACES:
                continue
            loaded[workspace_id] = {
                "id": workspace_id,
                "name": str(entry.get("name") or workspace_id).strip() or workspace_id,
                "path": path,
                "created": int(entry.get("created") or 0),
            }
    with workspace_lock:
        custom_workspaces.clear()
        custom_workspaces.update(loaded)
        closed_default_workspaces.clear()
        closed_default_workspaces.update(closed_defaults)
        workspace_order[:] = loaded_order
        workspace_pins[:] = loaded_pins
        workspace_terminal_sessions.clear()
        workspace_terminal_sessions.update(loaded_sessions)
        workspace_terminal_sessions_loaded["value"] = sessions_loaded
        rebuild_workspace_maps_locked()


def save_workspaces_locked():
    ordered_custom_ids = [item for item in workspace_order if item in custom_workspaces]
    for workspace_id in sorted(custom_workspaces):
        _append_unique(ordered_custom_ids, workspace_id)
    items = []
    for workspace_id in ordered_custom_ids:
        entry = custom_workspaces[workspace_id]
        items.append(
            {
                "id": workspace_id,
                "name": entry.get("name") or workspace_id,
                "path": entry.get("path"),
                "created": entry.get("created") or int(time.time()),
            }
        )
    payload = {"workspaces": items, "workspace_order": list(workspace_order)}
    if workspace_terminal_sessions_loaded["value"]:
        payload["terminal_sessions"] = {key: value for key, value in workspace_terminal_sessions.items() if key in WORKSPACES}
    if workspace_pins:
        payload["pinned_workspaces"] = list(workspace_pins)
    hidden = sorted(item for item in closed_default_workspaces if item in DEFAULT_WORKSPACES)
    if hidden:
        payload["closed_default_workspaces"] = hidden
    os.makedirs(os.path.dirname(os.path.realpath(WORKSPACES_FILE)), exist_ok=True)
    temporary = WORKSPACES_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, WORKSPACES_FILE)


def set_workspace_order_locked(order):
    requested = normalize_workspace_order(order)
    next_order = []
    for workspace_id in requested:
        if workspace_id in WORKSPACES:
            _append_unique(next_order, workspace_id)
    for workspace_id in WORKSPACES:
        _append_unique(next_order, workspace_id)
    workspace_order[:] = next_order
    rebuild_workspace_maps_locked()
    save_workspaces_locked()
    return list(workspace_order)


def set_workspace_pinned_locked(workspace_id, pinned):
    if pinned:
        _append_unique(workspace_pins, workspace_id)
    else:
        workspace_pins[:] = [item for item in workspace_pins if item != workspace_id]
    rebuild_workspace_maps_locked()
    save_workspaces_locked()
    return list(workspace_pins)


def default_workspace_id_for_path(path):
    real_path = expand_workspace_path(path)
    for workspace_id, default_path in DEFAULT_WORKSPACES.items():
        if expand_workspace_path(default_path) == real_path:
            return workspace_id
    return ""


def is_default_workspace_id(workspace_id):
    return workspace_id in DEFAULT_WORKSPACES


def restore_default_workspace_locked(workspace_id):
    if workspace_id not in DEFAULT_WORKSPACES or workspace_id not in closed_default_workspaces:
        return False
    closed_default_workspaces.discard(workspace_id)
    rebuild_workspace_maps_locked()
    save_workspaces_locked()
    return True


def hide_default_workspace_locked(workspace_id):
    if workspace_id not in DEFAULT_WORKSPACES:
        return False
    closed_default_workspaces.add(workspace_id)
    rebuild_workspace_maps_locked()
    save_workspaces_locked()
    return True


def workspace_item(workspace_id):
    with workspace_lock:
        path = WORKSPACES.get(workspace_id)
        meta = dict(workspace_meta.get(workspace_id) or {})
        pinned = workspace_id in workspace_pins
    if not path:
        return None
    real_path = expand_workspace_path(path)
    return {
        "id": workspace_id,
        "name": meta.get("name") or workspace_id,
        "cwd": real_path,
        "exists": os.path.isdir(real_path),
        "source": meta.get("source") or "registered",
        "pinned": pinned,
        "agent_command": WORKSPACE_AGENT_COMMANDS.get(workspace_id),
    }


def build_workspace_list(workspace_terminal_summaries=lambda: {}, workspace_observation_summaries=lambda: {}):
    terminals_by_workspace = workspace_terminal_summaries()
    observations_by_workspace = workspace_observation_summaries()
    items = []
    for item in (workspace_item(workspace_id) for workspace_id in workspace_ids()):
        if not item:
            continue
        workspace_terminals = terminals_by_workspace.get(item["id"], [])
        item["observation"] = observations_by_workspace.get(item["id"], {
            "workspace_id": item["id"],
            "unread_hooks": 0,
            "terminals": [],
            "recent_hook_events": [],
        })
        item["terminals"] = workspace_terminals
        item["terminal"] = (
            next((term for term in workspace_terminals if term["id"] == DEFAULT_TERMINAL), None)
            or (workspace_terminals[0] if workspace_terminals else {
                "id": DEFAULT_TERMINAL,
                "name": DEFAULT_TERMINAL,
                "workspace_id": item["id"],
                "pid": None,
                "alive": False,
                "usable": False,
                "buffer_size": 0,
                "cwd": item.get("cwd"),
                "reader_alive": False,
            })
        )
        items.append(item)
    return items


def default_workspace_id():
    ids = workspace_ids()
    if DEFAULT_WORKSPACE_ID in ids:
        return DEFAULT_WORKSPACE_ID
    return ids[0] if ids else ""


def request_workspace_id(args_or_data):
    return str(args_or_data.get("workspace_id") or "").strip()


def workspace_path(workspace_id):
    with workspace_lock:
        path = WORKSPACES.get(workspace_id)
        meta = dict(workspace_meta.get(workspace_id) or {})
    if not path:
        return None
    real_path = expand_workspace_path(path)
    if not os.path.isdir(real_path):
        if meta.get("source") == "default":
            os.makedirs(real_path, exist_ok=True)
        else:
            return None
    return real_path


def safe_workspace_path(workspace_id, rel_path=""):
    root = workspace_path(workspace_id)
    if not root:
        return None, None
    abs_path = os.path.realpath(os.path.join(root, rel_path or ""))
    if os.path.commonpath([root, abs_path]) != root:
        return root, None
    return root, abs_path

def normalize_terminal_sessions(raw_sessions):
    sessions = {}
    if not isinstance(raw_sessions, dict):
        return sessions
    for raw_workspace_id, raw_items in raw_sessions.items():
        workspace_id = normalize_workspace_id(raw_workspace_id)
        if not workspace_id or not isinstance(raw_items, list):
            continue
        entries = []
        seen = set()
        for raw_item in raw_items:
            thread_id = ""
            if isinstance(raw_item, dict):
                terminal_id = normalize_terminal_id(raw_item.get("id") or raw_item.get("terminal_id"))
                name = str(raw_item.get("name") or terminal_id).strip() or terminal_id
                thread_id = normalize_thread_id(raw_item.get("thread_id"))
            else:
                terminal_id = normalize_terminal_id(raw_item)
                name = terminal_id
            if terminal_id in seen:
                continue
            seen.add(terminal_id)
            entry = {"id": terminal_id, "name": name}
            if thread_id:
                entry["thread_id"] = thread_id
            entries.append(entry)
        sessions[workspace_id] = entries
    return sessions


def prune_terminal_sessions_locked():
    for workspace_id in list(workspace_terminal_sessions):
        if workspace_id not in WORKSPACES:
            workspace_terminal_sessions.pop(workspace_id, None)
            continue
        normalized = normalize_terminal_sessions({workspace_id: workspace_terminal_sessions.get(workspace_id, [])})
        workspace_terminal_sessions[workspace_id] = normalized.get(workspace_id, [])



def terminal_sessions_loaded():
    return bool(workspace_terminal_sessions_loaded.get("value"))


def workspace_terminal_session_specs():
    with workspace_lock:
        return {
            workspace_id: [dict(entry) for entry in workspace_terminal_sessions.get(workspace_id, [])]
            for workspace_id in workspace_order
            if workspace_id in WORKSPACES
        }


def record_terminal_session(workspace_id, terminal_id=DEFAULT_TERMINAL, name=None):
    workspace_id = normalize_workspace_id(workspace_id)
    terminal_id = normalize_terminal_id(terminal_id)
    if not workspace_id or not terminal_id:
        return False
    terminal_name = str(name or terminal_id).strip() or terminal_id
    with workspace_lock:
        if workspace_id not in WORKSPACES:
            return False
        workspace_terminal_sessions_loaded["value"] = True
        sessions = workspace_terminal_sessions.setdefault(workspace_id, [])
        for entry in sessions:
            if entry.get("id") == terminal_id:
                changed = False
                if entry.get("name") != terminal_name:
                    entry["name"] = terminal_name
                    changed = True
                if changed:
                    save_workspaces_locked()
                return changed
        entry = {"id": terminal_id, "name": terminal_name}
        sessions.append(entry)
        save_workspaces_locked()
        return True


def rename_terminal_session(workspace_id, terminal_id, name):
    workspace_id = normalize_workspace_id(workspace_id)
    terminal_id = normalize_terminal_id(terminal_id)
    terminal_name = str(name or "").strip()
    if not workspace_id or not terminal_id or not terminal_name or len(terminal_name) > 80:
        return False
    with workspace_lock:
        if workspace_id not in WORKSPACES:
            return False
        for entry in workspace_terminal_sessions.get(workspace_id, []):
            if entry.get("id") != terminal_id:
                continue
            if entry.get("name") != terminal_name:
                entry["name"] = terminal_name
                save_workspaces_locked()
            return True
    return False


def record_terminal_thread(workspace_id, terminal_id, thread_id):
    workspace_id = normalize_workspace_id(workspace_id)
    terminal_id = normalize_terminal_id(terminal_id)
    thread_id = normalize_thread_id(thread_id)
    if not workspace_id or not terminal_id or not thread_id:
        return False
    with workspace_lock:
        if workspace_id not in WORKSPACES:
            return False
        workspace_terminal_sessions_loaded["value"] = True
        sessions = workspace_terminal_sessions.setdefault(workspace_id, [])
        for entry in sessions:
            if entry.get("id") != terminal_id:
                continue
            if entry.get("thread_id") == thread_id:
                return False
            entry["thread_id"] = thread_id
            save_workspaces_locked()
            return True
        sessions.append({"id": terminal_id, "name": terminal_id, "thread_id": thread_id})
        save_workspaces_locked()
        return True


def forget_terminal_session(workspace_id, terminal_id=DEFAULT_TERMINAL):
    workspace_id = normalize_workspace_id(workspace_id)
    terminal_id = normalize_terminal_id(terminal_id)
    with workspace_lock:
        sessions = workspace_terminal_sessions.get(workspace_id)
        if not sessions:
            workspace_terminal_sessions_loaded["value"] = True
            save_workspaces_locked()
            return False
        next_sessions = [entry for entry in sessions if entry.get("id") != terminal_id]
        if len(next_sessions) == len(sessions):
            workspace_terminal_sessions_loaded["value"] = True
            save_workspaces_locked()
            return False
        if next_sessions:
            workspace_terminal_sessions[workspace_id] = next_sessions
        else:
            workspace_terminal_sessions.pop(workspace_id, None)
        save_workspaces_locked()
        return True


def forget_workspace_terminal_sessions_locked(workspace_id):
    workspace_id = normalize_workspace_id(workspace_id)
    removed = workspace_terminal_sessions.pop(workspace_id, None) is not None
    workspace_terminal_sessions_loaded["value"] = True
    return removed
