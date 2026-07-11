use anyhow::{anyhow, bail, Result};
use serde_json::{Number, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

use crate::cache_models::ArtifactCache;

#[derive(Clone, Debug, Eq, PartialEq)]
enum RuntimeSettingKind {
    String,
    Integer,
    Float,
    Boolean,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeSettingSpec {
    key: String,
    kind: RuntimeSettingKind,
    default: Option<Value>,
    pub suggested_env_var: Option<String>,
    pub description: String,
}

impl RuntimeSettingSpec {
    pub fn string(
        key: impl Into<String>,
        default: Option<impl Into<Value>>,
        suggested_env_var: Option<impl Into<String>>,
    ) -> Self {
        Self {
            key: normalize_runtime_setting_key(&key.into()),
            kind: RuntimeSettingKind::String,
            default: default.map(Into::into),
            suggested_env_var: suggested_env_var.map(Into::into),
            description: String::new(),
        }
    }

    pub fn integer(
        key: impl Into<String>,
        default: Option<impl Into<Value>>,
        suggested_env_var: Option<impl Into<String>>,
    ) -> Self {
        Self {
            key: normalize_runtime_setting_key(&key.into()),
            kind: RuntimeSettingKind::Integer,
            default: default.map(Into::into),
            suggested_env_var: suggested_env_var.map(Into::into),
            description: String::new(),
        }
    }

    pub fn floating(
        key: impl Into<String>,
        default: Option<impl Into<Value>>,
        suggested_env_var: Option<impl Into<String>>,
    ) -> Self {
        Self {
            key: normalize_runtime_setting_key(&key.into()),
            kind: RuntimeSettingKind::Float,
            default: default.map(Into::into),
            suggested_env_var: suggested_env_var.map(Into::into),
            description: String::new(),
        }
    }

    pub fn boolean(
        key: impl Into<String>,
        default: Option<impl Into<Value>>,
        suggested_env_var: Option<impl Into<String>>,
    ) -> Self {
        Self {
            key: normalize_runtime_setting_key(&key.into()),
            kind: RuntimeSettingKind::Boolean,
            default: default.map(Into::into),
            suggested_env_var: suggested_env_var.map(Into::into),
            description: String::new(),
        }
    }

    pub fn normalized_key(&self) -> String {
        self.key.clone()
    }

    pub fn has_default(&self) -> bool {
        self.default.is_some()
    }

    fn parse_value(&self, value: Value) -> Result<Value> {
        match self.kind {
            RuntimeSettingKind::String => Ok(Value::String(coerce_runtime_setting_string(
                value, &self.key,
            )?)),
            RuntimeSettingKind::Integer => Ok(Value::Number(Number::from(
                coerce_runtime_setting_int(value, &self.key)?,
            ))),
            RuntimeSettingKind::Float => {
                let value = coerce_runtime_setting_float(value, &self.key)?;
                let number = Number::from_f64(value)
                    .ok_or_else(|| anyhow!("invalid runtime setting float: {}", self.key))?;
                Ok(Value::Number(number))
            }
            RuntimeSettingKind::Boolean => {
                Ok(Value::Bool(coerce_runtime_setting_bool(value, &self.key)?))
            }
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeStageSetting {
    pub spec: RuntimeSettingSpec,
    pub required: bool,
}

impl RuntimeStageSetting {
    pub fn optional(spec: RuntimeSettingSpec) -> Self {
        Self {
            spec,
            required: false,
        }
    }

    pub fn required(spec: RuntimeSettingSpec) -> Self {
        Self {
            spec,
            required: true,
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
struct RuntimeSettingScope {
    stage_ids: Option<BTreeSet<String>>,
}

impl RuntimeSettingScope {
    fn matches(&self, active_stage_ids: Option<&BTreeSet<String>>) -> bool {
        match (&self.stage_ids, active_stage_ids) {
            (None, _) | (_, None) => true,
            (Some(scope), Some(active)) => !scope.is_disjoint(active),
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct PipelineRuntimeSettings {
    values: BTreeMap<String, Value>,
    declared_values: BTreeMap<String, Value>,
    specs: BTreeMap<String, RuntimeSettingSpec>,
    active_scopes: BTreeMap<String, Vec<RuntimeSettingScope>>,
    required_scopes: BTreeMap<String, Vec<RuntimeSettingScope>>,
}

impl PipelineRuntimeSettings {
    pub fn has(&self, key: &str) -> bool {
        self.values
            .contains_key(&normalize_runtime_setting_key(key))
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.values.get(&normalize_runtime_setting_key(key))
    }

    pub fn get_string(&self, key: &str) -> Result<String> {
        let key = normalize_runtime_setting_key(key);
        let value = self
            .values
            .get(&key)
            .cloned()
            .ok_or_else(|| anyhow!("missing required runtime setting: {key}"))?;
        coerce_runtime_setting_string(value, &key)
    }

    pub fn get_int(&self, key: &str) -> Result<i64> {
        let key = normalize_runtime_setting_key(key);
        let value = self
            .values
            .get(&key)
            .cloned()
            .ok_or_else(|| anyhow!("missing required runtime setting: {key}"))?;
        coerce_runtime_setting_int(value, &key)
    }

    pub fn get_float(&self, key: &str) -> Result<f64> {
        let key = normalize_runtime_setting_key(key);
        let value = self
            .values
            .get(&key)
            .cloned()
            .ok_or_else(|| anyhow!("missing required runtime setting: {key}"))?;
        coerce_runtime_setting_float(value, &key)
    }

    pub fn get_bool(&self, key: &str) -> Result<bool> {
        let key = normalize_runtime_setting_key(key);
        let value = self
            .values
            .get(&key)
            .cloned()
            .ok_or_else(|| anyhow!("missing required runtime setting: {key}"))?;
        coerce_runtime_setting_bool(value, &key)
    }

    pub fn describe_env_var(&self, key: &str) -> Option<String> {
        self.specs
            .get(&normalize_runtime_setting_key(key))
            .map(|spec| suggested_env_var(spec))
    }

    pub fn resolve_for_stages<I, S>(&self, stage_ids: I) -> Result<Self>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let active_stage_ids = normalize_stage_ids(stage_ids);
        let active_specs = self.active_specs(Some(&active_stage_ids));
        let mut values = self.declared_values.clone();

        for (key, spec) in &active_specs {
            if values.contains_key(key) {
                continue;
            }
            if let Some(default) = spec.default.clone() {
                values.insert(key.clone(), spec.parse_value(default)?);
                continue;
            }
            if let Ok(raw) = std::env::var(suggested_env_var(spec)) {
                values.insert(key.clone(), spec.parse_value(Value::String(raw))?);
            }
        }

        for (key, spec) in &active_specs {
            if let Some(value) = values.get(key).cloned() {
                values.insert(key.clone(), spec.parse_value(value)?);
            }
        }

        let mut missing = Vec::new();
        for key in active_specs.keys() {
            if values.contains_key(key) || !self.is_required(key, Some(&active_stage_ids)) {
                continue;
            }
            let env_name = self.describe_env_var(key).unwrap_or_else(|| key.clone());
            missing.push(format!("{key} (env: {env_name})"));
        }
        if !missing.is_empty() {
            missing.sort();
            bail!("missing required runtime settings: {}", missing.join(", "));
        }

        Ok(Self {
            values,
            declared_values: self.declared_values.clone(),
            specs: self.specs.clone(),
            active_scopes: self.active_scopes.clone(),
            required_scopes: self.required_scopes.clone(),
        })
    }

    fn active_specs(
        &self,
        active_stage_ids: Option<&BTreeSet<String>>,
    ) -> BTreeMap<String, RuntimeSettingSpec> {
        self.specs
            .iter()
            .filter_map(|(key, spec)| {
                let scopes = self.active_scopes.get(key);
                if scopes.is_none()
                    || scopes
                        .into_iter()
                        .flatten()
                        .any(|scope| scope.matches(active_stage_ids))
                {
                    Some((key.clone(), spec.clone()))
                } else {
                    None
                }
            })
            .collect()
    }

    fn is_required(&self, key: &str, active_stage_ids: Option<&BTreeSet<String>>) -> bool {
        self.required_scopes
            .get(key)
            .into_iter()
            .flatten()
            .any(|scope| scope.matches(active_stage_ids))
    }
}

#[derive(Default)]
pub struct PipelineRuntimeSettingsBuilder {
    values: BTreeMap<String, Value>,
    specs: BTreeMap<String, RuntimeSettingSpec>,
    active_scopes: BTreeMap<String, Vec<RuntimeSettingScope>>,
    required_scopes: BTreeMap<String, Vec<RuntimeSettingScope>>,
    sealed: bool,
}

impl PipelineRuntimeSettingsBuilder {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set(&mut self, key: impl AsRef<str>, value: impl Into<Value>) -> Result<&mut Self> {
        self.assert_not_sealed()?;
        let normalized = normalize_runtime_setting_key(key.as_ref());
        if self.values.contains_key(&normalized) {
            bail!("duplicate runtime setting value: {normalized}");
        }
        self.values.insert(normalized, value.into());
        Ok(self)
    }

    pub fn declare<I, S>(&mut self, spec: RuntimeSettingSpec, stage_ids: I) -> Result<&mut Self>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        self.assert_not_sealed()?;
        let normalized = spec.normalized_key();
        if let Some(existing) = self.specs.get(&normalized) {
            if existing != &spec {
                bail!("conflicting runtime setting spec: {normalized}");
            }
        } else {
            self.specs.insert(normalized.clone(), spec);
        }
        self.active_scopes
            .entry(normalized)
            .or_default()
            .push(RuntimeSettingScope {
                stage_ids: Some(normalize_stage_ids(stage_ids)),
            });
        Ok(self)
    }

    pub fn declare_global(&mut self, spec: RuntimeSettingSpec) -> Result<&mut Self> {
        self.assert_not_sealed()?;
        let normalized = spec.normalized_key();
        if let Some(existing) = self.specs.get(&normalized) {
            if existing != &spec {
                bail!("conflicting runtime setting spec: {normalized}");
            }
        } else {
            self.specs.insert(normalized.clone(), spec);
        }
        self.active_scopes
            .entry(normalized)
            .or_default()
            .push(RuntimeSettingScope { stage_ids: None });
        Ok(self)
    }

    pub fn require<I, S>(&mut self, spec: RuntimeSettingSpec, stage_ids: I) -> Result<&mut Self>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let normalized_ids = normalize_stage_ids(stage_ids);
        self.assert_not_sealed()?;
        self.declare(spec.clone(), normalized_ids.iter().map(|id| id.as_str()))?;
        self.required_scopes
            .entry(spec.normalized_key())
            .or_default()
            .push(RuntimeSettingScope {
                stage_ids: Some(normalized_ids),
            });
        Ok(self)
    }

    pub fn install_stage_settings(
        &mut self,
        stage_id: &str,
        settings: &[RuntimeStageSetting],
    ) -> Result<&mut Self> {
        for setting in settings {
            if setting.required {
                self.require(setting.spec.clone(), [stage_id])?;
            } else {
                self.declare(setting.spec.clone(), [stage_id])?;
            }
        }
        Ok(self)
    }

    pub fn freeze(&mut self) -> Result<PipelineRuntimeSettings> {
        self.sealed = true;
        Ok(PipelineRuntimeSettings {
            values: BTreeMap::new(),
            declared_values: self.values.clone(),
            specs: self.specs.clone(),
            active_scopes: self.active_scopes.clone(),
            required_scopes: self.required_scopes.clone(),
        })
    }

    fn assert_not_sealed(&self) -> Result<()> {
        if self.sealed {
            bail!("runtime settings builder is already frozen");
        }
        Ok(())
    }
}

#[derive(Clone)]
pub struct PipelineRuntimeServices {
    pub artifact_cache: Arc<dyn ArtifactCache>,
}

#[derive(Default)]
pub struct PipelineRuntimeServicesBuilder {
    artifact_cache: Option<Arc<dyn ArtifactCache>>,
    frozen: Option<PipelineRuntimeServices>,
}

impl PipelineRuntimeServicesBuilder {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set_artifact_cache(
        &mut self,
        artifact_cache: Arc<dyn ArtifactCache>,
    ) -> Result<&mut Self> {
        if self.frozen.is_some() {
            bail!("runtime services builder is already frozen");
        }
        if self.artifact_cache.is_some() {
            bail!("runtime service slot already configured: artifact_cache");
        }
        self.artifact_cache = Some(artifact_cache);
        Ok(self)
    }

    pub fn freeze(&mut self) -> Result<PipelineRuntimeServices> {
        if let Some(frozen) = &self.frozen {
            return Ok(frozen.clone());
        }
        let runtime_services = PipelineRuntimeServices {
            artifact_cache: self
                .artifact_cache
                .clone()
                .ok_or_else(|| anyhow!("artifact_cache runtime service must be configured"))?,
        };
        self.frozen = Some(runtime_services.clone());
        Ok(runtime_services)
    }
}

fn normalize_runtime_setting_key(key: &str) -> String {
    key.trim().to_ascii_lowercase()
}

fn normalize_stage_ids<I, S>(stage_ids: I) -> BTreeSet<String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    stage_ids
        .into_iter()
        .map(|stage_id| stage_id.as_ref().trim().to_owned())
        .filter(|stage_id| !stage_id.is_empty())
        .collect()
}

fn suggested_env_var(spec: &RuntimeSettingSpec) -> String {
    spec.suggested_env_var
        .clone()
        .unwrap_or_else(|| spec.normalized_key().to_ascii_uppercase().replace('.', "_"))
}

fn coerce_runtime_setting_string(value: Value, key: &str) -> Result<String> {
    match value {
        Value::String(value) => Ok(value),
        Value::Number(value) => Ok(value.to_string()),
        Value::Bool(value) => Ok(value.to_string()),
        other => bail!("runtime setting {key} must be string-like, got {other}"),
    }
}

fn coerce_runtime_setting_int(value: Value, key: &str) -> Result<i64> {
    match value {
        Value::Number(value) => value
            .as_i64()
            .ok_or_else(|| anyhow!("runtime setting {key} must be an integer")),
        Value::String(value) => value
            .parse::<i64>()
            .map_err(|_| anyhow!("runtime setting {key} must be an integer")),
        other => bail!("runtime setting {key} must be an integer, got {other}"),
    }
}

fn coerce_runtime_setting_float(value: Value, key: &str) -> Result<f64> {
    match value {
        Value::Number(value) => value
            .as_f64()
            .ok_or_else(|| anyhow!("runtime setting {key} must be numeric")),
        Value::String(value) => value
            .parse::<f64>()
            .map_err(|_| anyhow!("runtime setting {key} must be numeric")),
        other => bail!("runtime setting {key} must be numeric, got {other}"),
    }
}

fn coerce_runtime_setting_bool(value: Value, key: &str) -> Result<bool> {
    match value {
        Value::Bool(value) => Ok(value),
        Value::String(value) => match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok(true),
            "0" | "false" | "no" | "off" => Ok(false),
            _ => bail!("runtime setting {key} must be boolean-like"),
        },
        other => bail!("runtime setting {key} must be boolean-like, got {other}"),
    }
}
