use anyhow::{anyhow, bail, Result};
use serde_json::{Number, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;

use crate::cache_models::ArtifactCache;
use crate::stage_models::{PipelineFeature, StageManifestBuilderAccess};

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
    pub env_var: Option<String>,
    pub description: String,
}

impl RuntimeSettingSpec {
    pub fn string(
        key: impl Into<String>,
        default: Option<impl Into<Value>>,
        env_var: Option<impl Into<String>>,
    ) -> Self {
        Self {
            key: normalize_runtime_setting_key(&key.into()),
            kind: RuntimeSettingKind::String,
            default: default.map(Into::into),
            env_var: env_var.map(Into::into),
            description: String::new(),
        }
    }

    pub fn integer(
        key: impl Into<String>,
        default: Option<impl Into<Value>>,
        env_var: Option<impl Into<String>>,
    ) -> Self {
        Self {
            key: normalize_runtime_setting_key(&key.into()),
            kind: RuntimeSettingKind::Integer,
            default: default.map(Into::into),
            env_var: env_var.map(Into::into),
            description: String::new(),
        }
    }

    pub fn floating(
        key: impl Into<String>,
        default: Option<impl Into<Value>>,
        env_var: Option<impl Into<String>>,
    ) -> Self {
        Self {
            key: normalize_runtime_setting_key(&key.into()),
            kind: RuntimeSettingKind::Float,
            default: default.map(Into::into),
            env_var: env_var.map(Into::into),
            description: String::new(),
        }
    }

