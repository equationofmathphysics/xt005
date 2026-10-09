"""Exercise real PTYs in a separate process with isolated workspace and HOME."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PtyIntegrationTests(unittest.TestCase):
    def test_real_terminal_control_disconnect_exit_and_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, HOME=directory, DEFAULT_WORKSPACE=directory,
                       WORKSPACES_FILE=directory+'/workspaces.json', CODEX_HISTORY_FILE=directory+'/history.json',
                       CODEX_COMMAND=directory+'/not-installed', ENV_FILE=directory+'/missing.env')
            source = r'''
import os,sys,time,threading,json
from codexws_server.bootstrap import create_server
from codexws_server.state import terminals
server=create_server(start_runtime=True)
runtime=server.terminal_runtime

def wait(predicate):
    until=time.monotonic()+5
    while time.monotonic()<until:
        if predicate(): return
        time.sleep(.02)
    raise AssertionError('timed out')

try:
    info=runtime.ensure_terminal('codexws',terminal_id='job',command=[sys.executable,'-u','-c',
        'import os,sys,time; print("TTY",sys.stdin.isatty(),os.tcgetpgrp(0)==os.getpgrp()); print("中文测试"); time.sleep(30)'])
    saved=json.load(open(os.environ['WORKSPACES_FILE']))
    assert any(entry['id']=='job' for entry in saved['terminal_sessions']['codexws'])
    wait(lambda:'中文测试' in info['history_buffer'].to_string())
    assert 'TTY True True' in info['history_buffer'].to_string()
    client=server.socketio.test_client(server.app)
    client.emit('terminal_attach',{'workspace_id':'codexws','terminal_id':'job','attach_seq':1,'cols':96,'rows':28})
    assert any(event['name']=='terminal_ready' for event in client.get_received())
    assert info['cols']==96 and info['rows']==28
    client.disconnect()
    assert info['proc'].poll() is None, 'browser disconnect killed process'
    client=server.socketio.test_client(server.app)
    client.emit('terminal_attach',{'workspace_id':'codexws','terminal_id':'job','attach_seq':2})
    assert any(event['name']=='terminal_ready' for event in client.get_received())
    client.emit('terminal_input',{'attach_seq':2,'data':'\x03'})
    wait(lambda:info['proc'].poll() is not None)
    time.sleep(.2)
    assert terminals.get('codexws:job') is info, 'managed process was silently replaced'
    client.disconnect()
    runtime.close_terminal('codexws','job')
    assert 'codexws:job' not in terminals
    orphan=runtime.ensure_terminal('codexws',terminal_id='orphan',command=[sys.executable,'-u','-c',
        'import subprocess; child=subprocess.Popen(["sleep","30"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(child.pid)'])
    wait(lambda:orphan['proc'].poll() is not None)
    wait(lambda:orphan['history_buffer'].to_string().strip().isdigit())
    orphan_pid=int(orphan['history_buffer'].to_string().strip())
    runtime.close_terminal('codexws','orphan')
    from codexws_server.process_tree import pid_running
    wait(lambda:not pid_running(orphan_pid))
    stubborn=runtime.ensure_terminal('codexws',terminal_id='stubborn',command=[sys.executable,'-u','-c',
        'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("ready"); time.sleep(30)'])
    wait(lambda:'ready' in stubborn['history_buffer'].to_string())
    process=stubborn['proc']
    server.context.stop()
    assert process.poll() is not None and not terminals
    assert runtime.ensure_terminal('codexws','late') is None
    assert server.app.test_client().get('/api/check').get_json()['transport']=='pty'
finally:
    server.context.stop()
'''
            result = subprocess.run([sys.executable, '-c', source], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_runtime_sources_have_no_removed_protocol(self):
        root = Path(__file__).resolve().parents[1]
        files = list((root/'src').rglob('*.py')) + list((root/'frontend/assets').glob('app-*.js'))
        files += [root/'install.sh', root/'run.sh', root/'pyproject.toml', root/'.env.example']
        for path in files:
            with self.subTest(path=path.name):
                text = path.read_text().lower()
                for forbidden in ('appserver', 'app-server', 'app_server'):
                    self.assertNotIn(forbidden, text)
