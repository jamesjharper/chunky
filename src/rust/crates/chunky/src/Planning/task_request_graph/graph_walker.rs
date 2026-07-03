//! Fixed-point request graph expansion.
//!
//! Fixed-point request graph expansion.

use anyhow::{anyhow, bail, Result};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet, VecDeque};

use crate::artifact_models::{ArtifactKey, ArtifactRequirement, ArtifactRole};
use crate::stage_manifest::StageProducer;
use crate::stage_models::ResolvedSettingsView;

use super::binding_models::{ArtifactRequirementResult, ArtifactResolution, MissingOptionalArtifact};
use super::graph_state_models::GraphBuildState;
use super::producers::{
    AmbiguousProducerSelection, InvalidProducerCandidate, ProducerCandidateProbeResult,
    ProducerPathUnavailable, ProducerSelectError, ProducerSelector, ValidProducerCandidate,
};
use super::request_models::TaskRequestContext;
use super::requirement_models::{ArtifactNeed, ProducedArtifact};
use super::settings::PlannerSettingsResolver;
use super::shared_models::ProducerSelection;

/// Fixed-point expansion for one mutable graph-build state.
pub struct StageManifestGraphWalker<'ctx, 'a> {
    context: &'ctx TaskRequestContext<'a>,
    settings_resolver: &'ctx PlannerSettingsResolver<'a>,
    producer_selector: &'ctx ProducerSelector<'a>,
    state: GraphBuildState<'a>,
    work: GraphWorkQueue,
}

impl<'ctx, 'a> StageManifestGraphWalker<'ctx, 'a> {
    pub fn new(
        context: &'ctx TaskRequestContext<'a>,
        settings_resolver: &'ctx PlannerSettingsResolver<'a>,
        producer_selector: &'ctx ProducerSelector<'a>,
        work_queue: GraphWorkQueue,
        initial_state: Option<GraphBuildState<'a>>,
    ) -> Self {
        Self {
            context,
            settings_resolver,
            producer_selector,
            state: initial_state.unwrap_or_default(),
            work: work_queue,
        }
    }

    pub fn expand(mut self) -> Result<GraphBuildState<'a>> {
        while self.work.has_pending_work() {
            while self.work.has_unmet_artifact_requirements() {
                let work_item = self.work.pop_unmet_artifact_requirement()?;
                self.resolve_artifact_requirement(work_item)?;
            }

            while self.work.has_unresolved_invocation() && !self.work.has_unmet_artifact_requirements() {
                let candidate_key = self.work.pop_unresolved_invocation()?;
                let requirements = InvocationRequirementResolver::new(
                    self.context,
                    self.settings_resolver,
                    &mut self.state,
                )
                .resolve(&candidate_key)?;
                self.work.add_requirements_to_plan(requirements);
            }
        }
        Ok(self.state)
    }

    fn resolve_artifact_requirement(&mut self, work_item: GraphWorkItem) -> Result<()> {
        let need = work_item.need;
        let result = ArtifactRequirementResolver::new(
            self.context,
            self.settings_resolver,
            self.producer_selector,
            &mut self.state,
        )
        .resolve(&need, work_item.force_required)?;

        let ArtifactRequirementResult::Resolved(result) = result else {
            return Ok(());
        };

        if need.is_invocation_input() {
            self.state.record_invocation_input_binding(&need, &result)?;
        } else if need.is_requested_output() {
            let requested_output_id = need
                .requested_output_id
                .clone()
                .ok_or_else(|| anyhow!("requested output need missing requested id"))?;
            self.state.record_requested_output_binding(requested_output_id, &result);
        }

        if let Some(producer_key) = &result.producer_invocation_key {
            self.work.queue_invocation(producer_key.clone());
        }

        Ok(())
    }
}

/// Clone/expand/commit wrapper used to validate candidate producer paths.
#[derive(Clone)]
pub struct SpeculativeGraphBranch<'ctx, 'a> {
    context: &'ctx TaskRequestContext<'a>,
    settings_resolver: &'ctx PlannerSettingsResolver<'a>,
    producer_selector: &'ctx ProducerSelector<'a>,
    parent_state: *mut GraphBuildState<'a>,
    state: GraphBuildState<'a>,
    active: bool,
}

