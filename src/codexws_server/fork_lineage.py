"""Durable fork lineage fallback for local conversation history."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import fcntl


STATE_VERSION = 1
MAX_SESSION_META_BYTES = 4 * 1024 * 1024


class ForkLineageStore:
    """Persist child-to-parent thread ids missing from ``thread/list``."""

    def __init__(self, path: str | Path, *, sessions_dir: str | Path | None = None):
        self.path = Path(path).expanduser().resolve()
        self.sessions_dir = (
            Path(sessions_dir).expanduser().resolve() if sessions_dir is not None else None
        )
        self._thread_lock = threading.RLock()
        self._parents = self._read()
        self._merge(self._discover_rollout_lineage(), overwrite=False)

    def parent_for(self, thread_id: object) -> str | None:
        if not isinstance(thread_id, str):
            return None
        with self._thread_lock:
            return self._parents.get(thread_id)

    def record(self, child_id: object, parent_id: object) -> None:
        if not self._valid_pair(child_id, parent_id):
            return
        self._merge({child_id: parent_id}, overwrite=True)

    @staticmethod
    def _valid_pair(child_id: object, parent_id: object) -> bool:
        return (
            isinstance(child_id, str)
            and isinstance(parent_id, str)
            and bool(child_id.strip())
            and bool(parent_id.strip())
            and child_id != parent_id
        )

    def _discover_rollout_lineage(self) -> dict[str, str]:
        if self.sessions_dir is None or not self.sessions_dir.is_dir():
            return {}

        parents: dict[str, str] = {}
        for rollout in self.sessions_dir.rglob("rollout-*.jsonl"):
            try:
                with rollout.open("r", encoding="utf-8") as handle:
                    first_line = handle.readline(MAX_SESSION_META_BYTES + 1)
                if len(first_line.encode("utf-8")) > MAX_SESSION_META_BYTES:
                    continue
                record = json.loads(first_line)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue

            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            child_id = payload.get("id") or payload.get("session_id")
            parent_id = payload.get("forked_from_id") or payload.get("forkedFromId")
            if self._valid_pair(child_id, parent_id):
                parents[child_id] = parent_id
        return parents

    def _read(self) -> dict[str, str]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        raw_parents = payload.get("parents")
        if not isinstance(raw_parents, dict):
            return {}
        return {
            child_id: parent_id
            for child_id, parent_id in raw_parents.items()
            if self._valid_pair(child_id, parent_id)
        }

    @contextmanager
    def _file_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        with lock_path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _merge(self, additions: dict[str, str], *, overwrite: bool) -> None:
        if not additions:
            return
        with self._thread_lock, self._file_lock():
            on_disk = self._read()
            merged = dict(on_disk)
            for child_id, parent_id in self._parents.items():
                merged.setdefault(child_id, parent_id)
            for child_id, parent_id in additions.items():
                if overwrite:
                    merged[child_id] = parent_id
                else:
                    merged.setdefault(child_id, parent_id)
            if merged != on_disk:
                self._write(merged)
            self._parents = merged

    def _write(self, parents: dict[str, str]) -> None:
        file_descriptor, temp_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {"version": STATE_VERSION, "parents": parents},
                    handle,
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
