import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from codexws_server.bootstrap import create_server
from codexws_server.socket_protocol import TerminalSocketEvent


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def load_yaml(relative_path):
    with (PROJECT_ROOT / relative_path).open(encoding="utf-8") as contract_file:
        return yaml.safe_load(contract_file)


def javascript_constant_values(relative_path, constant_name):
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    match = re.search(
        rf"const\s+{re.escape(constant_name)}\s*=\s*Object\.freeze\(\{{(.*?)\}}\);",
        source,
        flags=re.DOTALL,
    )
    if not match:
        raise AssertionError(f"JavaScript constant not found: {constant_name}")
    return set(re.findall(r"\w+\s*:\s*'([^']+)'", match.group(1)))


class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime = MagicMock()
        with (
            patch("codexws_server.bootstrap.load_workspaces"),
            patch("codexws_server.bootstrap.migrate_history_metadata"),
            patch("codexws_server.bootstrap.create_terminal_runtime", return_value=runtime),
        ):
            cls.server = create_server(start_runtime=False)
        cls.openapi = load_yaml("docs/api/openapi.yaml")
        cls.asyncapi = load_yaml("docs/api/asyncapi.yaml")

    def test_openapi_paths_and_methods_match_flask(self):
        flask_operations = {}
        for rule in self.server.app.url_map.iter_rules():
            if not rule.rule.startswith("/api/"):
                continue
            path = re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", rule.rule)
            flask_operations[path] = set(rule.methods) - {"HEAD", "OPTIONS"}

        documented_operations = {
            path: {
                method.upper()
                for method in operations
                if method.lower() in HTTP_METHODS
            }
            for path, operations in self.openapi["paths"].items()
        }
        self.assertEqual(flask_operations, documented_operations)

    def test_http_client_paths_match_openapi(self):
        client_paths = javascript_constant_values(
            "frontend/assets/app-api.js",
            "HTTP_API_PATHS",
        )
        self.assertEqual(client_paths, set(self.openapi["paths"]))

    def test_socket_event_names_match_python_javascript_and_asyncapi(self):
        python_events = {
            value
            for name, value in vars(TerminalSocketEvent).items()
            if name.isupper()
        }
        javascript_events = {
            event
            for event in javascript_constant_values(
                "frontend/assets/app-socket.js",
                "TERMINAL_SOCKET_EVENTS",
            )
            if event.startswith("terminal_")
        }
        channels = self.asyncapi["channels"]
        documented_events = set(channels)

        self.assertEqual(python_events, documented_events)
        self.assertEqual(javascript_events, documented_events)
        self.assertTrue(all(name == channel["address"] for name, channel in channels.items()))


if __name__ == "__main__":
    unittest.main()