impl<'ctx, 'a> SpeculativeGraphBranch<'ctx, 'a> {
    pub fn new(
        context: &'ctx TaskRequestContext<'a>,
        settings_resolver: &'ctx PlannerSettingsResolver<'a>,
        producer_selector: &'ctx ProducerSelector<'a>,
        parent_state: &mut GraphBuildState<'a>,
    ) -> Self {
        let state = parent_state.clone();
        Self {
            context,
            settings_resolver,
            producer_selector,
            parent_state,
            state,
            active: true,
        }
    }

    fn ensure_active(&self) -> Result<()> {
        if !self.active {
            bail!("speculative planning branch has already been closed");
        }
        Ok(())
    }

    fn expand_queue(&mut self, work_queue: GraphWorkQueue) -> Result<()> {
        self.ensure_active()?;
        self.state = StageManifestGraphWalker::new(
            self.context,
            self.settings_resolver,
            self.producer_selector,
            work_queue,
            Some(self.state.clone()),
        )
        .expand()?;
        Ok(())
    }

    pub fn commit(mut self) -> Result<()> {
        self.ensure_active()?;
        // Speculative branches currently replace parent state in place. A later
        // refactor should model branch ownership explicitly and remove the raw
        // pointer.
        unsafe {
            (*self.parent_state).replace_with(&self.state);
        }
        self.active = false;
        Ok(())
    }

    pub fn discard(mut self) -> Result<()> {
        self.ensure_active()?;
        self.active = false;
        Ok(())
    }

    pub fn resolved_input_binding(&self, need: &ArtifactNeed) -> Result<ArtifactResolution> {
        self.ensure_active()?;
        let binding = self.state.input_binding_for(need).ok_or_else(|| {
            anyhow!(
                "optional probe for {} succeeded without recording a consumer binding",
                need.requirement.artifact_id
            )
        })?;
        Ok(ArtifactResolution::from_input_binding(binding))
    }

    pub fn expand_from_requirement(&mut self, need: ArtifactNeed, force_required: bool) -> Result<()> {
        self.expand_queue(GraphWorkQueue::for_requirement(need, force_required))
    }

    pub fn expand_from_requirement_producer(
        &mut self,
        need: &ArtifactNeed,
        selection: ProducerSelection<'a>,
    ) -> Result<ArtifactResolution> {
        self.ensure_active()?;
        let producer_context = self.settings_resolver.build_stage_context(
            self.context,
            &selection.registration,
            vec![selection.declared_output_id.clone()],
            need.inherited_settings.clone(),
        )?;
        let producer_artifact = ProducedArtifact::for_selection(&producer_context, selection);

        if let Some(existing_producer) = self
            .state
            .find_invocation_key_by_instance(&producer_artifact.resolved_instance_id)
        {
            return Ok(ArtifactResolution::produced(
                producer_artifact.resolved_artifact_id,
                producer_artifact.resolved_instance_id,
                existing_producer,
            ));
        }

        let invocation_key = self
            .state
            .find_or_create_invocation(producer_artifact.clone(), need.inherited_settings.clone())?
            .key
            .clone();

        Ok(ArtifactResolution::produced(
            producer_artifact.resolved_artifact_id,
            producer_artifact.resolved_instance_id,
            invocation_key,
        ))
    }

    pub fn expand_from_candidate(
        &mut self,
        need: &ArtifactNeed,
        candidate: &StageProducer,
    ) -> Result<ArtifactResolution, ProducerPathUnavailable> {
        self.ensure_active()
            .map_err(|error| ProducerPathUnavailable::new(error.to_string()))?;
        let selection = self
            .producer_selector
            .selection_from_candidate(&need.requirement.artifact_id, candidate)
            .map_err(|error| ProducerPathUnavailable::new(error.to_string()))?;
        let resolution = self
            .expand_from_requirement_producer(need, selection)
            .map_err(|error| ProducerPathUnavailable::new(error.to_string()))?;
        self.expand_from_resolution(&resolution)
            .map_err(|error| ProducerPathUnavailable::new(error.to_string()))?;
        Ok(resolution)
    }

