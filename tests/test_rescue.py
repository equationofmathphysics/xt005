import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from rescue.server import Manager, handler_for


class RescueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.dirty = False
        self.previous = self.root/'original'; self.previous.mkdir()
        (self.previous/'run.sh').write_text('#!/bin/sh\n')
        self.releases = self.root/'releases'; self.releases.mkdir()
        (self.releases/'current').symlink_to(self.previous)
        self.config = {'main_unit':'xt005.service','state_dir':str(self.root/'state'),
                       'repository':str(self.previous),'release_dir':str(self.releases),
                       'probe_url':'http://127.0.0.1:1/api/check'}
        self.manager = Manager(self.config, self.runner)

    def runner(self, argv, **kwargs):
        self.calls.append(argv)
        output = ''
        if argv[:3] == ['git','status','--porcelain']: output = ' M changed' if self.dirty else ''
        elif argv[:2] == ['git','rev-parse']: output = 'a'*40
        elif argv[:2] == ['git','archive']:
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w') as bundle:
                for name in ['requirements.txt','src/codexws_server/bootstrap.py','run.sh']:
                    item = tarfile.TarInfo(name); item.size=0; bundle.addfile(item, io.BytesIO())
            output = buffer.getvalue()
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr='' if isinstance(output,str) else b'')

    def test_update_refuses_dirty_tree_before_service_changes(self):
        self.dirty = True
        with self.assertRaisesRegex(RuntimeError,'未提交'):
            self.manager.update('HEAD')
        self.assertFalse(any(call[0]=='systemctl' for call in self.calls))
        self.assertEqual((self.releases/'current').resolve(),self.previous)

    def test_failed_health_restores_previous_version(self):
        with patch.object(self.manager,'healthy',side_effect=RuntimeError('unhealthy')):
            with self.assertRaisesRegex(RuntimeError,'unhealthy'):
                self.manager.update('HEAD')
        self.assertEqual((self.releases/'current').resolve(), self.previous)
        self.assertEqual([call[2] for call in self.calls if call[0]=='systemctl'],['stop','start','stop','start'])
        self.assertEqual(json.loads((self.root/'state/rollback.json').read_text())['previous'],str(self.previous))

    def test_single_operation_and_rejects_arbitrary_commands(self):
        self.manager.guard.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError,'已有'):
                self.manager.submit('restart')
        finally: self.manager.guard.release()
        with self.assertRaises(ValueError): self.manager.submit('rm -rf')
        with self.assertRaises(ValueError): self.manager.submit('update','--help')

    def test_console_is_available_when_main_is_down_and_mutations_require_origin(self):
        server = ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.manager,'x'*32,'http://unused'))
        origin = f'http://127.0.0.1:{server.server_port}'
        server.RequestHandlerClass = handler_for(self.manager,'x'*32,origin)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        with urlopen(origin) as response: self.assertIn('工作区抢救台',response.read().decode())
        with self.assertRaises(HTTPError) as denied: urlopen(origin+'/api/status')
        self.assertEqual(denied.exception.code,403)
        request=Request(origin+'/api/status',headers={'Authorization':'Bearer '+'x'*32})
        with urlopen(request) as response:self.assertIn('job',json.load(response))
        request=Request(origin+'/api/action',data=b'{"action":"restart"}',headers={'Authorization':'Bearer '+'x'*32,'Origin':'http://attacker.invalid'})
        with self.assertRaises(HTTPError) as denied:urlopen(request)
        self.assertEqual(denied.exception.code,403)
        self.assertFalse(any(call[:3]==['systemctl','--user','restart'] for call in self.calls))
