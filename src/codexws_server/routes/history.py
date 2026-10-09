import os

from flask import jsonify, request

from ..codex_history_store import (
    history_entries,
    normalize_thread_id,
    update_history_entry,
)
from ..history_reader import read_page
from ..codex_threads import (
    codex_home_path,
    find_codex_thread,
    load_codex_native_index,
    parse_codex_rollout,
)


MAX_HISTORY_CONVERSATIONS = 200


def _truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _session_allowed_for_workspace(cwd, allowed_root):
    if not cwd or not allowed_root:
        return False
    return os.path.realpath(cwd) == os.path.realpath(allowed_root)


def _conversation_from_thread(thread, metadata):
    metadata = metadata or {}
    custom_title = metadata.get("title") or thread.get("native_title") or ""
    preview = thread.get("preview") or "Untitled"
    title = custom_title or preview
    timestamp = int(thread.get("timestamp") or 0)
    return {
        "threadId": thread["thread_id"],
        "forkedFromId": thread.get("forked_from_id") or None,
        "title": title,
        "customTitle": custom_title or None,
        "rawTitle": preview,
        "cwd": thread.get("cwd") or "",
        "timestamp": timestamp,
        "important": bool(metadata.get("important")),
        "archived": bool(metadata.get("archived")),
        "updated_at": int(metadata.get("updated_at") or 0),
    }


def _normalize_history_updates(data):
    updates = {}
    if "title" in data:
        updates["title"] = data.get("title")
    for key in ("important", "archived"):
        if key in data:
            updates[key] = bool(data.get(key))
    return updates


def register_history_routes(app, deps):
    @app.get("/api/codex-history/<thread_id>/read")
    def read_conversation(thread_id):
        root = deps.workspace_path(str(request.args.get("workspace_id") or ""))
        thread = find_codex_thread(thread_id)
        if not root or not thread or thread.get("is_subagent") or not _session_allowed_for_workspace(thread.get("cwd"), root):
            return jsonify({"error": "会话不存在或不属于此工作区"}), 404
        try:
            page = read_page(thread["path"], int(request.args.get("cursor", 0)))
        except (ValueError, OSError) as error:
            return jsonify({"error": str(error)}), 400
        return jsonify(dict(page, threadId=thread_id))

    @app.get("/api/codex-history/<thread_id>/location")
    def locate_codex_conversation(thread_id):
        thread_id = normalize_thread_id(thread_id)
        if not thread_id:
            return jsonify({"error": "thread id 非法"}), 400
        thread = find_codex_thread(thread_id)
        if not thread or thread.get("is_subagent"):
            return jsonify({"error": "找不到这个会话"}), 404
        thread_cwd = os.path.realpath(str(thread.get("cwd") or ""))
        workspace = next(
            (
                item
                for item in deps.workspace_list()
                if item.get("exists") is not False
                and os.path.realpath(str(item.get("cwd") or "")) == thread_cwd
            ),
            None,
        )
        if not workspace:
            return jsonify({"error": "会话所属工作区未注册"}), 404
        return jsonify(
            {
                "threadId": thread_id,
                "workspaceId": workspace["id"],
                "cwd": thread.get("cwd") or "",
            }
        )

    @app.route("/api/codex-history", methods=["GET"])
    def list_codex_conversations():

        codex_home = codex_home_path()
        codex_base = os.path.join(codex_home, "sessions")
        conversations = []
        workspace_id = str(request.args.get("workspace_id") or "").strip()
        selected_workspace = deps.workspace_path(workspace_id)
        include_archived = _truthy(request.args.get("include_archived"))
        if not selected_workspace:
            return jsonify({"conversations": []})
        allowed_root = os.path.realpath(selected_workspace)

        if not os.path.isdir(codex_base):
            return jsonify({"conversations": []})

        metadata_by_thread = history_entries()
        native_index = load_codex_native_index(codex_home)
        threads_by_id = {}
        for root, _, files in os.walk(codex_base):
            for filename in files:
                if not filename.endswith(".jsonl"):
                    continue
                filepath = os.path.join(root, filename)
                thread = parse_codex_rollout(filepath)
                if not thread:
                    continue

                thread_id = thread["thread_id"]
                indexed = native_index.get(thread_id) or {}
                if indexed.get("title"):
                    thread["native_title"] = indexed["title"]
                thread["timestamp"] = max(
                    int(thread.get("timestamp") or 0),
                    int(indexed.get("timestamp") or 0),
                )
                previous = threads_by_id.get(thread_id)
                if previous and int(previous.get("timestamp") or 0) > thread["timestamp"]:
                    continue
                threads_by_id[thread_id] = thread

        for thread in threads_by_id.values():
            if thread.get("is_subagent"):
                continue
            thread_id = thread["thread_id"]
            metadata = metadata_by_thread.get(thread_id) or {}
            if not _session_allowed_for_workspace(thread.get("cwd"), allowed_root):
                continue
            if metadata.get("archived") and not include_archived:
                continue
            conversations.append(_conversation_from_thread(thread, metadata))

        conversations.sort(
            key=lambda item: int(item.get("timestamp") or 0),
            reverse=True,
        )
        offset = max(0, request.args.get("offset", default=0, type=int))
        total = len(conversations)
        conversations = conversations[offset:offset + MAX_HISTORY_CONVERSATIONS]
        next_offset = offset + len(conversations)
        return jsonify({"conversations": conversations, "nextOffset": next_offset if next_offset < total else None})

    @app.route("/api/codex-history/<thread_id>", methods=["PATCH", "POST"])
    def update_codex_conversation(thread_id):
        thread_id = normalize_thread_id(thread_id)
        if not thread_id:
            return jsonify({"error": "thread id 非法"}), 400
        data = request.get_json(silent=True) or {}
        updates = _normalize_history_updates(data)
        if not updates:
            return jsonify({"error": "没有可更新字段"}), 400
        metadata = update_history_entry(thread_id, updates)
        return jsonify({"ok": True, "metadata": metadata})