    pub fn expand_from_resolution(&mut self, resolution: &ArtifactResolution) -> Result<()> {
        if !resolution.is_produced_by_invocation() {
            return Ok(());
        }
        let producer_invocation_key = resolution
            .producer_invocation_key
            .clone()
            .ok_or_else(|| anyhow!("produced resolution missing producer key"))?;
        self.expand_queue(GraphWorkQueue {
            unmet_artifact_requirements: VecDeque::new(),
            unresolved_stages: VecDeque::from([producer_invocation_key.clone()]),
            unresolved_stage_keys: BTreeSet::from([producer_invocation_key]),
        })
    }
}

#[derive(Clone, Debug)]
pub struct GraphWorkItem {
    pub need: ArtifactNeed,
    pub force_required: bool,
}

#[derive(Clone, Debug, Default)]
pub struct GraphWorkQueue {
    pub unmet_artifact_requirements: VecDeque<GraphWorkItem>,
    pub unresolved_stages: VecDeque<String>,
    pub unresolved_stage_keys: BTreeSet<String>,
}

impl GraphWorkQueue {
    pub fn from_requested_outputs(
        requested_outputs: impl IntoIterator<Item = ArtifactKey>,
        requirement_factory: impl Fn(ArtifactKey) -> ArtifactRequirement,
    ) -> Self {
        let unmet_artifact_requirements = requested_outputs
            .into_iter()
            .map(|output_id| GraphWorkItem {
                need: ArtifactNeed {
                    requirement: requirement_factory(output_id.clone()),
                    inherited_settings: Vec::new(),
                    consumer_invocation_key: None,
                    requested_output_id: Some(output_id),
                },
                force_required: false,
            })
            .collect();

        Self {
            unmet_artifact_requirements,
            unresolved_stages: VecDeque::new(),
            unresolved_stage_keys: BTreeSet::new(),
        }
    }

    pub fn for_requirement(requirement: ArtifactNeed, force_required: bool) -> Self {
        Self {
            unmet_artifact_requirements: VecDeque::from([GraphWorkItem {
                need: requirement,
                force_required,
            }]),
            unresolved_stages: VecDeque::new(),
            unresolved_stage_keys: BTreeSet::new(),
        }
    }

    pub fn has_pending_work(&self) -> bool {
        !self.unmet_artifact_requirements.is_empty() || !self.unresolved_stages.is_empty()
    }

    pub fn has_unmet_artifact_requirements(&self) -> bool {
        !self.unmet_artifact_requirements.is_empty()
    }

    pub fn has_unresolved_invocation(&self) -> bool {
        !self.unresolved_stages.is_empty()
    }

    pub fn pop_unmet_artifact_requirement(&mut self) -> Result<GraphWorkItem> {
        self.unmet_artifact_requirements
            .pop_front()
            .ok_or_else(|| anyhow!("graph work queue has no unmet artifact requirement"))
    }

    pub fn queue_invocation(&mut self, candidate_key: impl Into<String>) {
        let candidate_key = candidate_key.into();
        if self.unresolved_stage_keys.insert(candidate_key.clone()) {
            self.unresolved_stages.push_back(candidate_key);
        }
    }

    pub fn pop_unresolved_invocation(&mut self) -> Result<String> {
        let candidate_key = self
            .unresolved_stages
            .pop_front()
            .ok_or_else(|| anyhow!("graph work queue has no unresolved invocation"))?;
        self.unresolved_stage_keys.remove(&candidate_key);
        Ok(candidate_key)
    }

    pub fn add_requirements_to_plan(&mut self, requirements: impl IntoIterator<Item = ArtifactNeed>) {
        self.unmet_artifact_requirements.extend(
            requirements.into_iter().map(|need| GraphWorkItem {
                need,
                force_required: false,
            }),
        );
    }
}

pub struct ArtifactRequirementResolver<'s, 'ctx, 'a> {
    context: &'ctx TaskRequestContext<'a>,
    settings_resolver: &'ctx PlannerSettingsResolver<'a>,
    producer_selector: &'ctx ProducerSelector<'a>,
    state: &'s mut GraphBuildState<'a>,
}