    pub fn boolean(
        key: impl Into<String>,
        default: Option<impl Into<Value>>,
        env_var: Option<impl Into<String>>,
    ) -> Self {
        Self {
            key: normalize_runtime_setting_key(&key.into()),
            kind: RuntimeSettingKind::Boolean,
            default: default.map(Into::into),
            env_var: env_var.map(Into::into),
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

pub trait RuntimeSettingSource: Send + Sync {
    fn resolve(&self, spec: &RuntimeSettingSpec) -> Option<Value>;

    fn env_name_for(&self, _spec: &RuntimeSettingSpec) -> Option<String> {
        None
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MappingRuntimeSettingSource {
    values: BTreeMap<String, Value>,
}

impl MappingRuntimeSettingSource {
    pub fn new(values: BTreeMap<String, Value>) -> Self {
        Self {
            values: values
                .into_iter()
                .map(|(key, value)| (normalize_runtime_setting_key(&key), value))
                .collect(),
        }
    }
}

impl RuntimeSettingSource for MappingRuntimeSettingSource {
    fn resolve(&self, spec: &RuntimeSettingSpec) -> Option<Value> {
        self.values.get(&spec.normalized_key()).cloned()
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct EnvironmentVariableRuntimeSettingSource {
    environ: BTreeMap<String, String>,
    env_name_overrides: BTreeMap<String, String>,
}

impl EnvironmentVariableRuntimeSettingSource {
    pub fn new(
        environ: Option<BTreeMap<String, String>>,
        env_name_overrides: Option<BTreeMap<String, String>>,
        dotenv_files: Option<Vec<PathBuf>>,
    ) -> Result<Self> {
        let normalized_dotenv_files = dotenv_files.unwrap_or_default();
        let mut combined = load_dotenv_files(&normalized_dotenv_files)?;
        combined.extend(environ.unwrap_or_else(|| std::env::vars().collect()));
        Ok(Self {
            environ: combined,
            env_name_overrides: env_name_overrides
                .unwrap_or_default()
                .into_iter()
                .map(|(key, value)| (normalize_runtime_setting_key(&key), value))
                .collect(),
        })
    }
}

impl RuntimeSettingSource for EnvironmentVariableRuntimeSettingSource {
    fn resolve(&self, spec: &RuntimeSettingSpec) -> Option<Value> {
        self.environ
            .get(&self.env_name_for(spec)?)
            .cloned()
            .map(Value::String)
    }

    fn env_name_for(&self, spec: &RuntimeSettingSpec) -> Option<String> {
        let normalized = spec.normalized_key();
        if let Some(value) = self.env_name_overrides.get(&normalized) {
            return Some(value.clone());
        }
        Some(env_var(spec))
    }
}

// TODO(runtime-naming): Rename PipelineRuntimeSettings and its builder to
// RuntimeSettings and RuntimeSettingsBuilder to match specs/01-runtime.
#[derive(Clone)]
pub struct PipelineRuntimeSettings {
    values: BTreeMap<String, Value>,
    declared_values: BTreeMap<String, Value>,
    specs: BTreeMap<String, RuntimeSettingSpec>,
    active_scopes: BTreeMap<String, Vec<RuntimeSettingScope>>,
    required_scopes: BTreeMap<String, Vec<RuntimeSettingScope>>,
    sources: Vec<Arc<dyn RuntimeSettingSource>>,
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
        let spec = self.specs.get(&normalize_runtime_setting_key(key))?;
        for source in &self.sources {
            if let Some(env_name) = source.env_name_for(spec) {
                return Some(env_name);
            }
        }
        Some(env_var(spec))
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
            for source in &self.sources {
                if let Some(raw) = source.resolve(spec) {
                    values.insert(key.clone(), spec.parse_value(raw)?);
                    break;
                }
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
            sources: self.sources.clone(),
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
    sources: Vec<Arc<dyn RuntimeSettingSource>>,
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

    pub fn install_source(
        &mut self,
        source: Arc<dyn RuntimeSettingSource>,
    ) -> Result<&mut Self> {
        self.assert_not_sealed()?;
        self.sources.push(source);
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
            sources: self.sources.clone(),
        })
    }

    fn assert_not_sealed(&self) -> Result<()> {
        if self.sealed {
            bail!("runtime settings builder is already frozen");
        }
        Ok(())
    }
}

// TODO(runtime-naming): Rename PipelineRuntimeServices and its builder to
// RuntimeServices and RuntimeServicesBuilder to match specs/01-runtime.
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

fn env_var(spec: &RuntimeSettingSpec) -> String {
    spec.env_var
        .clone()
        .unwrap_or_else(|| spec.normalized_key().to_ascii_uppercase().replace('.', "_"))
}

pub struct RuntimeSettingSourceFeature {
    feature_id: String,
    configuration_fingerprint: String,
    source: Arc<dyn RuntimeSettingSource>,
}

impl RuntimeSettingSourceFeature {
    pub fn new(
        source: Arc<dyn RuntimeSettingSource>,
        feature_id: Option<String>,
        configuration_fingerprint: Option<String>,
    ) -> Self {
        Self {
            feature_id: feature_id.unwrap_or_else(|| "chunky.runtime.setting-source".to_owned()),
            configuration_fingerprint: configuration_fingerprint
                .unwrap_or_else(|| "runtime-setting-source".to_owned()),
            source,
        }
    }
}

impl PipelineFeature for RuntimeSettingSourceFeature {
    fn feature_id(&self) -> &str {
        &self.feature_id
    }

    fn configuration_fingerprint(&self) -> &str {
        &self.configuration_fingerprint
    }

    fn install(
        &self,
        manifest_builder: &mut dyn StageManifestBuilderAccess,
        service_locator: &mut PipelineRuntimeServicesBuilder,
        env_settings: &mut PipelineRuntimeSettingsBuilder,
    ) -> Result<()> {
        let _ = manifest_builder;
        let _ = service_locator;
        env_settings.install_source(self.source.clone())?;
        Ok(())
    }
}

pub struct EnvironmentVariableRuntimeSettingSourceFeature {
    inner: RuntimeSettingSourceFeature,
}

impl EnvironmentVariableRuntimeSettingSourceFeature {
    pub fn new(
        environ: Option<BTreeMap<String, String>>,
        env_name_overrides: Option<BTreeMap<String, String>>,
        dotenv_files: Option<Vec<PathBuf>>,
    ) -> Result<Self> {
        let normalized_dotenv_files = dotenv_files.unwrap_or_default();
        let mut fingerprint_parts = vec!["runtime-setting-source:environment-variable".to_owned()];
        if let Some(overrides) = &env_name_overrides {
            if !overrides.is_empty() {
                fingerprint_parts.push(format!(
                    "overrides:{}",
                    overrides
                        .iter()
                        .map(|(key, value)| format!("{}={value}", normalize_runtime_setting_key(key)))
                        .collect::<Vec<_>>()
                        .join(",")
                ));
            }
        }
        if !normalized_dotenv_files.is_empty() {
            fingerprint_parts.push(format!(
                "dotenv:{}",
                normalized_dotenv_files
                    .iter()
                    .map(|path| path.expanduser_for_fingerprint())
                    .collect::<Vec<_>>()
                    .join(",")
            ));
        }
        let source = Arc::new(EnvironmentVariableRuntimeSettingSource::new(
            environ,
            env_name_overrides,
            Some(normalized_dotenv_files),
        )?);
        Ok(Self {
            inner: RuntimeSettingSourceFeature::new(
                source,
                Some("chunky.runtime.environment-variable-setting-source".to_owned()),
                Some(fingerprint_parts.join("|")),
            ),
        })
    }
}

impl PipelineFeature for EnvironmentVariableRuntimeSettingSourceFeature {
    fn feature_id(&self) -> &str {
        self.inner.feature_id()
    }

    fn configuration_fingerprint(&self) -> &str {
        self.inner.configuration_fingerprint()
    }

    fn install(
        &self,
        manifest_builder: &mut dyn StageManifestBuilderAccess,
        service_locator: &mut PipelineRuntimeServicesBuilder,
        env_settings: &mut PipelineRuntimeSettingsBuilder,
    ) -> Result<()> {
        self.inner
            .install(manifest_builder, service_locator, env_settings)
    }
}

trait PathFingerprintExt {
    fn expanduser_for_fingerprint(&self) -> String;
}

impl PathFingerprintExt for PathBuf {
    fn expanduser_for_fingerprint(&self) -> String {
        self.to_string_lossy().into_owned()
    }
}

fn load_dotenv_files(dotenv_files: &[PathBuf]) -> Result<BTreeMap<String, String>> {
    let mut values = BTreeMap::new();
    for dotenv_file in dotenv_files {
        let path = dotenv_file.as_path();
        let contents = fs::read_to_string(path)
            .map_err(|error| anyhow!("failed to read dotenv file {}: {error}", path.display()))?;
        for raw_line in contents.lines() {
            let mut line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if let Some(stripped) = line.strip_prefix("export ") {
                line = stripped.trim();
            }
            let Some((key, value)) = line.split_once('=') else {
                bail!("invalid dotenv line in {}: {}", path.display(), raw_line);
            };
            let normalized_key = key.trim();
            if normalized_key.is_empty() {
                bail!("invalid dotenv key in {}: {}", path.display(), raw_line);
            }
            values.insert(
                normalized_key.to_owned(),
                strip_optional_quotes(value.trim()).to_owned(),
            );
        }
    }
    Ok(values)
}

fn strip_optional_quotes(value: &str) -> &str {
    if value.len() >= 2 {
        let bytes = value.as_bytes();
        if (bytes[0] == b'"' && bytes[value.len() - 1] == b'"')
            || (bytes[0] == b'\'' && bytes[value.len() - 1] == b'\'')
        {
            return &value[1..value.len() - 1];
        }
    }
    value
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn runtime_settings_resolve_from_environment_source() -> Result<()> {
        let mut builder = PipelineRuntimeSettingsBuilder::new();
        builder.install_source(Arc::new(EnvironmentVariableRuntimeSettingSource::new(
            Some(BTreeMap::from([(
                "EXAMPLE_REQUIRED".to_owned(),
                "resolved-from-env".to_owned(),
            )])),
            None,
            None,
        )?))?;
        builder.require(
            RuntimeSettingSpec::string(
                "example.required",
                Option::<Value>::None,
                Some("EXAMPLE_REQUIRED"),
            ),
            ["stage.a"],
        )?;
        builder.declare(
            RuntimeSettingSpec::string("example.optional", Some("fallback"), None::<String>),
            ["stage.b"],
        )?;

        let frozen = builder.freeze()?;
        let for_stage_a = frozen.resolve_for_stages(["stage.a"])?;
        let for_stage_b = frozen.resolve_for_stages(["stage.b"])?;

        assert_eq!("resolved-from-env", for_stage_a.get_string("example.required")?);
        assert!(!for_stage_b.has("example.required"));
        assert_eq!("fallback", for_stage_b.get_string("example.optional")?);
        Ok(())
    }

    #[test]
    fn environment_runtime_setting_source_can_load_dotenv_files() -> Result<()> {
        let temp_dir = tempdir()?;
        let dotenv_path = temp_dir.path().join(".env");
        fs::write(&dotenv_path, "EXAMPLE_REQUIRED=resolved-from-dotenv\n")?;

        let mut builder = PipelineRuntimeSettingsBuilder::new();
        builder.install_source(Arc::new(EnvironmentVariableRuntimeSettingSource::new(
            Some(BTreeMap::new()),
            None,
            Some(vec![dotenv_path]),
        )?))?;
        builder.require(
            RuntimeSettingSpec::string(
                "example.required",
                Option::<Value>::None,
                Some("EXAMPLE_REQUIRED"),
            ),
            ["stage.a"],
        )?;

        let resolved = builder.freeze()?.resolve_for_stages(["stage.a"])?;
        assert_eq!("resolved-from-dotenv", resolved.get_string("example.required")?);
        Ok(())
    }

    #[test]
    fn explicit_environment_overrides_dotenv_values() -> Result<()> {
        let temp_dir = tempdir()?;
        let dotenv_path = temp_dir.path().join(".env");
        fs::write(&dotenv_path, "EXAMPLE_REQUIRED=resolved-from-dotenv\n")?;

        let source = EnvironmentVariableRuntimeSettingSource::new(
            Some(BTreeMap::from([(
                "EXAMPLE_REQUIRED".to_owned(),
                "resolved-from-env".to_owned(),
            )])),
            None,
            Some(vec![dotenv_path]),
        )?;
        let spec = RuntimeSettingSpec::string(
            "example.required",
            Option::<Value>::None,
            Some("EXAMPLE_REQUIRED"),
        );

        assert_eq!(
            Some(Value::String("resolved-from-env".to_owned())),
            source.resolve(&spec)
        );
        Ok(())
    }
}
