import uuid
import re
import glob
import json
import os
import sqlite3
from datetime import datetime

from .codex_history_store import normalize_thread_id, history_entry


def codex_home_path(codex_home=None):
    configured = codex_home or os.environ.get("CODEX_HOME") or "~/.codex"
    return os.path.realpath(os.path.expandvars(os.path.expanduser(str(configured))))


def codex_timestamp(value):
    raw = str(value or "").strip()
    if not raw:
        return 0
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0


def _source_kind(source):
    if isinstance(source, str):
        return source.strip().lower()
    if isinstance(source, dict) and "subagent" in source:
        return "subagent"
    return ""


def normalize_model_provider(value):
    return str(value or "").strip()


def parse_codex_rollout(filepath):
    """Read a rollout using only its first, canonical SessionMeta identity."""
    canonical = None
    preview = None
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                payload = entry.get("payload") or {}
                if canonical is None and entry.get("type") == "session_meta":
                    thread_id = normalize_thread_id(payload.get("id"))
                    if not thread_id:
                        return None
                    parent_thread_id = normalize_thread_id(payload.get("parent_thread_id"))
                    source_kind = _source_kind(payload.get("source"))
                    canonical = {
                        "thread_id": thread_id,
                        "session_id": normalize_thread_id(payload.get("session_id")) or thread_id,
                        "forked_from_id": normalize_thread_id(payload.get("forked_from_id")),
                        "is_subagent": bool(parent_thread_id or source_kind == "subagent"),
                        "cwd": str(payload.get("cwd") or ""),
                        "source": source_kind,
                        "model_provider": normalize_model_provider(payload.get("model_provider")),
                        "cli_version": str(payload.get("cli_version") or ""),
                        "created_at": codex_timestamp(entry.get("timestamp")),
                        "path": filepath,
                    }
                    continue
                if (
                    canonical is not None
                    and preview is None
                    and entry.get("type") == "event_msg"
                    and payload.get("type") == "user_message"
                ):
                    message = str(payload.get("message") or "").strip()
                    if message:
                        preview = message[:80] + ("..." if len(message) > 80 else "")
                        break
    except OSError:
        return None

    if canonical is None:
        return None
    canonical["preview"] = preview or "Untitled"
    try:
        canonical["timestamp"] = int(os.path.getmtime(filepath))
    except OSError:
        canonical["timestamp"] = canonical["created_at"]
    return canonical


def find_codex_thread(thread_id, codex_home=None):
    """Locate one canonical rollout without trusting its filename alone."""
    thread_id = normalize_thread_id(thread_id)
    if not thread_id:
        return None
    sessions_dir = os.path.join(codex_home_path(codex_home), "sessions")
    if not os.path.isdir(sessions_dir):
        return None

    checked = set()
    candidates = glob.glob(
        os.path.join(sessions_dir, "**", f"*{thread_id}*.jsonl"),
        recursive=True,
    )
    for filepath in candidates:
        checked.add(filepath)
        thread = parse_codex_rollout(filepath)
        if thread and thread.get("thread_id") == thread_id:
            return thread

    # Imported or renamed rollouts may not contain the canonical id in the filename.
    for root, _, files in os.walk(sessions_dir):
        for filename in files:
            if not filename.endswith(".jsonl"):
                continue
            filepath = os.path.join(root, filename)
            if filepath in checked:
                continue
            thread = parse_codex_rollout(filepath)
            if thread and thread.get("thread_id") == thread_id:
                return thread
    return None


def _merge_native_thread(entries, thread_id, title, timestamp=0, named=False):
    thread_id = normalize_thread_id(thread_id)
    title = str(title or "").strip()
    if not thread_id:
        return
    try:
        timestamp = int(timestamp or 0)
    except (TypeError, ValueError):
        timestamp = 0
    previous = entries.get(thread_id) or {}
    previous_named = bool(previous.get("named"))
    previous_timestamp = int(previous.get("timestamp") or 0)
    if previous and (previous_named and not named):
        previous["timestamp"] = max(previous_timestamp, timestamp)
        return
    if previous and previous_named == named and timestamp < previous_timestamp:
        return
    entries[thread_id] = {
        "title": title or str(previous.get("title") or ""),
        "timestamp": max(previous_timestamp, timestamp),
        "named": bool(named),
    }

def _load_session_index(codex_home, entries):
    filepath = os.path.join(codex_home, "session_index.jsonl")
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    raw = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                _merge_native_thread(
                    entries,
                    raw.get("id") or raw.get("session_id"),
                    raw.get("thread_name") or raw.get("title"),
                    codex_timestamp(raw.get("updated_at")),
                    named=True,
                )
    except OSError:
        pass


def _sqlite_thread_columns(connection):
    try:
        return {str(row[1]) for row in connection.execute("PRAGMA table_info(threads)")}
    except sqlite3.Error:
        return set()