impl<'s, 'ctx, 'a> ArtifactRequirementResolver<'s, 'ctx, 'a> {
    pub fn new(
        context: &'ctx TaskRequestContext<'a>,
        settings_resolver: &'ctx PlannerSettingsResolver<'a>,
        producer_selector: &'ctx ProducerSelector<'a>,
        state: &'s mut GraphBuildState<'a>,
    ) -> Self {
        Self { context, settings_resolver, producer_selector, state }
    }

    pub fn resolve(&mut self, need: &ArtifactNeed, force_required: bool) -> Result<ArtifactRequirementResult> {
        if let Some(setting_resolution) = self.resolve_inherited_setting_artifact(need)? {
            return Ok(setting_resolution.into());
        }

        if let Some(supplied_resolution) = self.resolve_supplied_artifact(need) {
            return Ok(supplied_resolution.into());
        }

        if self.is_optional_source(need) {
            return Ok(MissingOptionalArtifact { artifact_id: need.requirement.artifact_id.clone() }.into());
        }

        if !need.is_required() && !force_required {
            return self.try_expand_and_resolve_optional_requirement(need);
        }

        self.resolve_artifact_producer(need).map(Into::into)
    }

    fn resolve_inherited_setting_artifact(&self, need: &ArtifactNeed) -> Result<Option<ArtifactResolution>> {
        let selector = &need.requirement.artifact_id;
        let declaration = self.context.manifest.artifacts.get(selector);
        if !matches!(declaration.map(|decl| decl.role), Some(ArtifactRole::Settings)) {
            return Ok(None);
        }

        let inherited_matches: Vec<ArtifactKey> = need
            .inherited_settings
            .iter()
            .filter(|(key, _)| key.without_override() == selector.without_override())
            .map(|(key, _)| key.clone())
            .collect();

        if inherited_matches.is_empty() {
            return Ok(None);
        }
        if inherited_matches.len() > 1 {
            let rendered = inherited_matches
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join(", ");
            bail!("multiple inherited settings can satisfy {selector}: {rendered}");
        }

        let inherited_key = inherited_matches[0].clone();
        if !self.context.supplied_instances.contains_key(&inherited_key) {
            bail!(
                "inherited setting {inherited_key} was selected for {selector} but has no supplied/default artifact instance"
            );
        }

        Ok(Some(ArtifactResolution::supplied_setting(inherited_key)))
    }

    fn resolve_supplied_artifact(&self, need: &ArtifactNeed) -> Option<ArtifactResolution> {
        let requirement = &need.requirement;
        let (supplied_id, supplied_instance_id) = self.producer_selector.select_supplied(
            self.context,
            &requirement.artifact_id,
            requirement.phase_match,
        )?;
        Some(ArtifactResolution::supplied(supplied_id, supplied_instance_id))
    }

