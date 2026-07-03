//! Cache provider contracts and the filesystem-backed example implementation.

pub mod cache_models;
pub mod file_cache_provider;

pub use cache_models::{
    ArtifactCache, CacheHit, CacheResolution, CacheWait, CacheWaitHandle,
    CacheWriteReservation, CacheableOutputBootstrap,
};
pub use file_cache_provider::{
    FileCacheProvider, FileCacheProviderConfig, FileCacheProviderEntryCorruptError,
    FileCacheProviderError, FileCacheProviderEvent, FileCacheProviderEventHandler,
    FileCacheProviderGarbageCollectionPolicy, FileCacheProviderGarbageCollectionResult,
    FileCacheProviderRecoveryResult, FileCacheProviderReservationLostError,
    FileCacheProviderSourceHandle, FileCacheProviderStatistics, FileCacheProviderTargetHandle,
    FileCacheProviderWaitHandle,
};
