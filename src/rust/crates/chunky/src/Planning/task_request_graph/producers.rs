//! Producer and root selection for evaluated graph expansion.
//!
//! Producer and root selection for request graph expansion.

use anyhow::Result;
use std::collections::BTreeSet;
use std::fmt::{Display, Formatter};

use crate::artifact_models::{ArtifactKey, PhaseMatchMode};
use crate::stage_manifest::{StageManifest, StageProducer};

use super::binding_models::ArtifactResolution;
use super::request_models::TaskRequestContext;
use super::shared_models::{instance_id, ProducerSelection};

#[derive(Debug, Clone)]
pub struct ProducerPathUnavailable {
    pub message: String,
}

impl ProducerPathUnavailable {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl Display for ProducerPathUnavailable {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.message)
    }
}

impl std::error::Error for ProducerPathUnavailable {}

#[derive(Clone)]
pub struct ValidProducerCandidate<'ctx, 'a> {
    pub candidate: StageProducer,
    /// Speculative branch that should be committed only when this candidate wins.
    ///
    /// The Rust source keeps this as an erased placeholder because the branch
    /// owns a cloned graph state and is defined in `graph_walker.rs`.
    pub branch: super::graph_walker::SpeculativeGraphBranch<'ctx, 'a>,
    pub binding: ArtifactResolution,
}

#[derive(Clone)]
pub struct InvalidProducerCandidate {
    pub candidate: StageProducer,
    pub error: ProducerPathUnavailable,
}

#[derive(Clone, Default)]
pub struct ProducerCandidateProbeResult<'ctx, 'a> {
    pub valid_candidates: Vec<ValidProducerCandidate<'ctx, 'a>>,
    pub invalid_candidates: Vec<InvalidProducerCandidate>,
}

#[derive(Debug, Clone)]
pub struct AmbiguousProducerSelection {
    pub message: String,
    pub selector: ArtifactKey,
    pub highest_phase: i32,
    pub candidates: Vec<StageProducer>,
    pub valid_candidate_stage_ids: Vec<String>,
    pub invalid_candidate_stage_ids: Vec<String>,
}

impl AmbiguousProducerSelection {
    pub fn new(
        message: impl Into<String>,
        selector: ArtifactKey,
        highest_phase: i32,
        candidates: Vec<StageProducer>,
    ) -> Self {
        Self {
            message: message.into(),
            selector,
            highest_phase,
            candidates,
            valid_candidate_stage_ids: Vec::new(),
            invalid_candidate_stage_ids: Vec::new(),
        }
    }

    pub fn with_probe_result(
        mut self,
        probe_result: &ProducerCandidateProbeResult<'_, '_>,
    ) -> Self {
        self.valid_candidate_stage_ids = probe_result
            .valid_candidates
            .iter()
            .map(|candidate| candidate.candidate.stage_id.clone())
            .collect();
        self.invalid_candidate_stage_ids = probe_result
            .invalid_candidates
            .iter()
            .map(|candidate| candidate.candidate.stage_id.clone())
            .collect();
        self
    }
}

impl Display for AmbiguousProducerSelection {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.message)
    }
}

impl std::error::Error for AmbiguousProducerSelection {}

pub enum ProducerSelectError {
    PathUnavailable(ProducerPathUnavailable),
    Ambiguous(AmbiguousProducerSelection),
    Other(anyhow::Error),
}

impl From<anyhow::Error> for ProducerSelectError {
    fn from(value: anyhow::Error) -> Self {
        Self::Other(value)
    }
}

pub struct ProducerSelector<'a> {
    manifest: &'a StageManifest,
}

impl<'a> ProducerSelector<'a> {
    pub fn new(manifest: &'a StageManifest) -> Self {
        Self { manifest }
    }

    pub fn select_supplied(
        &self,
        state: &TaskRequestContext<'a>,
        selector: &ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> Option<(ArtifactKey, String)> {
        let selected = self.manifest.phase_catalog.highest_matching_key(
            &state
                .supplied_instances
                .keys()
                .cloned()
                .collect::<BTreeSet<_>>(),
            selector,
            phase_match,
        )?;
        let instance_id = instance_id(&selected, "");
        Some((selected, instance_id))
    }

    pub fn select_producer(
        &self,
        selector: &ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> std::result::Result<ProducerSelection<'a>, ProducerSelectError> {
        let (ranked, highest_phase) = self.ranked_candidates(selector, phase_match)?;

        let default_stage_id = self.manifest.default_producers.get(selector).or_else(|| {
            self.manifest
                .default_producers
                .get(&ranked[0].declared_output_id)
        });

        if let Some(default_stage_id) = default_stage_id {
            for candidate in &ranked {
                if &candidate.stage_id == default_stage_id {
                    return self
                        .selection_from_candidate(selector, candidate)
                        .map_err(Into::into);
                }
            }
        }

        if ranked.len() > 1 {
            let ids = ranked
                .iter()
                .map(|item| item.stage_id.clone())
                .collect::<Vec<_>>()
                .join(", ");
            return Err(ProducerSelectError::Ambiguous(
                AmbiguousProducerSelection::new(
                    format!("ambiguous producers for {selector} at phase {highest_phase}: {ids}"),
                    selector.clone(),
                    highest_phase,
                    ranked,
                ),
            ));
        }

        self.selection_from_candidate(selector, &ranked[0])
            .map_err(Into::into)
    }

    pub fn selection_from_candidate(
        &self,
        selector: &ArtifactKey,
        candidate: &StageProducer,
    ) -> Result<ProducerSelection<'a>> {
        let resolved_output_id = self
            .manifest
            .phase_catalog
            .resolve_output_id(selector, &candidate.declared_output_id)?;
        Ok(ProducerSelection {
            registration: self.manifest.find_stage(&candidate.stage_id)?,
            declared_output_id: candidate.declared_output_id.clone(),
            resolved_output_id,
        })
    }

    fn ranked_candidates(
        &self,
        selector: &ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> std::result::Result<(Vec<StageProducer>, i32), ProducerSelectError> {
        let candidates = self
            .manifest
            .find_producers_for_requirement(selector, phase_match);
        if candidates.is_empty() {
            return Err(ProducerSelectError::PathUnavailable(
                ProducerPathUnavailable::new(format!("no supplied stage can satisfy {selector}")),
            ));
        }

        let highest_phase = candidates
            .iter()
            .map(|candidate| {
                candidate
                    .declared_output_id
                    .phase
                    .map(|phase| phase as i32)
                    .unwrap_or(-1)
            })
            .max()
            .unwrap_or(-1);

        let phase_valid: Vec<StageProducer> = candidates
            .into_iter()
            .filter(|candidate| {
                candidate
                    .declared_output_id
                    .phase
                    .map(|phase| phase as i32)
                    .unwrap_or(-1)
                    == highest_phase
            })
            .collect();

        let default_stage_id = self.manifest.default_producers.get(selector).or_else(|| {
            self.manifest
                .default_producers
                .get(&phase_valid[0].declared_output_id)
        });

        if let Some(default_stage_id) = default_stage_id {
            for candidate in &phase_valid {
                if &candidate.stage_id == default_stage_id {
                    return Ok((vec![candidate.clone()], highest_phase));
                }
            }
        }

        let mut ranked = phase_valid;
        ranked.sort_by(|left, right| left.stage_id.cmp(&right.stage_id));
        Ok((ranked, highest_phase))
    }
}
