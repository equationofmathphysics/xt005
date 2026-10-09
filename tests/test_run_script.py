import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class RunScriptProxyTests(unittest.TestCase):
    def test_gnome_socks_proxy_is_inherited_when_environment_is_empty(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            output = root / "environment.txt"
            fake_gsettings = bin_dir / "gsettings"
            fake_gsettings.write_text(
                "#!/bin/sh\n"
                "case \"$2:$3\" in\n"
                "  org.gnome.system.proxy:mode) echo \"'manual'\" ;;\n"
                "  org.gnome.system.proxy.*:host) echo \"'127.0.0.1'\" ;;\n"
                "  org.gnome.system.proxy.socks:port) echo 1080 ;;\n"
                "  org.gnome.system.proxy.*:port) echo 1081 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_python = bin_dir / "python"
            fake_python.write_text(
                f"#!/bin/sh\nenv > {output}\n",
                encoding="utf-8",
            )
            fake_gsettings.chmod(0o755)
            fake_python.chmod(0o755)
            environment = {
                key: value
                for key, value in os.environ.items()
                if "proxy" not in key.casefold()
            }
            environment.update({
                "PATH": f"{bin_dir}:{environment.get('PATH', '')}",
                "PYTHON_BIN": str(fake_python),
                "ENV_FILE": str(root / "missing.env"),
            })
            subprocess.run(
                [str(project_root / "run.sh")],
                cwd=project_root,
                env=environment,
                check=True,
            )
            inherited = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            self.assertEqual(inherited["HTTP_PROXY"], "http://127.0.0.1:1081")
            self.assertEqual(inherited["HTTPS_PROXY"], "http://127.0.0.1:1081")
            self.assertEqual(inherited["ALL_PROXY"], "socks5h://127.0.0.1:1080")
            self.assertEqual(inherited["http_proxy"], inherited["HTTP_PROXY"])
            self.assertEqual(inherited["https_proxy"], inherited["HTTPS_PROXY"])
            self.assertEqual(inherited["all_proxy"], inherited["ALL_PROXY"])
            self.assertIn("127.0.0.1", inherited["NO_PROXY"])

    def test_local_proxy_ports_are_defaults_without_system_settings(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            output = root / "environment.txt"
            fake_gsettings = bin_dir / "gsettings"
            fake_gsettings.write_text(
                "#!/bin/sh\n"
                "echo \"'none'\"\n",
                encoding="utf-8",
            )
            fake_python = bin_dir / "python"
            fake_python.write_text(
                f"#!/bin/sh\nenv > {output}\n",
                encoding="utf-8",
            )
            fake_gsettings.chmod(0o755)
            fake_python.chmod(0o755)
            environment = {
                key: value
                for key, value in os.environ.items()
                if "proxy" not in key.casefold()
            }
            environment.update({
                "PATH": f"{bin_dir}:{environment.get('PATH', '')}",
                "PYTHON_BIN": str(fake_python),
                "ENV_FILE": str(root / "missing.env"),
            })
            subprocess.run(
                [str(project_root / "run.sh")],
                cwd=project_root,
                env=environment,
                check=True,
            )
            inherited = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            self.assertEqual(inherited["HTTP_PROXY"], "http://127.0.0.1:1081")
            self.assertEqual(inherited["HTTPS_PROXY"], "http://127.0.0.1:1081")
            self.assertEqual(inherited["ALL_PROXY"], "socks5h://127.0.0.1:1080")


if __name__ == "__main__":
    unittest.main()
