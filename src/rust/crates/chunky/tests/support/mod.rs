#![allow(dead_code)]

use anyhow::{anyhow, Result};
use async_trait::async_trait;
use chunky_example::artifact_binding_models::{AnyArtifactInstance, ArtifactHandle, ArtifactInstance};
use chunky_example::artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle};
use chunky_example::artifact_models::{
    AnyArtifactDeclaration, ArtifactCapability, ArtifactDeclaration, ArtifactKey,
    ArtifactRequirement, ArtifactRole, ArtifactValue, Codec, ErasedCodec, PhaseMatchMode,
};
use chunky_example::manifest_graph::StageManifestGraphBuilder;
use chunky_example::stage_manifest::{StageConsumer, StageManifest, StageProducer};
use chunky_example::artifact_binding_models::{InputArtifacts, OutputArtifacts};
use chunky_example::stage_models::{
    BoxFuture, PlanningRunPolicy, ResolvedStageRequirements, ResourcePolicy, StageExecutionContext,
    StageExecutor, StageOutputResolver, StagePlanningContext, StageRegistration, StageResourceResolver,
};
use chunky_example::task_request_graph::{
    PlannerRequestSettings, PlannerSettingsResolver, ProducerSelector, TaskRequestContext,
    TaskRequestGraph, TaskRequestGraphBuilder,
};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex};

#[derive(Clone, Copy, Debug)]
pub struct JsonValueCodec;

impl Codec<Value> for JsonValueCodec {
    fn codec_id(&self) -> &'static str {
        "test-jsonish"
    }

    fn codec_version(&self) -> u32 {
        1
    }

    fn mime_type(&self) -> &'static str {
        "application/json"
    }

    fn extension(&self) -> &'static str {
        ".json"
    }

    fn serialize(&self, value: &Value) -> Result<Vec<u8>> {
        Ok(serde_json::to_vec(value)?)
    }

    fn deserialize(&self, raw: &[u8]) -> Result<Value> {
        Ok(serde_json::from_slice(raw)?)
    }
}

#[derive(Debug)]
pub struct MemorySource {
    value: Value,
    closed: Mutex<bool>,
}

impl MemorySource {
    pub fn new(value: Value) -> Arc<Self> {
        Arc::new(Self {
            value,
            closed: Mutex::new(false),
        })
    }

    pub fn value(&self) -> Value {
        self.value.clone()
    }

    pub fn is_closed(&self) -> bool {
        *self.closed.lock().expect("source mutex poisoned")
    }
}

#[async_trait]
impl ArtifactSourceHandle for MemorySource {
    fn kind(&self) -> &'static str {
        "memory"
    }

    async fn get_value(&self, _codec: &dyn ErasedCodec) -> Result<ArtifactValue> {
        if self.is_closed() {
            return Err(anyhow!("source is closed"));
        }
        Ok(Arc::new(self.value.clone()))
    }

    async fn get_content_fingerprint(&self, codec: &dyn ErasedCodec) -> Result<String> {
        let value: ArtifactValue = Arc::new(self.value.clone());
        Ok(hex_bytes(&codec.serialize_any(&value)?))
    }

    async fn close(&self) -> Result<()> {
        *self.closed.lock().expect("source mutex poisoned") = true;
        Ok(())
    }
}

#[derive(Default)]
struct RecordingTargetState {
    prepared: Vec<(Value, String)>,
    committed: bool,
    aborted: bool,
}

#[derive(Clone)]
pub struct RecordingTarget {
    pub name: String,
    state: Arc<Mutex<RecordingTargetState>>,
}

impl RecordingTarget {
    pub fn new(name: impl Into<String>) -> Arc<Self> {
        Arc::new(Self {
            name: name.into(),
            state: Arc::new(Mutex::new(RecordingTargetState::default())),
        })
    }

    pub fn prepared(&self) -> Vec<(Value, String)> {
        self.state.lock().expect("target mutex poisoned").prepared.clone()
    }

    pub fn committed(&self) -> bool {
        self.state.lock().expect("target mutex poisoned").committed
    }

    pub fn aborted(&self) -> bool {
        self.state.lock().expect("target mutex poisoned").aborted
    }
}

#[async_trait]
impl ArtifactTargetHandle for RecordingTarget {
    fn kind(&self) -> &'static str {
        "recording-target"
    }

    async fn prepare(
        &self,
        value: ArtifactValue,
        _codec: &dyn ErasedCodec,
        fingerprint: &str,
    ) -> Result<()> {
        let mut state = self.state.lock().expect("target mutex poisoned");
        if state.committed || state.aborted {
            return Err(anyhow!("target {} is terminal", self.name));
        }
        let value = value
            .as_ref()
            .downcast_ref::<Value>()
            .ok_or_else(|| anyhow!("recording target received non-json value"))?
            .clone();
        state.prepared.push((value, fingerprint.to_owned()));
        Ok(())
    }

    async fn commit(&self) -> Result<()> {
        let mut state = self.state.lock().expect("target mutex poisoned");
        if state.aborted {
            return Ok(());
        }
        if state.prepared.is_empty() {
            return Err(anyhow!("target {} was not prepared", self.name));
        }
        state.committed = true;
        Ok(())
    }

    async fn abort(&self) -> Result<()> {
        let mut state = self.state.lock().expect("target mutex poisoned");
        if state.committed {
            return Ok(());
        }
        state.aborted = true;
        Ok(())
    }
}

