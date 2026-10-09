# Doubao Voice Input

独立的豆包/火山引擎语音输入模块。它负责全局热键、麦克风录音、ASR
转写和粘贴，不依赖 codexws 主服务。

## 安装

系统需要 Python 3.10+、PortAudio 和桌面剪贴板工具：

```bash
sudo apt install python3-pip libportaudio2 xclip xdotool
cd voice_input
./install.sh
```

安装脚本把 Python 依赖放在本目录的 `.deps/`，并生成
`~/.config/systemd/user/doubao-voice-input.service`。

首次安装会创建本目录的 `.env`。填入：

```dotenv
DOUBAO_ASR_APP_KEY=
DOUBAO_ASR_ACCESS_KEY=
DOUBAO_ASR_RESOURCE_ID=volc.seedasr.sauc.duration
DOUBAO_ASR_ENDPOINT=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
VOICE_INPUT_HOTKEY=mouse9
VOICE_INPUT_INJECT=paste
VOICE_INPUT_LIVE=true
VOICE_INPUT_SAVE_RECORDINGS=true
VOICE_INPUT_RECORDINGS_KEEP=30
```

已有凭据时服务会立即启动；否则填好后执行：

```bash
systemctl --user start doubao-voice-input
journalctl --user -u doubao-voice-input -f
```

## 手动测试

```bash
./run.sh --once-seconds 5 --inject none --debug
./run.sh --hotkey mouse9 --inject paste --live
```

默认使用系统当前输入设备。按住 `mouse9` 录音，松开后转写并粘贴。
常用选项包括 `--inject clipboard`、`--no-live`、`--save-recordings`
和 `--recordings-keep 30`。

卸载自启动服务但保留凭据、依赖和录音：

```bash
./install.sh --uninstall
```