    fn resolve_producer_artifact(&self, need: &ArtifactNeed) -> Result<ProducedArtifact<'a>, ProducerSelectError> {
        let selection = self.producer_selector.select_producer(
            &need.requirement.artifact_id,
            need.requirement.phase_match,
        )?;
        let producer_context = self
            .settings_resolver
            .build_stage_context(
                self.context,
                &selection.registration,
                vec![selection.declared_output_id.clone()],
                need.inherited_settings.clone(),
            )
            .map_err(ProducerSelectError::from)?;
        Ok(ProducedArtifact::for_selection(&producer_context, selection))
    }

    fn resolve_artifact_producer(&mut self, need: &ArtifactNeed) -> Result<ArtifactResolution> {
        let producer_artifact = match self.resolve_producer_artifact(need) {
            Ok(producer_artifact) => producer_artifact,
            Err(ProducerSelectError::Ambiguous(ambiguity)) => {
                return self.try_expand_and_resolve_ambiguous_producer(need, ambiguity);
            }
            Err(ProducerSelectError::PathUnavailable(error)) => return Err(anyhow!(error)),
            Err(ProducerSelectError::Other(error)) => return Err(error),
        };

        if let Some(existing_producer) = self.state.find_invocation_key_by_instance(&producer_artifact.resolved_instance_id) {
            return Ok(ArtifactResolution::produced(
                producer_artifact.resolved_artifact_id,
                producer_artifact.resolved_instance_id,
                existing_producer,
            ));
        }

        let invocation_key = self
            .state
            .find_or_create_invocation(producer_artifact.clone(), need.inherited_settings.clone())?
            .key
            .clone();

        Ok(ArtifactResolution::produced(
            producer_artifact.resolved_artifact_id,
            producer_artifact.resolved_instance_id,
            invocation_key,
        ))
    }

    fn try_expand_and_resolve_optional_requirement(&mut self, need: &ArtifactNeed) -> Result<ArtifactRequirementResult> {
        let mut branch = SpeculativeGraphBranch::new(
            self.context,
            self.settings_resolver,
            self.producer_selector,
            self.state,
        );
        if let Err(error) = branch.expand_from_requirement(need.clone(), true) {
            let _ = branch.discard();
            let _ = error;
            return Ok(MissingOptionalArtifact { artifact_id: need.requirement.artifact_id.clone() }.into());
        }

        let resolution = branch.resolved_input_binding(need)?;
        branch.commit()?;
        Ok(resolution.into())
    }

    fn try_expand_and_resolve_ambiguous_producer(
        &mut self,
        need: &ArtifactNeed,
        ambiguity: AmbiguousProducerSelection,
    ) -> Result<ArtifactResolution> {
        let probe_result = self.probe_producer_candidates(need, &ambiguity.candidates);

        if probe_result.valid_candidates.len() == 1 {
            let winner = probe_result.valid_candidates.into_iter().next().expect("one winner");
            let binding = winner.binding.clone();
            winner.branch.commit()?;
            return Ok(binding);
        }

        let has_valid_candidates = !probe_result.valid_candidates.is_empty();
        let ambiguity = ambiguity.with_probe_result(&probe_result);
        for valid_candidate in probe_result.valid_candidates {
            let _ = valid_candidate.branch.discard();
        }

        if !has_valid_candidates {
            let invalid_stage_ids = probe_result
                .invalid_candidates
                .iter()
                .map(|candidate| candidate.candidate.stage_id.clone())
                .collect::<Vec<_>>()
                .join(", ");
            return Err(anyhow!(ProducerPathUnavailable::new(format!(
                "no valid producer path can satisfy {}: {}",
                need.requirement.artifact_id, invalid_stage_ids
            ))));
        }

        Err(anyhow!(ambiguity))
    }

    fn probe_producer_candidates(
        &mut self,
        need: &ArtifactNeed,
        candidates: &[StageProducer],
    ) -> ProducerCandidateProbeResult<'ctx, 'a> {
        let mut valid_candidates = Vec::new();
        let mut invalid_candidates = Vec::new();

        for candidate in candidates {
            let mut branch = SpeculativeGraphBranch::new(
                self.context,
                self.settings_resolver,
                self.producer_selector,
                self.state,
            );
            match branch.expand_from_candidate(need, candidate) {
                Ok(resolution) => valid_candidates.push(ValidProducerCandidate {
                    candidate: candidate.clone(),
                    branch,
                    binding: resolution,
                }),
                Err(error) => {
                    let _ = branch.discard();
                    invalid_candidates.push(InvalidProducerCandidate {
                        candidate: candidate.clone(),
                        error,
                    });
                }
            }
        }

        ProducerCandidateProbeResult { valid_candidates, invalid_candidates }
    }

    fn is_optional_source(&self, need: &ArtifactNeed) -> bool {
        let declaration = self.context.manifest.artifacts.get(&need.requirement.artifact_id);
        matches!(declaration.map(|decl| decl.role), Some(ArtifactRole::Source)) && !need.is_required()
    }
}

pub struct InvocationRequirementResolver<'s, 'ctx, 'a> {
    context: &'ctx TaskRequestContext<'a>,
    settings_resolver: &'ctx PlannerSettingsResolver<'a>,
    state: &'s mut GraphBuildState<'a>,
}

impl<'s, 'ctx, 'a> InvocationRequirementResolver<'s, 'ctx, 'a> {
    pub fn new(
        context: &'ctx TaskRequestContext<'a>,
        settings_resolver: &'ctx PlannerSettingsResolver<'a>,
        state: &'s mut GraphBuildState<'a>,
    ) -> Self {
        Self { context, settings_resolver, state }
    }

