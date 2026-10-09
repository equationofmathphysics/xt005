import unittest

from codexws_server.terminal_history import TerminalHistoryBuffer


class TerminalHistoryBufferTests(unittest.TestCase):
    def test_append_and_slice_across_chunks(self):
        history = TerminalHistoryBuffer(20, chunk_size=4)
        history.append("abcdef")
        history.append("ghijkl")

        self.assertEqual(len(history), 12)
        self.assertEqual(history.start, 0)
        self.assertEqual(history.end, 12)
        self.assertEqual(history[2:10], "cdefghij")
        self.assertEqual(history.tail(5), "hijkl")

    def test_limit_trims_oldest_text_and_tracks_absolute_offsets(self):
        history = TerminalHistoryBuffer(8, chunk_size=4)
        history.append("abcdef")
        history.append("ghij")

        self.assertEqual(history.to_string(), "cdefghij")
        self.assertEqual(history.start, 2)
        self.assertEqual(history.end, 10)

    def test_large_append_keeps_only_tail(self):
        history = TerminalHistoryBuffer(5, chunk_size=2)
        history.append("0123456789")

        self.assertEqual(history.to_string(), "56789")
        self.assertEqual(history.start, 5)
        self.assertEqual(history.end, 10)

    def test_zero_limit_tracks_offsets_without_storing_text(self):
        history = TerminalHistoryBuffer(0)
        history.append("abcdef")

        self.assertFalse(history)
        self.assertEqual(history.start, 6)
        self.assertEqual(history.end, 6)


if __name__ == "__main__":
    unittest.main()
