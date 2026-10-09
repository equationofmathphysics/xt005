# xt005 API contract

`openapi.yaml` 定义 HTTP 接口；`asyncapi.yaml` 定义 Socket.IO 终端协议。
HTTP 管理工作区、文件、历史和终端生命周期，Socket.IO 传输输入、窗口大小、
屏幕快照和增量输出。同步算法见 [terminal-sync-protocol.md](../terminal-sync-protocol.md)。

`GET /api/check` 检查 Web/PTY 服务是否已初始化，返回 `ok: true`、`transport: pty`。
它不调用任何 agent、账号或模型，也不表示模型请求成功。
终端状态、资源用量见 `GET /api/system-stats`。

`POST /api/workspace-terminals/create` 创建普通 shell；`/agent` 启动可选 CLI；
`/resume` 在工作区内恢复本地历史；`/interrupt` 请求 Ctrl-C；`/close` 结束终端。
相同会话的并发恢复复用既有可用终端。具体路径以 OpenAPI 为准。

`GET /api/codex-history/<thread_id>/read?workspace_id=...&cursor=...` 只读分页，
游标为 JSONL 字节边界，单条记录最多 2 MiB。未写完的尾行不消费，
未知记录以文本展示。工作区检查不通过返回错误，不自动启动会话。

抢救服务是另一进程和端口：`GET /api/status`、`GET /api/logs`、
`POST /api/action`；API 需要 Bearer 令牌，写操作要求匹配 Origin。
动作仅允许 `start`、`stop`、`restart`、`update`、`rollback`。
