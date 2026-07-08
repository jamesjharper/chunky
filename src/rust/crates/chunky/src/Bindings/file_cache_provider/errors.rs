//! Errors exposed by `FileCacheProvider`.

use thiserror::Error;

#[derive(Debug, Error)]
#[error("{0}")]
pub struct FileCacheProviderError(pub String);

#[derive(Debug, Error)]
#[error("{0}")]
pub struct FileCacheProviderReservationLostError(pub String);

#[derive(Debug, Error)]
#[error("{0}")]
pub struct FileCacheProviderEntryCorruptError(pub String);
