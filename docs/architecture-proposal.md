# PTY 架构与迁移记录

2026-10-08：主服务已改为直接持有 PTY。迁移前实现和计划分别保存于
`5fbb4b0`、`4f783a9`。本次删除 Codex app-server 客户端、运行时、RPC 路由、
专属协议测试与部署维护脚本；没有保留备用后端。

```text
浏览器 xterm.js ── Socket.IO ── Web worker ── PTY ── shell / CLI
浏览器工作区页 ── HTTP ───────────┤
                               └─ 文件、注册表、本地只读历史

独立抢救网页 ── HTTP + token ── 标准库抢救服务 ── systemd user unit
                                                └─ 候选发布目录 / 当前指针 / 回退记录
```

## 主服务

- 一个 Gunicorn worker 拥有运行时内存和全部终端，不支持多 worker 部署。
- `terminal_runtime.py` 管理终端实例、生命周期锁、I/O 队列、Socket.IO 事件与监督线程。
- `pty_child.py` 在独立会话中取得控制终端再 exec；避免在多线程服务中使用 preexec_fn。
- `terminal_screen.py`、`terminal_history.py`、`socket_protocol.py` 管理 ANSI 屏幕、
  有界历史、epoch/sequence 对齐。恢复旧终端实现时保留了对应回归用例。
- 终端启动和停止串行化；服务停止先禁止新建，再回收进程树。浏览器连接不拥有进程。
- 可选 `cli_adapter.py` 只构造 CLI argv，检查独立模式与会话工作区；默认路径是普通 shell。
- 当前运行时仍集中处理 I/O 和 Socket.IO；进一步拆分适配层属于后续内部重构。

## 兼容边界

保留当前工作区存储、排序/置顶、文件接口、标题/重要元数据和草稿键。
原始会话文件不重写。主服务不以账号健康作为启动或网页可用条件。
旧偏好、fork 元数据保留在磁盘，不强行映射为新 CLI 参数。结构化审批、turn 状态、
自动 fork 管理改由 CLI 自身处理；终端界面没有可靠完成信号时显示未知。

## 独立恢复

抢救服务使用系统 Python 标准库、独立目录和独立 user unit，无主服务启动依赖。
它只允许固定 systemd 操作，采用令牌、精确 Host/Origin 校验与单任务文件锁。
更新准备发生在新发布目录，先安装依赖和检查，再停止主服务并切换启动指针。
失败自动尝试回退，回退意图先持久化；自身中断后保留人工恢复入口。

它不能修复宿主机/systemd/系统 Python 本身故障，也不自动升级自身。
首次迁移需安装新 unit/启动指针并在获授权后重启真实服务。

## 验证范围

覆盖 PTY 控制终端、Unicode、resize、浏览器断开/重连、显式退出、顽固子进程清理、
历史分页、工作区保留、幂等 resume、草稿与浏览器屏幕同步。
独立抢救 API 在主服务不可用情况下验证访问控制；更新流程通过受控 systemctl 替身验证
并发排斥、dirty 工作区拒绝、失败切回。真实 systemd unit 切换需部署授权后验收。

### 本轮验证结果

- 锁定依赖环境全量 `unittest discover -v`：174 项通过，无跳过。
- 包含真实 Gunicorn + Chromium 的终端、重连、中文、草稿、离线历史、置顶、
  缺少可选 CLI 场景；模型请求未发起。
- 包含真实 PTY 与残留后台子进程回收，以及安装器临时目录/systemctl 替身测试。
- `git diff --check`、shell 语法检查通过；生成 `xt005-3.0.0-source.tar.gz`。
- 实际服务未重启；真实 systemd 切换与已登录 CLI 恢复仍留在部署验收清单中。
