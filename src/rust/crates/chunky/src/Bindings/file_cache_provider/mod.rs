//! SQLite-coordinated local filesystem cache provider.

pub mod configuration;
pub mod constants;
pub mod descriptors;
pub mod errors;
pub mod filesystem;
pub mod handles;
pub mod maintenance;
pub mod maintenance_store;
pub mod metadata_store;
pub mod models;
pub mod provider;
pub mod sqlite_file_cache;

pub use configuration::{FileCacheProviderConfig, FileCacheProviderGarbageCollectionPolicy};
pub use errors::{
    FileCacheProviderEntryCorruptError, FileCacheProviderError,
    FileCacheProviderReservationLostError,
};
pub use handles::{
    FileCacheProviderSourceHandle, FileCacheProviderTargetHandle, FileCacheProviderWaitHandle,
};
pub use models::{
    FileCacheProviderEvent, FileCacheProviderEventHandler,
    FileCacheProviderGarbageCollectionResult, FileCacheProviderRecoveryResult,
    FileCacheProviderStatistics,
};
pub use provider::FileCacheProvider;
