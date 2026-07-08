//! Focused artifact binding and materialization helpers.
//!
//! These helpers receive planner/cache results. They do not build the graph,
//! calculate pedigree, schedule stages, or choose caller output targets.

use crate::{
    artifact_binding_models::{
        AnyArtifactInstance, AnyArtifactReader, AnyArtifactWriter, ArtifactHandle,
        ArtifactInstance, ProvidedStageOutput, ResolvedStageOutputBindings, StageCacheWait,
        StageOutputBindings, StageOutputResolution,
    },
    artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle, IntermediateArtifactHandle},
    artifact_models::{AnyArtifactDeclaration, ArtifactDeclaration},
    cache_models::{ArtifactCache, CacheResolution, CacheWait, CacheableOutputBootstrap},
    fingerprints::FingerprintProvider,
};
use anyhow::{bail, Result};
use std::{collections::HashMap, sync::Arc};

/// Cache-backed artifact bootstrap and materialization runtime.
///
/// The scheduler owns waiting/retry policy at the invocation level. This
/// provider resolves stage outputs into one of two outcomes: a concrete binding
/// set, or the first cache wait that blocks the complete output set.
#[derive(Clone)]
pub struct ArtifactBindingsProvider {
    cache: Arc<dyn ArtifactCache>,
    fingerprint_provider: FingerprintProvider,
}

impl ArtifactBindingsProvider {
    pub fn new(cache: Arc<dyn ArtifactCache>) -> Self {
        Self::with_fingerprint_provider(cache, FingerprintProvider)
    }

    pub fn with_fingerprint_provider(
        cache: Arc<dyn ArtifactCache>,
        fingerprint_provider: FingerprintProvider,
    ) -> Self {
        Self {
            cache,
            fingerprint_provider,
        }
    }

    pub fn fingerprint_provider(&self) -> &FingerprintProvider {
        &self.fingerprint_provider
    }

    async fn bind_cacheable_output(
        &self,
        declaration: &AnyArtifactDeclaration,
        pedigree_fingerprint: &str,
        force_cache_overwrite: bool,
    ) -> Result<CacheableOutputBootstrap> {
        let publication = IntermediateArtifactHandle::new();
        let fingerprint = self
            .fingerprint_provider
            .build_intermediate_artifact_fingerprint(declaration, pedigree_fingerprint)
            .await?;
        let resolution = self
            .cache
            .resolve(declaration, &fingerprint, force_cache_overwrite)
            .await?;

        match resolution {
            CacheResolution::Hit(hit) => {
                Ok(CacheableOutputBootstrap::Available(AnyArtifactInstance {
                    handle: ArtifactHandle::new(
                        declaration.clone(),
                        fingerprint,
                        Some(hit.source_handle),
                        vec![],
                    ),
                }))
            }
            CacheResolution::Write(reservation) => {
                let source: Arc<dyn ArtifactSourceHandle> = publication.clone();
                let publication_target: Arc<dyn ArtifactTargetHandle> = publication;
                let writer = AnyArtifactWriter::new(ArtifactHandle::new(
                    declaration.clone(),
                    fingerprint,
                    Some(source),
                    vec![
                        // Durable semantic representation commits first.
                        reservation.target_handle,
                        // Downstream stages see the value only afterwards.
                        publication_target,
                    ],
                ))?;
                Ok(CacheableOutputBootstrap::Produce(writer))
            }
            CacheResolution::Wait(wait) => Ok(CacheableOutputBootstrap::Waiting(wait)),
        }
    }

    async fn bind_cacheable_stage_outputs(
        &self,
        output_declarations: &[AnyArtifactDeclaration],
        pedigree_fingerprint: &str,
        force_cache_overwrite: bool,
    ) -> Result<StageOutputBindings> {
        let mut available = Vec::new();
        let mut writers = Vec::new();
        let mut waits = Vec::new();

        for declaration in output_declarations {
            match self
                .bind_cacheable_output(declaration, pedigree_fingerprint, force_cache_overwrite)
                .await?
            {
                CacheableOutputBootstrap::Available(instance) => available.push(instance),
                CacheableOutputBootstrap::Produce(writer) => writers.push(writer),
                CacheableOutputBootstrap::Waiting(wait) => waits.push(StageCacheWait {
                    artifact_id: declaration.artifact_id.clone(),
                    cache_wait: wait,
                }),
            }
        }

        Ok(StageOutputBindings {
            available,
            writers,
            waits,
        })
    }

    async fn bind_non_cacheable_stage_outputs(
        &self,
        output_declarations: &[AnyArtifactDeclaration],
        pedigree_fingerprint: &str,
    ) -> Result<Vec<AnyArtifactWriter>> {
        let mut writers = Vec::new();

        for declaration in output_declarations {
            let publication = IntermediateArtifactHandle::new();
            let fingerprint = self
                .fingerprint_provider
                .build_intermediate_artifact_fingerprint(declaration, pedigree_fingerprint)
                .await?;
            let source: Arc<dyn ArtifactSourceHandle> = publication.clone();
            let target: Arc<dyn ArtifactTargetHandle> = publication;
            writers.push(AnyArtifactWriter::new(ArtifactHandle::new(
                declaration.clone(),
                fingerprint,
                Some(source),
                vec![target],
            ))?);
        }

        Ok(writers)
    }

    pub async fn bind_supplied<T>(
        &self,
        declaration: &ArtifactDeclaration<T>,
        source: Arc<dyn ArtifactSourceHandle>,
    ) -> Result<ArtifactInstance<T>>
    where
        T: Send + Sync + 'static,
    {
        let erased = declaration.erase();
        let fingerprint = self
            .fingerprint_provider
            .build_supplied_artifact_fingerprint(&erased, source.as_ref())
            .await?;
        ArtifactInstance::new(ArtifactHandle::new(
            erased,
            fingerprint,
            Some(source),
            vec![],
        ))
    }

