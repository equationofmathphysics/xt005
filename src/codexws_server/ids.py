import os
import re

from .config import DEFAULT_TERMINAL


def normalize_workspace_id(value):
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    raw = re.sub(r"-{2,}", "-", raw)
    return raw[:48]


def normalize_terminal_id(value):
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    raw = re.sub(r"-{2,}", "-", raw)
    return raw[:48] or DEFAULT_TERMINAL


def terminal_key(workspace_id, terminal_id=DEFAULT_TERMINAL):
    return f"{workspace_id}:{normalize_terminal_id(terminal_id)}"


def split_terminal_key(key):
    if ":" not in key:
        return key, DEFAULT_TERMINAL
    workspace_id, terminal_id = key.split(":", 1)
    return workspace_id, terminal_id or DEFAULT_TERMINAL


def workspace_id_from_path(name, path):
    seed = name or os.path.basename(os.path.normpath(path)) or "workspace"
    return normalize_workspace_id(seed) or "workspace"
