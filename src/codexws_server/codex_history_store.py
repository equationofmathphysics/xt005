import copy
import json
import os
import threading
import time

from .config import CODEX_HISTORY_FILE


_history_lock = threading.RLock()
_THREAD_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def normalize_thread_id(value):
    raw = str(value or "").strip()
    if not raw or len(raw) > 160:
        return ""
    if any(char not in _THREAD_ID_CHARS for char in raw):
        return ""
    return raw


# Codex historically called thread IDs session IDs in CLI arguments and local APIs.
normalize_session_id = normalize_thread_id


def _clean_title(value):
    if value is None:
        return None
    title = str(value).strip()
    return title[:200] if title else ""


def _bool_value(value):
    return bool(value)


def _empty_payload():
    return {"version": 3, "threads": {}}


def _normalize_entry(thread_id, raw):
    thread_id = normalize_thread_id(thread_id)
    if not thread_id or not isinstance(raw, dict):
        return None
    entry = {"thread_id": thread_id}

    title = _clean_title(raw.get("title") or raw.get("name"))
    if title:
        entry["title"] = title

    for key in ("important", "archived"):
        if key in raw:
            entry[key] = _bool_value(raw.get(key))

    for key in ("updated_at", "created_at"):
        try:
            value = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            entry[key] = value
    return entry


def _load_payload_locked():
    if not os.path.exists(CODEX_HISTORY_FILE):
        return _empty_payload()
    try:
        with open(CODEX_HISTORY_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, TypeError):
        return _empty_payload()
    if not isinstance(raw, dict):
        return _empty_payload()

    threads = {}
    raw_threads = raw.get("threads") or raw.get("sessions") or {}
    for thread_id, entry in raw_threads.items():
        normalized = _normalize_entry(thread_id, entry)
        if normalized:
            threads[normalized["thread_id"]] = normalized
    return {"version": 3, "threads": threads}


def _save_payload_locked(payload):
    directory = os.path.dirname(CODEX_HISTORY_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = CODEX_HISTORY_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, CODEX_HISTORY_FILE)


def history_entries():
    with _history_lock:
        return copy.deepcopy(_load_payload_locked()["threads"])


def migrate_history_metadata():
    """Move legacy session metadata to thread IDs without losing user labels."""
    if not os.path.exists(CODEX_HISTORY_FILE):
        return False
    with _history_lock:
        try:
            with open(CODEX_HISTORY_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError, TypeError):
            return False
        if not isinstance(raw, dict):
            return False
        normalized = _load_payload_locked()
        if raw == normalized:
            return False
        _save_payload_locked(normalized)
        return True


def history_entry(thread_id):
    thread_id = normalize_thread_id(thread_id)
    if not thread_id:
        return None
    with _history_lock:
        entry = _load_payload_locked()["threads"].get(thread_id)
        return copy.deepcopy(entry) if entry else None


def update_history_entry(thread_id, updates):
    thread_id = normalize_thread_id(thread_id)
    if not thread_id:
        return None
    updates = updates or {}
    now = int(time.time())
    with _history_lock:
        payload = _load_payload_locked()
        threads = payload["threads"]
        entry = dict(threads.get(thread_id) or {"thread_id": thread_id, "created_at": now})

        if "title" in updates or "name" in updates:
            title = _clean_title(updates.get("title", updates.get("name")))
            if title:
                entry["title"] = title
            else:
                entry.pop("title", None)

        for key in ("important", "archived"):
            if key in updates:
                entry[key] = _bool_value(updates.get(key))

        entry["updated_at"] = now
        threads[thread_id] = _normalize_entry(thread_id, entry) or entry
        _save_payload_locked(payload)
        return copy.deepcopy(threads[thread_id])