pub struct NoopStageExecutor;

impl StageExecutor for NoopStageExecutor {
    fn execute<'a>(
        &'a mut self,
        _inputs: &'a InputArtifacts,
        _outputs: &'a OutputArtifacts,
        _context: &'a StageExecutionContext,
    ) -> BoxFuture<'a, Result<()>> {
        Box::pin(async { Ok(()) })
    }
}

pub fn dummy_executor_factory() -> chunky_example::stage_models::StageExecutorFactory {
    Arc::new(|| Box::new(NoopStageExecutor))
}

pub fn artifact_key(name: &str) -> ArtifactKey {
    if name.starts_with("setting_") {
        return setting_key(name);
    }
    ArtifactKey::parse(&format!("graph//{}", canonical_node_id(name))).unwrap()
}

pub fn setting_key(name: &str) -> ArtifactKey {
    let name = canonical_node_id(name);
    if let Some(base) = name.strip_suffix("_user_or_defaults") {
        return ArtifactKey::parse(&format!("settings//{base}")).unwrap();
    }
    let marker = "_override_stage_";
    if let Some((base, target_suffix)) = name.split_once(marker) {
        return ArtifactKey::parse(&format!("stage_{target_suffix}//settings//{base}")).unwrap();
    }
    ArtifactKey::parse(&format!("settings//{name}")).unwrap()
}

pub fn canonical_node_id(name: &str) -> String {
    let normalized = name.replace('-', "_");
    normalized
        .strip_suffix("_optional")
        .unwrap_or(&normalized)
        .to_owned()
}

pub fn decl(name_or_key: impl Into<KeyOrName>, role: ArtifactRole) -> ArtifactDeclaration<Value> {
    let key = match name_or_key.into() {
        KeyOrName::Key(key) => key,
        KeyOrName::Name(name) => artifact_key_with_role(&name, role),
    };
    ArtifactDeclaration::new(
        key,
        role,
        1,
        JsonValueCodec,
        role != ArtifactRole::Source,
    )
    .unwrap()
}

pub enum KeyOrName {
    Key(ArtifactKey),
    Name(String),
}

impl From<ArtifactKey> for KeyOrName {
    fn from(value: ArtifactKey) -> Self {
        Self::Key(value)
    }
}

impl From<&str> for KeyOrName {
    fn from(value: &str) -> Self {
        Self::Name(value.to_owned())
    }
}

pub fn artifact_key_with_role(name: &str, _role: ArtifactRole) -> ArtifactKey {
    if name.contains("//") {
        ArtifactKey::parse(name).unwrap()
    } else {
        artifact_key(name)
    }
}

pub fn requirement(key: ArtifactKey) -> ArtifactRequirement {
    ArtifactRequirement {
        artifact_id: key,
        required: true,
        phase_match: PhaseMatchMode::AtMost,
        pushed_settings: vec![],
    }
}

pub fn optional_requirement(key: ArtifactKey) -> ArtifactRequirement {
    ArtifactRequirement {
        artifact_id: key,
        required: false,
        phase_match: PhaseMatchMode::AtMost,
        pushed_settings: vec![],
    }
}

pub fn exact_requirement(key: ArtifactKey) -> ArtifactRequirement {
    ArtifactRequirement {
        artifact_id: key,
        required: true,
        phase_match: PhaseMatchMode::Exact,
        pushed_settings: vec![],
    }
}

pub fn stage_registration(
    stage_id: &str,
    input_ids: Vec<ArtifactKey>,
    output_ids: Vec<ArtifactKey>,
    requirements: impl Fn(&StagePlanningContext) -> Vec<ArtifactRequirement> + Send + Sync + 'static,
) -> StageRegistration {
    stage_registration_with_resolvers(stage_id, input_ids, output_ids, requirements, None, None)
}

