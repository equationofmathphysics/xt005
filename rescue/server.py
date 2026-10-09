#!/usr/bin/env python3
"""Standalone recovery service. Uses only Python's standard library."""
import argparse
import fcntl
import hmac
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import urlopen


def atomic_json(path, payload):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)


class Manager:
    ACTIONS = {'start', 'stop', 'restart', 'update', 'rollback'}

    def __init__(self, config, runner=subprocess.run):
        self.config = config
        self.unit = config['main_unit']
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service', self.unit):
            raise ValueError('Invalid main_unit')
        self.runner = runner
        self.root = Path(config['state_dir']).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.guard = threading.Lock()
        self.job_path = self.root / 'job.json'
        self.log_path = self.root / 'operation.log'
        self.job = self.load_job()
        if self.job.get('state') == 'running':
            with (self.root / 'operation.lock').open('a+b') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass  # Another recovery process still owns the operation.
                else:
                    self.job.update(state='interrupted', error='抢救服务曾中断；检查主服务状态，可手动回退')
                    atomic_json(self.job_path, self.job)

    def load_job(self):
        try:
            return json.loads(self.job_path.read_text())
        except (OSError, ValueError):
            return {'state': 'idle'}

    def phase(self, name):
        self.job.update(phase=name, updated_at=time.time())
        atomic_json(self.job_path, self.job)

    def command(self, argv, timeout=30, cwd=None):
        result = self.runner(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        with self.log_path.open('a') as log:
            log.write('\n$ ' + ' '.join(map(str, argv)) + '\n' + result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f'命令失败（{result.returncode}），请查看操作日志')
        return result.stdout.strip()

    def service(self, action):
        return self.command(['systemctl', '--user', action, self.unit], timeout=90)

    def snapshot(self):
        result = self.runner(['systemctl', '--user', 'show', self.unit, '--property=ActiveState,SubState,MainPID,Result'], capture_output=True, text=True, timeout=5)
        return {'service': result.stdout.strip() or result.stderr.strip(), 'job': dict(self.job)}

    def logs(self):
        result = self.runner(['journalctl', '--user', '-u', self.unit, '-n', '100', '--no-pager', '-o', 'short-iso'], capture_output=True, text=True, timeout=5)
        operation = ''
        if self.log_path.exists():
            with self.log_path.open('rb') as handle:
                handle.seek(max(0, self.log_path.stat().st_size - 65536))
                operation = handle.read().decode('utf-8', 'replace')
        return {'journal': result.stdout[-65536:] or result.stderr[-65536:], 'operation': operation}

    def submit(self, action, ref='HEAD'):
        if action not in self.ACTIONS:
            raise ValueError('未知操作')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,159}', ref):
            raise ValueError('无效 Git ref')
        if not self.guard.acquire(blocking=False):
            raise RuntimeError('已有管理任务正在执行')
        lock = (self.root / 'operation.lock').open('a+b')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close(); self.guard.release()
            raise RuntimeError('另一个抢救进程正在操作')
        self.job = {'state': 'running', 'action': action, 'ref': ref, 'started_at': time.time()}
        try:
            self.log_path.write_text('')
            self.phase('queued')
            threading.Thread(target=self.execute, args=(action, ref, lock), daemon=True).start()
        except Exception:
            lock.close(); self.guard.release(); raise
        return dict(self.job)

    def execute(self, action, ref, lock):
        try:
            self.phase(action)
            if action == 'update':
                self.update(ref)
            elif action == 'rollback':
                self.rollback()
            else:
                self.service(action)
            self.job['state'] = 'complete'
        except Exception as error:
            self.job.update(state='failed', error=str(error))
        finally:
            try:
                self.phase('finished')
            finally:
                lock.close(); self.guard.release()

    def switch(self, target):
        current = Path(self.config['release_dir']).resolve() / 'current'
        temporary = current.with_name('current.next')
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, current)

    def healthy(self):
        for _ in range(25):
            try:
                with urlopen(self.config['probe_url'], timeout=2) as response:
                    data = json.load(response)
                if data.get('ok') and data.get('transport') == 'pty':
                    return
            except (OSError, ValueError):
                pass
            time.sleep(1)
        raise RuntimeError('主服务未通过 PTY 健康检查')

    def update(self, ref):
        repo = Path(self.config['repository']).resolve()
        if self.command(['git', 'status', '--porcelain'], cwd=repo):
            raise RuntimeError('源工作区有未提交改动；已拒绝覆盖')
        self.phase('resolving_version')
        remotes = self.command(['git', 'remote'], cwd=repo).splitlines()
        if 'origin' in remotes:
            self.command(['git', 'fetch', 'origin'], cwd=repo, timeout=120)
        commit = self.command(['git', 'rev-parse', '--verify', ref + '^{commit}'], cwd=repo)
        releases = Path(self.config['release_dir']).resolve()
        current = releases / 'current'
        if not current.is_symlink():
            raise RuntimeError('主服务未配置 releases/current；请先安装托管启动路径')
        previous = str(current.resolve())
        candidate = releases / (commit[:12] + '-' + str(time.time_ns()))
        candidate.mkdir(parents=True)
        self.phase('preparing_candidate')
        archive = self.runner(['git', 'archive', '--format=tar', commit], cwd=repo, capture_output=True, timeout=30)
        if archive.returncode:
            raise RuntimeError('无法生成候选源码归档')
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
            for member in bundle.getmembers():
                dest = (candidate / member.name).resolve()
                if not dest.is_relative_to(candidate) or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise RuntimeError('发布包包含不支持的路径或链接')
            bundle.extractall(candidate)
        python = candidate / '.venv/bin/python'
        self.command([sys.executable, '-m', 'venv', str(candidate / '.venv')], timeout=90)
        dependencies = candidate / 'requirements.lock'
        if not dependencies.is_file():
            dependencies = candidate / 'requirements.txt'
        self.command([str(python), '-m', 'pip', 'install', '-r', str(dependencies)], timeout=300)
        self.command([str(python), '-m', 'compileall', '-q', str(candidate / 'src')])
        self.command([str(python), '-c', 'import sys; sys.path.insert(0, "src"); from codexws_server.bootstrap import create_server'], cwd=candidate)
        # Persist rollback intent before stopping or changing the running version.
        atomic_json(self.root / 'rollback.json', {'previous': previous, 'candidate': str(candidate), 'commit': commit})
        self.phase('switching_version')
        self.service('stop')
        try:
            self.switch(candidate)
            self.service('start')
            self.healthy()
        except Exception:
            self.phase('restoring_previous')
            self.service('stop')
            self.switch(previous)
            self.service('start')
            raise
        self.job['commit'] = commit

    def rollback(self):
        record = json.loads((self.root / 'rollback.json').read_text())
        previous = Path(record['previous'])
        if not (previous / 'run.sh').is_file():
            raise RuntimeError('回退目录不可用')
        self.service('stop')
        self.switch(previous)
        self.service('start')
        self.healthy()


