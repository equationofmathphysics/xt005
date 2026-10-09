# 安装、切换和恢复

## 安装

依赖：Linux、Python 3.10+（含 venv）、systemd 用户会话；首次安装需下载锁定的 Python 包。
Codex CLI 是可选程序，普通终端不需要它。

```bash
./install.sh --no-start
systemctl --user start xt005 xt005-rescue
```

不带 `--no-start` 的安装命令会重启两个服务。仓库的 `agents.md` 要求执行真实服务重启前取得授权。
安装器保留已有配置和运行数据；默认路径：

| 内容 | 路径 |
| --- | --- |
| 主服务环境 | `~/.config/xt005/env` |
| 工作区、历史元数据和偏好 | `~/.local/state/xt005/` |
| 主服务启动指针 | `~/.local/state/xt005/releases/current` |
| 独立抢救程序 | `~/.local/share/xt005-rescue/` |
| 抢救配置 / 令牌 | `~/.config/xt005/rescue.json` / `rescue-token` |
| 抢救任务、日志和回退记录 | `~/.local/state/xt005/rescue/` |
| 两个独立 user unit | `~/.config/systemd/user/xt005{,-rescue}.service` |

安装器首次将启动指针指向当前源码目录，更新器之后切换至独立发布目录。
重新执行安装器会将指针重新指向该安装器所在目录。
可用 `XT005_CONFIG_DIR`、`XT005_STATE_DIR`、`XT005_SYSTEMD_USER_DIR`、
`XT005_RESCUE_DIR` 自定义安装路径；首次主服务地址通过 `XT005_HOST`、
`XT005_PORT` 设置，初始工作区通过 `XT005_WORKSPACE` 设置。

## 配置

```dotenv
HOST=127.0.0.1
PORT=51437
DEFAULT_WORKSPACE=/absolute/workspace
WORKSPACES_FILE=/absolute/state/workspaces.json
CODEX_HISTORY_FILE=/absolute/state/codex_history.json
APP_PREFERENCES_FILE=/absolute/state/app-preferences.json
# 以下仅用于可选 CLI 和本地历史
CODEX_HOME=/absolute/.codex
CODEX_COMMAND=/absolute/bin/codex
```

一个 Gunicorn worker 持有终端，不能扩展为多个 worker。
`run.sh` 优先使用 `PYTHON_BIN`，否则使用项目 `.venv/bin/python`。
显式代理变量优先，其次读取 GNOME 手动代理，最后沿用本项目原有的本地代理默认值；
终端子进程继承这些环境变量。

抢救配置首次生成后不会被安装器覆盖。修改主服务地址时同步修改
`rescue.json` 的 `probe_url`。抢救页通过配置中的精确 `origin` 访问，
必须匹配浏览器地址（包括端口）；反向代理或 SSH 转发变更地址时相应更新。

## 抢救页

默认打开 `http://127.0.0.1:51438/`，手动输入 `rescue-token` 文件内容。
该页由独立 Python 标准库服务提供，不导入主应用、不依赖主应用 venv。
主服务失败或停止时仍可管理它。管理令牌、Host 与写操作 Origin 均会检查。

- 启动 / 停止 / 重启：操作固定的 `main_unit`，没有任意命令接口。
- 更新：填写已存在的 Git ref，例如 `HEAD` 或 `origin/main`。有 origin 时先 fetch；
  `HEAD` 表示源码目录当前提交，不会自动选择远程最新版本。
- 更新前拒绝 dirty 源目录；导出提交至新目录，建立 venv、安装锁定依赖、编译和导入检查。
- 写入回退记录后停止主服务、原子切换启动指针、启动并检测 `/api/check`。
  检测失败则尝试切回上一目录并启动，错误和日志保留。
- 页面关闭不会取消后台任务。抢救服务自身崩溃后将任务标为中断，可检查状态并手动回退。
- 回退按钮使用记录的上一目录；保留上一版本目录和其 venv。

首次从旧实现切换应先备份配置和状态，记录旧 Git 提交，然后运行安装器。
更新器的 PTY 健康检查用于新架构后续发布，不代替首次迁移部署。
抢救程序自身升级需重新运行其安装器，不随主服务发布目录切换。

## 生命周期与操作

```bash
systemctl --user status xt005 xt005-rescue
journalctl --user -u xt005 -n 100 --no-pager
systemctl --user restart xt005
```

浏览器断开保留终端；主服务停止会回收终端进程树。强制 SIGKILL 由 systemd
unit 的 `KillMode=mixed` 的最终 cgroup 清理 兜底。重新唤醒历史是重新运行 CLI 的 resume，
不是恢复原进程内存。页面“中断”发送 Ctrl-C；“卸载”结束进程。
