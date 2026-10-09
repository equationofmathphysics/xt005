#!/usr/bin/env python3
"""One-time migration of rollout cwd values after moving projects under ~/A_disk."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


def migrated_cwd(cwd: object, home: Path, disk_root: Path) -> str | None:
    value = str(cwd or "")
    try:
        relative = Path(value).relative_to(home)
    except ValueError:
        return None
    if not relative.parts or relative.parts[0] == disk_root.name:
        return None
    target = disk_root / relative
    return str(target) if target.exists() and not Path(value).exists() else None


def migrate_file(
    path: Path,
    sessions_dir: Path,
    home: Path,
    disk_root: Path,
    backup_root: Path | None,
) -> tuple[bool, str, str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if row.get("type") != "session_meta" or not isinstance(row.get("payload"), dict):
            continue
        old = str(row["payload"].get("cwd") or "")
        new = migrated_cwd(old, home, disk_root) or old
        if new == old:
            return False, old, old
        row["payload"]["cwd"] = new
        newline = "\n" if line.endswith("\n") else ""
        lines[index] = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + newline
        if backup_root is not None:
            relative = path.relative_to(sessions_dir)
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                shutil.copy2(path, backup)
            fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.writelines(lines)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return True, old, new
    return False, "", ""


def migrate_state_db(
    state_db: Path,
    home: Path,
    disk_root: Path,
    backup_root: Path | None,
) -> int:
    if not state_db.is_file():
        return 0
    connection = sqlite3.connect(state_db)
    try:
        rows = connection.execute("SELECT id, cwd FROM threads").fetchall()
        changes = []
        for thread_id, old in rows:
            new = migrated_cwd(old, home, disk_root)
            if new is not None:
                changes.append((new, thread_id, old))
        for new, thread_id, old in changes:
            print(f"index: cwd={old} -> {new}  [{thread_id}]")
        if backup_root is not None and changes:
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_path = backup_root / state_db.name
            if not backup_path.exists():
                backup_connection = sqlite3.connect(backup_path)
                try:
                    connection.backup(backup_connection)
                finally:
                    backup_connection.close()
            with connection:
                connection.executemany(
                    "UPDATE threads SET cwd = ? WHERE id = ? AND cwd = ?",
                    changes,
                )
        return len(changes)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-dir", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--disk-root", type=Path, default=Path.home() / "A_disk")
    parser.add_argument("--state-db", type=Path, default=Path.home() / ".codex" / "state_5.sqlite")
    parser.add_argument("--apply", action="store_true", help="write changes; default is a dry run")
    parser.add_argument("--backup-dir", type=Path, help="backup destination used with --apply")
    options = parser.parse_args()
    sessions_dir = options.sessions_dir.resolve()
    backup_root = options.backup_dir.resolve() if options.backup_dir else None
    if options.apply and backup_root is None:
        parser.error("--apply requires --backup-dir")

    home = Path.home().resolve()
    disk_root = options.disk_root.resolve()
    changed = 0
    for path in sessions_dir.rglob("*.jsonl"):
        did_change, old, new = migrate_file(
            path,
            sessions_dir,
            home,
            disk_root,
            backup_root if options.apply else None,
        )
        if did_change:
            changed += 1
            print(f"{old} -> {new}  [{path}]")
    indexed = migrate_state_db(
        options.state_db.resolve(),
        home,
        disk_root,
        backup_root if options.apply else None,
    )
    verb = "Migrated" if options.apply else "Would migrate"
    print(f"{verb} {changed} rollout(s) and {indexed} indexed thread(s).")


if __name__ == "__main__":
    main()
