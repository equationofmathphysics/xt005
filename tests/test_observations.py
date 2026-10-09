import unittest

from codexws_server.observations import (
    hook_status_for_line,
    strip_terminal_control,
)


class ObservationParsingTests(unittest.TestCase):
    def test_hook_status_for_line(self):
        self.assertEqual(hook_status_for_line("post hook completed successfully"), "done")
        self.assertEqual(hook_status_for_line("hook failed with error"), "failed")
        self.assertIsNone(hook_status_for_line("ordinary build output"))

    def test_strip_terminal_control(self):
        self.assertEqual(strip_terminal_control("a\x1b[31mb\r\nc"), "ab\n\nc")


if __name__ == "__main__":
    unittest.main()
