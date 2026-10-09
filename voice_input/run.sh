#!/usr/bin/env bash
set -euo pipefail

MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$MODULE_DIR")"

if [[ -f "$MODULE_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$MODULE_DIR/.env"
  set +a
fi

export PYTHONPATH="$PARENT_DIR:$MODULE_DIR/.deps${PYTHONPATH:+:$PYTHONPATH}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
export VOICE_INPUT_HOTKEY="${VOICE_INPUT_HOTKEY:-mouse9}"
export VOICE_INPUT_INJECT="${VOICE_INPUT_INJECT:-paste}"
export VOICE_INPUT_LIVE="${VOICE_INPUT_LIVE:-true}"
export VOICE_INPUT_SAVE_RECORDINGS="${VOICE_INPUT_SAVE_RECORDINGS:-true}"
export VOICE_INPUT_RECORDINGS_KEEP="${VOICE_INPUT_RECORDINGS_KEEP:-30}"

if [[ -z "${DISPLAY:-}" ]]; then
  for socket in /tmp/.X11-unix/X*; do
    [[ -S "$socket" ]] || continue
    export DISPLAY=":${socket##*X}"
    break
  done
fi

if [[ -z "${XAUTHORITY:-}" ]]; then
  if [[ -f "$XDG_RUNTIME_DIR/gdm/Xauthority" ]]; then
    export XAUTHORITY="$XDG_RUNTIME_DIR/gdm/Xauthority"
  elif [[ -f "$HOME/.Xauthority" ]]; then
    export XAUTHORITY="$HOME/.Xauthority"
  fi
fi

cd "$PARENT_DIR"
exec "${PYTHON_BIN:-python3}" -m voice_input "$@"
