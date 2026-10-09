"""codexws server package."""

__all__ = ["app"]
__version__ = "3.0.0"


def __getattr__(name):
    if name != "app":
        raise AttributeError(name)
    from .wsgi import app

    return app
