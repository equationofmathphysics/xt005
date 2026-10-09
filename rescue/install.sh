#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config_dir="${XT005_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/xt005}"
state_dir="${XT005_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/xt005}"
service_dir="${XT005_SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
rescue_dir="${XT005_RESCUE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/xt005-rescue}"
python_command="${PYTHON_COMMAND:-python3}"
mkdir -p "$config_dir" "$state_dir/rescue" "$service_dir" "$rescue_dir"
chmod 700 "$state_dir/rescue" "$rescue_dir"
cp "$project_dir/rescue/server.py" "$project_dir/rescue/index.html" "$rescue_dir/"
"$python_command" - "$config_dir" "$state_dir" "$service_dir" "$rescue_dir" "$project_dir" <<'PY'
import json,os,secrets,sys
from pathlib import Path
config,state,services,runtime,repo=map(Path,sys.argv[1:])
token=config/'rescue-token'
if not token.exists():
    fd=os.open(token,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as handle: handle.write(secrets.token_urlsafe(32)+'\n')
settings=config/'rescue.json'
main_env={}
if (config/'env').exists():
    for line in (config/'env').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key,value=line.split('=',1); main_env[key]=value.strip()
probe_port=main_env.get('PORT','51437')
probe_host=main_env.get('HOST','127.0.0.1')
if probe_host in {'0.0.0.0','::'}: probe_host='127.0.0.1'
if ':' in probe_host: probe_host='['+probe_host+']'
probe_default='http://'+probe_host+':'+probe_port+'/api/check'
if not settings.exists():
    settings.write_text(json.dumps({'main_unit':os.environ.get('XT005_MAIN_UNIT','xt005.service'),'repository':str(repo),'release_dir':str(state/'releases'),'state_dir':str(state/'rescue'),'token_file':str(token),'probe_url':os.environ.get('XT005_PROBE_URL',probe_default),'origin':os.environ.get('XT005_RESCUE_ORIGIN','http://127.0.0.1:51438'),'bind':'127.0.0.1'},indent=2)+'\n')
os.chmod(settings,0o600)
def quote(value): return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
unit='[Unit]\nDescription=xt005 independent recovery console\nAfter=network.target\n\n[Service]\nType=simple\nExecStart='+quote(sys.executable)+' '+quote(runtime/'server.py')+' --config '+quote(settings)+'\nRestart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=default.target\n'
(services/'xt005-rescue.service').write_text(unit)
print('Recovery configuration: '+str(settings))
print('Management token file: '+str(token))
PY
systemctl --user daemon-reload
systemctl --user enable xt005-rescue.service
if [[ "${1:-}" != "--no-start" ]]; then
  systemctl --user restart xt005-rescue.service
fi
