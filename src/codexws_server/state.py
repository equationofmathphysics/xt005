import threading


# Runtime terminal state: {"workspace_id:terminal_id": {fd, pid, proc, buffer, alive, lock}}
terminals = {}
socket_bindings = {}
workspace_tcp_servers = {}
workspace_meta = {}
custom_workspaces = {}
closed_default_workspaces = set()
workspace_order = []
workspace_terminal_sessions = {}
workspace_terminal_sessions_loaded = {"value": False}
workspace_observations = {}

terminals_lock = threading.Lock()
workspace_lock = threading.RLock()
observations_lock = threading.Lock()

OBSERVATION_EVENT_LIMIT = 80

workspace_pins = []
