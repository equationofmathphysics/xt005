import unittest
from unittest.mock import patch

from codexws_server.terminal_runtime import (
    append_terminal_history_buffer,
    append_terminal_replay_buffer,
    build_terminal_env,
    codex_activity_from_tail,
    ensure_terminal_utf8_locale,
    locale_is_utf8,
    next_terminal_stream_epoch,
    sanitize_terminal_replay_buffer,
    slice_terminal_replay_tail,
)


class TerminalRuntimePureFunctionTests(unittest.TestCase):
    def test_sanitize_terminal_replay_buffer_removes_query_controls(self):
        text = "a\x1b[6nb\x1b]10;rgb:ffff/ffff/ffff\x07c"
        self.assertEqual(sanitize_terminal_replay_buffer(text), "abc")

    def test_append_terminal_replay_buffer_sanitizes_only_new_text(self):
        buffer = "old\x1b[6n"
        text = "a\x1b[6nb"
        self.assertEqual(append_terminal_replay_buffer(buffer, text, limit=20), "old\x1b[6nab")

    def test_append_terminal_replay_buffer_trims_to_limit(self):
        self.assertEqual(append_terminal_replay_buffer("abc\ndef", "gh", limit=5), "defgh")

    def test_append_terminal_replay_buffer_zero_limit_keeps_empty_buffer(self):
        self.assertEqual(append_terminal_replay_buffer("abcdef", "gh", limit=0), "")

    def test_slice_terminal_replay_tail_prefers_newline_boundary(self):
        buffer, start = slice_terminal_replay_tail("old line\nnew line", 10)
        self.assertEqual(buffer, "new line")
        self.assertEqual(start, 9)

    def test_slice_terminal_replay_tail_removes_leading_partial_csi_tail(self):
        buffer, start = slice_terminal_replay_tail("abc\x1b[31mred\x1b[0m\nnext", 7)
        self.assertEqual(buffer, "next")
        self.assertGreaterEqual(start, 0)

    def test_slice_terminal_replay_tail_can_start_at_escape_boundary(self):
        buffer, start = slice_terminal_replay_tail("abc\x1b[31mred", 8)
        self.assertEqual(buffer, "\x1b[31mred")
        self.assertEqual(start, 3)

    def test_append_terminal_history_buffer_tracks_absolute_offsets(self):
        history, start, end = append_terminal_history_buffer("abcdef", 10, "ghij", limit=8)
        self.assertEqual(history, "cdefghij")
        self.assertEqual(start, 12)
        self.assertEqual(end, 20)

    def test_append_terminal_history_buffer_sanitizes_new_text(self):
        history, start, end = append_terminal_history_buffer("", 0, "a\x1b[6nb", limit=20)
        self.assertEqual(history, "ab")
        self.assertEqual(start, 0)
        self.assertEqual(end, 2)

    def test_codex_activity_detects_working_line(self):
        info = {"buffer": "\x1b[32mWorking(esc to interrupt)\x1b[0m\r\n"}
        activity = codex_activity_from_tail(info, now=120)
        self.assertEqual(activity["state"], "working")
        self.assertIn("Working", activity["label"])

    def test_codex_activity_marks_stable_tail_unknown(self):
        info = {"buffer": "line 1\nline 2\nline 3\n"}
        self.assertEqual(codex_activity_from_tail(info, now=100)["state"], "unknown")
        activity = codex_activity_from_tail(info, now=103)
        self.assertEqual(activity["state"], "unknown")
        self.assertTrue(activity["sample_stable"])

    def test_codex_activity_marks_changed_tail_active(self):
        info = {"buffer": "line 1\nline 2\nline 3\n"}
        self.assertEqual(codex_activity_from_tail(info, now=100)["state"], "unknown")
        info["buffer"] = "line 1\nline 2\nline 4\n"
        activity = codex_activity_from_tail(info, now=103)
        self.assertEqual(activity["state"], "active")
        self.assertTrue(activity["sample_changed"])

    def test_codex_activity_defaults_to_unknown(self):
        self.assertEqual(codex_activity_from_tail({"buffer": "ready\n"})["state"], "unknown")
        self.assertEqual(codex_activity_from_tail(None)["state"], "unknown")

    def test_locale_is_utf8(self):
        self.assertTrue(locale_is_utf8("en_US.UTF-8"))
        self.assertTrue(locale_is_utf8("zh_CN.utf8"))
        self.assertFalse(locale_is_utf8("C"))
        self.assertFalse(locale_is_utf8(""))

    def test_ensure_terminal_utf8_locale_keeps_existing_utf8(self):
        env = {"LANG": "zh_CN.UTF-8", "LC_CTYPE": "zh_CN.UTF-8"}
        self.assertIs(ensure_terminal_utf8_locale(env), env)
        self.assertEqual(env["LANG"], "zh_CN.UTF-8")

    def test_ensure_terminal_utf8_locale_replaces_non_utf8(self):
        env = {"LANG": "C", "LC_ALL": "C"}
        ensure_terminal_utf8_locale(env)
        self.assertEqual(env["LANG"], "C.UTF-8")
        self.assertEqual(env["LC_CTYPE"], "C.UTF-8")
        self.assertEqual(env["LC_ALL"], "C.UTF-8")

    def test_build_terminal_env_declares_rich_xterm_capabilities(self):
        env = build_terminal_env(
            {"LANG": "C", "VSCODE_IPC_HOOK": "x", "__vsc_prompt": "1"},
            132,
            43,
            "workspace-alpha",
            "terminal-beta",
        )
        self.assertEqual(env["TERM"], "xterm-256color")
        self.assertEqual(env["COLORTERM"], "truecolor")
        self.assertEqual(env["CLICOLOR"], "1")
        self.assertEqual(env["TERM_PROGRAM"], "codexws")
        self.assertEqual(env["COLUMNS"], "132")
        self.assertEqual(env["LINES"], "43")
        self.assertEqual(env["CODEXWS_WORKSPACE_ID"], "workspace-alpha")
        self.assertEqual(env["CODEXWS_TERMINAL_ID"], "terminal-beta")
        self.assertNotIn("VSCODE_IPC_HOOK", env)
        self.assertNotIn("__vsc_prompt", env)

    def test_stream_epoch_remains_monotonic_when_wall_clock_moves_back(self):
        with patch("codexws_server.terminal_runtime.time.time", side_effect=[2000, 1000]):
            first = next_terminal_stream_epoch()
            second = next_terminal_stream_epoch()

        self.assertGreater(second, first)


if __name__ == "__main__":
    unittest.main()
