"""Real Gunicorn + browser smoke with temporary state and no installed agent."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


class PtyLiveBrowserTests(unittest.TestCase):
    def test_live_browser(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)
            sessions=state/'codex/sessions'; sessions.mkdir(parents=True)
            thread_id='12345678-1234-1234-1234-123456789abc'
            (sessions/f'rollout-2026-10-08-{thread_id}.jsonl').write_text('\n'.join(json.dumps(row) for row in [
                {'type':'session_meta','payload':{'id':thread_id,'cwd':directory,'timestamp':'2026-10-08T00:00:00Z'}},
                {'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'离线历史内容'}]}}
            ])+'\n')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
            env=dict(os.environ,HOME=directory,DEFAULT_WORKSPACE=directory,CODEX_HOME=str(state/'codex'),
                     WORKSPACES_FILE=str(state/'workspaces.json'),CODEX_HISTORY_FILE=str(state/'history.json'),
                     ENV_FILE=str(state/'absent.env'),CODEX_COMMAND=str(state/'missing-codex'),HOST='127.0.0.1',PORT=str(port),
                     GUNICORN_EXTRA_BINDS='',PYTHON_BIN=sys.executable)
            url=f'http://127.0.0.1:{port}'
            with (state/'server.log').open('w+') as log:
                server=subprocess.Popen(['bash',str(root/'run.sh')],env=env,stdout=log,stderr=log)
                try:
                    for _ in range(100):
                        if server.poll() is not None:
                            log.seek(0); self.fail(log.read())
                        try:
                            with urlopen(url+'/api/check',timeout=.2) as response:
                                if json.load(response)['transport']=='pty': break
                        except OSError: time.sleep(.1)
                    else: self.fail('server startup timed out')
                    try:
                        result=subprocess.run(['node',str(root/'tests/pty_live_smoke.mjs'),url],capture_output=True,text=True,timeout=65)
                    except FileNotFoundError: self.skipTest('node unavailable')
                    if result.returncode==77: self.skipTest(result.stdout or 'Playwright/Chromium unavailable')
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                finally:
                    server.terminate()
                    try: server.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        server.kill(); server.wait(); self.fail('Gunicorn failed to shut down promptly')
