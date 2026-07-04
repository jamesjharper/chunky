//! Artifact fingerprint construction.

use crate::{artifact_handles::ArtifactSourceHandle, artifact_models::AnyArtifactDeclaration};
use anyhow::Result;
use blake3::Hasher;
use serde::Serialize;
use std::collections::BTreeMap;

pub fn stable_hash<T: Serialize>(payload: &T) -> Result<String> {
    // BOILERPLATE: canonical JSON encoded and hashed with BLAKE3.
    let raw = serde_json::to_vec(payload)?;
    Ok(stable_hash_bytes(&raw))
}

pub fn stable_hash_bytes(raw: &[u8]) -> String {
    let mut hasher = Hasher::new();
    hasher.update(raw);
    hasher.finalize().to_hex().to_string()
}

/// Artifact fingerprint construction seam used by the example runtime.
///
/// The real implementation may inject a different deterministic hashing and
/// canonicalization provider. Keeping this behind an object prevents artifact
/// bindings from owning fingerprint policy.
#[derive(Clone, Default)]
pub struct FingerprintProvider;

impl FingerprintProvider {
    pub async fn build_intermediate_artifact_fingerprint(
        &self,
        declaration: &AnyArtifactDeclaration,
        pedigree_fingerprint: &str,
    ) -> Result<String> {
        stable_hash(&serde_json::json!({
            "artifact": {
                "artifact_id": declaration.artifact_id.to_string(),
                "role": declaration.role.as_str(),
                "version": declaration.version,
                "codec": {
                    "codec_id": declaration.codec.codec_id(),
                    "codec_version": declaration.codec.codec_version(),
                    "mime_type": declaration.codec.mime_type(),
                    "extension": declaration.codec.extension(),
                }
            },
            "pedigree": pedigree_fingerprint,
        }))
    }

    pub async fn build_supplied_artifact_fingerprint(
        &self,
        declaration: &AnyArtifactDeclaration,
        source_handle: &dyn ArtifactSourceHandle,
    ) -> Result<String> {
        let content_fingerprint = source_handle
            .get_content_fingerprint(declaration.codec.as_ref())
            .await?;
        stable_hash(&serde_json::json!({
            "artifact": {
                "artifact_id": declaration.artifact_id.to_string(),
                "role": declaration.role.as_str(),
                "version": declaration.version,
            },
            "content": content_fingerprint,
        }))
    }

    pub fn build_stage_pedigree_fingerprint(
        &self,
        stage_id: &str,
        behavior_version: &str,
        input_fingerprints: &BTreeMap<String, String>,
        effective_settings_fingerprint: &str,
    ) -> Result<String> {
        stable_hash(&serde_json::json!({
            "stage": {
                "stage_id": stage_id,
                "behavior_version": behavior_version,
            },
            "inputs": input_fingerprints,
            "effective_settings_fingerprint": effective_settings_fingerprint,
        }))
    }
}
