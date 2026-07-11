use std::collections::BTreeMap;
use std::sync::Arc;

use crate::artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle};
use crate::artifact_models::{ArtifactInstanceLocator, ArtifactKey};
use crate::stage_models::PlanningRunPolicy;

#[derive(Clone, Debug, Default)]
pub struct ArtifactCachePolicy {
    pub ignore_existing_cache: bool,
    pub do_not_cache: bool,
    pub desired_ttl_seconds: Option<u64>,
}

#[derive(Clone)]
pub enum ProvidedArtifactRoot {
    Source(Arc<dyn ArtifactSourceHandle>),
    Cached(ArtifactInstanceLocator),
}

impl From<Arc<dyn ArtifactSourceHandle>> for ProvidedArtifactRoot {
    fn from(value: Arc<dyn ArtifactSourceHandle>) -> Self {
        Self::Source(value)
    }
}

impl From<ArtifactInstanceLocator> for ProvidedArtifactRoot {
    fn from(value: ArtifactInstanceLocator) -> Self {
        Self::Cached(value)
    }
}

#[derive(Clone, Default)]
pub struct RunRequest {
    pub inputs: BTreeMap<ArtifactKey, ProvidedArtifactRoot>,
    pub outputs: BTreeMap<ArtifactKey, Vec<Arc<dyn ArtifactTargetHandle>>>,
    pub run_policy: Option<PlanningRunPolicy>,
    pub cache_policy: BTreeMap<ArtifactKey, ArtifactCachePolicy>,
}

impl RunRequest {
    pub fn new(
        inputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactSourceHandle>>,
        outputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactTargetHandle>>,
    ) -> Self {
        Self {
            inputs: inputs
                .into_iter()
                .map(|(key, source)| (key, ProvidedArtifactRoot::Source(source)))
                .collect(),
            outputs: outputs
                .into_iter()
                .map(|(key, target)| (key, vec![target]))
                .collect(),
            run_policy: None,
            cache_policy: BTreeMap::new(),
        }
    }

    pub fn new_with_output_fanout(
        inputs: BTreeMap<ArtifactKey, Arc<dyn ArtifactSourceHandle>>,
        outputs: BTreeMap<ArtifactKey, Vec<Arc<dyn ArtifactTargetHandle>>>,
    ) -> Self {
        Self {
            inputs: inputs
                .into_iter()
                .map(|(key, source)| (key, ProvidedArtifactRoot::Source(source)))
                .collect(),
            outputs,
            run_policy: None,
            cache_policy: BTreeMap::new(),
        }
    }

    pub fn with_policy(mut self, policy: PlanningRunPolicy) -> Self {
        self.run_policy = Some(policy);
        self
    }

    pub fn with_cache_policy(
        mut self,
        cache_policy: BTreeMap<ArtifactKey, ArtifactCachePolicy>,
    ) -> Self {
        self.cache_policy = cache_policy;
        self
    }
}
