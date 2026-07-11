//! Request-scoped planning models.
//!
//! Request-scoped planning models.

use anyhow::Result;
use serde_json::Value;
use std::borrow::Borrow;
use std::collections::{BTreeMap, BTreeSet};

use crate::artifact_binding_models::{AnyArtifactInstance, ArtifactReader};
use crate::artifact_models::{ArtifactKey, ArtifactRole};
use crate::stage_manifest::StageManifest;
use crate::stage_models::{PlanningRunPolicy, ResolvedSettingsView};

use crate::planning::manifest_graph::StageManifestGraph;

/// Request-scoped settings already read from supplied/default artifacts.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PlannerRequestSettings {
    pub default_settings: ResolvedSettingsView,
    pub user_settings: ResolvedSettingsView,
}

impl PlannerRequestSettings {
    pub async fn read_from<I, K>(
        manifest: &StageManifest,
        supplied_instances: &BTreeMap<ArtifactKey, AnyArtifactInstance>,
        user_supplied_settings: I,
    ) -> Result<Self>
    where
        I: IntoIterator<Item = K>,
        K: Borrow<ArtifactKey>,
    {
        let user_supplied_settings: BTreeSet<ArtifactKey> = user_supplied_settings
            .into_iter()
            .map(|artifact_id| artifact_id.borrow().clone())
            .collect();
        let setting_ids = manifest
            .find_artifacts_by_role(supplied_instances.keys().cloned(), ArtifactRole::Settings);

        let mut defaults: BTreeMap<ArtifactKey, Value> = BTreeMap::new();
        let mut default_fingerprints: BTreeMap<ArtifactKey, String> = BTreeMap::new();
        let mut user: BTreeMap<ArtifactKey, Value> = BTreeMap::new();
        let mut user_fingerprints: BTreeMap<ArtifactKey, String> = BTreeMap::new();

        for (artifact_id, instance) in supplied_instances {
            if !setting_ids.contains(artifact_id) {
                continue;
            }
            let value = (*ArtifactReader::<Value>::new(instance.handle.clone())?
                .read()
                .await?)
                .clone();
            if user_supplied_settings.contains(artifact_id) {
                user.insert(artifact_id.clone(), value);
                user_fingerprints.insert(artifact_id.clone(), instance.fingerprint().to_owned());
            } else {
                defaults.insert(artifact_id.clone(), value);
                default_fingerprints.insert(artifact_id.clone(), instance.fingerprint().to_owned());
            }
        }

        Ok(Self {
            default_settings: ResolvedSettingsView::new_with_fingerprints(
                defaults,
                default_fingerprints,
            ),
            user_settings: ResolvedSettingsView::new_with_fingerprints(user, user_fingerprints),
        })
    }

    pub fn merge_with_inherited(
        &self,
        inherited_settings: &ResolvedSettingsView,
    ) -> ResolvedSettingsView {
        self.default_settings
            .merge(&[inherited_settings.clone(), self.user_settings.clone()])
    }
}

/// Immutable inputs shared by one evaluated-plan expansion pass.
#[derive(Clone)]
pub struct TaskRequestContext<'a> {
    pub manifest: &'a StageManifest,
    pub manifest_graph: StageManifestGraph,
    pub requested_output_ids: Vec<ArtifactKey>,
    pub supplied_instances: BTreeMap<ArtifactKey, AnyArtifactInstance>,
    pub policy: PlanningRunPolicy,
    pub request_settings: PlannerRequestSettings,
}
