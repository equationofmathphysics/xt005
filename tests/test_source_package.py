import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


class SourcePackageTest(unittest.TestCase):
    def test_source_archive_contains_runtime_without_local_or_optional_modules(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as output_dir:
            result = subprocess.run(
                [str(project_root / "build-source-package.sh"), output_dir],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "SOURCE_DATE_EPOCH": "0"},
            )
            archive_path = Path(result.stdout.strip())
            self.assertTrue(archive_path.is_file())

            with tarfile.open(archive_path, "r:gz") as archive:
                members = {member.name: member for member in archive.getmembers()}
                root = next(name.split("/", 1)[0] for name in members)
                self.assertTrue(root.startswith("xt005-"))

                for relative_path in (
                    "frontend/assets/app-terminal.js",
                    "frontend/assets/vendor/marked.umd.js",
                    "frontend/index.html",
                    "src/codexws_server/__main__.py",
                    "xt005.service",
                    "install.sh",
                    "INSTALL.md",
                    "requirements.txt",
                    "requirements.lock",
                    "rescue/server.py",
                    ".cdnlocal/xterm/5.3.0/xterm.js",
                    "requirements-dev.txt",
                    "run.sh",
                    "frontend/assets/vendor/LICENSE.marked.txt",
                    "frontend/assets/vendor/LICENSE.temml.txt",
                ):
                    self.assertIn(f"{root}/{relative_path}", members)

                forbidden_top_level = {
                    ".deps",
                    ".git",
                    ".venv",
                    "codex-cli-source",
                    "staffctl",
                    "tests",
                    "voice_input",
                    ".codex_history.json",
                    ".app-preferences.json",
                    ".workspaces.json",
                    ".env",
                    "codex-cli-source",
                    "deploy",
                    "fork-lineage.json",
                }
                for name in members:
                    relative_name = name.split("/", 1)[1] if "/" in name else ""
                    top_level = relative_name.split("/", 1)[0]
                    self.assertNotIn(top_level, forbidden_top_level, f"unexpected archive member: {name}")

                self.assertTrue(members[f"{root}/install.sh"].mode & 0o111)
                self.assertTrue(members[f"{root}/run.sh"].mode & 0o111)

    def test_generated_unit_passes_systemd_validation(self):
        import shutil
        if not shutil.which("systemd-analyze"):
            self.skipTest("systemd-analyze unavailable")
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="xt005 unit ") as directory:
            unit = Path(directory) / "xt005-test.service"
            content = (root / "xt005.service").read_text()
            content = content.replace("@WORKING_DIRECTORY@", directory)
            content = content.replace('"@INSTALL_DIR@/run.sh"', '/bin/true')
            content = content.replace("@ENV_FILE@", directory + "/env")
            unit.write_text(content)
            result = subprocess.run(["systemd-analyze", "--user", "verify", str(unit)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_service_file_is_a_portable_user_unit_template(self):
        project_root = Path(__file__).resolve().parents[1]
        content = (project_root / "xt005.service").read_text(encoding="utf-8")
        self.assertIn("@INSTALL_DIR@", content)
        self.assertIn("@WORKING_DIRECTORY@", content)
        self.assertIn("@ENV_FILE@", content)
        self.assertIn("WantedBy=default.target", content)
        self.assertNotIn("/home/", content)
        self.assertNotIn("User=", content)
        self.assertNotIn("Group=", content)


if __name__ == "__main__":
    unittest.main()
