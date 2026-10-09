import os
import subprocess
import sys
import unittest
from pathlib import Path


class PackageImportTest(unittest.TestCase):
    def test_submodule_import_does_not_start_server_runtime(self):
        project_root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(project_root / "src"), env.get("PYTHONPATH", "")))
        )
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import codexws_server.ids; "
                    "assert 'codexws_server.app' not in sys.modules"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
