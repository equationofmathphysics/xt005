#!/usr/bin/env bash
set -euo pipefail

MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$MODULE_DIR")"
UNIT_NAME="doubao-voice-input.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_FILE="$UNIT_DIR/$UNIT_NAME"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
  rm -f "$UNIT_FILE"
  systemctl --user daemon-reload
  echo "Removed $UNIT_NAME; local dependencies, credentials, and recordings were kept."
  exit 0
fi

if [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--uninstall]" >&2
  exit 2
fi

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }

python3 -m pip install --upgrade --target "$MODULE_DIR/.deps" -r "$MODULE_DIR/requirements.txt"

if [[ ! -f "$MODULE_DIR/.env" ]]; then
  cp "$MODULE_DIR/.env.example" "$MODULE_DIR/.env"
  chmod 600 "$MODULE_DIR/.env"
fi

escape_systemd() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//%/%%}"
  printf '%s' "$value"
}

mkdir -p "$UNIT_DIR"
module_dir="$(escape_systemd "$MODULE_DIR")"
parent_dir="$(escape_systemd "$PARENT_DIR")"
cat >"$UNIT_FILE" <<EOF
[Unit]
Description=Doubao voice input
After=graphical-session.target network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory="$parent_dir"
ExecStart="$module_dir/run.sh"
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME"

credential_value() {
  local name="$1"
  sed -n "s/^$name=//p" "$MODULE_DIR/.env" | tail -n 1
}

if [[ -n "$(credential_value DOUBAO_ASR_APP_KEY)" && -n "$(credential_value DOUBAO_ASR_ACCESS_KEY)" ]]; then
  systemctl --user restart "$UNIT_NAME"
  echo "Installed and started $UNIT_NAME."
else
  echo "Installed $UNIT_NAME but did not start it."
  echo "Set DOUBAO_ASR_APP_KEY and DOUBAO_ASR_ACCESS_KEY in $MODULE_DIR/.env, then run:"
  echo "  systemctl --user start $UNIT_NAME"
fi
