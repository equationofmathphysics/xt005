from flask import Flask
from flask_socketio import SocketIO

from .codex_history_store import migrate_history_metadata
from .config import (
    DEFAULT_TERMINAL,
    TERMINAL_HISTORY_PAGE_SIZE,
    TERMINAL_SCREEN_SNAPSHOT_LIMIT,
    WORKSPACE_AGENT_COMMANDS,
    WORKSPACES,
)
from .context import ApplicationContext
from .frontend import frontend
from .http_transport import configure_http_transport
from .ids import (
    normalize_terminal_id,
    normalize_workspace_id,
    split_terminal_key,
    terminal_key,
    workspace_id_from_path,
)
from .observation_store import (
    record_hook_event,
    record_terminal_output_observation,
    terminal_observation_summaries,
    terminal_observation_summary,
    workspace_observation_summaries,
)
from .routes.files import register_file_routes
from .routes.history import register_history_routes
from .routes.observations import register_observation_routes
from .routes.system import register_system_routes
from .routes.workspaces import register_workspace_routes
from .services import (
    HistoryRouteDeps,
    ObservationRouteDeps,
    ServerRuntime,
    SystemRouteDeps,
    TerminalRuntimeDeps,
    WorkspaceFileRouteDeps,
    WorkspaceRouteDeps,
)
from .state import (
    custom_workspaces,
    observations_lock,
    terminals,
    terminals_lock,
    workspace_lock,
    workspace_observations,
)
from .system_stats import current_cpu_percent, process_rss_bytes, read_meminfo
from .terminal_runtime import create_terminal_runtime
from .workspace_registry import (
    build_workspace_list,
    default_workspace_id,
    default_workspace_id_for_path,
    expand_workspace_path,
    forget_terminal_session,
    hide_default_workspace_locked,
    is_default_workspace_id,
    load_workspaces,
    record_terminal_session,
    record_terminal_thread,
    rebuild_workspace_maps_locked,
    rename_terminal_session,
    request_workspace_id,
    restore_default_workspace_locked,
    safe_workspace_path,
    save_workspaces_locked,
    set_workspace_order_locked,
    set_workspace_pinned_locked,
    terminal_sessions_loaded,
    workspace_exists,
    workspace_ids,
    workspace_item,
    workspace_path,
    workspace_terminal_session_specs,
)


def create_server(start_runtime=False):
    flask_app = Flask(__name__)
    configure_http_transport(flask_app)
    socketio_server = SocketIO(
        flask_app,
        async_mode="threading",
        cors_allowed_origins=None,
        max_http_buffer_size=max(16 * 1024 * 1024, TERMINAL_SCREEN_SNAPSHOT_LIMIT * 2),
    )
    flask_app.register_blueprint(frontend)

    load_workspaces()
    migrate_history_metadata()

    terminal_runtime = create_terminal_runtime(socketio_server, TerminalRuntimeDeps(
        default_workspace_id=default_workspace_id,
        forget_terminal_session=forget_terminal_session,
        record_terminal_session=record_terminal_session,
        record_terminal_thread=record_terminal_thread,
        record_terminal_output_observation=record_terminal_output_observation,
        workspace_path=workspace_path,
        terminal_observation_summary=terminal_observation_summary,
        terminal_sessions_loaded=terminal_sessions_loaded,
        workspace_ids=workspace_ids,
        workspace_exists=workspace_exists,
        workspace_item=workspace_item,
        workspace_terminal_session_specs=workspace_terminal_session_specs,
    ))
    terminal_runtime.register_socket_handlers()

    def list_workspaces():
        return build_workspace_list(
            terminal_runtime.workspace_terminal_summaries,
            workspace_observation_summaries,
        )

    context = ApplicationContext(
        terminal_runtime=terminal_runtime,
        workspace_list=list_workspaces,
    )

    register_file_routes(flask_app, WorkspaceFileRouteDeps(
        safe_workspace_path=safe_workspace_path,
    ))
    register_system_routes(flask_app, SystemRouteDeps(
        current_cpu_percent=current_cpu_percent,
        default_terminal=DEFAULT_TERMINAL,
        expand_workspace_path=expand_workspace_path,
        process_rss_bytes=process_rss_bytes,
        read_meminfo=read_meminfo,
        split_terminal_key=split_terminal_key,
        terminal_observation_summaries=terminal_observation_summaries,
        terminal_summary=terminal_runtime.terminal_summary,
        terminals=terminals,
        terminals_lock=terminals_lock,
        workspace_observation_summaries=workspace_observation_summaries,
        workspace_terminal_summaries=terminal_runtime.workspace_terminal_summaries,
    ))
    register_history_routes(flask_app, HistoryRouteDeps(
        workspace_path=workspace_path,
        workspace_list=list_workspaces,
    ))
    register_workspace_routes(flask_app, WorkspaceRouteDeps(
        close_terminal=terminal_runtime.close_terminal,
        close_workspace_terminals=terminal_runtime.close_workspace_terminals,
        custom_workspaces=custom_workspaces,
        default_workspace_id_for_path=default_workspace_id_for_path,
        ensure_workspace_agent=terminal_runtime.ensure_workspace_agent,
        ensure_terminal=terminal_runtime.ensure_terminal,
        expand_workspace_path=expand_workspace_path,
        hide_default_workspace_locked=hide_default_workspace_locked,
        is_default_workspace_id=is_default_workspace_id,
        normalize_terminal_id=normalize_terminal_id,
        normalize_workspace_id=normalize_workspace_id,
        rename_terminal_session=rename_terminal_session,
        rebuild_workspace_maps_locked=rebuild_workspace_maps_locked,
        request_workspace_id=request_workspace_id,
        restore_default_workspace_locked=restore_default_workspace_locked,
        save_workspaces_locked=save_workspaces_locked,
        set_workspace_order_locked=set_workspace_order_locked,
        set_workspace_pinned_locked=set_workspace_pinned_locked,
        split_terminal_key=split_terminal_key,
        workspace_agent_commands=WORKSPACE_AGENT_COMMANDS,
        workspaces=WORKSPACES,
        terminal_key=terminal_key,
        terminal_history_page_size=TERMINAL_HISTORY_PAGE_SIZE,
        terminal_screen_frame=terminal_runtime.terminal_screen_frame,
        terminal_summary=terminal_runtime.terminal_summary,
        terminals=terminals,
        terminals_lock=terminals_lock,
        workspace_exists=workspace_exists,
        workspace_id_from_path=workspace_id_from_path,
        workspace_item=workspace_item,
        workspace_list=list_workspaces,
        workspace_lock=workspace_lock,
        workspace_terminal_summaries=terminal_runtime.workspace_terminal_summaries,
        write_terminal=terminal_runtime.write_terminal,
    ))
    register_observation_routes(flask_app, ObservationRouteDeps(
        default_terminal=DEFAULT_TERMINAL,
        normalize_terminal_id=normalize_terminal_id,
        observations_lock=observations_lock,
        record_hook_event=record_hook_event,
        request_workspace_id=request_workspace_id,
        split_terminal_key=split_terminal_key,
        terminal_observation_summaries=terminal_observation_summaries,
        workspace_exists=workspace_exists,
        workspace_observation_summaries=workspace_observation_summaries,
        workspace_observations=workspace_observations,
    ))

    server = ServerRuntime(
        app=flask_app,
        socketio=socketio_server,
        context=context,
    )
    if start_runtime:
        context.start()
    return server