pub fn stage_registration_with_resolvers(
    stage_id: &str,
    input_ids: Vec<ArtifactKey>,
    output_ids: Vec<ArtifactKey>,
    requirements: impl Fn(&StagePlanningContext) -> Vec<ArtifactRequirement> + Send + Sync + 'static,
    output_resolver: Option<StageOutputResolver>,
    resource_resolver: Option<StageResourceResolver>,
) -> StageRegistration {
    let outputs: Vec<ArtifactCapability> = output_ids
        .iter()
        .cloned()
        .map(|artifact_id| ArtifactCapability { artifact_id })
        .collect();
    let output_capabilities = outputs.clone();
    let output_resolver = output_resolver.unwrap_or_else(|| {
        let outputs = outputs.clone();
        Arc::new(move |_context: &StagePlanningContext| Ok(outputs.clone()))
    });
    let resource_resolver = resource_resolver
        .unwrap_or_else(|| Arc::new(|_context: &StagePlanningContext| Ok(ResourcePolicy::default())));

    StageRegistration {
        stage_id: stage_id.to_owned(),
        display_name: stage_id.to_owned(),
        registration_version: "test".to_owned(),
        behavior_version: "test".to_owned(),
        executor_factory: dummy_executor_factory(),
        owned_artifacts: vec![],
        input_capabilities: input_ids
            .into_iter()
            .map(|artifact_id| ArtifactCapability { artifact_id })
            .collect(),
        output_capabilities,
        resolve_requirements: Arc::new(move |context| {
            Ok(ResolvedStageRequirements {
                inputs: requirements(context),
            })
        }),
        resolve_output_capabilities: output_resolver,
        resolve_resource_policy: resource_resolver,
        concurrency_policy: None,
    }
}

pub fn manifest(
    artifacts: Vec<ArtifactDeclaration<Value>>,
    stages: Vec<StageRegistration>,
    default_producers: BTreeMap<ArtifactKey, String>,
) -> StageManifest {
    let artifacts_by_key: BTreeMap<ArtifactKey, AnyArtifactDeclaration> = artifacts
        .into_iter()
        .map(|declaration| (declaration.artifact_id().clone(), declaration.erase()))
        .collect();
    let stages_by_id: BTreeMap<String, StageRegistration> = stages
        .into_iter()
        .map(|stage| (stage.stage_id.clone(), stage))
        .collect();
    let mut producers: BTreeMap<ArtifactKey, Vec<StageProducer>> = BTreeMap::new();
    let mut consumers: BTreeMap<ArtifactKey, Vec<StageConsumer>> = BTreeMap::new();
    for stage in stages_by_id.values() {
        for output in &stage.output_capabilities {
            producers.entry(output.artifact_id.clone()).or_default().push(StageProducer {
                stage_id: stage.stage_id.clone(),
                declared_output_id: output.artifact_id.clone(),
            });
        }
        for input in &stage.input_capabilities {
            consumers.entry(input.artifact_id.clone()).or_default().push(StageConsumer {
                stage_id: stage.stage_id.clone(),
            });
        }
    }
    StageManifest::new(
        "test-manifest",
        BTreeMap::new(),
        stages_by_id,
        artifacts_by_key,
        producers,
        consumers,
        default_producers,
        json!({}),
    )
}

pub fn source_instance(
    declaration: &ArtifactDeclaration<Value>,
    value: Value,
) -> ArtifactInstance<Value> {
    let source = MemorySource::new(value);
    ArtifactInstance::new(ArtifactHandle::new(
        declaration.erase(),
        format!("supplied::{}", declaration.artifact_id()),
        Some(source),
        vec![],
    ))
    .unwrap()
}

pub async fn evaluate(
    manifest: &StageManifest,
    supplied: BTreeMap<ArtifactKey, ArtifactInstance<Value>>,
    requested: BTreeSet<ArtifactKey>,
    user_supplied_settings: BTreeSet<ArtifactKey>,
    run_policy: Option<PlanningRunPolicy>,
) -> Result<TaskRequestGraph<'_>> {
    let supplied_any: BTreeMap<ArtifactKey, AnyArtifactInstance> = supplied
        .iter()
        .map(|(artifact_id, instance)| (artifact_id.clone(), instance.erase()))
        .collect();
    let resolver = PlannerSettingsResolver::new(manifest);
    let producer_selector = ProducerSelector::new(manifest);
    let manifest_graph = StageManifestGraphBuilder::new(manifest).build();
    let request_settings = PlannerRequestSettings::read_from(
        manifest,
        &supplied_any,
        &user_supplied_settings,
    )
    .await?;
    let requested_output_ids = requested.into_iter().collect();
    let context = TaskRequestContext {
        manifest,
        manifest_graph,
        requested_output_ids,
        supplied_instances: supplied_any,
        policy: run_policy.unwrap_or_default(),
        request_settings,
    };
    TaskRequestGraphBuilder::new(manifest, &resolver, &producer_selector)
        .build(&context)
        .await
}

pub fn set(items: impl IntoIterator<Item = ArtifactKey>) -> BTreeSet<ArtifactKey> {
    items.into_iter().collect()
}

fn hex_bytes(raw: &[u8]) -> String {
    raw.iter().map(|byte| format!("{byte:02x}")).collect()
}
