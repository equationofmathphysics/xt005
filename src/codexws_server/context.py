import atexit
import threading
from dataclasses import dataclass, field
from typing import Callable

from .services import TerminalRuntime


@dataclass
class ApplicationContext:
    """Owns the one shared backend runtime and its process lifecycle."""

    terminal_runtime: TerminalRuntime
    workspace_list: Callable[[], list[dict]]
    _started: bool = field(default=False, init=False, repr=False)
    _atexit_registered: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def started(self):
        with self._lock:
            return self._started

    def start(self):
        with self._lock:
            if self._started:
                return False
            try:
                self.terminal_runtime.start_workspace_terminals()
                self.terminal_runtime.start_workspace_supervisor()
                self.terminal_runtime.start_workspace_tcp_servers()
                if not self._atexit_registered:
                    atexit.register(self.stop)
                    self._atexit_registered = True
            except Exception:
                self._cleanup_runtime()
                raise
            self._started = True
            return True

    def stop(self):
        with self._lock:
            if not self._started:
                return False
            self._started = False
            self._cleanup_runtime()
            return True

    def _cleanup_runtime(self):
        try:
            self.terminal_runtime.stop_workspace_supervisor()
        finally:
            try:
                self.terminal_runtime.close_workspace_tcp_servers()
            finally:
                self.terminal_runtime.cleanup_all_terminals()
