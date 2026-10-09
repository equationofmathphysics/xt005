import unittest

import pyte.modes as terminal_modes

from codexws_server.terminal_screen import TerminalScreenModel


def visible_lines(model):
    return [line.rstrip() for line in model.screen.display]


def history_lines(model):
    return [
        "".join(char.data for _, char in sorted(line.items())).rstrip()
        for line in model.screen.history.top
    ]


class TerminalScreenModelTests(unittest.TestCase):
    def assert_runtime_state_equal(self, left, right):
        left_screen = left.screen
        right_screen = right.screen
        self.assertEqual(left.active_buffer, right.active_buffer)
        self.assertEqual(left_screen.mode, right_screen.mode)
        self.assertEqual(left_screen.margins, right_screen.margins)
        self.assertEqual(
            (left_screen.cursor.x, left_screen.cursor.y, left_screen.cursor.attrs, left_screen.cursor.hidden),
            (right_screen.cursor.x, right_screen.cursor.y, right_screen.cursor.attrs, right_screen.cursor.hidden),
        )
        self.assertEqual(left_screen.charset, right_screen.charset)
        self.assertEqual(left_screen.g0_charset, right_screen.g0_charset)
        self.assertEqual(left_screen.g1_charset, right_screen.g1_charset)
        self.assertEqual(left_screen.tabstops, right_screen.tabstops)
        self.assertEqual(len(left_screen.savepoints), len(right_screen.savepoints))
        for left_savepoint, right_savepoint in zip(left_screen.savepoints, right_screen.savepoints):
            self.assertEqual(
                (
                    left_savepoint.cursor.x,
                    left_savepoint.cursor.y,
                    left_savepoint.cursor.attrs,
                    left_savepoint.cursor.hidden,
                    left_savepoint.charset,
                    left_savepoint.g0_charset,
                    left_savepoint.g1_charset,
                    left_savepoint.origin,
                    left_savepoint.wrap,
                ),
                (
                    right_savepoint.cursor.x,
                    right_savepoint.cursor.y,
                    right_savepoint.cursor.attrs,
                    right_savepoint.cursor.hidden,
                    right_savepoint.charset,
                    right_savepoint.g0_charset,
                    right_savepoint.g1_charset,
                    right_savepoint.origin,
                    right_savepoint.wrap,
                ),
            )

    def test_snapshot_round_trip_preserves_screen_and_cursor(self):
        source = TerminalScreenModel(20, 5, history_lines=20)
        source.feed("first\r\nsecond\r\nthird\r\nfourth\r\nfifth\r\nsixth")
        source.feed("\x1b[2;4H\x1b[31;1mSTATUS\x1b[0m\x1b[?2004h")

        restored = TerminalScreenModel(20, 5, history_lines=20)
        restored.feed(source.serialize())

        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assertEqual(
            (restored.screen.cursor.x, restored.screen.cursor.y),
            (source.screen.cursor.x, source.screen.cursor.y),
        )
        self.assertIn(2004 << 5, restored.screen.mode)

    def test_snapshot_is_not_delta_safe_during_partial_control_sequence(self):
        model = TerminalScreenModel(20, 5)
        self.assertTrue(model.delta_safe)

        model.feed("prefix\x1b[31")
        self.assertFalse(model.delta_safe)
        model.feed("mRED")
        self.assertTrue(model.delta_safe)

        model.feed("\x1b]10;?")
        self.assertFalse(model.delta_safe)
        model.feed("\x07")
        self.assertTrue(model.delta_safe)

        model.feed("\x1b[4:")
        self.assertFalse(model.delta_safe)
        model.feed("3m")
        self.assertTrue(model.delta_safe)

    def test_repeated_tui_redraw_serializes_current_screen_not_raw_tail(self):
        model = TerminalScreenModel(40, 10, history_lines=20)
        for counter in range(1, 80):
            model.feed(f"\x1b[2J\x1b[HConversation body\x1b[8;1H• Working {counter}")

        snapshot = model.serialize()
        restored = TerminalScreenModel(40, 10, history_lines=20)
        restored.feed(snapshot)
        display = visible_lines(restored)

        self.assertEqual(display[0], "Conversation body")
        self.assertEqual(display[7], "• Working 79")
        self.assertEqual(sum(bool(line) for line in display), 2)

    def test_bounded_snapshot_drops_old_scrollback_without_truncating_screen(self):
        model = TerminalScreenModel(80, 24, history_lines=1000)
        for index in range(500):
            model.feed(f"line {index:04d} " + ("x" * 60) + "\r\n")
        model.feed("CURRENT SCREEN")

        snapshot = model.serialize_bounded(12000)
        restored = TerminalScreenModel(80, 24, history_lines=1000)
        restored.feed(snapshot)

        self.assertTrue(snapshot)
        self.assertLessEqual(len(snapshot), 12000)
        self.assertIn("CURRENT SCREEN", "\n".join(visible_lines(restored)))

    def test_snapshot_preserves_cjk_without_serializing_wide_cell_stubs(self):
        source = TerminalScreenModel(80, 5)
        source.feed("Cityscapes 是什么，你给的模型\r\n参考：中文ABC")

        snapshot = source.serialize()
        restored = TerminalScreenModel(80, 5)
        restored.feed(snapshot)

        self.assertIn("Cityscapes 是什么，你给的模型", snapshot)
        self.assertNotIn("参 考 ：", snapshot)
        self.assertEqual(visible_lines(restored), visible_lines(source))

    def test_snapshot_preserves_emoji_and_combining_graphemes(self):
        source = TerminalScreenModel(80, 5)
        source.feed("A❤️B中文C A👨‍👩‍👧B e\u0301")

        restored = TerminalScreenModel(80, 5)
        restored.feed(source.serialize())

        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assertIn("A❤️B中文C A👨‍👩‍👧B é", visible_lines(restored)[0])

    def test_grapheme_width_is_stable_across_feed_boundaries(self):
        value = "A❤️B中文C"
        expected = TerminalScreenModel(80, 5)
        expected.feed(value)

        for split in range(1, len(value)):
            model = TerminalScreenModel(80, 5)
            model.feed(value[:split])
            model.feed(value[split:])
            self.assertEqual(visible_lines(model), visible_lines(expected), split)
            self.assertEqual(model.screen.cursor.x, expected.screen.cursor.x, split)

    def test_snapshot_round_trip_preserves_every_scrollback_line(self):
        source = TerminalScreenModel(12, 5, history_lines=40)
        for index in range(20):
            source.feed(f"line-{index:02d}\r\n")

        restored = TerminalScreenModel(12, 5, history_lines=40)
        restored.feed(source.serialize())

        self.assertEqual(history_lines(restored), history_lines(source))
        self.assertEqual(visible_lines(restored), visible_lines(source))

    def test_alternate_buffer_exit_restores_normal_screen(self):
        model = TerminalScreenModel(30, 5)
        model.feed("normal conversation\r\nsecond line")
        normal = visible_lines(model)

        model.feed("\x1b[?1049hALT 中文 screen\x1b[2;1Hworking")
        self.assertEqual(visible_lines(model)[1], "working    ALT 中文 screen")
        model.feed("\x1b[?1049l")

        self.assertEqual(visible_lines(model), normal)

    def test_alternate_buffer_entry_copies_normal_cursor_position(self):
        model = TerminalScreenModel(20, 5)
        model.feed("\x1b[3;7H\x1b[?1049hX")

        self.assertEqual(visible_lines(model)[2], "      X")
        self.assertEqual((model.screen.cursor.x, model.screen.cursor.y), (7, 2))

    def test_alternate_buffer_has_independent_margins_and_tabstops(self):
        model = TerminalScreenModel(20, 6)
        model.feed("\x1b[2;5r\x1b[3g\x1b[11G\x1bH")
        normal_margins = model.screen.margins
        self.assertEqual(model.screen.tabstops, {10})

        model.feed("\x1b[?1049h")
        self.assertIsNone(model.screen.margins)
        self.assertEqual(model.screen.tabstops, {8, 16})

        model.feed("\x1b[3;6r\x1b[6G\x1bH")
        self.assertEqual(model.screen.tabstops, {5, 8, 16})
        model.feed("\x1b[?1049l")

        self.assertEqual(model.screen.margins, normal_margins)
        self.assertEqual(model.screen.tabstops, {10})

    def test_snapshot_preserves_pending_autowrap_before_raw_delta(self):
        source = TerminalScreenModel(5, 3)
        source.feed("abcde")
        self.assertEqual((source.screen.cursor.x, source.screen.cursor.y), (5, 0))

        restored = TerminalScreenModel(5, 3)
        restored.feed(source.serialize())
        self.assertEqual((restored.screen.cursor.x, restored.screen.cursor.y), (5, 0))

        source.feed("Z")
        restored.feed("Z")
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assertEqual(visible_lines(restored)[:2], ["abcde", "Z"])

    def test_resize_adds_default_tab_stops_in_new_columns(self):
        model = TerminalScreenModel(80, 3)
        model.resize(120, 3)

        self.assertTrue(set(range(80, 120, 8)).issubset(model.screen.tabstops))
        model.feed("\x1b[1;79H\tX")
        self.assertEqual(visible_lines(model)[0][80], "X")

    def test_explicit_scroll_region_updates_screen_and_history(self):
        model = TerminalScreenModel(20, 6, history_lines=20)
        for row in range(1, 7):
            model.feed(f"\x1b[{row};1Hline-{row}")

        model.feed("\x1b[1;4r\x1b[2S")

        self.assertEqual(history_lines(model)[-2:], ["line-1", "line-2"])
        self.assertEqual(visible_lines(model), ["line-3", "line-4", "", "", "line-5", "line-6"])

        model.feed("\x1b[1T")
        self.assertEqual(visible_lines(model), ["", "line-3", "line-4", "", "line-5", "line-6"])

    def test_codex_scroll_keeps_wrapped_user_input_in_snapshot(self):
        model = TerminalScreenModel(40, 8, history_lines=40)
        first = "› 用户输入第一行"
        continuation = "  用户输入的完整续行"
        model.feed(f"\x1b[1;1H{first}\x1b[2;1H{continuation}")
        model.feed("\x1b[1;6r\x1b[2S\x1b[r\x1b[7;1H\x1b[J• Working")

        restored = TerminalScreenModel(40, 8, history_lines=40)
        restored.feed(model.serialize())
        transcript = "\n".join(history_lines(restored) + visible_lines(restored))

        self.assertIn(first, transcript)
        self.assertIn(continuation, transcript)
        self.assertIn("• Working", transcript)

    def test_dec_special_graphics_charset_round_trips_and_continues(self):
        source = TerminalScreenModel(20, 3)
        source.feed("\x1b(0lq")
        self.assertEqual(visible_lines(source)[0], "┌─")

        restored = TerminalScreenModel(20, 3)
        restored.feed(source.serialize())
        source.feed("k")
        restored.feed("k")

        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assertEqual(visible_lines(restored)[0], "┌─┐")

    def test_snapshot_preserves_dim_and_conceal_sgr(self):
        source = TerminalScreenModel(20, 3)
        source.feed("\x1b[2;8msecret\x1b[0m normal")

        restored = TerminalScreenModel(20, 3)
        restored.feed(source.serialize())

        self.assertEqual(visible_lines(restored), visible_lines(source))
        hidden = restored.screen.buffer[0][0]
        normal = restored.screen.buffer[0][7]
        self.assertTrue(hidden.dim)
        self.assertTrue(hidden.conceal)
        self.assertFalse(normal.dim)
        self.assertFalse(normal.conceal)

    def test_snapshot_preserves_extended_underline_and_overline_sgr(self):
        source = TerminalScreenModel(20, 3)
        source.feed("\x1b[4:3;53;58;2;1;2;3mX")

        styled = source.screen.buffer[0][0]
        self.assertTrue(styled.underscore)
        self.assertEqual(styled.underline_style, 3)
        self.assertEqual(styled.underline_color, "010203")
        self.assertTrue(styled.overline)
        self.assertFalse(styled.bold)
        self.assertFalse(styled.italics)
        self.assertFalse(styled.dim)
        self.assertTrue(source.delta_safe)

        restored = TerminalScreenModel(20, 3)
        restored.feed(source.serialize())
        source.feed("Y")
        restored.feed("Y")

        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assertEqual(restored.screen.buffer[0][0], source.screen.buffer[0][0])
        self.assertEqual(restored.screen.buffer[0][1], source.screen.buffer[0][1])
        self.assertEqual(restored.screen.cursor.attrs, source.screen.cursor.attrs)

    def test_sgr_58_consumes_color_parameters_without_enabling_text_styles(self):
        model = TerminalScreenModel(20, 3)
        model.feed("\x1b[58;2;2;1;3mX")

        char = model.screen.buffer[0][0]
        self.assertEqual(char.underline_color, "020103")
        self.assertFalse(char.bold)
        self.assertFalse(char.dim)
        self.assertFalse(char.italics)

    def test_snapshot_runtime_state_can_continue_with_raw_delta(self):
        source = TerminalScreenModel(30, 8, history_lines=40)
        source.feed("\x1b[2;7r\x1b[?6h\x1b[?7l\x1b[4;20h\x1b[?1;2004h")
        source.feed("\x1b[3;5H\x1b[31;1mSAVED\x1b7")
        source.feed("\x1b[4;8H\x1b[38;2;12;34;56;3mCURRENT\x1b[?25l")

        restored = TerminalScreenModel(30, 8, history_lines=40)
        restored.feed(source.serialize())

        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

        delta = "\x1b[2@XY\nnext"
        source.feed(delta)
        restored.feed(delta)
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

        source.feed("\x1b8Z")
        restored.feed("\x1b8Z")
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

    def test_snapshot_restores_custom_tabstops_before_raw_tab_delta(self):
        source = TerminalScreenModel(30, 5, history_lines=20)
        source.feed("\x1b[3g\x1b[5G\x1bH\x1b[12G\x1bH\x1b[H")

        restored = TerminalScreenModel(30, 5, history_lines=20)
        restored.feed(source.serialize())

        self.assertEqual(restored.screen.tabstops, {4, 11})
        self.assert_runtime_state_equal(source, restored)

        delta = "A\tB\tC"
        source.feed(delta)
        restored.feed(delta)
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

    def test_saved_cursor_is_a_single_persistent_terminal_slot(self):
        source = TerminalScreenModel(30, 5, history_lines=20)
        source.feed("\x1b[2;3H\x1b7\x1b[3;5H\x1b[32m\x1b7\x1b[5;20H")
        self.assertEqual(len(source.screen.savepoints), 1)

        restored = TerminalScreenModel(30, 5, history_lines=20)
        restored.feed(source.serialize())
        self.assert_runtime_state_equal(source, restored)

        for delta in ("\x1b8A", "\x1b[5;20H", "\x1b8B"):
            source.feed(delta)
            restored.feed(delta)
        self.assertEqual(len(source.screen.savepoints), 1)
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

    def test_restore_cursor_does_not_restore_origin_or_wrap_modes(self):
        model = TerminalScreenModel(30, 8, history_lines=20)
        model.feed("\x1b[2;7r\x1b[?6h\x1b[?7l\x1b[?25h\x1b[3;4H\x1b7")
        model.feed("\x1b[?6l\x1b[?7h\x1b[?25l\x1b[7;20H\x1b8")

        self.assertNotIn(terminal_modes.DECOM, model.screen.mode)
        self.assertIn(terminal_modes.DECAWM, model.screen.mode)
        self.assertTrue(model.screen.cursor.hidden)
        self.assertEqual((model.screen.cursor.x, model.screen.cursor.y), (3, 3))

    def test_snapshot_preserves_active_alternate_and_saved_normal_buffer(self):
        source = TerminalScreenModel(30, 7, history_lines=40)
        source.feed("normal body\x1b[2;6r\x1b[3;4H\x1b[31mN\x1b7")
        source.feed("\x1b[?1049h\x1b[2;6r\x1b[?6h")
        source.feed("\x1b[2;3H\x1b[34;1mALT 中文\x1b7\x1b[3;5H\x1b[35mlive")

        snapshot = source.serialize()
        restored = TerminalScreenModel(30, 7, history_lines=40)
        restored.feed(snapshot)

        self.assertIn("\x1b[?1049h", snapshot)
        self.assertEqual(restored.active_buffer, "alternate")
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

        source.feed(" delta")
        restored.feed(" delta")
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

        source.feed("\x1b[?1049l")
        restored.feed("\x1b[?1049l")
        self.assertEqual(restored.active_buffer, "normal")
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

        source.feed("N")
        restored.feed("N")
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

    def test_active_alternate_snapshot_does_not_clear_normal_scrollback(self):
        source = TerminalScreenModel(24, 5, history_lines=40)
        for index in range(16):
            source.feed(f"normal-{index:02d}\r\n")
        normal_history = history_lines(source)
        normal_display = visible_lines(source)
        source.feed("\x1b[?1049hALT LIVE\x1b[3;4Hworking")

        snapshot = source.serialize()
        alternate_replay = snapshot.split("\x1b[?1049h", 1)[1]
        self.assertNotIn("\x1b[3J", alternate_replay)

        restored = TerminalScreenModel(24, 5, history_lines=40)
        restored.feed(snapshot)
        self.assertEqual(restored.active_buffer, "alternate")
        self.assertEqual(visible_lines(restored), visible_lines(source))

        source.feed("\x1b[?1049l")
        restored.feed("\x1b[?1049l")
        self.assertEqual(history_lines(source), normal_history)
        self.assertEqual(history_lines(restored), normal_history)
        self.assertEqual(visible_lines(source), normal_display)
        self.assertEqual(visible_lines(restored), normal_display)
        self.assert_runtime_state_equal(source, restored)

    def test_resize_updates_active_and_saved_buffers_before_snapshot(self):
        source = TerminalScreenModel(20, 6, history_lines=40)
        source.feed("normal-width-content\x1b[6;20H\x1b[31mN")
        source.feed("\x1b[?1049hALT 中文\x1b[5;18H\x1b[34mA")
        source.resize(12, 4)

        restored = TerminalScreenModel(12, 4, history_lines=40)
        restored.feed(source.serialize())

        self.assertEqual((source.cols, source.rows), (12, 4))
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)

        source.feed("x\x1b[?1049lN")
        restored.feed("x\x1b[?1049lN")
        self.assertEqual(visible_lines(restored), visible_lines(source))
        self.assert_runtime_state_equal(source, restored)


if __name__ == "__main__":
    unittest.main()
