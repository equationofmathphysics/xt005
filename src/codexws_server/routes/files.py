import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime

from flask import jsonify, request, send_file


MAX_FILE_PREVIEW_BYTES = 10 * 1024 * 1024


def register_file_routes(app, deps):
    def workspace_id_from(source):
        workspace_id = str(source.get("workspace_id") or "").strip()
        return workspace_id or None

    def open_folder_in_file_manager(abs_dir):
        if sys.platform == "win32":
            os.startfile(abs_dir)  # type: ignore[attr-defined]
            return None
        if sys.platform == "darwin":
            command = ["open", abs_dir]
        else:
            command = None
            for name in ("gio", "xdg-open", "kde-open", "exo-open"):
                executable = shutil.which(name)
                if not executable:
                    continue
                command = [executable, "open", abs_dir] if name == "gio" else [executable, abs_dir]
                break
            if not command:
                return "未找到可用的资源管理器命令"

        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        threading.Thread(
            target=process.wait,
            name="codexws-folder-launcher-reaper",
            daemon=True,
        ).start()
        return None

    @app.route("/api/workspace-files", methods=["GET"])
    def list_workspace_files():
        workspace_id = workspace_id_from(request.args)
        if not workspace_id:
            return jsonify({"error": "缺少 workspace_id"}), 400
        rel_dir = request.args.get("dir", "")
        root, abs_dir = deps.safe_workspace_path(workspace_id, rel_dir)
        if not root:
            return jsonify({"error": "未知工作区"}), 400
        if not abs_dir or not os.path.isdir(abs_dir):
            return jsonify({"error": "目录不存在"}), 404
        rel_dir = os.path.relpath(abs_dir, root)
        if rel_dir == ".":
            rel_dir = ""
        parent_dir = None
        if abs_dir != root:
            parent_dir = os.path.relpath(os.path.dirname(abs_dir), root)
            if parent_dir == ".":
                parent_dir = ""
        items = []
        try:
            for name in sorted(os.listdir(abs_dir)):
                if name.startswith("."):
                    continue
                full = os.path.join(abs_dir, name)
                rel = os.path.relpath(full, root)
                is_dir = os.path.isdir(full)
                stat = os.stat(full)
                items.append({
                    "name": name,
                    "path": rel,
                    "is_dir": is_dir,
                    "size": stat.st_size if not is_dir else 0,
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
        except PermissionError:
            return jsonify({"error": "无权限"}), 403
        return jsonify({
            "files": items,
            "current_dir": rel_dir,
            "parent_dir": parent_dir,
            "workspace_id": workspace_id,
        })

    @app.route("/api/workspace-files/read", methods=["GET"])
    def read_workspace_file():
        workspace_id = workspace_id_from(request.args)
        if not workspace_id:
            return jsonify({"error": "缺少 workspace_id"}), 400
        rel_path = request.args.get("path", "")
        _, abs_path = deps.safe_workspace_path(workspace_id, rel_path)
        if not abs_path or not os.path.isfile(abs_path):
            return jsonify({"error": "文件不存在"}), 404
        try:
            stat = os.stat(abs_path)
            if stat.st_size > MAX_FILE_PREVIEW_BYTES:
                return jsonify({
                    "error": "文件超过 10 MiB，只支持下载",
                    "code": "file_too_large",
                    "size": stat.st_size,
                    "max_size": MAX_FILE_PREVIEW_BYTES,
                }), 413
            with open(abs_path, "rb") as f:
                raw_content = f.read(MAX_FILE_PREVIEW_BYTES + 1)
            if len(raw_content) > MAX_FILE_PREVIEW_BYTES:
                current_size = os.stat(abs_path).st_size
                return jsonify({
                    "error": "文件超过 10 MiB，只支持下载",
                    "code": "file_too_large",
                    "size": max(current_size, len(raw_content)),
                    "max_size": MAX_FILE_PREVIEW_BYTES,
                }), 413
            content = raw_content.decode("utf-8", errors="replace")
            return jsonify({
                "content": content,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/workspace-files/write", methods=["POST"])
    def write_workspace_file():
        data = request.get_json(silent=True) or {}
        workspace_id = workspace_id_from(data)
        if not workspace_id:
            return jsonify({"error": "缺少 workspace_id"}), 400
        rel_path = str(data.get("path") or "").strip()
        if not rel_path:
            return jsonify({"error": "缺少文件路径"}), 400
        content = data.get("content", "")
        _, abs_path = deps.safe_workspace_path(workspace_id, rel_path)
        if not abs_path:
            return jsonify({"error": "非法路径"}), 400
        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)
            stat = os.stat(abs_path)
            return jsonify({
                "ok": True,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/workspace-files/create", methods=["POST"])
    def create_workspace_file():
        data = request.get_json(silent=True) or {}
        workspace_id = workspace_id_from(data)
        if not workspace_id:
            return jsonify({"error": "缺少 workspace_id"}), 400
        rel_path = str(data.get("path") or "").strip()
        if not rel_path:
            return jsonify({"error": "请输入名称"}), 400
        is_dir = bool(data.get("is_dir", False))
        _, abs_path = deps.safe_workspace_path(workspace_id, rel_path)
        if not abs_path:
            return jsonify({"error": "非法路径"}), 400
        if os.path.exists(abs_path):
            return jsonify({"error": "已存在"}), 409
        try:
            if is_dir:
                os.makedirs(abs_path, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write("")
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/workspace-files/download", methods=["GET"])
    def download_workspace_file():
        workspace_id = workspace_id_from(request.args)
        if not workspace_id:
            return jsonify({"error": "缺少 workspace_id"}), 400
        rel_path = request.args.get("path", "")
        _, abs_path = deps.safe_workspace_path(workspace_id, rel_path)
        if not abs_path or not os.path.isfile(abs_path):
            return jsonify({"error": "文件不存在"}), 404
        return send_file(
            abs_path,
            as_attachment=True,
            download_name=os.path.basename(abs_path),
            conditional=True,
            etag=True,
            last_modified=os.path.getmtime(abs_path),
        )

    @app.route("/api/workspace-files/upload", methods=["POST"])
    def upload_workspace_file():
        workspace_id = workspace_id_from(request.form)
        if not workspace_id:
            return jsonify({"error": "缺少 workspace_id"}), 400
        rel_dir = request.form.get("dir", "")
        root, abs_dir = deps.safe_workspace_path(workspace_id, rel_dir)
        if not root:
            return jsonify({"error": "未知工作区"}), 400
        if not abs_dir:
            return jsonify({"error": "非法目录"}), 400
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "请选择一个文件"}), 400
        filename = os.path.basename((file.filename or "").replace("\\", "/")).strip()
        if not filename or filename in (".", ".."):
            return jsonify({"error": "文件名无效"}), 400
        target_path = os.path.realpath(os.path.join(abs_dir, filename))
        if os.path.commonpath([root, target_path]) != root:
            return jsonify({"error": "非法路径"}), 400
        try:
            os.makedirs(abs_dir, exist_ok=True)
            file.save(target_path)
            rel_path = os.path.relpath(target_path, root)
            stat = os.stat(target_path)
            return jsonify({
                "ok": True,
                "path": rel_path,
                "name": filename,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/workspace-files/open-folder", methods=["POST"])
    def open_workspace_folder():
        data = request.get_json(silent=True) or {}
        workspace_id = workspace_id_from(data)
        if not workspace_id:
            return jsonify({"error": "缺少 workspace_id"}), 400
        rel_dir = data.get("dir", "")
        root, abs_dir = deps.safe_workspace_path(workspace_id, rel_dir)
        if not root:
            return jsonify({"error": "未知工作区"}), 400
        if not abs_dir:
            return jsonify({"error": "非法目录"}), 400
        if not os.path.isdir(abs_dir):
            return jsonify({"error": "目录不存在"}), 404
        try:
            error = open_folder_in_file_manager(abs_dir)
            if error:
                return jsonify({"error": error}), 500
            return jsonify({"ok": True, "path": abs_dir})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
