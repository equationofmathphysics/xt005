import fcntl
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.repair_rollout_ordinals import corrected_suffix, repair


THREAD_ID = "01a079e6-b8e1-7b50-966e-a911c2ceacc3"


def line(ordinal, kind, payload):
    return (json.dumps({"ordinal": ordinal, "type": kind, "payload": payload},
                       ensure_ascii=False) + "\n").encode()


class RepairRolloutOrdinalsTest(unittest.TestCase):
    def setUp(self):
        self.prefix = line(0, "session_meta", {"id": THREAD_ID})
        self.suffix = line(0, "event_msg", {"type": "thread_settings_applied"})
        self.suffix += line(1, "response_item", {"text": '后续回复 "ordinal": 1', "ordinal": 1})

    def test_preserves_messages_and_indexed_bytes_and_is_idempotent(self):
        source = self.prefix + self.suffix
        fixed, report = corrected_suffix(source, (len(self.prefix), 1))
        self.assertEqual(fixed[:len(self.prefix)], self.prefix)
        self.assertEqual(report["changed_records"], 2)
        before = [json.loads(row) for row in source.splitlines()]
        after = [json.loads(row) for row in fixed.splitlines()]
        self.assertEqual([row.pop("ordinal") for row in after], [0, 1, 2])
        for row in before:
            row.pop("ordinal")
        self.assertEqual(before, after)
        self.assertEqual(corrected_suffix(fixed, (len(self.prefix), 1))[0], fixed)

    def test_refuses_gaps_partial_records_and_non_resume_duplicates(self):
        for suffix in [line(3, "event_msg", {}), self.suffix[:-1], line(0, "event_msg", {"type": "task_started"})]:
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                corrected_suffix(self.prefix + suffix, (len(self.prefix), 1))

    def test_backup_dry_run_and_active_writer_exclusion(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            path = home / "rollout.jsonl"
            original = self.prefix + self.suffix
            path.write_bytes(original)
            db = home / "thread_history_1.sqlite"
            with sqlite3.connect(db) as connection:
                connection.execute("CREATE TABLE thread_history_projection_state (thread_id TEXT, next_rollout_byte_offset INT, next_rollout_ordinal INT)")
                connection.execute("INSERT INTO thread_history_projection_state VALUES (?, ?, ?)", (THREAD_ID, len(self.prefix), 1))
            self.assertFalse(repair(path, home)["applied"])
            self.assertEqual(path.read_bytes(), original)
            with (home / "thread-writer-locks" / f"{THREAD_ID}.lock").open("a+b") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, "active writer"):
                    repair(path, home, home / "blocked")
            self.assertFalse((home / "blocked").exists())
            report = repair(path, home, home / "backup")
            self.assertTrue(report["applied"])
            self.assertEqual((home / "backup" / path.name).read_bytes(), original)
            with sqlite3.connect(home / "backup" / db.name) as connection:
                self.assertEqual(connection.execute("SELECT * FROM thread_history_projection_state").fetchone(), (THREAD_ID, len(self.prefix), 1))
            self.assertEqual(json.loads(path.read_bytes().splitlines()[-1])["ordinal"], 2)


if __name__ == "__main__":
    unittest.main()