    pub fn resolve(&mut self, invocation_key: &str) -> Result<Vec<ArtifactNeed>> {
        let candidate = self.state.find_invocation_by_key(invocation_key)?.clone();
        if !candidate.has_unresolved_requirements() {
            return Ok(Vec::new());
        }

        let declared_outputs: Vec<ArtifactKey> = candidate.output_bindings.keys().cloned().collect();
        let context = self.settings_resolver.build_stage_context(
            self.context,
            &candidate.registration,
            declared_outputs.clone(),
            candidate.inherited_settings.clone(),
        )?;

        let stage_output_capabilities = candidate.registration.resolve_validated_output_capabilities(&context)?;
        let stage_requirements = candidate.registration.resolve_validated_requirements(&context)?;
        let resource_policy = candidate.registration.resolve_validated_resource_policy(&context)?;

        self.validate_stage_can_produce_outputs(
            candidate.registration.stage_id(),
            &declared_outputs,
            &stage_output_capabilities.iter().map(|capability| capability.artifact_id.clone()).collect(),
        )?;

        let pending_requirements = self.input_requirements_for_invocation(
            &candidate,
            stage_requirements.inputs,
        )?;
        let candidate = self.state.find_invocation_by_key_mut(invocation_key)?;
        candidate.mark_current_requirements_as_resolved();
        candidate.resolved_settings = context.resolved_settings;
        candidate.resource_policy = Some(resource_policy);
        candidate.input_bindings.clear();

        Ok(pending_requirements)
    }

    fn validate_stage_can_produce_outputs(
        &self,
        stage_id: &str,
        declared_outputs: &[ArtifactKey],
        output_capability_ids: &BTreeSet<ArtifactKey>,
    ) -> Result<()> {
        let missing: Vec<ArtifactKey> = declared_outputs
            .iter()
            .filter(|artifact_id| !output_capability_ids.contains(*artifact_id))
            .cloned()
            .collect();
        if !missing.is_empty() {
            let rendered = missing.iter().map(ToString::to_string).collect::<Vec<_>>().join(", ");
            bail!("{stage_id} cannot expose requested outputs: {rendered}");
        }
        Ok(())
    }

    fn input_requirements_for_invocation(
        &self,
        invocation: &super::invocation_models::InvocationDraft<'a>,
        requirements: Vec<ArtifactRequirement>,
    ) -> Result<Vec<ArtifactNeed>> {
        let inherited_view = ResolvedSettingsView::new(invocation.inherited_settings.clone().into_iter().collect());
        let settings_used_by_this_stage = invocation.registration.declared_setting_ids();
        let pass_through_settings = inherited_view.remove(&settings_used_by_this_stage);
        let merged_settings = self.context.request_settings.merge_with_inherited(&inherited_view);

        let mut pending_inputs = Vec::new();
        for requirement in requirements {
            let inherited_settings = self.resolve_inherited_settings_for_input_requirement(
                &requirement,
                invocation,
                &pass_through_settings,
                &merged_settings,
            )?;
            pending_inputs.push(ArtifactNeed {
                requirement,
                inherited_settings,
                consumer_invocation_key: Some(invocation.key.clone()),
                requested_output_id: None,
            });
        }
        Ok(pending_inputs)
    }

    fn resolve_inherited_settings_for_input_requirement(
        &self,
        requirement: &ArtifactRequirement,
        invocation: &super::invocation_models::InvocationDraft<'a>,
        pass_through_settings: &ResolvedSettingsView,
        merged_settings: &ResolvedSettingsView,
    ) -> Result<Vec<(ArtifactKey, Value)>> {
        let mut inherited_settings: BTreeMap<ArtifactKey, Value> = if self.is_settings_artifact(&requirement.artifact_id) {
            invocation.inherited_settings.clone().into_iter().collect()
        } else {
            pass_through_settings.items().into_iter().collect()
        };

        for pushed in &requirement.pushed_settings {
            inherited_settings.insert(pushed.clone(), merged_settings.require(pushed)?.clone());
        }

        Ok(inherited_settings.into_iter().collect())
    }

    fn is_settings_artifact(&self, artifact_id: &ArtifactKey) -> bool {
        let declaration = self.context.manifest.artifacts.get(artifact_id);
        matches!(declaration.map(|decl| decl.role), Some(ArtifactRole::Settings))
    }
}
