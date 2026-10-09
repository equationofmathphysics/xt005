import json
import os
import tempfile
import unittest

from flask import Flask

from codexws_server import codex_history_store
from codexws_server.routes.history import register_history_routes
from codexws_server.services import HistoryRouteDeps


class HistoryRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_home = os.environ.get("HOME")
        self.old_codex_home = os.environ.pop("CODEX_HOME", None)
        self.old_history_file = codex_history_store.CODEX_HISTORY_FILE
        os.environ["HOME"] = self.tmp.name
        codex_history_store.CODEX_HISTORY_FILE = os.path.join(self.tmp.name, "history.json")
        self.workspace = os.path.join(self.tmp.name, "workspace")
        os.makedirs(self.workspace, exist_ok=True)

        app = Flask(__name__)
        register_history_routes(app, HistoryRouteDeps(
            workspace_path=lambda workspace_id: self.workspace if workspace_id == "alpha" else None,
            workspace_list=lambda: [{"id": "alpha", "cwd": self.workspace, "exists": True}],
        ))
        self.client = app.test_client()

    def tearDown(self):
        codex_history_store.CODEX_HISTORY_FILE = self.old_history_file
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        if self.old_codex_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.old_codex_home
        self.tmp.cleanup()

    def write_session(
        self,
        session_id,
        cwd,
        message,
        filename=None,
        metadata=None,
        copied_meta=None,
        codex_home=None,
    ):
        session_dir = os.path.join(
            codex_home or os.path.join(self.tmp.name, ".codex"),
            "sessions",
            "2026",
            "07",
            "06",
        )
        os.makedirs(session_dir, exist_ok=True)
        path = os.path.join(session_dir, (filename or session_id) + ".jsonl")
        payload = {"id": session_id, "session_id": session_id, "cwd": cwd, "source": "cli"}
        payload.update(metadata or {})
        rows = [{"type": "session_meta", "payload": payload}]
        if copied_meta:
            rows.append({"type": "session_meta", "payload": copied_meta})
        rows.append({"type": "event_msg", "payload": {"type": "user_message", "message": message}})
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        return path

    def write_session_index(self, rows):
        path = os.path.join(self.tmp.name, ".codex", "session_index.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        return path

    def test_history_listing_uses_native_time_and_archive_filter(self):
        path_a = self.write_session("session-a", self.workspace, "plain task")
        path_b = self.write_session("session-b", self.workspace, "important task")
        path_c = self.write_session("session-c", self.workspace, "archived task")
        os.utime(path_a, (1000, 1000))
        os.utime(path_b, (2000, 2000))
        os.utime(path_c, (3000, 3000))
        codex_history_store.update_history_entry("session-b", {
            "title": "Renamed",
            "important": True,
            "workspace_id": "alpha",
            "terminal_id": "build",
            "last_used_at": 100,
        })
        codex_history_store.update_history_entry("session-c", {"archived": True})

        response = self.client.get("/api/codex-history?workspace_id=alpha")
        self.assertEqual(response.status_code, 200)
        conversations = response.get_json()["conversations"]
        self.assertEqual([item["threadId"] for item in conversations], ["session-b", "session-a"])
        self.assertEqual(conversations[0]["title"], "Renamed")
        self.assertTrue(conversations[0]["important"])
        self.assertNotIn("terminal_id", conversations[0])
        self.assertNotIn("workspace_id", conversations[0])

        response = self.client.get("/api/codex-history?workspace_id=alpha&include_archived=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["threadId"] for item in response.get_json()["conversations"]],
            ["session-c", "session-b", "session-a"],
        )

    def test_history_listing_is_paginated_without_losing_older_conversations(self):
        for index in range(205):
            path = self.write_session(f"session-{index:02d}", self.workspace, f"task {index}")
            timestamp = 1000 + index
            os.utime(path, (timestamp, timestamp))

        response = self.client.get("/api/codex-history?workspace_id=alpha")
        self.assertEqual(response.status_code, 200)
        conversations = response.get_json()["conversations"]
        self.assertEqual(len(conversations), 200)
        self.assertEqual(conversations[0]["threadId"], "session-204")
        self.assertEqual(conversations[-1]["threadId"], "session-05")
        older = self.client.get("/api/codex-history?workspace_id=alpha&offset=200").get_json()
        self.assertEqual(len(older["conversations"]), 5)
        self.assertIsNone(older["nextOffset"])

    def test_history_listing_deduplicates_rollouts_and_uses_native_title(self):
        first = self.write_session("session-a", self.workspace, "first prompt", "rollout-a")
        second = self.write_session("session-a", self.workspace, "first prompt", "rollout-b")
        os.utime(first, (1000, 1000))
        os.utime(second, (2000, 2000))
        self.write_session_index([{
            "id": "session-a",
            "thread_name": "Native Codex title",
            "updated_at": "2026-07-13T10:00:00Z",
        }])

        response = self.client.get("/api/codex-history?workspace_id=alpha")

        self.assertEqual(response.status_code, 200)
        conversations = response.get_json()["conversations"]
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["threadId"], "session-a")
        self.assertEqual(conversations[0]["title"], "Native Codex title")

    def test_history_listing_respects_codex_home(self):
        custom_codex_home = os.path.join(self.tmp.name, "custom-codex-home")
        os.environ["CODEX_HOME"] = custom_codex_home
        self.write_session(
            "session-custom-home",
            self.workspace,
            "custom home task",
            codex_home=custom_codex_home,
        )

        response = self.client.get("/api/codex-history?workspace_id=alpha")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["threadId"] for item in response.get_json()["conversations"]],
            ["session-custom-home"],
        )

    def test_history_listing_requires_exact_workspace_cwd(self):
        child_workspace = os.path.join(self.workspace, "child-project")
        os.makedirs(child_workspace)
        exact = self.write_session("session-exact", self.workspace, "exact workspace")
        nested = self.write_session("session-nested", child_workspace, "nested workspace")
        os.utime(exact, (1000, 1000))
        os.utime(nested, (2000, 2000))

        response = self.client.get("/api/codex-history?workspace_id=alpha")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["threadId"] for item in response.get_json()["conversations"]],
            ["session-exact"],
        )

    def test_history_listing_for_unknown_workspace_is_empty(self):
        self.write_session("session-outside", self.tmp.name, "must not leak")

        response = self.client.get("/api/codex-history?workspace_id=unknown")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"conversations": []})

    def test_thread_location_resolves_registered_workspace(self):
        self.write_session("session-location", self.workspace, "locate me")

        response = self.client.get("/api/codex-history/session-location/location")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["workspaceId"], "alpha")

    def test_thread_location_rejects_unknown_thread_without_fallback(self):
        response = self.client.get("/api/codex-history/session-missing/location")

        self.assertEqual(response.status_code, 404)

    def test_history_metadata_migration_removes_terminal_bindings(self):
        legacy = {
            "version": 1,
            "sessions": {
                "session-a": {
                    "session_id": "session-a",
                    "title": "Keep me",
                    "important": True,
                    "workspace_id": "alpha",
                    "terminal_id": "build-1",
                    "bound_at": 100,
                    "last_used_at": 200,
                },
            },
        }
        with open(codex_history_store.CODEX_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(legacy, f)

        self.assertTrue(codex_history_store.migrate_history_metadata())

        with open(codex_history_store.CODEX_HISTORY_FILE, "r", encoding="utf-8") as f:
            migrated = json.load(f)
        self.assertEqual(migrated["version"], 3)
        entry = migrated["threads"]["session-a"]
        self.assertEqual(entry["thread_id"], "session-a")
        self.assertEqual(entry["title"], "Keep me")
        self.assertTrue(entry["important"])
        self.assertNotIn("workspace_id", entry)
        self.assertNotIn("terminal_id", entry)
        self.assertNotIn("bound_at", entry)
        self.assertNotIn("last_used_at", entry)

    def test_fork_uses_canonical_thread_id_and_exposes_lineage(self):
        parent = self.write_session("thread-parent", self.workspace, "父对话内容")
        child = self.write_session(
            "thread-child",
            self.workspace,
            "父对话内容",
            metadata={"forked_from_id": "thread-parent"},
            copied_meta={
                "id": "thread-parent",
                "session_id": "thread-parent",
                "cwd": self.workspace,
                "source": "cli",
            },
        )
        os.utime(parent, (1000, 1000))
        os.utime(child, (2000, 2000))

        response = self.client.get("/api/codex-history?workspace_id=alpha")

        self.assertEqual(response.status_code, 200)
        conversations = {item["threadId"]: item for item in response.get_json()["conversations"]}
        self.assertEqual(set(conversations), {"thread-parent", "thread-child"})
        fork = conversations["thread-child"]
        self.assertEqual(fork["forkedFromId"], "thread-parent")

    def test_subagents_are_not_listed_as_conversations(self):
        self.write_session("thread-root", self.workspace, "根对话")
        self.write_session(
            "thread-agent",
            self.workspace,
            "内部任务",
            metadata={
                "session_id": "thread-root",
                "forked_from_id": "thread-root",
                "parent_thread_id": "thread-root",
                "source": {"subagent": {"thread_spawn": {"depth": 1}}},
            },
        )

        response = self.client.get("/api/codex-history?workspace_id=alpha")

        self.assertEqual(
            [item["threadId"] for item in response.get_json()["conversations"]],
            ["thread-root"],
        )

    def test_history_binding_endpoint_is_not_registered(self):
        routes = {rule.rule for rule in self.client.application.url_map.iter_rules()}
        self.assertNotIn("/api/codex-history/bind", routes)

    def test_history_request_aliases_are_not_accepted(self):
        self.write_session("thread-archived", self.workspace, "archived")
        codex_history_store.update_history_entry("thread-archived", {"archived": True})

        listed = self.client.get(
            "/api/codex-history?workspace_id=alpha&archived=1"
        )
        updated = self.client.patch(
            "/api/codex-history/thread-archived",
            json={"name": "legacy title"},
        )

        self.assertEqual(listed.get_json()["conversations"], [])
        self.assertEqual(updated.status_code, 400)


if __name__ == "__main__":
    unittest.main()
