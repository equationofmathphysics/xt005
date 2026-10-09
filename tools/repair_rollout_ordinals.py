#!/usr/bin/env python3
"""Repair duplicate resume ordinals in an unindexed suffix of one idle rollout.

Dry-run by default. Unload the target thread before applying. This uses Codex's
writer locks, backs up the rollout and SQLite view, and never rewrites messages
or already indexed bytes. An explicit CLI resume can rebuild its own index afterwards.
This offline maintenance tool is never imported by the terminal runtime.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from uuid import UUID


@contextmanager
def writer_lock(codex_home: Path, thread_id: str):
    directory = codex_home / "thread-writer-locks"
    directory.mkdir(parents=True, exist_ok=True)
    # Keep coordination locked through maintenance so cleanup cannot unlink our
    # lock file and a concurrent writer cannot acquire a different inode.
    with (directory / ".coordination.lock").open("a+b") as coordination:
        fcntl.flock(coordination, fcntl.LOCK_EX)
        with (directory / f"{thread_id}.lock").open("a+b") as writer:
            try:
                fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Target thread has an active writer; unload it first") from error
            yield


def corrected_suffix(data: bytes, checkpoint: tuple[int, int]) -> tuple[bytes, dict]:
    offset, expected = checkpoint
    if offset > len(data) or offset < 0 or (offset and data[offset - 1:offset] != b"\n"):
        raise ValueError("Projection checkpoint is not a complete record boundary")
    if not data.endswith(b"\n"):
        raise ValueError("Rollout has an incomplete trailing record")
    previous = None
    for line in data[:offset].splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ordinal = row.get("ordinal")
        if type(ordinal) is not int or (previous is not None and ordinal != previous + 1):
            raise ValueError("Indexed prefix is not contiguous; refusing suffix-only repair")
        previous = ordinal
    if previous is None or previous + 1 != expected:
        raise ValueError("Indexed prefix does not match the projection checkpoint")
    repaired = []
    shift = 0
    changed = 0
    duplicates = []
    for line_number, line in enumerate(data[offset:].splitlines(keepends=True), 1):
        if not line.strip():
            repaired.append(line)
            continue
        row = json.loads(line)
        old = row.get("ordinal")
        if type(old) is not int:
            raise ValueError("Unindexed record has no integer ordinal")
        if old + shift == expected - 1:
            # Scope the repair to the observed resume boundary failure. Other
            # corruption requires separate investigation, never silent renumbering.
            if row.get("type") != "event_msg" or row.get("payload", {}).get("type") != "thread_settings_applied":
                raise ValueError("Duplicate is not a resume settings event")
            duplicates.append({"suffix_line": line_number, "old_ordinal": old})
            shift += 1
        if old + shift != expected:
            raise ValueError(f"Unexpected ordinal gap: expected {expected}, got {old + shift}")
        if shift:
            # Only replace the envelope ordinal; preserve all original payload
            # bytes, timestamps, whitespace and message identifiers.
            replacement, count = re.subn(
                rb'("ordinal"\s*:\s*)' + str(old).encode() + rb'(?=\s*[,}])',
                lambda match: match[1] + str(expected).encode(), line, count=1,
            )
            expected_row = dict(row, ordinal=expected)
            if count != 1 or json.loads(replacement) != expected_row:
                raise ValueError("Cannot safely replace envelope ordinal")
            line = replacement
            changed += 1
        repaired.append(line)
        expected += 1
    return data[:offset] + b"".join(repaired), {
        "changed_records": changed, "duplicate_boundaries": duplicates,
        "indexed_prefix_bytes": offset, "next_ordinal": expected,
    }


def repair(path: Path, codex_home: Path, backup_dir: Path | None = None) -> dict:
    with path.open("rb") as handle:
        meta = json.loads(handle.readline())
    thread_id = str(UUID(meta["payload"]["id"]))
    if meta.get("type") != "session_meta":
        raise ValueError("Expected session metadata")
    with writer_lock(codex_home, thread_id):
        database = codex_home / "thread_history_1.sqlite"
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            checkpoint = connection.execute(
                "SELECT next_rollout_byte_offset, next_rollout_ordinal "
                "FROM thread_history_projection_state WHERE thread_id = ?", (thread_id,),
            ).fetchone()
            if checkpoint is None:
                raise ValueError("Missing projection checkpoint")
            original = path.read_bytes()
            corrected, report = corrected_suffix(original, checkpoint)
            report.update(thread_id=thread_id, rollout=str(path), applied=False,
                          original_sha256=hashlib.sha256(original).hexdigest(),
                          corrected_sha256=hashlib.sha256(corrected).hexdigest())
            if backup_dir is not None and corrected != original:
                backup_dir.mkdir(parents=True, exist_ok=False)
                os.chmod(backup_dir, 0o700)
                shutil.copy2(path, backup_dir / path.name)
                with sqlite3.connect(backup_dir / database.name) as backup:
                    connection.backup(backup)
                (backup_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                fd, temporary = tempfile.mkstemp(prefix=path.name + ".repair-", dir=path.parent)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(corrected)
                        handle.flush()
                        os.fsync(handle.fileno())
                    shutil.copymode(path, temporary)
                    if path.read_bytes() != original:
                        raise RuntimeError("Rollout changed during repair")
                    os.replace(temporary, path)
                    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                report.update(applied=True, backup_dir=str(backup_dir))
                (backup_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rollout", type=Path)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.apply and not args.backup_dir:
        parser.error("--apply requires a new --backup-dir")
    print(json.dumps(repair(args.rollout.resolve(), args.codex_home.resolve(),
                            args.backup_dir.resolve() if args.apply else None), indent=2))
