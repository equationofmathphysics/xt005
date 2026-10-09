const TERMINAL_SOCKET_EVENTS = Object.freeze({
  connect: 'connect',
  connectError: 'connect_error',
  disconnect: 'disconnect',
  attach: 'terminal_attach',
  input: 'terminal_input',
  snapshot: 'terminal_snapshot',
  resize: 'terminal_resize',
  resizeWorkspace: 'terminal_resize_workspace',
  ready: 'terminal_ready',
  output: 'terminal_output',
  frame: 'terminal_frame',
  exit: 'terminal_exit',
  error: 'terminal_error',
});

function createTerminalSocket() {
  return io({ transports: ['websocket'], query: {} });
}
