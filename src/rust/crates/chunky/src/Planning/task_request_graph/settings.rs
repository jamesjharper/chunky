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
use serde_json::Value;
use std::collections::BTreeMap;

use crate::artifact_models::ArtifactKey;
use crate::fingerprints::FingerprintProvider;
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
        inherited_settings: ResolvedSettingsView,
    ) -> Result<StagePlanningContext> {
        let effective_settings = self.resolve_stage_settings(
            registration.declared_setting_ids(),
            &state.request_settings.default_settings,
            &inherited_settings,
            &state.request_settings.user_settings,
        )?;

        Ok(StagePlanningContext {
            stage_id: registration.stage_id().to_owned(),
            stage_fingerprint: state
                .manifest_graph
                .stage_fingerprints_by_id
                .get(registration.stage_id())
                .cloned()
                .ok_or_else(|| anyhow::anyhow!("unknown stage fingerprint: {}", registration.stage_id()))?,
            requested_outputs: declared_outputs.into_iter().collect(),
            resolved_settings: effective_settings,
            inherited_settings,
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

        let mut resolved = BTreeMap::new();
        let mut resolved_fingerprints = BTreeMap::new();

        for declared in declared_setting_ids {
            let default_value = find_exact_or_single_override(default_settings, &declared)?;
            let inherited_value = find_exact_or_single_override(inherited_settings, &declared)?;
            let user_value = find_exact(user_settings, &declared);

            if let Some(value) = default_value {
                resolved.insert(declared.clone(), value.value);
                if let Some(fingerprint) = value.fingerprint {
                    resolved_fingerprints.insert(declared.clone(), fingerprint);
                }
            }
            if let Some(value) = user_value {
                resolved.insert(declared.clone(), value.value);
                if let Some(fingerprint) = value.fingerprint {
                    resolved_fingerprints.insert(declared.clone(), fingerprint);
                }
            }
            if let Some(value) = inherited_value {
                // Edge-pushed settings are path-specific and intentionally win
                // over broader request-level defaults or user roots.
                resolved.insert(declared.clone(), value.value);
                if let Some(fingerprint) = value.fingerprint {
                    resolved_fingerprints.insert(declared.clone(), fingerprint);
                }
            }
        }

        Ok(ResolvedSettingsView::new_with_fingerprints(
            resolved,
            resolved_fingerprints,
        ))
    }
}

struct MaybeSetting {
    value: Value,
    fingerprint: Option<String>,
}

fn find_exact_or_single_override(
    values: &ResolvedSettingsView,
    declared: &ArtifactKey,
) -> Result<Option<MaybeSetting>> {
    if let Some(value) = values.get(declared) {
        return Ok(Some(MaybeSetting {
            value: value.clone(),
            fingerprint: values.fingerprint(declared).map(str::to_owned),
        }));
    }

    let matches: Vec<_> = values
        .items()
        .into_iter()
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

    Ok(Some(MaybeSetting {
        value: matches[0].1.clone(),
        fingerprint: values.fingerprint(&matches[0].0).map(str::to_owned),
    }))
}

fn find_exact(values: &ResolvedSettingsView, declared: &ArtifactKey) -> Option<MaybeSetting> {
    values.get(declared).map(|value| MaybeSetting {
        value: value.clone(),
        fingerprint: values.fingerprint(declared).map(str::to_owned),
    })
}

pub fn settings_fingerprint(settings: &ResolvedSettingsView) -> String {
    FingerprintProvider
        .build_settings_fingerprint(
            &settings
                .fingerprints()
                .expect("settings fingerprint derivation should succeed"),
        )
        .expect("settings fingerprint serialization should succeed")
}
