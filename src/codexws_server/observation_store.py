import secrets
import time

from .config import DEFAULT_TERMINAL
from .ids import normalize_terminal_id, split_terminal_key, terminal_key
from .observations import hook_status_for_line, strip_terminal_control
from .state import OBSERVATION_EVENT_LIMIT, observations_lock, workspace_observations


def observation_for_key_locked(key):
    workspace_id, terminal_id = split_terminal_key(key)
    return workspace_observations.setdefault(key, {
        "workspace_id": workspace_id,
        "terminal_id": terminal_id,
        "hook_events": [],
        "unread_hooks": 0,
        "last_hook_line": "",
        "last_hook_at": 0,
        "last_output_at": 0,
    })


def terminal_observation_summary(key):
    workspace_id, terminal_id = split_terminal_key(key)
    with observations_lock:
        obs = workspace_observations.get(key) or {}
        events = list(obs.get("hook_events", []))[-10:]
        return {
            "workspace_id": workspace_id,
            "terminal_id": terminal_id,
            "hook_events": events,
            "unread_hooks": int(obs.get("unread_hooks") or 0),
            "last_output_at": obs.get("last_output_at") or 0,
        }


def terminal_observation_summaries():
    with observations_lock:
        keys = list(workspace_observations)
    return {key: terminal_observation_summary(key) for key in keys}


def workspace_observation_summaries():
    grouped = {}
    with observations_lock:
        snapshot = {
            key: {
                "hook_events": list(value.get("hook_events", []))[-10:],
                "unread_hooks": int(value.get("unread_hooks") or 0),
                "last_output_at": value.get("last_output_at") or 0,
            }
            for key, value in workspace_observations.items()
        }
    for key, obs in snapshot.items():
        workspace_id, terminal_id = split_terminal_key(key)
        item = grouped.setdefault(workspace_id, {
            "workspace_id": workspace_id,
            "unread_hooks": 0,
            "terminals": [],
            "recent_hook_events": [],
        })
        item["unread_hooks"] += obs["unread_hooks"]
        terminal_obs = {
            "terminal_id": terminal_id,
            "hook_events": obs["hook_events"],
            "unread_hooks": obs["unread_hooks"],
            "last_output_at": obs["last_output_at"],
        }
        item["terminals"].append(terminal_obs)
        item["recent_hook_events"].extend(obs["hook_events"])
    for item in grouped.values():
        item["terminals"].sort(key=lambda term: (term["terminal_id"] != DEFAULT_TERMINAL, term["terminal_id"]))
        item["recent_hook_events"].sort(key=lambda event: event.get("timestamp", 0), reverse=True)
        item["recent_hook_events"] = item["recent_hook_events"][:10]
    return grouped


def record_hook_event(
    workspace_id,
    terminal_id,
    status="done",
    title="",
    message="",
    source="api",
    session_id="",
    turn_id="",
):
    terminal_id = normalize_terminal_id(terminal_id)
    key = terminal_key(workspace_id, terminal_id)
    now = int(time.time())
    event = {
        "id": f"{now}-{secrets.token_hex(4)}",
        "timestamp": now,
        "workspace_id": workspace_id,
        "terminal_id": terminal_id,
        "status": status or "done",
        "title": title or "hook",
        "message": message or "",
        "source": source,
        "session_id": str(session_id or ""),
        "turn_id": str(turn_id or ""),
    }
    with observations_lock:
        obs = observation_for_key_locked(key)
        obs["hook_events"].append(event)
        obs["hook_events"] = obs["hook_events"][-OBSERVATION_EVENT_LIMIT:]
        if event["status"] in ("done", "failed"):
            obs["unread_hooks"] = int(obs.get("unread_hooks") or 0) + 1
        obs["last_hook_line"] = message or title or "hook"
        obs["last_hook_at"] = now
    return event


def record_terminal_output_observation(term_key, text):
    now = int(time.time())
    clean = strip_terminal_control(text)
    with observations_lock:
        obs = observation_for_key_locked(term_key)
        obs["last_output_at"] = now

        events = []
        for line in clean.splitlines():
            line = line.strip()
            status = hook_status_for_line(line)
            if not status:
                continue
            if obs.get("last_hook_line") == line and now - int(obs.get("last_hook_at") or 0) < 3:
                continue
            workspace_id, terminal_id = split_terminal_key(term_key)
            event = {
                "id": f"{now}-{secrets.token_hex(4)}",
                "timestamp": now,
                "workspace_id": workspace_id,
                "terminal_id": terminal_id,
                "status": status,
                "title": "hook " + ("失败" if status == "failed" else "完成"),
                "message": line,
                "source": "terminal-output",
            }
            events.append(event)
            obs["last_hook_line"] = line
            obs["last_hook_at"] = now
        if events:
            obs["hook_events"].extend(events)
            obs["hook_events"] = obs["hook_events"][-OBSERVATION_EVENT_LIMIT:]
            obs["unread_hooks"] = int(obs.get("unread_hooks") or 0) + len(events)
