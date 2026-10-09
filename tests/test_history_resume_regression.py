import subprocess
import unittest
from pathlib import Path


class HistoryResumeRegressionTest(unittest.TestCase):
    def test_history_resume_regression(self):
        runner = Path(__file__).with_name("history_resume_regression.mjs")
        try:
            result = subprocess.run(
                ["node", str(runner)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            self.skipTest("node is not installed")
        output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        self.assertEqual(result.returncode, 0, output)


if __name__ == "__main__":
    unittest.main()