def handler_for(manager, token, origin):
    class Handler(BaseHTTPRequestHandler):
        def json_response(self, code, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code); self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)

        def authorized(self, mutation=False):
            expected_host = urlsplit(origin).netloc
            supplied = self.headers.get('Authorization', '').removeprefix('Bearer ')
            return (self.headers.get('Host') == expected_host
                    and hmac.compare_digest(supplied, token)
                    and (not mutation or self.headers.get('Origin') == origin))

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/':
                raw = Path(__file__).with_name('index.html').read_bytes()
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store'); self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
                return
            if not self.authorized():
                self.json_response(403, {'error': '管理令牌或访问地址不匹配'}); return
            try:
                if path == '/api/status': self.json_response(200, manager.snapshot())
                elif path == '/api/logs': self.json_response(200, manager.logs())
                else: self.json_response(404, {'error': 'Not found'})
            except Exception as error:
                self.json_response(503, {'error': str(error)})

        def do_POST(self):
            if not self.authorized(mutation=True):
                self.json_response(403, {'error': '管理令牌或来源不匹配'}); return
            try:
                if self.path != '/api/action':
                    self.json_response(404, {'error': 'Not found'}); return
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 4096:
                    raise ValueError('请求大小无效')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict): raise ValueError('请求必须是对象')
                job = manager.submit(body.get('action'), body.get('ref') or 'HEAD')
                self.json_response(202, job)
            except (ValueError, TypeError) as error:
                self.json_response(400, {'error': str(error)})
            except Exception as error:
                self.json_response(409, {'error': str(error)})

        def log_message(self, fmt, *args):
            pass  # Do not log auth headers or query strings.
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    token = Path(config['token_file']).read_text().strip()
    if len(token) < 24: raise ValueError('Use a random management token of at least 24 characters')
    origin = config.get('origin', 'http://127.0.0.1:51438')
    port = urlsplit(origin).port or 51438
    server = ThreadingHTTPServer((config.get('bind', '127.0.0.1'), port), handler_for(Manager(config), token, origin))
    server.serve_forever()


if __name__ == '__main__':
    main()
