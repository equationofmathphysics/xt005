import threading
import unittest
from types import SimpleNamespace

from flask import Flask

from codexws_server.routes.workspaces import register_workspace_routes


class WorkspaceRouteTests(unittest.TestCase):
    def setUp(self):
        self.received_orders = []
        self.received_pins = []

        def set_workspace_order(order):
            self.received_orders.append(order)
            return list(order)

        def set_workspace_pinned(workspace_id, pinned):
            self.received_pins.append((workspace_id, pinned))
            return [workspace_id] if pinned else []

        app = Flask(__name__)
        register_workspace_routes(app, SimpleNamespace(
            set_workspace_order_locked=set_workspace_order,
            set_workspace_pinned_locked=set_workspace_pinned,
            request_workspace_id=lambda data: str(data.get("workspace_id") or ""),
            workspace_exists=lambda workspace_id: workspace_id == "alpha",
            workspace_list=lambda: [{"id": "alpha", "pinned": bool(self.received_pins and self.received_pins[-1][1])}],
            workspace_lock=threading.Lock(),
        ))
        self.client = app.test_client()

    def test_reorder_uses_the_documented_order_field(self):
        response = self.client.post(
            "/api/workspaces/reorder",
            json={"order": ["beta", "alpha"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["order"], ["beta", "alpha"])
        self.assertEqual(self.received_orders, [["beta", "alpha"]])

    def test_reorder_rejects_the_removed_workspace_ids_alias(self):
        response = self.client.post(
            "/api/workspaces/reorder",
            json={"workspace_ids": ["beta", "alpha"]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.received_orders, [])

    def test_pin_updates_the_persistent_pin_queue(self):
        response = self.client.post(
            "/api/workspaces/pin",
            json={"workspace_id": "alpha", "pinned": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["pinned_workspaces"], ["alpha"])
        self.assertEqual(response.get_json()["workspaces"][0]["pinned"], True)
        self.assertEqual(self.received_pins, [("alpha", True)])

    def test_pin_rejects_invalid_workspace_or_state(self):
        self.assertEqual(
            self.client.post(
                "/api/workspaces/pin",
                json={"workspace_id": "missing", "pinned": True},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/workspaces/pin",
                json={"workspace_id": "alpha", "pinned": "yes"},
            ).status_code,
            400,
        )
        self.assertEqual(self.received_pins, [])


if __name__ == "__main__":
    unittest.main()
