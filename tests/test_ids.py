import unittest

from codexws_server.ids import normalize_workspace_id, workspace_id_from_path


class IdTests(unittest.TestCase):
    def test_normalize_workspace_id(self):
        self.assertEqual(normalize_workspace_id(" My Repo!! "), "my-repo")
        self.assertEqual(normalize_workspace_id("__A  B__"), "a-b")
        self.assertEqual(normalize_workspace_id(""), "")

    def test_workspace_id_from_path_prefers_name(self):
        self.assertEqual(
            workspace_id_from_path("Research Assistant", "/tmp/repo"),
            "research-assistant",
        )


if __name__ == "__main__":
    unittest.main()
