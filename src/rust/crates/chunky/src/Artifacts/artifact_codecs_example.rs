//! Small codecs used by the executable example.

use crate::artifact_models::{canonical_json_bytes, deserialize_json, Codec};
use anyhow::{bail, Result};
use serde::{de::DeserializeOwned, Serialize};
use std::marker::PhantomData;

#[derive(Clone, Copy, Default)]
pub struct BytesCodec;

impl Codec<Vec<u8>> for BytesCodec {
    fn codec_id(&self) -> &'static str {
        "bytes"
    }

    fn codec_version(&self) -> u32 {
        1
    }

    fn mime_type(&self) -> &'static str {
        "application/octet-stream"
    }

    fn extension(&self) -> &'static str {
        ".bin"
    }

    fn serialize(&self, value: &Vec<u8>) -> Result<Vec<u8>> {
        Ok(value.clone())
    }

    fn deserialize(&self, raw: &[u8]) -> Result<Vec<u8>> {
        Ok(raw.to_vec())
    }
}

#[derive(Clone, Copy)]
pub struct JsonCodec<T> {
    _marker: PhantomData<fn() -> T>,
}

impl<T> Default for JsonCodec<T> {
    fn default() -> Self {
        Self {
            _marker: PhantomData,
        }
    }
}

impl<T> Codec<T> for JsonCodec<T>
where
    T: Serialize + DeserializeOwned + Send + Sync + 'static,
{
    fn codec_id(&self) -> &'static str {
        "canonical-json"
    }

    fn codec_version(&self) -> u32 {
        1
    }

    fn mime_type(&self) -> &'static str {
        "application/json"
    }

    fn extension(&self) -> &'static str {
        ".json"
    }

    fn serialize(&self, value: &T) -> Result<Vec<u8>> {
        canonical_json_bytes(value)
    }

    fn deserialize(&self, raw: &[u8]) -> Result<T> {
        if raw.is_empty() {
            bail!("cannot deserialize empty JSON artifact")
        }
        deserialize_json(raw)
    }
}
