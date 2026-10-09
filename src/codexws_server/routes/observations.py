from flask import jsonify, request


def register_observation_routes(app, deps):
    @app.route("/api/workspace-observations", methods=["GET"])
    def list_workspace_observations_api():
        workspace_id = request.args.get("workspace_id") or ""
        workspaces = deps.workspace_observation_summaries()
        terminals_snapshot = deps.terminal_observation_summaries()
        if workspace_id:
            return jsonify({
                "workspace": workspaces.get(workspace_id, {
                    "workspace_id": workspace_id,
                    "unread_hooks": 0,
                    "terminals": [],
                    "recent_hook_events": [],
                }),
                "terminals": {
                    key: value for key, value in terminals_snapshot.items()
                    if deps.split_terminal_key(key)[0] == workspace_id
                },
            })
        return jsonify({"workspaces": workspaces, "terminals": terminals_snapshot})

    @app.route("/api/workspace-observations/read", methods=["POST"])
    def mark_workspace_observations_read_api():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        terminal_id = data.get("terminal_id")
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400
        target_terminal = deps.normalize_terminal_id(terminal_id) if terminal_id else None
        with deps.observations_lock:
            for key, obs in deps.workspace_observations.items():
                key_workspace_id, key_terminal_id = deps.split_terminal_key(key)
                if key_workspace_id != workspace_id:
                    continue
                if target_terminal and key_terminal_id != target_terminal:
                    continue
                obs["unread_hooks"] = 0
        return jsonify({"ok": True, "workspace_id": workspace_id, "terminal_id": target_terminal})

    @app.route("/api/hooks/report", methods=["POST"])
    def report_hook_event_api():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        terminal_id = deps.normalize_terminal_id(data.get("terminal_id") or deps.default_terminal)
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400
        status = str(data.get("status") or "done").strip().lower() or "done"
        if status not in ("done", "failed", "running", "info"):
            status = "done"
        event = deps.record_hook_event(
            workspace_id,
            terminal_id,
            status=status,
            title=str(data.get("title") or "hook").strip() or "hook",
            message=str(data.get("message") or "").strip(),
            source="codex-hook" if data.get("source") == "codex-hook" else "api",
            session_id=str(data.get("session_id") or "").strip(),
            turn_id=str(data.get("turn_id") or "").strip(),
        )
        return jsonify({"ok": True, "event": event})
