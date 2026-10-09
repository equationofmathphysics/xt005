import json
import os
import tempfile
import unittest

from codexws_server.codex_threads import (
    normalize_model_provider,
    parse_codex_rollout,
)


class CodexThreadParsingTests(unittest.TestCase):
    def test_provider_id_preserves_case(self):
        self.assertEqual(normalize_model_provider("OpenAI"), "OpenAI")
        self.assertEqual(normalize_model_provider("openai"), "openai")
        self.assertEqual(normalize_model_provider("custom"), "custom")

    def write_rollout(self, rows):
        handle = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        with handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return handle.name

    def test_fork_keeps_canonical_child_and_parent_identity(self):
        path = self.write_rollout([
            {
                "timestamp": "2026-07-20T10:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "thread-child",
                    "forked_from_id": "thread-parent",
                    "source": "cli",
                    "cwd": "/tmp/project",
                },
            },
            {"type": "event_msg", "payload": {"type": "user_message", "message": "parent content"}},
        ])
        thread = parse_codex_rollout(path)
        self.assertEqual(thread["thread_id"], "thread-child")
        self.assertEqual(thread["forked_from_id"], "thread-parent")
        self.assertEqual(thread["preview"], "parent content")
        self.assertFalse(thread["is_subagent"])

    def test_subagent_is_identified_from_canonical_metadata(self):
        path = self.write_rollout([{
            "type": "session_meta",
            "payload": {
                "id": "thread-agent",
                "forked_from_id": "thread-root",
                "parent_thread_id": "thread-root",
                "source": {"subagent": {"thread_spawn": {"depth": 1}}},
                "cwd": "/tmp/project",
            },
        }])
        self.assertTrue(parse_codex_rollout(path)["is_subagent"])

if __name__ == "__main__":
    unittest.main()
