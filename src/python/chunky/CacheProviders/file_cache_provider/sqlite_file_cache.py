"""Public import facade for the refactored file cache provider.

The implementation is split across focused modules in this package. Importing
from this module preserves a single obvious entry point without recreating the
former monolithic implementation file.
"""

from . import (
    FileCacheProvider,
    FileCacheProviderConfig,
    FileCacheProviderEntryCorruptError,
    FileCacheProviderError,
    FileCacheProviderEvent,
    FileCacheProviderGarbageCollectionPolicy,
    FileCacheProviderGarbageCollectionResult,
    FileCacheProviderRecoveryResult,
    FileCacheProviderReservationLostError,
    FileCacheProviderSourceHandle,
    FileCacheProviderStatistics,
    FileCacheProviderTargetHandle,
    FileCacheProviderWaitHandle,
)

__all__ = [
    "FileCacheProvider",
    "FileCacheProviderConfig",
    "FileCacheProviderEntryCorruptError",
    "FileCacheProviderError",
    "FileCacheProviderEvent",
    "FileCacheProviderGarbageCollectionPolicy",
    "FileCacheProviderGarbageCollectionResult",
    "FileCacheProviderRecoveryResult",
    "FileCacheProviderReservationLostError",
    "FileCacheProviderSourceHandle",
    "FileCacheProviderStatistics",
    "FileCacheProviderTargetHandle",
    "FileCacheProviderWaitHandle",
]
