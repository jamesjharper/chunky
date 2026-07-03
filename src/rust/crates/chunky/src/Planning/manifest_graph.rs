//! Request scoping for graph planning.
//!
//! Request-independent manifest connectivity graph.

use crate::artifact_models::ArtifactKey;
use crate::stage_manifest::StageManifest;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ManifestProducerEdge {
    pub stage_id: String,
    pub output_artifact_id: ArtifactKey,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ManifestConsumerEdge {
    pub stage_id: String,
    pub input_selector: ArtifactKey,
}

/// Static manifest connectivity before a request is applied.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StageManifestGraph {
    pub manifest_id: String,
    pub stage_ids: Vec<String>,
    pub producer_edges: Vec<ManifestProducerEdge>,
    pub consumer_edges: Vec<ManifestConsumerEdge>,
}

pub struct StageManifestGraphBuilder<'a> {
    manifest: &'a StageManifest,
}

impl<'a> StageManifestGraphBuilder<'a> {
    pub fn new(manifest: &'a StageManifest) -> Self {
        Self { manifest }
    }

    pub fn build(&self) -> StageManifestGraph {
        let mut stage_ids: Vec<String> = self.manifest.stages.keys().cloned().collect();
        stage_ids.sort();

        let mut producer_edges = Vec::new();
        for (artifact_id, producers) in &self.manifest.producers {
            for producer in producers {
                producer_edges.push(ManifestProducerEdge {
                    stage_id: producer.stage_id.clone(),
                    output_artifact_id: artifact_id.clone(),
                });
            }
        }
        producer_edges.sort_by(|left, right| {
            left.output_artifact_id
                .cmp(&right.output_artifact_id)
                .then_with(|| left.stage_id.cmp(&right.stage_id))
        });

        let mut consumer_edges = Vec::new();
        for (selector, consumers) in &self.manifest.consumers {
            for consumer in consumers {
                consumer_edges.push(ManifestConsumerEdge {
                    stage_id: consumer.stage_id.clone(),
                    input_selector: selector.clone(),
                });
            }
        }
        consumer_edges.sort_by(|left, right| {
            left.input_selector
                .cmp(&right.input_selector)
                .then_with(|| left.stage_id.cmp(&right.stage_id))
        });

        StageManifestGraph {
            manifest_id: self.manifest.manifest_id.clone(),
            stage_ids,
            producer_edges,
            consumer_edges,
        }
    }
}
