import json
import os
import tempfile
import unittest

from codexws_server import workspace_registry


class WorkspaceRegistryTests(unittest.TestCase):
    def snapshot_registry(self):
        return {
            "workspaces": dict(workspace_registry.WORKSPACES),
            "workspace_meta": dict(workspace_registry.workspace_meta),
            "custom_workspaces": dict(workspace_registry.custom_workspaces),
            "closed_default_workspaces": set(workspace_registry.closed_default_workspaces),
            "workspace_order": list(workspace_registry.workspace_order),
            "workspace_pins": list(workspace_registry.workspace_pins),
            "workspaces_file": workspace_registry.WORKSPACES_FILE,
        }

    def restore_registry(self, snapshot):
        workspace_registry.WORKSPACES.clear()
        workspace_registry.WORKSPACES.update(snapshot["workspaces"])
        workspace_registry.workspace_meta.clear()
        workspace_registry.workspace_meta.update(snapshot["workspace_meta"])
        workspace_registry.custom_workspaces.clear()
        workspace_registry.custom_workspaces.update(snapshot["custom_workspaces"])
        workspace_registry.closed_default_workspaces.clear()
        workspace_registry.closed_default_workspaces.update(snapshot["closed_default_workspaces"])
        workspace_registry.workspace_order[:] = snapshot["workspace_order"]
        workspace_registry.workspace_pins[:] = snapshot["workspace_pins"]
        workspace_registry.WORKSPACES_FILE = snapshot["workspaces_file"]

    def test_safe_workspace_path_stays_inside_workspace(self):
        snapshot = self.snapshot_registry()
        with tempfile.TemporaryDirectory() as root:
            try:
                with workspace_registry.workspace_lock:
                    workspace_registry.WORKSPACES.clear()
                    workspace_registry.WORKSPACES["testws"] = root
                    workspace_registry.workspace_meta.clear()
                    workspace_registry.workspace_meta["testws"] = {
                        "id": "testws", "name": "testws", "path": root, "source": "registered"
                    }
                base, path = workspace_registry.safe_workspace_path("testws", "src/app.py")
                self.assertEqual(base, os.path.realpath(root))
                self.assertEqual(path, os.path.realpath(os.path.join(root, "src/app.py")))
                base, path = workspace_registry.safe_workspace_path("testws", "../escape.py")
                self.assertEqual(base, os.path.realpath(root))
                self.assertIsNone(path)
            finally:
                with workspace_registry.workspace_lock:
                    self.restore_registry(snapshot)

    def test_workspace_order_is_persisted_without_runtime_state(self):
        snapshot = self.snapshot_registry()
        with tempfile.TemporaryDirectory() as root:
            try:
                with workspace_registry.workspace_lock:
                    workspace_registry.WORKSPACES_FILE = os.path.join(root, "workspaces.json")
                    workspace_registry.custom_workspaces.clear()
                    for index, name in enumerate(("alpha", "beta"), 1):
                        path = os.path.join(root, name)
                        os.makedirs(path)
                        workspace_registry.custom_workspaces[name] = {
                            "id": name, "name": name.title(), "path": path, "created": index
                        }
                    workspace_registry.closed_default_workspaces.clear()
                    workspace_registry.workspace_pins.clear()
                    workspace_registry.workspace_order[:] = ["beta", "codexws", "alpha"]
                    workspace_registry.rebuild_workspace_maps_locked()
                    saved = workspace_registry.set_workspace_order_locked(["alpha", "beta"])
                self.assertEqual(saved[:3], ["alpha", "beta", "codexws"])
                with open(workspace_registry.WORKSPACES_FILE, encoding="utf-8") as source:
                    payload = json.load(source)
                self.assertEqual(payload["workspace_order"][:3], ["alpha", "beta", "codexws"])
                self.assertEqual(set(payload), {"workspaces", "workspace_order"})
            finally:
                with workspace_registry.workspace_lock:
                    self.restore_registry(snapshot)

    def test_multiple_workspace_pins_keep_their_queue_order(self):
        snapshot = self.snapshot_registry()
        with tempfile.TemporaryDirectory() as root:
            try:
                with workspace_registry.workspace_lock:
                    workspace_registry.WORKSPACES_FILE = os.path.join(root, "workspaces.json")
                    workspace_registry.custom_workspaces.clear()
                    for index, name in enumerate(("alpha", "beta"), 1):
                        path = os.path.join(root, name)
                        os.makedirs(path)
                        workspace_registry.custom_workspaces[name] = {
                            "id": name, "name": name.title(), "path": path, "created": index
                        }
                    workspace_registry.closed_default_workspaces.clear()
                    workspace_registry.workspace_pins.clear()
                    workspace_registry.workspace_order[:] = ["beta", "codexws", "alpha"]
                    workspace_registry.rebuild_workspace_maps_locked()

                    workspace_registry.set_workspace_pinned_locked("alpha", True)
                    workspace_registry.set_workspace_pinned_locked("beta", True)
                    reordered = workspace_registry.set_workspace_order_locked(
                        ["codexws", "beta", "alpha"]
                    )
                    self.assertEqual(reordered[:3], ["alpha", "beta", "codexws"])

                    pins = workspace_registry.set_workspace_pinned_locked("alpha", False)
                    self.assertEqual(pins, ["beta"])
                    self.assertEqual(workspace_registry.workspace_order[:3], ["beta", "alpha", "codexws"])
                    self.assertTrue(workspace_registry.workspace_item("beta")["pinned"])
                    self.assertFalse(workspace_registry.workspace_item("alpha")["pinned"])

                with open(workspace_registry.WORKSPACES_FILE, encoding="utf-8") as source:
                    payload = json.load(source)
                self.assertEqual(payload["pinned_workspaces"], ["beta"])
            finally:
                with workspace_registry.workspace_lock:
                    self.restore_registry(snapshot)

if __name__ == "__main__":
    unittest.main()
