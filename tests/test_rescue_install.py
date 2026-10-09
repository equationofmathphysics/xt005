"""Install into temporary paths with a systemctl recorder, never the real manager."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class RescueInstallTests(unittest.TestCase):
    def test_independent_install_without_start_and_custom_probe(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); binaries=base/'bin'; binaries.mkdir()
            recorder=binaries/'systemctl'
            recorder.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$RECORD_CALLS"\n'); recorder.chmod(0o755)
            config=base/'config'; config.mkdir(); (config/'env').write_text('HOST=127.0.0.1\nPORT=55123\n')
            env=dict(os.environ,PATH=str(binaries)+':'+os.environ['PATH'],RECORD_CALLS=str(base/'calls'),
                     XT005_CONFIG_DIR=str(config),XT005_STATE_DIR=str(base/'state'),
                     XT005_SYSTEMD_USER_DIR=str(base/'units'),XT005_RESCUE_DIR=str(base/'runtime'))
            result=subprocess.run(['bash',str(root/'rescue/install.sh'),'--no-start'],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            settings=json.loads((config/'rescue.json').read_text())
            self.assertEqual(settings['probe_url'],'http://127.0.0.1:55123/api/check')
            unit=(base/'units/xt005-rescue.service').read_text()
            self.assertNotIn('Requires=',unit); self.assertNotIn('PartOf=',unit)
            self.assertIn(str(base/'runtime/server.py'),unit)
            self.assertNotIn('restart',(base/'calls').read_text())
            self.assertEqual((config/'rescue-token').stat().st_mode&0o777,0o600)
            (config/'rescue.json').write_text('{"preserved":true}')
            subprocess.run(['bash',str(root/'rescue/install.sh'),'--no-start'],env=env,check=True,capture_output=True)
            self.assertEqual(json.loads((config/'rescue.json').read_text()),{'preserved':True})

    def test_main_installer_generates_managed_path_without_restart(self):
        import shutil
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); repo=base/'repo'; repo.mkdir(); binaries=base/'bin'; binaries.mkdir()
            for name in ('install.sh','xt005.service','requirements.lock'):
                shutil.copy2(root/name,repo/name)
            shutil.copytree(root/'rescue',repo/'rescue',ignore=shutil.ignore_patterns('__pycache__'))
            venv=repo/'.venv/bin'; venv.mkdir(parents=True)
            (venv/'python').write_text('#!/bin/sh\nexit 0\n'); (venv/'python').chmod(0o755)
            recorder=binaries/'systemctl'
            recorder.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$RECORD_CALLS"\n'); recorder.chmod(0o755)
            env=dict(os.environ,PATH=str(binaries)+':'+os.environ['PATH'],RECORD_CALLS=str(base/'calls'),
                     XT005_CONFIG_DIR=str(base/'config'),XT005_STATE_DIR=str(base/'state'),
                     XT005_SYSTEMD_USER_DIR=str(base/'units'),XT005_RESCUE_DIR=str(base/'runtime'),
                     XT005_WORKSPACE=str(base),XT005_PORT='55125')
            result=subprocess.run(['bash',str(repo/'install.sh'),'--no-start'],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual((base/'state/releases/current').resolve(),repo)
            self.assertIn(str(base/'state/releases/current/run.sh'),(base/'units/xt005.service').read_text())
            self.assertNotIn('restart',(base/'calls').read_text())
            self.assertEqual(json.loads((base/'config/rescue.json').read_text())['probe_url'],'http://127.0.0.1:55125/api/check')
