import os
import time
from datetime import datetime

from flask import jsonify, request


def register_system_routes(app, deps):
    @app.route("/api/check", methods=["GET"])
    def check():
        return jsonify({"ok": True, "transport": "pty", "checks": {"web": "ready", "terminals": "available"}})

    @app.route("/api/fs-dirs", methods=["GET"])
    def list_system_dirs_api():
        raw_path = request.args.get("path", "") or os.path.expanduser("~")
        path = deps.expand_workspace_path(raw_path)
        if not os.path.isdir(path):
            path = os.path.dirname(path)
        if not path or not os.path.isdir(path):
            path = os.path.expanduser("~")
        try:
            entries = []
            for name in sorted(os.listdir(path)):
                if name.startswith("."):
                    continue
                full = os.path.join(path, name)
                if os.path.isdir(full):
                    try:
                        stat = os.stat(full)
                        modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                    except OSError:
                        modified = ""
                    entries.append({
                        "name": name,
                        "path": os.path.realpath(full),
                        "modified": modified,
                    })
            parent = None
            parent_path = os.path.dirname(path)
            if parent_path and parent_path != path:
                parent = os.path.realpath(parent_path)
            return jsonify({
                "path": os.path.realpath(path),
                "parent": parent,
                "dirs": entries,
            })
        except PermissionError:
            return jsonify({"error": "无权限访问该目录"}), 403
        except OSError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/system-stats", methods=["GET"])
    def system_stats():
        mem = deps.read_meminfo()
        with deps.terminals_lock:
            terminal_items = list(deps.terminals.items())
        terminal_snapshot = {
            key: deps.terminal_summary(key, info)
            for key, info in terminal_items
        }
        terminal_counts = {
            "total": len(terminal_snapshot),
            "active": sum(1 for item in terminal_snapshot.values() if item.get("alive") and item.get("usable")),
        }
        terminals_by_workspace = {}
        for key, summary in terminal_snapshot.items():
            workspace_id, _ = deps.split_terminal_key(key)
            terminals_by_workspace.setdefault(workspace_id, []).append(summary)
        for items in terminals_by_workspace.values():
            items.sort(key=lambda item: (item["id"] != deps.default_terminal, item["name"]))
        observations_by_workspace = deps.workspace_observation_summaries()
        return jsonify({
            "cpu_percent": deps.current_cpu_percent(),
            "memory": mem,
            "process": {"rss": deps.process_rss_bytes()},
            "terminals": terminal_snapshot,
            "terminal_counts": terminal_counts,
            "terminals_by_workspace": terminals_by_workspace,
            "observations_by_workspace": observations_by_workspace,
            "observations": deps.terminal_observation_summaries(),
            "timestamp": int(time.time()),
        })
