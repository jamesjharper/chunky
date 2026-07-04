//! Shared phase-family semantics for artifact selection and validation.
//!
//! The planner decides when to call these helpers; the catalog only describes
//! declaration-aware matching and terminal remapping rules.

use crate::artifact_models::{
    AnyArtifactDeclaration, ArtifactKey, ArtifactRequirement, ArtifactRole, PhaseMatchMode,
};
use anyhow::{bail, Result};
use std::collections::{BTreeMap, BTreeSet};

fn phase_value(key: &ArtifactKey) -> i64 {
    key.phase.map(i64::from).unwrap_or(-1)
}

pub fn capability_covers_requirement(
    capability: &ArtifactKey,
    requirement: &ArtifactRequirement,
) -> bool {
    let requested = &requirement.artifact_id;
    if capability == requested {
        return true;
    }
    if capability.family_key() != requested.family_key() {
        return false;
    }
    if capability.phase.is_none() {
        return true;
    }
    if requested.phase.is_none() {
        return false;
    }
    if requirement.phase_match == PhaseMatchMode::Exact {
        return capability.phase == requested.phase;
    }
    requested.phase <= capability.phase
}

#[derive(Clone)]
pub struct ArtifactPhaseCatalog {
    artifacts: BTreeMap<ArtifactKey, AnyArtifactDeclaration>,
    phased_families: BTreeMap<(Option<String>, String, String), Vec<ArtifactKey>>,
}

impl ArtifactPhaseCatalog {
    pub fn new(artifacts: BTreeMap<ArtifactKey, AnyArtifactDeclaration>) -> Self {
        let mut phased_families: BTreeMap<(Option<String>, String, String), Vec<ArtifactKey>> =
            BTreeMap::new();
        for (key, declaration) in &artifacts {
            if declaration.role == ArtifactRole::Intermediate && key.phase.is_some() {
                phased_families
                    .entry(key.family_key())
                    .or_default()
                    .push(key.clone());
            }
        }
        for keys in phased_families.values_mut() {
            keys.sort_by_key(phase_value);
        }
        Self {
            artifacts,
            phased_families,
        }
    }

    pub fn artifacts(&self) -> &BTreeMap<ArtifactKey, AnyArtifactDeclaration> {
        &self.artifacts
    }

    pub fn declared_keys_for_family(
        &self,
        family: &(Option<String>, String, String),
    ) -> Vec<ArtifactKey> {
        self.phased_families
            .get(family)
            .cloned()
            .unwrap_or_default()
    }

    pub fn declared_phases_for_family(
        &self,
        family: &(Option<String>, String, String),
    ) -> Vec<u32> {
        self.declared_keys_for_family(family)
            .into_iter()
            .filter_map(|key| key.phase)
            .collect()
    }

    pub fn family_has_phases(&self, family: &(Option<String>, String, String)) -> bool {
        self.phased_families
            .get(family)
            .map(|keys| !keys.is_empty())
            .unwrap_or(false)
    }

    pub fn concrete_artifact_is_known(&self, key: &ArtifactKey) -> bool {
        self.artifacts.contains_key(key)
    }

    pub fn selector_artifact_is_known(&self, key: &ArtifactKey) -> bool {
        if self.artifacts.contains_key(key) {
            return true;
        }
        let phases = self.declared_keys_for_family(&key.family_key());
        if phases.is_empty() {
            return false;
        }
        if key.phase.is_none() {
            return true;
        }
        phases.iter().any(|phase| phase.phase <= key.phase)
    }

    pub fn requestable_output_exists(&self, key: &ArtifactKey) -> bool {
        if key.phase.is_none() {
            return self.selector_artifact_is_known(key);
        }
        if self.concrete_artifact_is_known(key) {
            return true;
        }
        let phases = self.declared_keys_for_family(&key.family_key());
        if phases.is_empty() {
            return false;
        }
        let has_lower = phases.iter().any(|phase| phase.phase <= key.phase);
        let has_higher = phases.iter().any(|phase| phase.phase > key.phase);
        has_lower && has_higher
    }

    pub fn matching_concrete_keys(
        &self,
        selector: &ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> Vec<ArtifactKey> {
        let phases = self.declared_keys_for_family(&selector.family_key());
        if phases.is_empty() {
            return Vec::new();
        }
        match (selector.phase, phase_match) {
            (None, _) => phases,
            (Some(_), PhaseMatchMode::Exact) => {
                if self.artifacts.contains_key(selector) {
                    vec![selector.clone()]
                } else {
                    Vec::new()
                }
            }
            (Some(max_phase), PhaseMatchMode::AtMost) => phases
                .into_iter()
                .filter(|key| key.phase.is_some_and(|phase| phase <= max_phase))
                .collect(),
        }
    }

    pub fn highest_matching_key(
        &self,
        keys: &BTreeSet<ArtifactKey>,
        selector: &ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> Option<ArtifactKey> {
        if keys.contains(selector) {
            return Some(selector.clone());
        }
        if selector.phase.is_none() || phase_match == PhaseMatchMode::Exact {
            return None;
        }
        keys.iter()
            .filter(|key| {
                key.family_key() == selector.family_key()
                    && key.phase.is_some()
                    && key.phase <= selector.phase
            })
            .max_by_key(|key| phase_value(key))
            .cloned()
    }

    pub fn capability_covers_requirement(
        &self,
        capability: &ArtifactKey,
        requirement: &ArtifactRequirement,
    ) -> bool {
        capability_covers_requirement(capability, requirement)
    }

    pub fn resolve_output_id(
        &self,
        selector: &ArtifactKey,
        declared_output_id: &ArtifactKey,
    ) -> Result<ArtifactKey> {
        if selector.phase.is_some() || selector == declared_output_id {
            return Ok(declared_output_id.clone());
        }
        let declaration = self.artifacts.get(declared_output_id);
        let valid_terminal_remap = declaration.is_some_and(|declaration| {
            declaration.role == ArtifactRole::Intermediate
                && declared_output_id.family_key() == selector.family_key()
                && declared_output_id.phase.is_some()
                && selector.phase.is_none()
        });
        if !valid_terminal_remap {
            bail!(
                "invalid terminal remapping from {} to {}",
                declared_output_id,
                selector
            );
        }
        Ok(selector.clone())
    }

    pub fn validate_resolved_output(
        &self,
        declared_output_id: &ArtifactKey,
        resolved_output_id: &ArtifactKey,
    ) -> Result<()> {
        if resolved_output_id == declared_output_id {
            return Ok(());
        }
        let expected = self.resolve_output_id(resolved_output_id, declared_output_id)?;
        if &expected != resolved_output_id {
            bail!(
                "invalid terminal remapping from {} to {}",
                declared_output_id,
                resolved_output_id
            );
        }
        Ok(())
    }
}
