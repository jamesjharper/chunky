//! This module keeps bootstrap-time registration, manifest validation, producer
//! indexes, phase-family lookup, and immutable manifest description together,
//! The manifest owns registration, validation, producer indexes, and immutable
//! planning metadata.

use crate::artifact_models::{
    AnyArtifactDeclaration, ArtifactCapability, ArtifactKey, ArtifactRole, PhaseMatchMode,
};
use crate::fingerprints::FingerprintProvider;
use crate::phase_catalog::ArtifactPhaseCatalog;
use crate::stage_models::{
    FeatureRecord, ManifestStageRegistration, PipelineFeature, StageConcurrencyPolicy,
    StageManifestBuilderAccess, StageRegistration,
};
use anyhow::{anyhow, bail, Result};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StageProducer {
    pub stage_id: String,
    pub declared_output_id: ArtifactKey,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StageConsumer {
    pub stage_id: String,
}

#[derive(Clone)]
pub struct StageManifest {
    pub manifest_id: String,
    pub features: BTreeMap<String, FeatureRecord>,
    pub stages: BTreeMap<String, StageRegistration>,
    pub artifacts: BTreeMap<ArtifactKey, AnyArtifactDeclaration>,
    pub producers: BTreeMap<ArtifactKey, Vec<StageProducer>>,
    pub consumers: BTreeMap<ArtifactKey, Vec<StageConsumer>>,
    pub default_producers: BTreeMap<ArtifactKey, String>,
    pub phase_catalog: ArtifactPhaseCatalog,
    description: Value,
}

impl StageManifest {
    pub const MANIFEST_VERSION: &'static str = "1";

    pub fn new(
        manifest_id: impl Into<String>,
        features: BTreeMap<String, FeatureRecord>,
        stages: BTreeMap<String, StageRegistration>,
        artifacts: BTreeMap<ArtifactKey, AnyArtifactDeclaration>,
        producers: BTreeMap<ArtifactKey, Vec<StageProducer>>,
        consumers: BTreeMap<ArtifactKey, Vec<StageConsumer>>,
        default_producers: BTreeMap<ArtifactKey, String>,
        description: Value,
    ) -> Self {
        let phase_catalog = ArtifactPhaseCatalog::new(artifacts.clone());
        Self {
            manifest_id: manifest_id.into(),
            features,
            stages,
            artifacts,
            producers,
            consumers,
            default_producers,
            phase_catalog,
            description,
        }
    }

    pub fn find_artifact(&self, selector: &ArtifactKey) -> Result<AnyArtifactDeclaration> {
        self.artifacts
            .get(selector)
            .cloned()
            .ok_or_else(|| anyhow!("unknown concrete artifact declaration: {selector}"))
    }

    pub fn find_artifacts_by_role(
        &self,
        artifact_ids: impl IntoIterator<Item = ArtifactKey>,
        role: ArtifactRole,
    ) -> BTreeSet<ArtifactKey> {
        artifact_ids
            .into_iter()
            .filter(|artifact_id| {
                self.artifacts
                    .get(artifact_id)
                    .is_some_and(|declaration| declaration.role == role)
            })
            .collect()
    }

    pub fn find_stage(&self, stage_id: &str) -> Result<ManifestStageRegistration<'_>> {
        let registration = self
            .stages
            .get(stage_id)
            .ok_or_else(|| anyhow!("unknown stage registration: {stage_id}"))?;
        Ok(ManifestStageRegistration {
            manifest: self,
            registration,
        })
    }

    pub fn find_producers_for_requirement(
        &self,
        selector: &ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> Vec<StageProducer> {
        let declaration = self.artifacts.get(selector);
        if declaration.is_some_and(|declaration| {
            declaration.role != ArtifactRole::Intermediate
                || selector.phase.is_none()
                || phase_match == PhaseMatchMode::Exact
        }) {
            return self.producers.get(selector).cloned().unwrap_or_default();
        }

        let candidate_keys = self
            .phase_catalog
            .matching_concrete_keys(selector, phase_match);
        if candidate_keys.is_empty() {
            return self.producers.get(selector).cloned().unwrap_or_default();
        }

        let mut candidates = Vec::new();
        for key in candidate_keys {
            if let Some(producers) = self.producers.get(&key) {
                candidates.extend(producers.clone());
            }
        }
        candidates.sort_by(|left, right| {
            let left_phase = left.declared_output_id.phase.unwrap_or(0);
            let right_phase = right.declared_output_id.phase.unwrap_or(0);
            right_phase
                .cmp(&left_phase)
                .then_with(|| left.stage_id.cmp(&right.stage_id))
        });
        candidates
    }

    pub fn find_output_declaration(
        &self,
        declared_output_id: &ArtifactKey,
        resolved_output_id: &ArtifactKey,
    ) -> Result<AnyArtifactDeclaration> {
        let declaration = self.find_artifact(declared_output_id)?;
        if declared_output_id == resolved_output_id {
            return Ok(declaration);
        }

        self.phase_catalog
            .validate_resolved_output(declared_output_id, resolved_output_id)
            .map_err(|error| {
                anyhow!(
                    "invalid terminal remapping from {} to {}: {error}",
                    declared_output_id,
                    resolved_output_id
                )
            })?;

        // The Python example returns a declaration copy with the resolved
        // phase-less identity but the selected producer's concrete codec/schema.
        // The current Rust artifact example stores `artifact_id` inside an
        // erased Arc, so this source-level port exposes the validation seam and
        // returns the concrete declaration. A production Rust implementation
        // should provide an `AnyArtifactDeclaration::with_artifact_id` helper.
        Ok(declaration)
    }

    pub fn concrete_artifact_is_known(&self, selector: &ArtifactKey) -> bool {
        self.phase_catalog.concrete_artifact_is_known(selector)
    }

    pub fn selector_artifact_is_known(&self, selector: &ArtifactKey) -> bool {
        self.phase_catalog.selector_artifact_is_known(selector)
    }

    pub fn describe(&self) -> Value {
        self.description.clone()
    }

    pub fn describe_json(&self) -> Result<String> {
        Ok(serde_json::to_string_pretty(&self.description)?)
    }
}

#[derive(Default)]
pub struct StageManifestBuilder {
    features: BTreeMap<String, FeatureRecord>,
    installing: BTreeSet<String>,
    artifacts: BTreeMap<ArtifactKey, AnyArtifactDeclaration>,
    stages: BTreeMap<String, StageRegistration>,
    default_producers: BTreeMap<ArtifactKey, String>,
    frozen: bool,
}

impl StageManifestBuilder {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn install_feature(&mut self, feature: &dyn PipelineFeature) -> Result<&mut Self> {
        self.assert_mutable()?;
        let feature_id = non_empty(feature.feature_id(), "feature_id")?;
        let configuration_fingerprint = non_empty(
            feature.configuration_fingerprint(),
            &format!("feature {feature_id} configuration_fingerprint"),
        )?;

        if self.features.contains_key(&feature_id) {
            bail!("duplicate feature installation: {feature_id:?}");
        }
        if self.installing.contains(&feature_id) {
            bail!("recursive feature dependency: {feature_id}");
        }

        self.installing.insert(feature_id.clone());
        let install_result = feature.install(self);
        self.installing.remove(&feature_id);
        install_result?;
        self.features.insert(
            feature_id.clone(),
            FeatureRecord {
                feature_id,
                configuration_fingerprint,
            },
        );
        Ok(self)
    }

    pub fn add_artifact(&mut self, declaration: AnyArtifactDeclaration) -> Result<&mut Self> {
        self.assert_mutable()?;
        match self.artifacts.get(&declaration.artifact_id) {
            None => {
                self.artifacts
                    .insert(declaration.artifact_id.clone(), declaration);
            }
            Some(existing) if artifact_signature(existing) == artifact_signature(&declaration) => {}
            Some(_) => {
                bail!(
                    "conflicting declarations for artifact {}",
                    declaration.artifact_id
                );
            }
        }
        Ok(self)
    }

    pub fn add_stage(&mut self, registration: StageRegistration) -> Result<&mut Self> {
        self.assert_mutable()?;
        if self.stages.contains_key(&registration.stage_id) {
            bail!("duplicate stage id: {}", registration.stage_id);
        }
        for declaration in &registration.owned_artifacts {
            self.add_artifact(declaration.clone())?;
        }
        self.stages
            .insert(registration.stage_id.clone(), registration);
        Ok(self)
    }

    pub fn set_default_producer(
        &mut self,
        artifact_id: ArtifactKey,
        stage_id: impl Into<String>,
    ) -> Result<&mut Self> {
        self.assert_mutable()?;
        self.default_producers.insert(artifact_id, stage_id.into());
        Ok(self)
    }

    pub fn validate(&mut self) -> Result<&mut Self> {
        self.assert_mutable()?;
        validate_registry(&self.artifacts, &self.stages, &self.default_producers)?;
        Ok(self)
    }

    pub fn freeze(&mut self) -> Result<StageManifest> {
        self.assert_mutable()?;
        self.validate()?;
        let producers = build_producer_index(&self.stages);
        let consumers = build_consumer_index(&self.stages);
        let mut description = build_description(
            &self.features,
            &self.stages,
            &self.artifacts,
            &self.default_producers,
        );

        let manifest_id = FingerprintProvider
            .build_manifest_fingerprint(&description, StageManifest::MANIFEST_VERSION)?;
        if let Value::Object(ref mut object) = description {
            object.insert(
                "manifest_version".to_owned(),
                Value::String(StageManifest::MANIFEST_VERSION.to_owned()),
            );
            object.insert("manifest_id".to_owned(), Value::String(manifest_id.clone()));
        }
        self.frozen = true;

        Ok(StageManifest::new(
            manifest_id,
            self.features.clone(),
            self.stages.clone(),
            self.artifacts.clone(),
            producers,
            consumers,
            self.default_producers.clone(),
            description,
        ))
    }

    fn assert_mutable(&self) -> Result<()> {
        if self.frozen {
            bail!("stage manifest builder is already frozen");
        }
        Ok(())
    }
}

impl StageManifestBuilderAccess for StageManifestBuilder {
    fn add_artifact_boxed(&mut self, declaration: AnyArtifactDeclaration) -> Result<()> {
        self.add_artifact(declaration).map(|_| ())
    }

    fn add_stage_boxed(&mut self, registration: StageRegistration) -> Result<()> {
        self.add_stage(registration).map(|_| ())
    }
}

fn validate_registry(
    artifacts: &BTreeMap<ArtifactKey, AnyArtifactDeclaration>,
    stages: &BTreeMap<String, StageRegistration>,
    default_producers: &BTreeMap<ArtifactKey, String>,
) -> Result<()> {
    if stages.is_empty() {
        bail!("manifest must contain at least one stage");
    }

    let phase_catalog = ArtifactPhaseCatalog::new(artifacts.clone());
    for (key, declaration) in artifacts {
        if declaration.role == ArtifactRole::Intermediate
            && key.phase.is_none()
            && phase_catalog.family_has_phases(&key.family_key())
        {
            bail!(
                "a phased intermediate family is declared through concrete phases; the completed selector must not be separately declared: {key}"
            );
        }
    }

    for (stage_id, registration) in stages {
        non_empty(stage_id, "stage_id")?;
        non_empty(
            &registration.display_name,
            &format!("stage {stage_id} display_name"),
        )?;
        non_empty(
            &registration.registration_version,
            &format!("stage {stage_id} registration_version"),
        )?;
        non_empty(
            &registration.behavior_version,
            &format!("stage {stage_id} behavior_version"),
        )?;

        let owned_ids: Vec<_> = registration
            .owned_artifacts
            .iter()
            .map(|declaration| declaration.artifact_id.clone())
            .collect();
        if has_duplicates(&owned_ids) {
            bail!("stage {stage_id} owns a duplicate artifact declaration");
        }
        for declaration in &registration.owned_artifacts {
            let authoritative = artifacts.get(&declaration.artifact_id).ok_or_else(|| {
                anyhow!(
                    "stage {stage_id} has an inconsistent owned artifact: {}",
                    declaration.artifact_id
                )
            })?;
            if artifact_signature(authoritative) != artifact_signature(declaration) {
                bail!(
                    "stage {stage_id} has an inconsistent owned artifact: {}",
                    declaration.artifact_id
                );
            }
        }

        validate_input_capabilities(
            stage_id,
            &registration.input_capabilities,
            artifacts,
            &phase_catalog,
        )?;
        validate_output_capabilities(
            stage_id,
            &registration.output_capabilities,
            artifacts,
            &phase_catalog,
        )?;
    }

    let temporary = StageManifest::new(
        "validation",
        BTreeMap::new(),
        stages.clone(),
        artifacts.clone(),
        build_producer_index(stages),
        build_consumer_index(stages),
        default_producers.clone(),
        json!({}),
    );

    validate_unique_exact_producers(&temporary)?;
    for (artifact_id, stage_id) in default_producers {
        if !temporary.selector_artifact_is_known(artifact_id) {
            bail!("default producer references unknown artifact selector: {artifact_id}");
        }
        let candidates: BTreeSet<String> = temporary
            .find_producers_for_requirement(artifact_id, PhaseMatchMode::AtMost)
            .into_iter()
            .map(|producer| producer.stage_id)
            .collect();
        if !candidates.contains(stage_id) {
            bail!("default producer {stage_id:?} cannot satisfy {artifact_id}");
        }
    }

    validate_candidate_graph_has_no_cycles(&temporary)
}

fn validate_input_capabilities(
    stage_id: &str,
    capabilities: &[ArtifactCapability],
    artifacts: &BTreeMap<ArtifactKey, AnyArtifactDeclaration>,
    phase_catalog: &ArtifactPhaseCatalog,
) -> Result<()> {
    let ids: Vec<_> = capabilities
        .iter()
        .map(|capability| capability.artifact_id.clone())
        .collect();
    if has_duplicates(&ids) {
        bail!("stage {stage_id} has duplicate input capabilities");
    }
    for artifact_id in ids {
        if artifacts.contains_key(&artifact_id) {
            continue;
        }
        let phases = phase_catalog.declared_phases_for_family(&artifact_id.family_key());
        if phases.is_empty() {
            bail!(
                "stage {stage_id} input capability references unknown artifact selector: {artifact_id}"
            );
        }
        if let Some(requested_phase) = artifact_id.phase {
            if !phases.iter().any(|phase| *phase <= requested_phase) {
                bail!("stage {stage_id} input capability has no matching phase: {artifact_id}");
            }
        }
    }
    Ok(())
}

fn validate_output_capabilities(
    stage_id: &str,
    capabilities: &[ArtifactCapability],
    artifacts: &BTreeMap<ArtifactKey, AnyArtifactDeclaration>,
    phase_catalog: &ArtifactPhaseCatalog,
) -> Result<()> {
    let ids: Vec<_> = capabilities
        .iter()
        .map(|capability| capability.artifact_id.clone())
        .collect();
    if has_duplicates(&ids) {
        bail!("stage {stage_id} has duplicate output capabilities");
    }
    for artifact_id in ids {
        let declaration = artifacts.get(&artifact_id).ok_or_else(|| {
            anyhow!(
                "stage {stage_id} output capability references unknown concrete artifact: {artifact_id}"
            )
        })?;
        if declaration.role == ArtifactRole::Intermediate
            && phase_catalog.family_has_phases(&artifact_id.family_key())
            && artifact_id.phase.is_none()
        {
            bail!("phased intermediate output must declare a concrete phase: {artifact_id}");
        }
    }
    Ok(())
}

fn build_producer_index(
    stages: &BTreeMap<String, StageRegistration>,
) -> BTreeMap<ArtifactKey, Vec<StageProducer>> {
    build_stage_index(
        stages,
        |registration| &registration.output_capabilities,
        |registration, artifact_id| StageProducer {
            stage_id: registration.stage_id.clone(),
            declared_output_id: artifact_id.clone(),
        },
    )
}

fn validate_unique_exact_producers(manifest: &StageManifest) -> Result<()> {
    for (artifact_id, candidates) in &manifest.producers {
        if candidates.len() <= 1 {
            continue;
        }
        let ids = candidates
            .iter()
            .map(|candidate| candidate.stage_id.clone())
            .collect::<Vec<_>>()
            .join(", ");
        bail!("artifact {artifact_id} has multiple registered producers: {ids}");
    }
    Ok(())
}

fn build_consumer_index(
    stages: &BTreeMap<String, StageRegistration>,
) -> BTreeMap<ArtifactKey, Vec<StageConsumer>> {
    build_stage_index(
        stages,
        |registration| &registration.input_capabilities,
        |registration, _artifact_id| StageConsumer {
            stage_id: registration.stage_id.clone(),
        },
    )
}

fn build_stage_index<T, F, G>(
    stages: &BTreeMap<String, StageRegistration>,
    capabilities_for: F,
    entry_factory: G,
) -> BTreeMap<ArtifactKey, Vec<T>>
where
    T: StageIndexEntry,
    F: Fn(&StageRegistration) -> &[ArtifactCapability],
    G: Fn(&StageRegistration, &ArtifactKey) -> T,
{
    let mut mutable: BTreeMap<ArtifactKey, Vec<T>> = BTreeMap::new();
    for registration in stages.values() {
        for capability in capabilities_for(registration) {
            mutable
                .entry(capability.artifact_id.clone())
                .or_default()
                .push(entry_factory(registration, &capability.artifact_id));
        }
    }
    for values in mutable.values_mut() {
        values.sort_by(|left, right| left.stage_id().cmp(right.stage_id()));
    }
    mutable
}

trait StageIndexEntry {
    fn stage_id(&self) -> &str;
}

impl StageIndexEntry for StageProducer {
    fn stage_id(&self) -> &str {
        &self.stage_id
    }
}

impl StageIndexEntry for StageConsumer {
    fn stage_id(&self) -> &str {
        &self.stage_id
    }
}

fn validate_candidate_graph_has_no_cycles(manifest: &StageManifest) -> Result<()> {
    let mut graph: BTreeMap<String, BTreeSet<String>> = manifest
        .stages
        .keys()
        .map(|stage_id| (stage_id.clone(), BTreeSet::new()))
        .collect();

    for consumer in manifest.stages.values() {
        for capability in &consumer.input_capabilities {
            for producer in manifest
                .find_producers_for_requirement(&capability.artifact_id, PhaseMatchMode::AtMost)
            {
                graph
                    .entry(producer.stage_id)
                    .or_default()
                    .insert(consumer.stage_id.clone());
            }
        }
    }

    let mut visiting = BTreeSet::new();
    let mut visited = BTreeSet::new();
    for stage_id in graph.keys() {
        visit_candidate_stage(
            stage_id,
            &graph,
            &mut visiting,
            &mut visited,
            &mut Vec::new(),
        )?;
    }
    Ok(())
}

fn visit_candidate_stage(
    stage_id: &str,
    graph: &BTreeMap<String, BTreeSet<String>>,
    visiting: &mut BTreeSet<String>,
    visited: &mut BTreeSet<String>,
    path: &mut Vec<String>,
) -> Result<()> {
    if visiting.contains(stage_id) {
        path.push(stage_id.to_owned());
        bail!(
            "static candidate graph contains a cycle: {}",
            path.join(" -> ")
        );
    }
    if visited.contains(stage_id) {
        return Ok(());
    }

    visiting.insert(stage_id.to_owned());
    path.push(stage_id.to_owned());
    for downstream in graph.get(stage_id).into_iter().flatten() {
        visit_candidate_stage(downstream, graph, visiting, visited, path)?;
    }
    path.pop();
    visiting.remove(stage_id);
    visited.insert(stage_id.to_owned());
    Ok(())
}

fn build_description(
    features: &BTreeMap<String, FeatureRecord>,
    stages: &BTreeMap<String, StageRegistration>,
    artifacts: &BTreeMap<ArtifactKey, AnyArtifactDeclaration>,
    default_producers: &BTreeMap<ArtifactKey, String>,
) -> Value {
    let phase_catalog = ArtifactPhaseCatalog::new(artifacts.clone());
    let phased_families = phased_families_from_artifacts(artifacts);

    json!({
        "features": features.values().map(|record| {
            json!({
                "feature_id": record.feature_id.clone(),
                "configuration_fingerprint": record.configuration_fingerprint.clone(),
            })
        }).collect::<Vec<_>>(),
        "stages": stages.values().map(stage_description).collect::<Vec<_>>(),
        "artifacts": artifacts.values().map(artifact_description).collect::<Vec<_>>(),
        "phased_families": phased_families.into_iter().map(|family| {
            let (_override_domain, domain, content_kind) = family.clone();
            json!({
                "family_id": ArtifactKey::new(domain, content_kind, None, None).map(|key| key.to_string()).unwrap_or_default(),
                "declared_phases": phase_catalog.declared_phases_for_family(&family),
            })
        }).collect::<Vec<_>>(),
        "default_producers": default_producers.iter().map(|(artifact_id, stage_id)| {
            json!({"artifact_id": artifact_id.to_string(), "stage_id": stage_id.clone()})
        }).collect::<Vec<_>>(),
    })
}

fn stage_description(registration: &StageRegistration) -> Value {
    json!({
        "stage_id": registration.stage_id.clone(),
        "display_name": registration.display_name.clone(),
        "registration_version": registration.registration_version.clone(),
        "behavior_version": registration.behavior_version.clone(),
        "owned_artifact_ids": sorted_key_strings(registration.owned_artifacts.iter().map(|declaration| declaration.artifact_id.clone())),
        "input_capability_ids": sorted_key_strings(registration.input_capabilities.iter().map(|capability| capability.artifact_id.clone())),
        "output_capability_ids": sorted_key_strings(registration.output_capabilities.iter().map(|capability| capability.artifact_id.clone())),
        "concurrency_policy": concurrency_policy_description(registration.concurrency_policy.as_ref()),
    })
}

fn concurrency_policy_description(policy: Option<&StageConcurrencyPolicy>) -> Value {
    match policy {
        None => Value::Null,
        Some(policy) => json!({
            "max_concurrent_invocations": policy.max_concurrent_invocations,
            "concurrency_key": policy.concurrency_key.clone(),
            "singleton_executor_is_safe": policy.singleton_executor_is_safe,
        }),
    }
}

fn artifact_description(declaration: &AnyArtifactDeclaration) -> Value {
    json!({
        "artifact_id": declaration.artifact_id.to_string(),
        "role": declaration.role.as_str(),
        "version": declaration.version,
        "cacheable": declaration.cacheable,
        "codec": {
            "codec_id": declaration.codec.codec_id(),
            "codec_version": declaration.codec.codec_version(),
            "mime_type": declaration.codec.mime_type(),
            "extension": declaration.codec.extension(),
        },
    })
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct ArtifactSignature {
    artifact_id: ArtifactKey,
    role: ArtifactRole,
    version: u32,
    cacheable: bool,
    codec_id: &'static str,
    codec_version: u32,
    mime_type: &'static str,
    extension: &'static str,
}

fn artifact_signature(declaration: &AnyArtifactDeclaration) -> ArtifactSignature {
    ArtifactSignature {
        artifact_id: declaration.artifact_id.clone(),
        role: declaration.role,
        version: declaration.version,
        cacheable: declaration.cacheable,
        codec_id: declaration.codec.codec_id(),
        codec_version: declaration.codec.codec_version(),
        mime_type: declaration.codec.mime_type(),
        extension: declaration.codec.extension(),
    }
}

fn non_empty(value: &str, field_name: &str) -> Result<String> {
    if value.trim().is_empty() {
        bail!("{field_name} must be a non-empty string");
    }
    Ok(value.to_owned())
}

fn has_duplicates<T: Ord + Clone>(values: &[T]) -> bool {
    let unique: BTreeSet<T> = values.iter().cloned().collect();
    unique.len() != values.len()
}

fn sorted_key_strings(keys: impl IntoIterator<Item = ArtifactKey>) -> Vec<String> {
    let mut values: Vec<_> = keys.into_iter().map(|key| key.to_string()).collect();
    values.sort();
    values
}

fn phased_families_from_artifacts(
    artifacts: &BTreeMap<ArtifactKey, AnyArtifactDeclaration>,
) -> BTreeSet<(Option<String>, String, String)> {
    artifacts
        .iter()
        .filter_map(|(key, declaration)| {
            if declaration.role == ArtifactRole::Intermediate && key.phase.is_some() {
                Some(key.family_key())
            } else {
                None
            }
        })
        .collect()
}
