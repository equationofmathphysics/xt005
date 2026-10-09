"""Optional browser regression test for terminal fitting and cached view switches."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


class TerminalLayoutRegressionTest(unittest.TestCase):
    def test_terminal_layout_regression(self) -> None:
        runner = Path(__file__).with_name("terminal_layout_regression.mjs")
        try:
            result = subprocess.run(
                ["node", str(runner)],
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except FileNotFoundError:
            self.skipTest("node is not installed")

        output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if result.returncode == 77:
            self.skipTest(output or "Playwright/Chromium is not available")
        self.assertEqual(result.returncode, 0, output)


if __name__ == "__main__":
    unittest.main()
