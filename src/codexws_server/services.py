from dataclasses import dataclass
from typing import Any, Callable, MutableMapping


JsonObject = dict[str, Any]


@dataclass(frozen=True)
class TerminalRuntimeDeps:
    default_workspace_id: Callable[[], str]
    forget_terminal_session: Callable[..., bool]
    record_terminal_session: Callable[..., bool]
    record_terminal_thread: Callable[[str, str, str], bool]
    record_terminal_output_observation: Callable[[str, str], None]
    workspace_path: Callable[[str], str | None]
    terminal_observation_summary: Callable[[str], JsonObject]
    terminal_sessions_loaded: Callable[[], bool]
    workspace_ids: Callable[[], list[str]]
    workspace_exists: Callable[[str], bool]
    workspace_item: Callable[[str], JsonObject | None]
    workspace_terminal_session_specs: Callable[[], dict[str, list[JsonObject]]]


@dataclass(frozen=True)
class TerminalRuntime:
    cleanup_all_terminals: Callable[[], None]
    close_workspace_tcp_servers: Callable[[], None]
    close_terminal: Callable[..., None]
    close_workspace_terminals: Callable[[str], None]
    ensure_workspace_agent: Callable[..., None]
    ensure_terminal: Callable[..., JsonObject | None]
    get_terminal_info: Callable[..., JsonObject | None]
    register_socket_handlers: Callable[[], None]
    resize_terminal: Callable[..., None]
    start_workspace_supervisor: Callable[[], Any]
    start_workspace_tcp_servers: Callable[[], None]
    start_workspace_terminals: Callable[[], None]
    stop_workspace_supervisor: Callable[[], None]
    terminal_has_codex_process: Callable[[JsonObject | None], bool]
    terminal_screen_frame: Callable[..., JsonObject | None]
    terminal_summary: Callable[[str, JsonObject | None], JsonObject]
    workspace_terminal_summaries: Callable[[], dict[str, list[JsonObject]]]
    write_terminal: Callable[..., bool]


@dataclass(frozen=True)
class WorkspaceFileRouteDeps:
    safe_workspace_path: Callable[..., tuple[str | None, str | None]]


@dataclass(frozen=True)
class SystemRouteDeps:
    current_cpu_percent: Callable[[], float | None]
    default_terminal: str
    expand_workspace_path: Callable[[str], str]
    process_rss_bytes: Callable[[], int]
    read_meminfo: Callable[[], JsonObject | None]
    split_terminal_key: Callable[[str], tuple[str, str]]
    terminal_observation_summaries: Callable[[], dict[str, JsonObject]]
    terminal_summary: Callable[[str, JsonObject | None], JsonObject]
    terminals: MutableMapping[str, JsonObject]
    terminals_lock: Any
    workspace_observation_summaries: Callable[[], dict[str, JsonObject]]
    workspace_terminal_summaries: Callable[[], dict[str, list[JsonObject]]]


@dataclass(frozen=True)
class HistoryRouteDeps:
    workspace_path: Callable[[str], str | None]
    workspace_list: Callable = lambda: []


@dataclass(frozen=True)
class WorkspaceRouteDeps:
    close_terminal: Callable[..., None]
    close_workspace_terminals: Callable[[str], None]
    custom_workspaces: MutableMapping[str, JsonObject]
    default_workspace_id_for_path: Callable[[str], str]
    ensure_workspace_agent: Callable[..., None]
    ensure_terminal: Callable[..., JsonObject | None]
    expand_workspace_path: Callable[[str], str]
    hide_default_workspace_locked: Callable[[str], bool]
    is_default_workspace_id: Callable[[str], bool]
    normalize_terminal_id: Callable[[Any], str]
    normalize_workspace_id: Callable[[Any], str]
    rename_terminal_session: Callable[[str, str, str], bool]
    rebuild_workspace_maps_locked: Callable[[], None]
    request_workspace_id: Callable[[Any], str]
    restore_default_workspace_locked: Callable[[str], bool]
    save_workspaces_locked: Callable[[], None]
    set_workspace_order_locked: Callable[[list[str]], list[str]]
    set_workspace_pinned_locked: Callable
    split_terminal_key: Callable[[str], tuple[str, str]]
    workspace_agent_commands: MutableMapping[str, str]
    workspaces: MutableMapping[str, str]
    terminal_key: Callable[..., str]
    terminal_history_page_size: int
    terminal_screen_frame: Callable[..., JsonObject | None]
    terminal_summary: Callable[[str, JsonObject | None], JsonObject]
    terminals: MutableMapping[str, JsonObject]
    terminals_lock: Any
    workspace_exists: Callable[[str], bool]
    workspace_id_from_path: Callable[[str, str], str]
    workspace_item: Callable[[str], JsonObject | None]
    workspace_list: Callable[[], list[JsonObject]]
    workspace_lock: Any
    workspace_terminal_summaries: Callable[[], dict[str, list[JsonObject]]]
    write_terminal: Callable[..., bool]


@dataclass(frozen=True)
class ObservationRouteDeps:
    default_terminal: str
    normalize_terminal_id: Callable[[Any], str]
    observations_lock: Any
    record_hook_event: Callable[..., JsonObject]
    request_workspace_id: Callable[[Any], str]
    split_terminal_key: Callable[[str], tuple[str, str]]
    terminal_observation_summaries: Callable[[], dict[str, JsonObject]]
    workspace_exists: Callable[[str], bool]
    workspace_observation_summaries: Callable[[], dict[str, JsonObject]]
    workspace_observations: MutableMapping[str, JsonObject]


@dataclass(frozen=True)
class ServerRuntime:
    app: Any
    socketio: Any
    context: Any

    @property
    def terminal_runtime(self):
        return self.context.terminal_runtime

    @property
    def workspace_list(self):
        return self.context.workspace_list
