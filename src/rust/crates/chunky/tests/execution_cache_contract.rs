mod support;

use anyhow::{anyhow, Result};
use async_trait::async_trait;
use chunky_example::artifact_bindings::ArtifactBindingsProvider;
use chunky_example::artifact_models::{AnyArtifactDeclaration, ArtifactKey, ArtifactRole};
use chunky_example::cache_models::{
    ArtifactCache, CacheHit, CacheResolution, CacheWait, CacheWaitHandle, CacheWriteReservation,
};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use support::{decl, JsonValueCodec, MemorySource, RecordingTarget};

#[derive(Default)]
struct RecordingWaitHandle {
    waited: Mutex<bool>,
}

impl RecordingWaitHandle {
    fn waited(&self) -> bool {
        *self.waited.lock().expect("wait mutex poisoned")
    }
}

#[async_trait]
impl CacheWaitHandle for RecordingWaitHandle {
    async fn wait(&self) -> Result<()> {
        *self.waited.lock().expect("wait mutex poisoned") = true;
        Ok(())
    }
}

struct ScriptedCache {
    outcomes: Mutex<VecDeque<CacheResolution>>,
    calls: Mutex<Vec<(ArtifactKey, String, bool)>>,
}

impl ScriptedCache {
    fn new(outcomes: Vec<CacheResolution>) -> Arc<Self> {
        Arc::new(Self {
            outcomes: Mutex::new(outcomes.into()),
            calls: Mutex::new(vec![]),
        })
    }

    fn force_write_calls(&self) -> Vec<bool> {
        self.calls
            .lock()
            .expect("calls mutex poisoned")
            .iter()
            .map(|(_, _, force)| *force)
            .collect()
    }

    fn artifact_and_force_calls(&self) -> Vec<(ArtifactKey, bool)> {
        self.calls
            .lock()
            .expect("calls mutex poisoned")
            .iter()
            .map(|(key, _, force)| (key.clone(), *force))
            .collect()
    }

    fn call_count(&self) -> usize {
        self.calls.lock().expect("calls mutex poisoned").len()
    }
}

#[async_trait]
impl ArtifactCache for ScriptedCache {
    async fn resolve(
        &self,
        declaration: &AnyArtifactDeclaration,
        fingerprint: &str,
        force_write: bool,
    ) -> Result<CacheResolution> {
        self.calls.lock().expect("calls mutex poisoned").push((
            declaration.artifact_id.clone(),
            fingerprint.to_owned(),
            force_write,
        ));
        self.outcomes
            .lock()
            .expect("outcomes mutex poisoned")
            .pop_front()
            .ok_or_else(|| anyhow!("cache script exhausted"))
    }
}

#[tokio::test]
async fn all_requested_cacheable_outputs_hit_returns_available_instances_and_no_writers(
) -> Result<()> {
    let out_a = decl("graph//out_a", ArtifactRole::Intermediate);
    let out_b = decl("graph//out_b", ArtifactRole::Intermediate);
    let source_a = MemorySource::new(serde_json::json!({"out": "a"}));
    let source_b = MemorySource::new(serde_json::json!({"out": "b"}));
    let cache = ScriptedCache::new(vec![
        CacheResolution::Hit(CacheHit {
            source_handle: source_a,
        }),
        CacheResolution::Hit(CacheHit {
            source_handle: source_b,
        }),
    ]);
    let provider = ArtifactBindingsProvider::new(cache.clone());

    let resolved = provider
        .bind_stage_outputs(&[out_a.erase(), out_b.erase()], "stage-pedigree")
        .await?
        .into_resolved_for_test()?;

    assert_eq!(2, resolved.available.len());
    assert_eq!(0, resolved.writers.len());
    assert_eq!(vec![false, false], cache.force_write_calls());
    Ok(())
}

#[tokio::test]
async fn partial_cache_hit_for_multi_output_stage_retries_with_force_write_for_complete_set(
) -> Result<()> {
    let out_a = decl("graph//out_a", ArtifactRole::Intermediate);
    let out_b = decl("graph//out_b", ArtifactRole::Intermediate);
    let provisional_hit = MemorySource::new(serde_json::json!({"out": "a-old"}));
    let provisional_writer = RecordingTarget::new("out-b-provisional");
    let force_writer_a = RecordingTarget::new("out-a-force");
    let force_writer_b = RecordingTarget::new("out-b-force");
    let cache = ScriptedCache::new(vec![
        CacheResolution::Hit(CacheHit {
            source_handle: provisional_hit.clone(),
        }),
        CacheResolution::Write(CacheWriteReservation {
            target_handle: provisional_writer.clone(),
        }),
        CacheResolution::Write(CacheWriteReservation {
            target_handle: force_writer_a,
        }),
        CacheResolution::Write(CacheWriteReservation {
            target_handle: force_writer_b,
        }),
    ]);
    let provider = ArtifactBindingsProvider::new(cache.clone());

    let resolved = provider
        .bind_stage_outputs(&[out_a.erase(), out_b.erase()], "stage-pedigree")
        .await?
        .into_resolved_for_test()?;

    assert!(provisional_hit.is_closed());
    assert!(provisional_writer.aborted());
    assert_eq!(0, resolved.available.len());
    assert_eq!(2, resolved.writers.len());
    assert_eq!(vec![false, false, true, true], cache.force_write_calls());
    Ok(())
}

