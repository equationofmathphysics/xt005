"""Small persistent preferences shared by every workspace and browser."""

import json
import os
import threading

from .config import APP_PREFERENCES_FILE


_preferences_lock = threading.RLock()
_DEFAULTS = {
    "yolo": True,
    "fast": False,
    "hideThinking": False,
    "hideCommands": False,
}


def _load_locked():
    try:
        with open(APP_PREFERENCES_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (FileNotFoundError, OSError, ValueError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    fast_by_thread = raw.get("fastByThread")
    if not isinstance(fast_by_thread, dict):
        fast_by_thread = {}
    return {
        "version": 1,
        "yolo": bool(raw.get("yolo", _DEFAULTS["yolo"])),
        "fast": bool(raw.get("fast", _DEFAULTS["fast"])),
        "hideThinking": bool(raw.get("hideThinking", _DEFAULTS["hideThinking"])),
        "hideCommands": bool(raw.get("hideCommands", _DEFAULTS["hideCommands"])),
        "fastByThread": {
            str(thread_id): bool(enabled)
            for thread_id, enabled in fast_by_thread.items()
            if str(thread_id)
        },
    }


def preferences():
    with _preferences_lock:
        payload = _load_locked()
        result = {key: payload[key] for key in _DEFAULTS}
        result["fastByThread"] = dict(payload["fastByThread"])
        return result


def effective_fast(thread_id=None):
    with _preferences_lock:
        payload = _load_locked()
        if thread_id is not None:
            override = payload["fastByThread"].get(str(thread_id))
            if override is not None:
                return override
        return payload["fast"]


def fast_override(thread_id):
    if thread_id is None:
        return None
    with _preferences_lock:
        return _load_locked()["fastByThread"].get(str(thread_id))


def update_preferences(*, yolo=None, fast=None, hide_thinking=None, hide_commands=None, thread_id=None):
    with _preferences_lock:
        payload = _load_locked()
        if yolo is not None:
            payload["yolo"] = bool(yolo)
        if fast is not None:
            if thread_id is None:
                payload["fast"] = bool(fast)
            else:
                payload["fastByThread"][str(thread_id)] = bool(fast)
        if hide_thinking is not None:
            payload["hideThinking"] = bool(hide_thinking)
        if hide_commands is not None:
            payload["hideCommands"] = bool(hide_commands)
        directory = os.path.dirname(os.path.realpath(APP_PREFERENCES_FILE))
        os.makedirs(directory, exist_ok=True)
        temporary = APP_PREFERENCES_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, APP_PREFERENCES_FILE)
        result = {key: payload[key] for key in _DEFAULTS}
        result["fastByThread"] = dict(payload["fastByThread"])
        return result
