"""Stop owned terminals before Gunicorn waits for long-lived terminal connections to finish."""

import sys
import threading

from gunicorn.workers.gthread import ThreadWorker


class GracefulThreadWorker(ThreadWorker):
    _drain_thread = None

    def handle_exit(self, sig, frame):
        super().handle_exit(sig, frame)
        module = sys.modules.get("codexws_server.wsgi")
        context = getattr(module, "context", None)
        if context is not None and self._drain_thread is None:
            self._drain_thread = threading.Thread(
                target=context.stop,
                name="terminal-shutdown", daemon=True,
            )
            self._drain_thread.start()

    def run(self):
        try:
            super().run()
        finally:
            if self._drain_thread is not None:
                self._drain_thread.join(timeout=36)