#[tokio::test]
async fn cache_wait_aborts_provisional_writers_and_returns_wait_without_starting_duplicate_writer(
) -> Result<()> {
    let out_a = decl("graph//out_a", ArtifactRole::Intermediate);
    let out_b = decl("graph//out_b", ArtifactRole::Intermediate);
    let provisional_writer = RecordingTarget::new("out-a-provisional");
    let wait_handle = Arc::new(RecordingWaitHandle::default());
    let cache_wait = CacheWait {
        fingerprint: "blocked-fingerprint".to_owned(),
        owner_task_id: Some("other-task".to_owned()),
        wait_handle: wait_handle.clone(),
    };
    let cache = ScriptedCache::new(vec![
        CacheResolution::Write(CacheWriteReservation {
            target_handle: provisional_writer.clone(),
        }),
        CacheResolution::Wait(cache_wait.clone()),
    ]);
    let provider = ArtifactBindingsProvider::new(cache);

    let result = provider
        .bind_stage_outputs(&[out_a.erase(), out_b.erase()], "stage-pedigree")
        .await?
        .into_wait_for_test()?;

    assert_eq!(out_b.artifact_id(), &result.artifact_id);
    assert_eq!(cache_wait.fingerprint, result.cache_wait.fingerprint);
    assert!(provisional_writer.aborted());
    assert!(
        !wait_handle.waited(),
        "the scheduler, not binding, owns cache waiting"
    );
    Ok(())
}

#[tokio::test]
async fn cache_metadata_mismatch_is_observed_as_miss_and_bound_as_writer() -> Result<()> {
    let out_a = decl("graph//out_a", ArtifactRole::Intermediate);
    let replacement_writer = RecordingTarget::new("replacement-after-metadata-mismatch");
    // The cache implementation owns metadata validation. This scripted cache
    // represents a mismatched entry by returning a write reservation instead of
    // a hit for the requested fingerprint.
    let cache = ScriptedCache::new(vec![CacheResolution::Write(CacheWriteReservation {
        target_handle: replacement_writer,
    })]);
    let provider = ArtifactBindingsProvider::new(cache.clone());

    let resolved = provider
        .bind_stage_outputs(&[out_a.erase()], "stage-pedigree")
        .await?
        .into_resolved_for_test()?;

    assert_eq!(0, resolved.available.len());
    assert_eq!(1, resolved.writers.len());
    assert_eq!(
        vec![(out_a.artifact_id().clone(), false)],
        cache.artifact_and_force_calls()
    );
    Ok(())
}

#[tokio::test]
async fn mixed_cacheable_hit_and_non_cacheable_output_runs_stage_for_non_cacheable_writer_only(
) -> Result<()> {
    let cacheable = decl("graph//cacheable_out", ArtifactRole::Intermediate);
    let diagnostic = chunky_example::artifact_models::ArtifactDeclaration::new(
        legacy_key("graph//diagnostic_out")?,
        ArtifactRole::Diagnostic,
        1,
        JsonValueCodec,
        false,
    )?;
    let source = MemorySource::new(serde_json::json!({"cached": true}));
    let cache = ScriptedCache::new(vec![CacheResolution::Hit(CacheHit {
        source_handle: source,
    })]);
    let provider = ArtifactBindingsProvider::new(cache);

    let resolved = provider
        .bind_stage_outputs(&[cacheable.erase(), diagnostic.erase()], "stage-pedigree")
        .await?
        .into_resolved_for_test()?;

    assert_eq!(1, resolved.available.len());
    assert_eq!(1, resolved.writers.len());
    assert_eq!(
        diagnostic.artifact_id(),
        &resolved.writers[0].handle.declaration.artifact_id
    );
    Ok(())
}

#[tokio::test]
async fn same_semantic_target_can_be_materialized_to_multiple_sinks_without_changing_fingerprint(
) -> Result<()> {
    let out_a = decl("graph//out_a", ArtifactRole::Target);
    let source = MemorySource::new(serde_json::json!({"semantic": "value"}));
    let cache = ScriptedCache::new(vec![CacheResolution::Hit(CacheHit {
        source_handle: source,
    })]);
    let provider = ArtifactBindingsProvider::new(cache.clone());
    let resolved = provider
        .bind_stage_outputs(&[out_a.erase()], "stage-pedigree")
        .await?
        .into_resolved_for_test()?;
    let instance = resolved.available.first().expect("one cache hit").clone();
    let first_sink = RecordingTarget::new("first-caller-path");
    let second_sink = RecordingTarget::new("second-caller-path");

    provider.materialize(&instance, first_sink.clone()).await?;
    provider.materialize(&instance, second_sink.clone()).await?;

    assert_eq!(
        1,
        cache.call_count(),
        "caller materialization must not re-resolve semantic cache identity"
    );
    assert_eq!(instance.fingerprint(), first_sink.prepared()[0].1);
    assert_eq!(instance.fingerprint(), second_sink.prepared()[0].1);
    assert_ne!(first_sink.name, second_sink.name);
    Ok(())
}

trait StageOutputResolutionTestExt {
    fn into_resolved_for_test(
        self,
    ) -> Result<chunky_example::artifact_binding_models::ResolvedStageOutputBindings>;
    fn into_wait_for_test(self) -> Result<chunky_example::artifact_binding_models::StageCacheWait>;
}

impl StageOutputResolutionTestExt
    for chunky_example::artifact_binding_models::StageOutputResolution
{
    fn into_resolved_for_test(
        self,
    ) -> Result<chunky_example::artifact_binding_models::ResolvedStageOutputBindings> {
        match self {
            Self::Resolved(value) => Ok(value),
            Self::Wait(_) => Err(anyhow!("expected resolved output bindings")),
        }
    }

    fn into_wait_for_test(self) -> Result<chunky_example::artifact_binding_models::StageCacheWait> {
        match self {
            Self::Wait(value) => Ok(value),
            Self::Resolved(_) => Err(anyhow!("expected cache wait")),
        }
    }
}
