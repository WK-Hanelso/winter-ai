"""Adapter failures shared across ports and application orchestration."""


class AdapterUnavailableError(RuntimeError):
    """Raised when a selected adapter cannot serve a request."""
