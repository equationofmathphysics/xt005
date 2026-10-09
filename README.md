# xt005 · PTY Workspace

xt005 是 Linux 本地工作区与常驻终端网页。一个 Web worker 持有 PTY 与子进程，
浏览器通过 Socket.IO 连接终端。主服务不需要 Codex 账号或安装任何 agent。

## 功能与生命周期

- 工作区注册、排序、置顶，以及文件查看、编辑、上传与下载。
- 多终端、ANSI 屏幕、中文、窗口调整、断线重连与终端大屏。
- 浏览器关闭或切换工作区后，终端继续运行；“卸载”结束对应终端及其子进程。
- 主服务停止/重启会结束全部终端。再次启动恢复普通 shell 标签，不自动唤醒历史 agent 会话。
- 可选 Codex CLI 新建/恢复入口，直接在 PTY 执行 `codex --no-daemon` 或
  `codex resume --no-daemon <id>`。缺少该 CLI 不影响普通终端、文件和历史查看。
- 本地 Codex JSONL 历史分页只读查看，保留标题、重要标记、排序与浏览器草稿。
- 独立抢救页支持状态、日志、启动、停止、重启、版本更新和回退。

终端输出不能可靠证明模型任务是否完成；无可靠状态时显示“未知”。
模型、权限、审批和其他 agent 功能在终端程序中操作。网页不模拟结构化审批或自动发送草稿。

## 安装与运行

需要 Linux、Python 3.10+、venv；安装常驻服务另需 systemd 用户会话。

```bash
./install.sh --no-start
# 审核配置后启动
systemctl --user start xt005 xt005-rescue
```

`./install.sh` 不带参数会安装并重启两个服务。默认主页面
`http://127.0.0.1:51437/`，抢救页 `http://127.0.0.1:51438/`。
抢救页令牌位于 `~/.config/xt005/rescue-token`，不会保存在浏览器存储中。
部署及更新步骤见 [INSTALL.md](INSTALL.md)。

从源码临时运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
./run.sh
```

主服务具有本机 shell 能力，默认只监听 loopback。远程访问使用 SSH 转发，
或经过认证的可信反向代理；不要直接暴露端口。

## 开发

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONPATH="$PWD/src:$PWD" .venv/bin/python -m unittest discover -v
./build-source-package.sh
```

浏览器测试需要 Node.js、Playwright 与 Chromium；缺少时相关用例跳过。
可以用 `NODE_PATH` 指向已有 Playwright 安装。

- [当前架构与迁移记录](docs/architecture-proposal.md)
- [HTTP / Socket.IO 协议](docs/api/README.md)
- [交互和数据边界](docs/interaction-model.md)
- [完成项与后续工作](TODO.md)
- [可选语音模块](voice_input/README.md)

## License

本仓库使用 [WH Covenant Public License 1.0](LICENSE)，与
`equationofmathphysics/longtail` 保持一致。第三方前端库的许可证见
`frontend/assets/vendor/` 和 `.cdnlocal/`。
