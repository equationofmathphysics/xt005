#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
config_dir="${XT005_CONFIG_DIR:-$config_home/xt005}"
state_dir="${XT005_STATE_DIR:-$state_home/xt005}"
service_dir="${XT005_SYSTEMD_USER_DIR:-$config_home/systemd/user}"
env_file="$config_dir/env"
service_file="$service_dir/xt005.service"
python_command="${PYTHON_COMMAND:-python3}"
start_service=1

if [[ "${1:-}" == "--no-start" ]]; then
  start_service=0
  shift
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  printf 'Usage: %s [--no-start]\n' "${0##*/}"
  printf '  XT005_WORKSPACE  initial workspace (default: $HOME)\n'
  printf '  XT005_HOST       listen address (default: 127.0.0.1)\n'
  printf '  XT005_PORT       listen port (default: 51437)\n'
  exit 0
fi
if (($#)); then
  printf 'Unknown option: %s\n' "$1" >&2
  exit 2
fi

if ! command -v "$python_command" >/dev/null 2>&1; then
  printf 'Python command not found: %s\n' "$python_command" >&2
  exit 1
fi
if ! "$python_command" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  printf 'xt005 requires Python 3.10 or newer.\n' >&2
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
  printf 'systemctl is required for the xt005 user service.\n' >&2
  exit 1
fi

if [[ ! -d "$project_dir/.venv" ]]; then
  if ! "$python_command" -m venv "$project_dir/.venv"; then
    printf 'Could not create .venv. Install python3-venv and retry.\n' >&2
    exit 1
  fi
fi
env -u PYTHONPATH "$project_dir/.venv/bin/python" -m pip install \
  --disable-pip-version-check -r "$project_dir/requirements.lock"

mkdir -p "$config_dir" "$state_dir" "$service_dir"
if [[ ! -f "$env_file" ]]; then
  workspace="${XT005_WORKSPACE:-$HOME}"
  host="${XT005_HOST:-127.0.0.1}"
  port="${XT005_PORT:-51437}"
  if [[ ! -d "$workspace" ]]; then
    printf 'Initial workspace does not exist: %s\n' "$workspace" >&2
    exit 2
  fi
  if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1 || port > 65535)); then
    printf 'XT005_PORT must be an integer from 1 to 65535.\n' >&2
    exit 2
  fi
  {
    printf 'HOST=%s\n' "$host"
    printf 'PORT=%s\n' "$port"
    printf 'DEFAULT_WORKSPACE=%s\n' "$workspace"
    printf 'WORKSPACES_FILE=%s/workspaces.json\n' "$state_dir"
    printf 'CODEX_HISTORY_FILE=%s/codex_history.json\n' "$state_dir"
    printf 'APP_PREFERENCES_FILE=%s/app-preferences.json\n' "$state_dir"
  } > "$env_file"
else
  printf 'Keeping existing configuration: %s\n' "$env_file"
  if ! grep -q '^APP_PREFERENCES_FILE=' "$env_file"; then
    printf 'APP_PREFERENCES_FILE=%s/app-preferences.json\n' "$state_dir" >> "$env_file"
  fi

fi
chmod 600 "$env_file"

systemd_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//%/%%}"
  printf '%s' "$value"
}

# The rescue updater switches this symlink atomically. Runtime state stays outside it.
mkdir -p "$state_dir/releases"
ln -sfn "$project_dir" "$state_dir/releases/current.next"
mv -Tf "$state_dir/releases/current.next" "$state_dir/releases/current"
runtime_dir="$state_dir/releases/current"

service_template="$(<"$project_dir/xt005.service")"
service_template="${service_template//@WORKING_DIRECTORY@/$(systemd_escape "$runtime_dir")}"
service_template="${service_template//@INSTALL_DIR@/$(systemd_escape "$runtime_dir")}"
service_template="${service_template//@ENV_FILE@/$(systemd_escape "$env_file")}"
printf '%s\n' "$service_template" > "$service_file"

XT005_CONFIG_DIR="$config_dir" XT005_STATE_DIR="$state_dir" XT005_SYSTEMD_USER_DIR="$service_dir" \
  "$project_dir/rescue/install.sh" --no-start

systemctl --user daemon-reload
systemctl --user enable xt005.service
if ((start_service)); then
  systemctl --user restart xt005.service
  systemctl --user restart xt005-rescue.service
fi

printf '\nxt005 is installed from %s\n' "$project_dir"
printf 'Configuration: %s\n' "$env_file"
if ((start_service)); then
  printf 'Service restarted. Open the address configured in %s\n' "$env_file"
else
  printf 'Start it with: systemctl --user start xt005\n'
fi
