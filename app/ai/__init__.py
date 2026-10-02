"""Optional AI assistance. Importing this package does not call a model or write data."""

from .client import AiUnavailable, call_json

__all__ = ["AiUnavailable", "call_json"]