    pub async fn bind_supplied_any(
        &self,
        declaration: &AnyArtifactDeclaration,
        source: Arc<dyn ArtifactSourceHandle>,
    ) -> Result<AnyArtifactInstance> {
        let fingerprint = self
            .fingerprint_provider
            .build_supplied_artifact_fingerprint(declaration, source.as_ref())
            .await?;
        Ok(AnyArtifactInstance {
            handle: ArtifactHandle::new(declaration.clone(), fingerprint, Some(source), vec![]),
        })
    }

    pub async fn bind_stage_outputs(
        &self,
        output_declarations: &[AnyArtifactDeclaration],
        pedigree_fingerprint: &str,
    ) -> Result<StageOutputResolution> {
        let (cacheable_outputs, non_cacheable_outputs) =
            partition_output_declarations(output_declarations);
        let mut force_cache_overwrite = false;

        loop {
            let bindings = self
                .bind_cacheable_stage_outputs(
                    &cacheable_outputs,
                    pedigree_fingerprint,
                    force_cache_overwrite,
                )
                .await?;

            if bindings.has_waits() {
                // A production cache hit owns a read reservation. This pass is
                // being discarded, so provisional hit handles must be closed
                // before the scheduler waits and retries the whole output set.
                close_instances(&bindings.available).await;
                abort_writers(&bindings.writers).await;
                return Ok(StageOutputResolution::Wait(bindings.waits[0].clone()));
            }

            if bindings.has_partial_cache_hit() {
                // Keep one stage invocation's cacheable outputs coherent: a
                // partial hit reruns and refreshes the complete planned set.
                close_instances(&bindings.available).await;
                abort_writers(&bindings.writers).await;
                force_cache_overwrite = true;
                continue;
            }

            let non_cacheable_writers = self
                .bind_non_cacheable_stage_outputs(&non_cacheable_outputs, pedigree_fingerprint)
                .await?;

            if bindings.all_available() && non_cacheable_writers.is_empty() {
                return Ok(StageOutputResolution::Resolved(
                    ResolvedStageOutputBindings {
                        available: bindings.available,
                        writers: vec![],
                    },
                ));
            }

            if bindings.all_reserved_for_write() {
                let mut writers = bindings.writers;
                writers.extend(non_cacheable_writers);
                return Ok(StageOutputResolution::Resolved(
                    ResolvedStageOutputBindings {
                        available: vec![],
                        writers,
                    },
                ));
            }

            if bindings.all_available() {
                return Ok(StageOutputResolution::Resolved(
                    ResolvedStageOutputBindings {
                        available: bindings.available,
                        writers: non_cacheable_writers,
                    },
                ));
            }

            bail!("unexpected stage output bootstrap state");
        }
    }

    pub async fn materialize(
        &self,
        instance: &AnyArtifactInstance,
        target: Arc<dyn ArtifactTargetHandle>,
    ) -> Result<()> {
        let value = AnyArtifactReader::new(instance.handle.clone())?
            .read_any()
            .await?;
        let writer = AnyArtifactWriter::new(ArtifactHandle::new(
            instance.handle.declaration.clone(),
            instance.handle.fingerprint.clone(),
            instance.handle.source.clone(),
            vec![target],
        ))?;
        writer.write_any(value).await?;
        Ok(())
    }

    pub async fn materialize_provided_outputs(
        &self,
        instances: &[AnyArtifactInstance],
        provided_outputs: &[ProvidedStageOutput],
    ) -> Result<()> {
        if provided_outputs.is_empty() {
            return Ok(());
        }

        let instances_by_id: HashMap<_, _> = instances
            .iter()
            .map(|instance| (instance.artifact_id().clone(), instance))
            .collect();
        for provided_output in provided_outputs {
            let instance = instances_by_id
                .get(&provided_output.declaration.artifact_id)
                .ok_or_else(|| {
                    anyhow::anyhow!(
                        "missing stage output instance for provided target: {}",
                        provided_output.declaration.artifact_id
                    )
                })?;
            self.materialize(instance, provided_output.target.clone())
                .await?;
        }
        Ok(())
    }

    pub fn merge_stage_output_instances(
        available: &[AnyArtifactInstance],
        produced: &[AnyArtifactInstance],
    ) -> Vec<AnyArtifactInstance> {
        let mut merged: HashMap<_, _> = available
            .iter()
            .cloned()
            .map(|instance| (instance.artifact_id().clone(), instance))
            .collect();
        for instance in produced {
            merged.insert(instance.artifact_id().clone(), instance.clone());
        }
        merged.into_values().collect()
    }
}

fn partition_output_declarations(
    output_declarations: &[AnyArtifactDeclaration],
) -> (Vec<AnyArtifactDeclaration>, Vec<AnyArtifactDeclaration>) {
    output_declarations
        .iter()
        .cloned()
        .partition(|declaration| declaration.cacheable)
}

async fn abort_writers(writers: &[AnyArtifactWriter]) {
    for writer in writers.iter().rev() {
        writer.abort_if_unwritten().await;
    }
}

async fn close_instances(instances: &[AnyArtifactInstance]) {
    for instance in instances.iter().rev() {
        if let Some(source) = instance.handle.source.as_ref() {
            let _ = source.close().await;
        }
    }
}

#[allow(dead_code)]
async fn wait_for_cache_writes(waits: &[CacheWait]) -> Result<()> {
    for wait in waits {
        wait.wait_handle.wait().await?;
    }
    Ok(())
}