def _load_sqlite_thread_index(filepath, entries):
    try:
        connection = sqlite3.connect(f"file:{filepath}?mode=ro", uri=True, timeout=0.2)
    except sqlite3.Error:
        return
    try:
        columns = _sqlite_thread_columns(connection)
        if "id" not in columns:
            return
        title_columns = [column for column in ("name", "title", "preview", "first_user_message") if column in columns]
        if not title_columns:
            return
        timestamp_column = (
            "updated_at_ms" if "updated_at_ms" in columns
            else "updated_at" if "updated_at" in columns
            else None
        )
        selected = ["id", *title_columns]
        if timestamp_column:
            selected.append(timestamp_column)
        query = "SELECT " + ", ".join(selected) + " FROM threads"
        for row in connection.execute(query):
            values = dict(zip(selected, row))
            name = str(values.get("name") or "").strip()
            title = name or next(
                (str(values.get(column) or "").strip() for column in title_columns if column != "name" and values.get(column)),
                "",
            )
            timestamp = values.get(timestamp_column) if timestamp_column else 0
            if timestamp_column == "updated_at_ms":
                try:
                    timestamp = int(timestamp or 0) // 1000
                except (TypeError, ValueError):
                    timestamp = 0
            _merge_native_thread(
                entries,
                values.get("id"),
                title,
                timestamp,
                named=bool(name),
            )
    except sqlite3.Error:
        pass
    finally:
        connection.close()


def load_codex_native_index(codex_home=None):
    """Return Codex-native titles keyed by canonical thread ID."""
    codex_home = codex_home_path(codex_home)
    entries = {}
    _load_session_index(codex_home, entries)
    state_files = glob.glob(os.path.join(codex_home, "state*.sqlite"))
    try:
        state_files.sort(key=os.path.getmtime)
    except OSError:
        state_files.sort()
    for filepath in state_files:
        _load_sqlite_thread_index(filepath, entries)
    return {
        thread_id: {
            "title": entry.get("title") or "",
            "timestamp": int(entry.get("timestamp") or 0),
        }
        for thread_id, entry in entries.items()
    }

def canonical_thread_id_from_title(value):
    """Accept an OSC title only when it is exactly a UUID thread identity."""
    thread_id = normalize_thread_id(value)
    if not thread_id:
        return ""
    try:
        parsed = uuid.UUID(thread_id)
    except (ValueError, AttributeError):
        return ""
    return thread_id if str(parsed) == thread_id.lower() else ""


def _command_thread_ids(args):
    ids = []
    for index, arg in enumerate(args or ()):
        # `fork <id>` names the parent; the new thread ID only exists in its rollout.
        if str(arg).lower() != "resume":
            continue
        for candidate in args[index + 1:]:
            candidate = canonical_thread_id_from_title(candidate)
            if candidate:
                ids.append(candidate)
                break
    return ids


def process_rollout_paths(pid):
    paths = []
    fd_dir = f"/proc/{int(pid)}/fd"
    try:
        entries = os.listdir(fd_dir)
    except (OSError, TypeError, ValueError):
        return paths
    for entry in entries:
        try:
            path = os.readlink(os.path.join(fd_dir, entry))
        except OSError:
            continue
        normalized_path = os.path.normpath(path)
        parts = normalized_path.split(os.sep)
        filename = os.path.basename(normalized_path)
        if (
            "sessions" not in parts[:-1]
            or not filename.startswith("rollout-")
            or not filename.endswith(".jsonl")
        ):
            continue
        if normalized_path not in paths:
            paths.append(normalized_path)
    return paths


def codex_thread_from_processes(processes, preferred_thread_id="", terminal_title=""):
    """Find the root thread opened by foreground Codex processes."""
    descriptors = {}
    command_ids = []
    for pid, args in processes or ():
        command_ids.extend(_command_thread_ids(args))
        for filepath in process_rollout_paths(pid):
            descriptor = parse_codex_rollout(filepath)
            if descriptor and not descriptor.get("is_subagent"):
                descriptors[descriptor["thread_id"]] = descriptor

    preferred = normalize_thread_id(preferred_thread_id)
    title_thread_id = canonical_thread_id_from_title(terminal_title)
    selected_id = title_thread_id if title_thread_id in descriptors else ""
    if not selected_id and len(descriptors) == 1:
        selected_id = next(iter(descriptors))
    if not selected_id and preferred in descriptors:
        selected_id = preferred
    if not selected_id:
        selected_id = next(iter(command_ids), "")
    if not selected_id:
        return None

    descriptor = dict(descriptors.get(selected_id) or {"thread_id": selected_id})
    descriptor["title"] = codex_thread_title(selected_id, descriptor.get("preview") or "")
    return descriptor

def codex_thread_title(thread_id, fallback="", native_index=None):
    """Resolve the display title without changing Codex or terminal state."""
    thread_id = normalize_thread_id(thread_id)
    if not thread_id:
        return ""
    metadata = history_entry(thread_id) or {}
    custom = str(metadata.get("title") or "").strip()
    if custom:
        return custom
    native_index = native_index if native_index is not None else load_codex_native_index()
    native = str((native_index.get(thread_id) or {}).get("title") or "").strip()
    return native or str(fallback or "").strip()
