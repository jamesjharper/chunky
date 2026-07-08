"""Errors exposed by ``FileCacheProvider``."""


class FileCacheProviderError(RuntimeError):
    """Base error raised by the local filesystem cache provider."""


class FileCacheProviderReservationLostError(FileCacheProviderError):
    """Raised when a handle no longer owns its SQLite reservation lease."""


class FileCacheProviderEntryCorruptError(FileCacheProviderError):
    """Raised when committed cache bytes fail integrity validation."""
