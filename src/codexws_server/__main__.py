from .bootstrap import create_server
from .config import SERVER_HOST, SERVER_PORT


def main():
    server = create_server(start_runtime=True)
    print(f"服务启动: http://{SERVER_HOST}:{SERVER_PORT}")
    try:
        server.socketio.run(
            server.app,
            host=SERVER_HOST,
            port=SERVER_PORT,
            debug=False,
            allow_unsafe_werkzeug=True,
        )
    finally:
        server.context.stop()


if __name__ == "__main__":
    main()
