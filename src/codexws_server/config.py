import os


PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(PACKAGE_DIR, os.pardir, os.pardir))
ENV_FILE = os.environ.get("ENV_FILE", os.path.join(PROJECT_ROOT, ".env"))


def _is_env_key(value):
    return value and (value[0].isalpha() or value[0] == "_") and all(
        char.isalnum() or char == "_" for char in value
    )


def _clean_env_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_workspace_env():
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        key, separator, value = stripped.partition("=")
        key = key.strip()
        if separator and _is_env_key(key) and key not in os.environ:
            os.environ[key] = _clean_env_value(value)


load_workspace_env()


def default_bind_host():
    return os.environ.get("HOST", "127.0.0.1").strip() or "127.0.0.1"


FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
WORK_DIR = PROJECT_ROOT
WORKSPACES_FILE = os.environ.get("WORKSPACES_FILE", os.path.join(WORK_DIR, ".workspaces.json"))
CODEX_HISTORY_FILE = os.environ.get("CODEX_HISTORY_FILE", os.path.join(WORK_DIR, ".codex_history.json"))
APP_PREFERENCES_FILE = os.environ.get(
    "APP_PREFERENCES_FILE", os.path.join(WORK_DIR, ".app-preferences.json")
)
CODEX_HOME = os.path.realpath(os.path.expanduser(os.environ.get("CODEX_HOME", "~/.codex")))
_CODEX_COMMAND = os.environ.get("CODEX_COMMAND", "codex")


def normalize_codex_command(command):
    expanded = os.path.expanduser(command)
    if os.path.sep not in expanded:
        return expanded
    # Preserve launchers such as an NVM symlink: its parent directory selects
    # the matching Node runtime when the CLI adapter builds PATH.
    return os.path.abspath(expanded)


CODEX_COMMAND = normalize_codex_command(_CODEX_COMMAND)
SERVER_HOST = default_bind_host()
SERVER_PORT = int(os.environ.get("PORT", "51437"))

DEFAULT_WORKSPACES = {
    "codexws": os.environ.get("DEFAULT_WORKSPACE", os.path.expanduser("~/codexws")),
}
WORKSPACES = dict(DEFAULT_WORKSPACES)
DEFAULT_WORKSPACE_ID = "codexws"

CDNLOCAL_DIR = os.path.join(PROJECT_ROOT, ".cdnlocal")
DEFAULT_TERMINAL = "main"
TERMINAL_BUFFER_LIMIT = int(os.environ.get("TERMINAL_BUFFER_LIMIT", "262144"))
TERMINAL_HISTORY_LIMIT = int(os.environ.get("TERMINAL_HISTORY_LIMIT", "67108864"))
TERMINAL_HISTORY_PAGE_SIZE = int(os.environ.get("TERMINAL_HISTORY_PAGE_SIZE", "131072"))
TERMINAL_SCREEN_SNAPSHOT_LIMIT = int(os.environ.get("TERMINAL_SCREEN_SNAPSHOT_LIMIT", "67108864"))

WORKSPACE_PTY_PORTS = {}
WORKSPACE_PTY_HOST = os.environ.get("WORKSPACE_PTY_HOST", "127.0.0.1")
WORKSPACE_AGENT_COMMANDS = {}
WORKSPACE_AGENT_CHECK_INTERVAL = float(os.environ.get("WORKSPACE_AGENT_CHECK_INTERVAL", "8"))
WORKSPACE_AGENT_RESTART_GRACE = float(os.environ.get("WORKSPACE_AGENT_RESTART_GRACE", "15"))
WORKSPACE_AGENT_START_DELAY = float(os.environ.get("WORKSPACE_AGENT_START_DELAY", "1"))
