#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"

ENV_FILE="${ENV_FILE:-$PWD/.env}"
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      value="${BASH_REMATCH[3]}"
      if [[ "$value" == \"*\" && "$value" == *\" ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
        value="${value:1:${#value}-2}"
      fi
      key="${BASH_REMATCH[2]}"
      if [[ ! -v "$key" ]]; then
        export "$key=$value"
      fi
    fi
  done < "$ENV_FILE"
fi

# Systemd user services do not automatically inherit desktop proxy settings.
# Prefer explicit variables, then GNOME's manual proxy, then the local defaults.
if [[ -z "${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}" ]] \
  && command -v gsettings >/dev/null 2>&1 \
  && [[ "$(gsettings get org.gnome.system.proxy mode 2>/dev/null || true)" == "'manual'" ]]; then
  for proxy_kind in http https socks; do
    proxy_host="$(gsettings get "org.gnome.system.proxy.${proxy_kind}" host 2>/dev/null || true)"
    proxy_host="${proxy_host#\'}"
    proxy_host="${proxy_host%\'}"
    proxy_port="$(gsettings get "org.gnome.system.proxy.${proxy_kind}" port 2>/dev/null || true)"
    if [[ -z "$proxy_host" || ! "$proxy_port" =~ ^[1-9][0-9]*$ || "$proxy_port" -gt 65535 ]]; then
      continue
    fi
    case "$proxy_kind" in
      http) export HTTP_PROXY="http://${proxy_host}:${proxy_port}" ;;
      https) export HTTPS_PROXY="http://${proxy_host}:${proxy_port}" ;;
      socks) export ALL_PROXY="socks5h://${proxy_host}:${proxy_port}" ;;
    esac
  done
fi

# Keep both spellings because different HTTP clients recognize different forms.
export HTTP_PROXY="${HTTP_PROXY:-${http_proxy:-http://127.0.0.1:1081}}"
export HTTPS_PROXY="${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-}}}"
export ALL_PROXY="${ALL_PROXY:-${all_proxy:-socks5h://127.0.0.1:1080}}"
export http_proxy="${http_proxy:-${HTTP_PROXY:-}}"
export https_proxy="${https_proxy:-${HTTPS_PROXY:-}}"
export all_proxy="${all_proxy:-${ALL_PROXY:-}}"
proxy_no_proxy_default="localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
export NO_PROXY="${NO_PROXY:-${no_proxy:-$proxy_no_proxy_default}}"
export no_proxy="${no_proxy:-$NO_PROXY}"

export DEFAULT_WORKSPACE="${DEFAULT_WORKSPACE:-$PWD}"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  python_bin="$PYTHON_BIN"
elif [[ -x "$PWD/.venv/bin/python" ]]; then
  python_bin="$PWD/.venv/bin/python"
else
  python_bin="python3"
fi

export HOST="${HOST:-127.0.0.1}"

bind_args=(--bind "${HOST}:${PORT:-51437}")
if [[ -n "${GUNICORN_EXTRA_BINDS:-}" ]]; then
  read -r -a extra_binds <<< "$GUNICORN_EXTRA_BINDS"
  for extra_bind in "${extra_binds[@]}"; do
    bind_args+=(--bind "$extra_bind")
  done
fi

exec "$python_bin" -m gunicorn \
  --workers 1 \
  --threads "${GUNICORN_THREADS:-100}" \
  --worker-class codexws_server.worker.GracefulThreadWorker \
  --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-45}" \
  "${bind_args[@]}" \
  --access-logfile - \
  --error-logfile - \
  codexws_server.wsgi:app "$@"
