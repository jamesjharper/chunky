//! Settings resolution for planning.
//!
//! The planner uses settings in two distinct ways:
//! - inherited settings are propagated along artifact edges, often as override
//!   settings such as `speech_processing//audio_preprocessing//audio_settings`.
//! - resolved settings are the settings a concrete stage invocation consumes and
//!   therefore contribute to its semantic fingerprint.
//!
//! Precedence: defaults < user-configured settings < inherited settings.
//! Settings resolution for planning.

use anyhow::{bail, Result};
use serde_json::{json, Value};
use std::collections::BTreeMap;

use crate::artifact_models::ArtifactKey;
use crate::fingerprints::stable_hash;
use crate::stage_manifest::StageManifest;
use crate::stage_models::{ManifestStageRegistration, ResolvedSettingsView, StagePlanningContext};

use super::request_models::TaskRequestContext;

pub struct PlannerSettingsResolver<'a> {
    #[allow(dead_code)]
    manifest: &'a StageManifest,
}

impl<'a> PlannerSettingsResolver<'a> {
    pub fn new(manifest: &'a StageManifest) -> Self {
        Self { manifest }
    }

    pub fn build_stage_context(
        &self,
        state: &TaskRequestContext<'a>,
        registration: &ManifestStageRegistration<'a>,
        declared_outputs: impl IntoIterator<Item = ArtifactKey>,
        inherited_settings: Vec<(ArtifactKey, Value)>,
    ) -> Result<StagePlanningContext> {
        let inherited_view = ResolvedSettingsView::new(inherited_settings.into_iter().collect());
        let effective_settings = self.resolve_stage_settings(
            registration.declared_setting_ids(),
            &state.request_settings.default_settings,
            &inherited_view,
            &state.request_settings.user_settings,
        )?;

        Ok(StagePlanningContext {
            stage_id: registration.stage_id().to_owned(),
            requested_outputs: declared_outputs.into_iter().collect(),
            resolved_settings: effective_settings,
            inherited_settings: inherited_view,
            run_policy: state.policy.clone(),
        })
    }

    pub fn resolve_stage_settings(
        &self,
        declared_setting_ids: impl IntoIterator<Item = ArtifactKey>,
        default_settings: &ResolvedSettingsView,
        inherited_settings: &ResolvedSettingsView,
        user_settings: &ResolvedSettingsView,
    ) -> Result<ResolvedSettingsView> {
        let declared_setting_ids: Vec<ArtifactKey> = declared_setting_ids.into_iter().collect();
        if declared_setting_ids.is_empty() {
            return Ok(ResolvedSettingsView::default());
        }

        let default_items: BTreeMap<_, _> = default_settings.items().into_iter().collect();
        let inherited_items: BTreeMap<_, _> = inherited_settings.items().into_iter().collect();
        let user_items: BTreeMap<_, _> = user_settings.items().into_iter().collect();
        let mut resolved = BTreeMap::new();

        for declared in declared_setting_ids {
            let default_value = find_exact_or_single_override(&default_items, &declared)?;
            let inherited_value = find_exact_or_single_override(&inherited_items, &declared)?;
            let user_value = find_exact(&user_items, &declared);

            if let Some(value) = default_value {
                resolved.insert(declared.clone(), value);
            }
            if let Some(value) = user_value {
                resolved.insert(declared.clone(), value);
            }
            if let Some(value) = inherited_value {
                // Edge-pushed settings are path-specific and intentionally win
                // over broader request-level defaults or user roots.
                resolved.insert(declared.clone(), value);
            }
        }

        Ok(ResolvedSettingsView::new(resolved))
    }
}

fn find_exact_or_single_override(
    values: &BTreeMap<ArtifactKey, Value>,
    declared: &ArtifactKey,
) -> Result<Option<Value>> {
    if let Some(value) = values.get(declared) {
        return Ok(Some(value.clone()));
    }

    let matches: Vec<_> = values
        .iter()
        .filter(|(key, _)| key.without_override() == declared.without_override())
        .collect();

    if matches.is_empty() {
        return Ok(None);
    }
    if matches.len() > 1 {
        let rendered = matches
            .iter()
            .map(|(key, _)| key.to_string())
            .collect::<Vec<_>>()
            .join(", ");
        bail!("multiple override settings resolved for {declared}: {rendered}");
    }

    Ok(Some(matches[0].1.clone()))
}

fn find_exact(values: &BTreeMap<ArtifactKey, Value>, declared: &ArtifactKey) -> Option<Value> {
    values.get(declared).cloned()
}

pub fn settings_fingerprint(settings: &ResolvedSettingsView) -> String {
    let items = settings.items();
    if items.is_empty() {
        return String::new();
    }

    // Use canonical JSON over the resolved settings map so fingerprints are
    // stable across runs and implementations.
    let settings_map: serde_json::Map<String, Value> = items
        .into_iter()
        .map(|(key, value)| (key.to_string(), value))
        .collect();
    stable_hash(&json!({ "settings": settings_map }))
        .expect("settings fingerprint serialization should succeed")
}
