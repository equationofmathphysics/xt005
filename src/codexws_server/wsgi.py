from .bootstrap import create_server


server = create_server(start_runtime=True)
app = server.app
socketio = server.socketio
context = server.context
