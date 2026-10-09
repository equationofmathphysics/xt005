import unittest
from unittest import mock

from voice_input import voice_service


class VoiceInputInjectionTests(unittest.TestCase):
    def test_default_injection_mode_is_paste(self):
        parser = voice_service.build_parser()
        self.assertEqual(parser.parse_args([]).inject, "paste")

    def test_parser_rejects_keyboard_injection_modes(self):
        parser = voice_service.build_parser()
        for mode in ("auto", "type"):
            with self.subTest(mode=mode):
                with self.assertRaises(SystemExit):
                    parser.parse_args(["--inject", mode])

    def test_parser_rejects_paste_keystroke_argument(self):
        parser = voice_service.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--paste-keystroke", "auto"])

    def test_main_rejects_keyboard_injection_from_environment(self):
        for mode in ("auto", "type"):
            with self.subTest(mode=mode):
                with mock.patch.dict("os.environ", {"VOICE_INPUT_INJECT": mode}):
                    with self.assertRaises(SystemExit):
                        voice_service.main([])

    def test_paste_mode_copies_text_and_uses_service_paste_action(self):
        with mock.patch.object(voice_service, "read_clipboard", return_value=None), \
                mock.patch.object(voice_service, "copy_to_clipboard", return_value=True) as copy, \
                mock.patch.object(voice_service, "paste_from_clipboard", return_value=True) as paste, \
                mock.patch.object(voice_service, "clear_clipboard", return_value=True):
            voice_service.inject_text("hello", "paste")
        copy.assert_called_once_with("hello")
        paste.assert_called_once_with()

    def test_clipboard_mode_only_copies_text(self):
        with mock.patch.object(voice_service, "copy_to_clipboard", return_value=True) as copy, \
                mock.patch.object(voice_service, "paste_from_clipboard") as paste:
            voice_service.inject_text("hello", "clipboard")
        copy.assert_called_once_with("hello")
        paste.assert_not_called()

    def test_paste_shortcut_is_fixed_by_target_window(self):
        with mock.patch.object(voice_service, "active_window_info", return_value={"name": "codexws"}):
            self.assertEqual(voice_service.resolve_paste_shortcut(), "ctrl+shift+v")
        with mock.patch.object(voice_service, "active_window_info", return_value={"name": "Document"}):
            self.assertEqual(voice_service.resolve_paste_shortcut(), "ctrl+v")


if __name__ == "__main__":
    unittest.main()
