import os
import shlex
import subprocess
import threading
import time

from flask import jsonify, request

from ..codex_history_store import normalize_thread_id
from ..cli_adapter import launch_argv
from ..process_tree import terminal_info_usable


HISTORY_RESUME_RETRY_SECONDS = 15.0



def register_workspace_routes(app, deps):
    history_resume_lock = threading.Lock()

    def clamp_int(value, default, minimum, maximum):
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(number, maximum))

    def next_terminal_id(workspace_id, base_id):
        with deps.terminals_lock:
            existing_ids = {
                deps.split_terminal_key(key)[1]
                for key in deps.terminals
                if deps.split_terminal_key(key)[0] == workspace_id
            }
        terminal_id = base_id
        counter = 2
        while terminal_id in existing_ids:
            terminal_id = f"{base_id}-{counter}"
            counter += 1
        return terminal_id

    def terminal_summary_for_thread(workspace_id, thread_id):
        matches = [
            terminal
            for terminal in deps.workspace_terminal_summaries().get(workspace_id, [])
            if normalize_thread_id(terminal.get("thread_id")) == thread_id
        ]
        if not matches:
            return None
        matches.sort(key=lambda terminal: (
            not bool(terminal.get("codex_active")),
            not bool(terminal.get("usable")),
            str(terminal.get("id") or ""),
        ))
        return matches[0]

    @app.route("/api/workspaces", methods=["GET"])
    def list_workspaces_api():
        items = deps.workspace_list()
        return jsonify({"workspaces": items})

    @app.route("/api/workspaces/reorder", methods=["POST"])
    def reorder_workspaces_api():
        data = request.get_json(silent=True) or {}
        raw_order = data.get("order")
        if not isinstance(raw_order, list):
            return jsonify({"error": "顺序数据无效"}), 400
        with deps.workspace_lock:
            order = deps.set_workspace_order_locked(raw_order)
        return jsonify({
            "ok": True,
            "order": order,
            "workspaces": deps.workspace_list(),
        })

    @app.post("/api/workspaces/pin")
    def pin_workspace_api():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400
        if not isinstance(data.get("pinned"), bool):
            return jsonify({"error": "pinned 必须是布尔值"}), 400
        with deps.workspace_lock:
            pins = deps.set_workspace_pinned_locked(workspace_id, data["pinned"])
        return jsonify(
            {
                "ok": True,
                "pinned_workspaces": pins,
                "workspaces": deps.workspace_list(),
            }
        )


    @app.route("/api/workspace-terminals", methods=["GET"])
    def list_workspace_terminals_api():
        data = request.args
        workspace_id = deps.request_workspace_id(data)
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400
        return jsonify({
            "workspace": deps.workspace_item(workspace_id),
            "terminals": deps.workspace_terminal_summaries().get(workspace_id, []),
        })

    @app.route("/api/terminal-buffer", methods=["GET"])
    def terminal_buffer_api():
        data = request.args
        workspace_id = deps.request_workspace_id(data)
        terminal_id = deps.normalize_terminal_id(data.get("terminal_id") or "main")
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400

        key = deps.terminal_key(workspace_id, terminal_id)
        with deps.terminals_lock:
            info = deps.terminals.get(key)
        if not info:
            return jsonify({"error": "终端不存在"}), 404

        limit = clamp_int(data.get("limit"), deps.terminal_history_page_size, 4096, 131072)
        with info["lock"]:
            history = info.get("history_buffer", "") or ""
            history_start = int(info.get("history_start") or 0)
            history_end = int(info.get("history_end") or (history_start + len(history)))
            before = clamp_int(data.get("before"), history_end, history_start, history_end)
            rel_end = max(0, min(len(history), before - history_start))
            if data.get("after") is not None:
                after = clamp_int(data.get("after"), history_start, history_start, before)
                rel_start = max(0, min(rel_end, after - history_start))
                rel_end = min(rel_end, rel_start + limit)
            else:
                rel_start = max(0, rel_end - limit)
            page = history[rel_start:rel_end]
            page_start = history_start + rel_start
            page_end = history_start + rel_end

        return jsonify({
            "ok": True,
            "workspace_id": workspace_id,
            "terminal_id": terminal_id,
            "buffer": page,
            "start": page_start,
            "end": page_end,
            "history_start": history_start,
            "history_end": history_end,
            "has_more": page_start > history_start,
            "has_more_after": page_end < before,
        })

    @app.route("/api/terminal-screen", methods=["GET"])
    def terminal_screen_api():
        data = request.args
        workspace_id = deps.request_workspace_id(data)
        terminal_id = deps.normalize_terminal_id(data.get("terminal_id") or "main")
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400
        frame = deps.terminal_screen_frame(workspace_id, terminal_id=terminal_id)
        if not frame:
            return jsonify({"error": "终端屏幕不可用"}), 404
        return jsonify({"ok": True, "screen_frame_supported": True, **frame})

    @app.route("/api/workspace-terminals/create", methods=["POST"])
    def create_workspace_terminal_api():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400
        item = deps.workspace_item(workspace_id)
        if not item or not item.get("exists"):
            return jsonify({"error": f"工作区目录不存在: {(item or {}).get('cwd') or workspace_id}"}), 400

        requested_name = str(data.get("name") or "").strip()
        if data.get("thread_id"):
            return jsonify({
                "error": "History 恢复协议已更新，请刷新页面后重试",
            }), 409
        base_id = deps.normalize_terminal_id(data.get("id") or requested_name or "terminal")
        terminal_id = next_terminal_id(workspace_id, base_id)

        info = deps.ensure_terminal(
            workspace_id,
            terminal_id=terminal_id,
            name=requested_name or terminal_id,
            reopen=True,
        )
        if not info:
            return jsonify({"error": "终端启动失败"}), 500
        return jsonify({
            "ok": True,
            "workspace": deps.workspace_item(workspace_id),
            "terminal": deps.terminal_summary(deps.terminal_key(workspace_id, terminal_id), info),
        })

    @app.route("/api/workspace-terminals/resume", methods=["POST"])
    def resume_workspace_terminal_api():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400
        item = deps.workspace_item(workspace_id)
        if not item or not item.get("exists"):
            return jsonify({"error": f"工作区目录不存在: {(item or {}).get('cwd') or workspace_id}"}), 400

        thread_id = normalize_thread_id(data.get("thread_id"))
        if not thread_id:
            return jsonify({"error": "thread id 非法"}), 400
        with history_resume_lock:
            existing = terminal_summary_for_thread(workspace_id, thread_id)
            if existing and existing.get("usable") and (existing.get("managed") or existing.get("codex_active")):
                return jsonify({"ok": True, "created": False, "reused": True,
                                "resume_started": False, "workspace": item, "terminal": existing})
            try:
                command = launch_argv(item["cwd"], thread_id)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                return jsonify({"error": str(error)}), 409
            terminal_id = existing["id"] if existing and not existing.get("usable") else next_terminal_id(workspace_id, "session-" + thread_id[:12])
            info = deps.ensure_terminal(workspace_id, terminal_id=terminal_id,
                name=str(data.get("name") or terminal_id), thread_id=thread_id,
                command=command, reopen=True)
            if not info:
                return jsonify({"error": "终端启动失败或服务正在关闭"}), 503
            return jsonify({"ok": True, "created": existing is None, "reused": existing is not None,
                            "resume_started": True, "workspace": item,
                            "terminal": deps.terminal_summary(deps.terminal_key(workspace_id, terminal_id), info)})

    @app.post("/api/workspace-terminals/agent")
    def start_agent():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        item = deps.workspace_item(workspace_id)
        if not item or not item.get("exists"):
            return jsonify({"error": "工作区不存在"}), 400
        try:
            command = launch_argv(item["cwd"])
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            return jsonify({"error": str(error)}), 409
        with history_resume_lock:
            terminal_id = next_terminal_id(workspace_id, "agent")
            info = deps.ensure_terminal(workspace_id, terminal_id=terminal_id,
                                       command=command, name="Codex", reopen=True)
        if not info:
            return jsonify({"error": "服务正在关闭"}), 503
        return jsonify({"ok": True, "workspace": item,
                        "terminal": deps.terminal_summary(deps.terminal_key(workspace_id, terminal_id), info)})

    @app.post("/api/workspace-terminals/interrupt")
    def interrupt_terminal():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        terminal_id = deps.normalize_terminal_id(data.get("terminal_id"))
        key = deps.terminal_key(workspace_id, terminal_id)
        with deps.terminals_lock:
            info = deps.terminals.get(key)
        if not terminal_info_usable(info):
            return jsonify({"error": "终端未运行"}), 409
        # Ctrl-C is sent to the foreground application; acknowledgement is not completion.
        accepted = deps.write_terminal(workspace_id, b"\x03", terminal_id=terminal_id, expected_info=info)
        return jsonify({"ok": accepted, "status": "interruptRequested"}), 202 if accepted else 409

    @app.route("/api/workspace-terminals/close", methods=["POST"])
    def close_workspace_terminal_api():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        terminal_id = deps.normalize_terminal_id(data.get("terminal_id") or "main")
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400

        deps.close_terminal(workspace_id, terminal_id)
        workspaces = deps.workspace_list()
        workspace = next((item for item in workspaces if item.get("id") == workspace_id), deps.workspace_item(workspace_id))
        return jsonify({
            "ok": True,
            "workspace": workspace,
            "terminal_id": terminal_id,
        })

    @app.route("/api/workspace-terminals/rename", methods=["POST"])
    def rename_workspace_terminal_api():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        terminal_id = deps.normalize_terminal_id(data.get("terminal_id") or "main")
        name = str(data.get("name") or "").strip()
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400
        if not name:
            return jsonify({"error": "终端名称不能为空"}), 400
        if len(name) > 80:
            return jsonify({"error": "终端名称不能超过 80 个字符"}), 400
        if not deps.rename_terminal_session(workspace_id, terminal_id, name):
            return jsonify({"error": "终端不存在"}), 404

        key = deps.terminal_key(workspace_id, terminal_id)
        with deps.terminals_lock:
            info = deps.terminals.get(key)
            if info:
                info["name"] = name
        terminal = deps.terminal_summary(key, info) if info else {
            "id": terminal_id,
            "name": name,
            "workspace_id": workspace_id,
            "alive": False,
            "usable": False,
        }
        return jsonify({
            "ok": True,
            "workspace": deps.workspace_item(workspace_id),
            "terminal": terminal,
        })

    @app.route("/api/workspaces/register", methods=["POST"])
    def register_workspace_api():
        data = request.get_json(silent=True) or {}
        raw_path = data.get("path", "")
        path = deps.expand_workspace_path(raw_path)
        create = bool(data.get("create"))
        if not raw_path:
            return jsonify({"error": "请输入目录路径"}), 400
        if create:
            try:
                os.makedirs(path, exist_ok=True)
            except Exception as e:
                return jsonify({"error": f"创建目录失败: {e}"}), 400
        if not os.path.isdir(path):
            return jsonify({"error": "请选择一个已存在的文件夹，或使用新建目录模式"}), 400

        name = str(data.get("name") or os.path.basename(os.path.normpath(path)) or path).strip()
        requested_id = deps.normalize_workspace_id(data.get("id"))
        restored_default_id = ""

        with deps.workspace_lock:
            for existing_id, existing_path in deps.workspaces.items():
                if deps.expand_workspace_path(existing_path) == path:
                    item = deps.workspace_item(existing_id)
                    return jsonify({"ok": True, "existing": True, "workspace": item})

            default_workspace_id = deps.default_workspace_id_for_path(path)
            if default_workspace_id and deps.restore_default_workspace_locked(default_workspace_id):
                restored_default_id = default_workspace_id
                item = deps.workspace_item(default_workspace_id)
            else:
                if requested_id:
                    if deps.is_default_workspace_id(requested_id):
                        return jsonify({"error": "工作区 ID 已保留"}), 409
                    workspace_id = requested_id
                    if workspace_id in deps.workspaces:
                        return jsonify({"error": "工作区 ID 已存在"}), 409
                else:
                    base_id = deps.workspace_id_from_path(name, path)
                    workspace_id = base_id
                    counter = 2
                    while workspace_id in deps.workspaces:
                        workspace_id = f"{base_id}-{counter}"
                        counter += 1

                entry = {
                    "id": workspace_id,
                    "name": name or workspace_id,
                    "path": path,
                    "created": int(time.time()),
                }
                deps.custom_workspaces[workspace_id] = entry
                deps.rebuild_workspace_maps_locked()
                deps.save_workspaces_locked()

        if restored_default_id:
            deps.ensure_terminal(restored_default_id, reopen_workspace=True)
            return jsonify({"ok": True, "existing": True, "workspace": item})

        deps.ensure_terminal(workspace_id, reopen_workspace=True)
        return jsonify({"ok": True, "workspace": deps.workspace_item(workspace_id)})

    @app.route("/api/workspaces/close", methods=["POST"])
    def close_workspace_api():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400

        item = deps.workspace_item(workspace_id)
        deps.close_workspace_terminals(workspace_id)
        removed = False
        if item and item.get("source") == "default":
            with deps.workspace_lock:
                removed = deps.hide_default_workspace_locked(workspace_id)
        elif item:
            with deps.workspace_lock:
                deps.custom_workspaces.pop(workspace_id, None)
                deps.rebuild_workspace_maps_locked()
                deps.save_workspaces_locked()
            removed = True

        return jsonify({
            "ok": True,
            "removed": removed,
            "workspace_id": workspace_id,
            "workspaces": deps.workspace_list(),
        })

    @app.route("/api/workspace-agent/restart", methods=["POST"])
    def restart_workspace_agent_api():
        data = request.get_json(silent=True) or {}
        workspace_id = deps.request_workspace_id(data)
        if not deps.workspace_exists(workspace_id):
            return jsonify({"error": "未知工作区"}), 400
        if workspace_id not in deps.workspace_agent_commands:
            return jsonify({"error": "该工作区未配置后台 agent"}), 400

        session_id = str(data.get("thread_id", "")).strip()
        allowed_session_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        if session_id and any(ch not in allowed_session_chars for ch in session_id):
            return jsonify({"error": "session id 非法"}), 400

        command = deps.workspace_agent_commands[workspace_id]
        if session_id:
            command = f"{command} resume {shlex.quote(session_id)}"
        deps.ensure_workspace_agent(workspace_id, command_override=command, force_restart=True)
        return jsonify({"ok": True, "workspace_id": workspace_id, "agent_command": command})
