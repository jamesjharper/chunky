//! Public import facade for the refactored file cache provider.

pub use super::{
    FileCacheProvider, FileCacheProviderConfig, FileCacheProviderEntryCorruptError,
    FileCacheProviderError, FileCacheProviderEvent, FileCacheProviderEventHandler,
    FileCacheProviderGarbageCollectionPolicy, FileCacheProviderGarbageCollectionResult,
    FileCacheProviderRecoveryResult, FileCacheProviderReservationLostError,
    FileCacheProviderSourceHandle, FileCacheProviderStatistics, FileCacheProviderTargetHandle,
    FileCacheProviderWaitHandle,
};
