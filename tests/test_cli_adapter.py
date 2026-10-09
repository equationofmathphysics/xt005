import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from codexws_server import cli_adapter


class CliAdapterTests(unittest.TestCase):
    def test_optional_cli_validation_and_resume_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            binary=Path(directory)/'agent'
            binary.write_text('#!/bin/sh\nprintf "%s\\n" "--no-daemon"\n')
            binary.chmod(0o755)
            with patch.object(cli_adapter,'CODEX_COMMAND',str(binary)):
                self.assertEqual(cli_adapter.launch_argv(directory),[str(binary),'--no-daemon'])
                with patch.object(cli_adapter,'find_codex_thread',return_value={'cwd':directory}):
                    self.assertEqual(cli_adapter.launch_argv(directory,'abc'),[str(binary),'resume','--no-daemon','abc'])
                with patch.object(cli_adapter,'find_codex_thread',return_value={'cwd':'/different'}):
                    with self.assertRaisesRegex(ValueError,'不属于'): cli_adapter.launch_argv(directory,'abc')
            with patch.object(cli_adapter,'CODEX_COMMAND',str(binary)+'-missing'):
                with self.assertRaisesRegex(ValueError,'未安装'): cli_adapter.launch_argv(directory)
