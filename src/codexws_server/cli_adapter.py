"""Optional Codex CLI launch adapter. The terminal service itself needs no agent."""
import os
import shutil
import subprocess
from functools import lru_cache

from .config import CODEX_COMMAND
from .codex_threads import find_codex_thread


@lru_cache(maxsize=8)
def _supports_standalone(binary, mtime):
    result = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=5)
    return result.returncode == 0 and "--no-daemon" in result.stdout


def launch_argv(workspace, thread_id=None):
    binary = shutil.which(CODEX_COMMAND)
    if not binary:
        raise ValueError("未安装 Codex CLI；通用终端仍可使用")
    if not _supports_standalone(binary, os.stat(binary).st_mtime_ns):
        raise ValueError("当前 CLI 不支持 --no-daemon，请在通用终端中自行选择程序")
    if thread_id:
        thread = find_codex_thread(thread_id)
        if not thread or thread.get("is_subagent") or os.path.realpath(thread.get("cwd") or "") != os.path.realpath(workspace):
            raise ValueError("会话不存在或不属于此工作区")
        return [binary, "resume", "--no-daemon", thread_id]
    return [binary, "--no-daemon"]
