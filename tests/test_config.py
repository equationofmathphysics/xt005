import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codexws_server.config import default_bind_host, normalize_codex_command


class ConfigTests(unittest.TestCase):
    def test_explicit_codex_launcher_symlink_is_not_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "package" / "codex.js"
            target.parent.mkdir()
            target.touch()
            launcher = root / "bin" / "codex"
            launcher.parent.mkdir()
            launcher.symlink_to(target)

            self.assertEqual(normalize_codex_command(str(launcher)), str(launcher))

    def test_command_name_is_left_for_path_lookup(self):
        self.assertEqual(normalize_codex_command("codex"), "codex")

    def test_bind_host_defaults_to_loopback(self):
        with patch.dict(os.environ, {"HOST": "", "WG_INTERFACE": "wg0"}):
            self.assertEqual(default_bind_host(), "127.0.0.1")

    def test_bind_host_uses_explicit_value(self):
        with patch.dict(os.environ, {"HOST": "10.0.0.8"}):
            self.assertEqual(default_bind_host(), "10.0.0.8")


if __name__ == "__main__":
    unittest.main()
