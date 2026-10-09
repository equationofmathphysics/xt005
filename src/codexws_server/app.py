"""Application factory without process-start side effects."""

from .bootstrap import create_server


__all__ = ["create_server"]
