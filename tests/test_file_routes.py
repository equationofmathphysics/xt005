import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask

from codexws_server.routes.files import MAX_FILE_PREVIEW_BYTES, register_file_routes


class WorkspaceFileRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = os.path.join(self.tmp.name, "workspace")
        os.makedirs(self.workspace)

        def safe_workspace_path(workspace_id, rel_path=""):
            if workspace_id != "alpha":
                return None, None
            root = os.path.realpath(self.workspace)
            path = os.path.realpath(os.path.join(root, rel_path or ""))
            if os.path.commonpath([root, path]) != root:
                return root, None
            return root, path

        app = Flask(__name__)
        register_file_routes(app, SimpleNamespace(
            safe_workspace_path=safe_workspace_path,
        ))
        self.app = app
        self.client = app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_delete_routes_are_not_registered(self):
        routes = {rule.rule for rule in self.app.url_map.iter_rules()}
        self.assertNotIn("/api/delete", routes)
        self.assertNotIn("/api/staff-delete", routes)
        self.assertFalse(any("delete" in route for route in routes))

    def test_legacy_file_routes_are_not_registered(self):
        routes = {rule.rule for rule in self.app.url_map.iter_rules()}
        self.assertTrue({
            "/api/files",
            "/api/read",
            "/api/write",
            "/api/staff-files",
            "/api/staff-read",
            "/api/staff-write",
        }.isdisjoint(routes))

    def test_workspace_id_is_the_only_workspace_selector(self):
        missing = self.client.get("/api/workspace-files")
        legacy = self.client.get("/api/workspace-files?staff=alpha")
        selected = self.client.get("/api/workspace-files?workspace_id=alpha")

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(legacy.status_code, 400)
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.get_json()["workspace_id"], "alpha")

    def test_workspace_file_write_and_read(self):
        written = self.client.post(
            "/api/workspace-files/write",
            json={
                "workspace_id": "alpha",
                "path": "notes/today.txt",
                "content": "hello",
            },
        )
        read = self.client.get(
            "/api/workspace-files/read?workspace_id=alpha&path=notes/today.txt"
        )

        self.assertEqual(written.status_code, 200)
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.get_json()["content"], "hello")

    def test_workspace_file_read_rejects_files_over_preview_limit(self):
        path = os.path.join(self.workspace, "large.log")
        with open(path, "wb") as target:
            target.truncate(MAX_FILE_PREVIEW_BYTES + 1)

        response = self.client.get(
            "/api/workspace-files/read?workspace_id=alpha&path=large.log"
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json(), {
            "error": "文件超过 10 MiB，只支持下载",
            "code": "file_too_large",
            "size": MAX_FILE_PREVIEW_BYTES + 1,
            "max_size": MAX_FILE_PREVIEW_BYTES,
        })

    def test_workspace_file_download_supports_range_and_validators(self):
        path = os.path.join(self.workspace, "archive.bin")
        with open(path, "wb") as target:
            target.write(b"0123456789")

        response = self.client.get(
            "/api/workspace-files/download?workspace_id=alpha&path=archive.bin",
            headers={"Range": "bytes=3-6"},
        )

        try:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, b"3456")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.headers["Content-Range"], "bytes 3-6/10")
            self.assertIn("ETag", response.headers)
            self.assertIn("Last-Modified", response.headers)
        finally:
            response.close()

    def test_open_folder_uses_gnome_launcher_and_reaps_it(self):
        process = MagicMock()
        reaper = MagicMock()
        with (
            patch("codexws_server.routes.files.sys.platform", "linux"),
            patch(
                "codexws_server.routes.files.shutil.which",
                side_effect=lambda name: "/usr/bin/gio" if name == "gio" else None,
            ),
            patch(
                "codexws_server.routes.files.subprocess.Popen",
                return_value=process,
            ) as launcher,
            patch(
                "codexws_server.routes.files.threading.Thread",
                return_value=reaper,
            ) as thread_factory,
        ):
            response = self.client.post(
                "/api/workspace-files/open-folder",
                json={"workspace_id": "alpha", "dir": ""},
            )

        self.assertEqual(response.status_code, 200)
        launcher.assert_called_once()
        self.assertEqual(
            launcher.call_args.args[0],
            ["/usr/bin/gio", "open", os.path.realpath(self.workspace)],
        )
        self.assertIs(thread_factory.call_args.kwargs["target"], process.wait)
        self.assertTrue(thread_factory.call_args.kwargs["daemon"])
        reaper.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
