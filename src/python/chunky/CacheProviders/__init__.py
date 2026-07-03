"""
Cache provider contracts and implementations.
"""

from .cache_models import (
    ArtifactCache,
    CacheHit,
    CacheResolution,
    CacheWait,
    CacheWaitHandle,
    CacheWriteReservation,
)
from .file_cache_provider import (
    FileCacheProvider,
    FileCacheProviderConfig,
    FileCacheProviderEvent,
    FileCacheProviderGarbageCollectionPolicy,
    FileCacheProviderGarbageCollectionResult,
    FileCacheProviderRecoveryResult,
    FileCacheProviderStatistics,
)

__all__ = [
    "ArtifactCache",
    "CacheHit",
    "CacheResolution",
    "CacheWait",
    "CacheWaitHandle",
    "CacheWriteReservation",
    "FileCacheProvider",
    "FileCacheProviderConfig",
    "FileCacheProviderEvent",
    "FileCacheProviderGarbageCollectionPolicy",
    "FileCacheProviderGarbageCollectionResult",
    "FileCacheProviderRecoveryResult",
    "FileCacheProviderStatistics",
]
