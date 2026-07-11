//! Executable topology example used by tests and demos.
//!
//! These helpers are intentionally small and deterministic.  They produce a
//! toy WebVTT topology whose payloads make graph shape, settings propagation,
//! and cache reuse visible during tests or demos.

use anyhow::{bail, Result};
use async_trait::async_trait;
use serde_json::{json, Value};
use std::sync::Arc;

use crate::artifact_binding_models::{InputArtifacts, OutputArtifacts};
use crate::artifact_codecs_example::{BytesCodec, JsonCodec};
use crate::artifact_handles::ArtifactSourceHandle;
use crate::artifact_handles_examples::InMemorySource;
use crate::artifact_models::{
    ArtifactCapability, ArtifactDeclaration, ArtifactKey, ArtifactRequirement, ArtifactRole, Codec,
    DefaultValueSourceProvider, PhaseMatchMode,
};
use crate::runtime::{
    PipelineRuntimeServicesBuilder, PipelineRuntimeSettings, PipelineRuntimeSettingsBuilder,
    RuntimeSettingSpec, RuntimeStageSetting,
};
use crate::stage_manifest::StageManifestBuilder;
use crate::stage_models::{
    BoxFuture, PipelineFeature, ResolvedStageRequirements, ResourcePolicy, StageExecutionContext,
    StageExecutor, StageManifestBuilderAccess, StagePlanningContext, StageRegistration,
};

#[derive(Clone, Copy, Default)]
pub struct TextCodec;

impl Codec<String> for TextCodec {
    fn codec_id(&self) -> &'static str {
        "utf8-text"
    }
    fn codec_version(&self) -> u32 {
        1
    }
    fn mime_type(&self) -> &'static str {
        "text/plain"
    }
    fn extension(&self) -> &'static str {
        ".txt"
    }
    fn serialize(&self, value: &String) -> Result<Vec<u8>> {
        Ok(value.as_bytes().to_vec())
    }
    fn deserialize(&self, raw: &[u8]) -> Result<String> {
        Ok(String::from_utf8(raw.to_vec())?)
    }
}

#[derive(Clone, Copy, Default)]
pub struct AudioSettingsDefaultValueSourceProvider;

fn resolved_audio_settings(value: Option<Value>) -> Result<Value> {
    let mut resolved = serde_json::Map::new();
    resolved.insert("sample_rate_hz".to_owned(), json!(44100));
    resolved.insert("channels".to_owned(), json!(1));

    match value {
        None => {}
        Some(Value::Object(user_values)) => resolved.extend(user_values),
        Some(other) => bail!("audio settings must be mapping-like, got {other}"),
    }

    Ok(Value::Object(resolved))
}

fn key(domain: &str, content_kind: &str, phase: Option<u32>) -> ArtifactKey {
    ArtifactKey::new(domain, content_kind, phase, None).expect("valid artifact key")
}

fn legacy_key(value: &str) -> ArtifactKey {
    let (path, phase) = match value.rsplit_once('#') {
        Some((path, phase)) => (path, Some(phase.parse::<u32>().expect("valid phase"))),
        None => (value, None),
    };
    let parts = path.split("//").collect::<Vec<_>>();
    match parts.as_slice() {
        [domain, content_kind] => key(domain, content_kind, phase),
        [projection, domain, content_kind] => key(domain, content_kind, phase)
            .with_override_domain(*projection)
            .expect("valid projection"),
        _ => panic!("invalid legacy artifact key: {value}"),
    }
}

#[async_trait]
impl DefaultValueSourceProvider<Value> for AudioSettingsDefaultValueSourceProvider {
    async fn resolve_source(
        &self,
        supplied_source: Option<Arc<dyn ArtifactSourceHandle>>,
        declaration: &ArtifactDeclaration<Value>,
    ) -> Result<Arc<dyn ArtifactSourceHandle>> {
        let supplied_value = match supplied_source {
            None => None,
            Some(source) => {
                let value = source.get_value(declaration.erase().codec.as_ref()).await?;
                Some(
                    value
                        .as_ref()
                        .downcast_ref::<Value>()
                        .ok_or_else(|| {
                            anyhow::anyhow!("audio settings source returned the wrong value type")
                        })?
                        .clone(),
                )
            }
        };
        Ok(InMemorySource::new(
            "audio-settings-default",
            resolved_audio_settings(supplied_value)?,
        ))
    }
}

pub fn source_audio() -> ArtifactDeclaration<Vec<u8>> {
    ArtifactDeclaration::new(
        legacy_key("source//audio"),
        ArtifactRole::Source,
        1,
        BytesCodec,
        false,
    )
    .expect("valid source declaration")
}

pub fn audio_settings() -> ArtifactDeclaration<Value> {
    json_decl(
        "audio_preprocessing//audio_settings",
        ArtifactRole::Settings,
        true,
    )
    .with_default_value_source_provider(Arc::new(AudioSettingsDefaultValueSourceProvider))
}
pub fn speech_audio_settings() -> ArtifactDeclaration<Value> {
    audio_settings()
        .with_override_domain("speech_processing")
        .expect("valid override")
}
pub fn music_audio_settings() -> ArtifactDeclaration<Value> {
    audio_settings()
        .with_override_domain("music_processing")
        .expect("valid override")
}
pub fn asr_settings() -> ArtifactDeclaration<Value> {
    json_decl("settings//asr", ArtifactRole::Settings, false)
}
pub fn language_settings() -> ArtifactDeclaration<Value> {
    json_decl("settings//language", ArtifactRole::Settings, false)
}
pub fn diarization_settings() -> ArtifactDeclaration<Value> {
    json_decl("settings//diarization", ArtifactRole::Settings, false)
}
pub fn alignment_settings() -> ArtifactDeclaration<Value> {
    json_decl("settings//alignment", ArtifactRole::Settings, false)
}
pub fn vtt_settings() -> ArtifactDeclaration<Value> {
    json_decl("settings//vtt", ArtifactRole::Settings, false)
}
pub fn render_settings() -> ArtifactDeclaration<Value> {
    json_decl("settings//render", ArtifactRole::Settings, false)
}
pub fn prepared_audio() -> ArtifactDeclaration<Value> {
    json_decl("audio//prepared", ArtifactRole::Intermediate, true)
}
pub fn token_text() -> ArtifactDeclaration<Value> {
    json_decl("transcript//token_text", ArtifactRole::Intermediate, true)
}
pub fn speaker_segments() -> ArtifactDeclaration<Value> {
    json_decl("speaker//segments", ArtifactRole::Intermediate, true)
}
pub fn music_spans() -> ArtifactDeclaration<Value> {
    json_decl("audio//music_spans", ArtifactRole::Intermediate, true)
}
pub fn timed_words() -> ArtifactDeclaration<Value> {
    json_decl("transcript//timed_words", ArtifactRole::Intermediate, true)
}

pub fn webvtt_transcript() -> ArtifactDeclaration<String> {
    ArtifactDeclaration::new(
        legacy_key("render//webvtt_transcript"),
        ArtifactRole::Target,
        1,
        TextCodec,
        false,
    )
    .expect("valid vtt declaration")
}

fn json_decl(id: &str, role: ArtifactRole, cacheable: bool) -> ArtifactDeclaration<Value> {
    ArtifactDeclaration::new(
        legacy_key(id),
        role,
        1,
        JsonCodec::<Value>::default(),
        cacheable,
    )
    .expect("valid example declaration")
}

fn required(key: &ArtifactKey) -> ArtifactRequirement {
    ArtifactRequirement {
        artifact_id: key.clone(),
        required: true,
        phase_match: PhaseMatchMode::AtMost,
        pushed_settings: vec![],
    }
}

fn required_with_pushed_setting(key: &ArtifactKey, pushed: &ArtifactKey) -> ArtifactRequirement {
    ArtifactRequirement {
        artifact_id: key.clone(),
        required: true,
        phase_match: PhaseMatchMode::AtMost,
        pushed_settings: vec![pushed.clone()],
    }
}

fn fixed_outputs(outputs: Vec<ArtifactCapability>) -> crate::stage_models::StageOutputResolver {
    Arc::new(move |_context: &StagePlanningContext| Ok(outputs.clone()))
}

fn cpu_resources() -> crate::stage_models::StageResourceResolver {
    Arc::new(|_context: &StagePlanningContext| ResourcePolicy::new(["cpu_compute_bound"]))
}

fn unbound_resources() -> crate::stage_models::StageResourceResolver {
    Arc::new(|_context: &StagePlanningContext| Ok(ResourcePolicy::unbound()))
}

fn payload(context: &StageExecutionContext, stage: &str, extra: Value) -> Value {
    let mut object = serde_json::Map::new();
    object.insert("stage".to_owned(), Value::String(stage.to_owned()));
    object.insert(
        "invocation_id".to_owned(),
        Value::String(context.invocation_id.clone()),
    );
    if let Value::Object(extra) = extra {
        object.extend(extra);
    }
    Value::Object(object)
}

fn invocation_id_from(value: &Value) -> Value {
    value.get("invocation_id").cloned().unwrap_or(Value::Null)
}

pub const TRANSCRIBE_STAGE_ID: &str = "asr.transcribe";
pub const ASR_PROVIDER_NAME_SETTING: &str = "example.asr.provider_name";
pub const ASR_API_KEY_SETTING: &str = "example.asr.api_key";
pub const ASR_CONCURRENCY_LIMIT_SETTING: &str = "example.asr.concurrency_limit";

fn transcribe_runtime_settings() -> Vec<RuntimeStageSetting> {
    vec![
        RuntimeStageSetting::optional(RuntimeSettingSpec::string(
            ASR_PROVIDER_NAME_SETTING,
            Some("example-asr"),
            Some("EXAMPLE_ASR_PROVIDER_NAME"),
        )),
        RuntimeStageSetting::required(RuntimeSettingSpec::string(
            ASR_API_KEY_SETTING,
            None::<Value>,
            Some("EXAMPLE_ASR_API_KEY"),
        )),
        RuntimeStageSetting::optional(RuntimeSettingSpec::integer(
            ASR_CONCURRENCY_LIMIT_SETTING,
            Some(4),
            Some("EXAMPLE_ASR_CONCURRENCY_LIMIT"),
        )),
    ]
}

pub struct PrepareAudioExecutor;
impl StageExecutor for PrepareAudioExecutor {
    fn execute<'a>(
        &'a mut self,
        inputs: &'a InputArtifacts,
        outputs: &'a OutputArtifacts,
        context: &'a StageExecutionContext,
        _runtime_settings: &'a PipelineRuntimeSettings,
    ) -> BoxFuture<'a, Result<()>> {
        Box::pin(async move {
            let source = source_audio();
            let prepared = prepared_audio();
            let raw = inputs.get_artifact(&source)?.read().await?;
            let audio_settings_key = audio_settings().artifact_id().clone();
            let audio_settings = context.settings.require(&audio_settings_key)?.clone();
            context.cancellation.raise_if_cancelled()?;
            outputs
                .get_artifact(&prepared)?
                .write(payload(
                    context,
                    "prepare_audio",
                    json!({"source_bytes": raw.len(), "audio_settings": audio_settings}),
                ))
                .await?;
            Ok(())
        })
    }
}

pub struct TranscribeExecutor;
impl StageExecutor for TranscribeExecutor {
    fn execute<'a>(
        &'a mut self,
        inputs: &'a InputArtifacts,
        outputs: &'a OutputArtifacts,
        context: &'a StageExecutionContext,
        runtime_settings: &'a PipelineRuntimeSettings,
    ) -> BoxFuture<'a, Result<()>> {
        Box::pin(async move {
            let prepared = inputs.get_artifact(&prepared_audio())?.read().await?;
            let asr = inputs.get_artifact(&asr_settings())?.read().await?;
            let language = inputs.get_artifact(&language_settings())?.read().await?;
            context.cancellation.raise_if_cancelled()?;
            outputs
                .get_artifact(&token_text())?
                .write(payload(
                    context,
                    "transcribe",
                    json!({
                        "prepared_audio_invocation": invocation_id_from(&prepared),
                        "asr": (*asr).clone(),
                        "language": (*language).clone(),
                        "provider_name": runtime_settings.get_string(ASR_PROVIDER_NAME_SETTING)?,
                        "concurrency_limit": runtime_settings.get_int(ASR_CONCURRENCY_LIMIT_SETTING)?,
                        "api_key_configured": runtime_settings.has(ASR_API_KEY_SETTING),
                    }),
                ))
                .await?;
            Ok(())
        })
    }
}

pub struct DiarizeExecutor;
impl StageExecutor for DiarizeExecutor {
    fn execute<'a>(
        &'a mut self,
        inputs: &'a InputArtifacts,
        outputs: &'a OutputArtifacts,
        context: &'a StageExecutionContext,
        _runtime_settings: &'a PipelineRuntimeSettings,
    ) -> BoxFuture<'a, Result<()>> {
        Box::pin(async move {
            let prepared = inputs.get_artifact(&prepared_audio())?.read().await?;
            let diarization = inputs.get_artifact(&diarization_settings())?.read().await?;
            context.cancellation.raise_if_cancelled()?;
            outputs
                .get_artifact(&speaker_segments())?
                .write(payload(
                    context,
                    "diarize",
                    json!({
                        "prepared_audio_invocation": invocation_id_from(&prepared),
                        "diarization": (*diarization).clone(),
                    }),
                ))
                .await?;
            Ok(())
        })
    }
}

pub struct DetectMusicExecutor;
impl StageExecutor for DetectMusicExecutor {
    fn execute<'a>(
        &'a mut self,
        inputs: &'a InputArtifacts,
        outputs: &'a OutputArtifacts,
        context: &'a StageExecutionContext,
        _runtime_settings: &'a PipelineRuntimeSettings,
    ) -> BoxFuture<'a, Result<()>> {
        Box::pin(async move {
            let prepared = inputs.get_artifact(&prepared_audio())?.read().await?;
            context.cancellation.raise_if_cancelled()?;
            outputs
                .get_artifact(&music_spans())?
                .write(payload(
                    context,
                    "detect_music",
                    json!({"prepared_audio_invocation": invocation_id_from(&prepared)}),
                ))
                .await?;
            Ok(())
        })
    }
}

pub struct AlignExecutor;
impl StageExecutor for AlignExecutor {
    fn execute<'a>(
        &'a mut self,
        inputs: &'a InputArtifacts,
        outputs: &'a OutputArtifacts,
        context: &'a StageExecutionContext,
        _runtime_settings: &'a PipelineRuntimeSettings,
    ) -> BoxFuture<'a, Result<()>> {
        Box::pin(async move {
            let token_text_value = inputs.get_artifact(&token_text())?.read().await?;
            let speaker_segments_value = inputs.get_artifact(&speaker_segments())?.read().await?;
            let alignment = inputs.get_artifact(&alignment_settings())?.read().await?;
            context.cancellation.raise_if_cancelled()?;
            outputs
                .get_artifact(&timed_words())?
                .write(payload(
                    context,
                    "align",
                    json!({
                        "token_text_invocation": invocation_id_from(&token_text_value),
                        "speaker_segments_invocation": invocation_id_from(&speaker_segments_value),
                        "alignment": (*alignment).clone(),
                    }),
                ))
                .await?;
            Ok(())
        })
    }
}

pub struct RenderVttExecutor;
impl StageExecutor for RenderVttExecutor {
    fn execute<'a>(
        &'a mut self,
        inputs: &'a InputArtifacts,
        outputs: &'a OutputArtifacts,
        context: &'a StageExecutionContext,
        _runtime_settings: &'a PipelineRuntimeSettings,
    ) -> BoxFuture<'a, Result<()>> {
        Box::pin(async move {
            let timed_words_value = inputs.get_artifact(&timed_words())?.read().await?;
            let music_spans_value = inputs.get_artifact(&music_spans())?.read().await?;
            let vtt = inputs.get_artifact(&vtt_settings())?.read().await?;
            let render = inputs.get_artifact(&render_settings())?.read().await?;
            context.cancellation.raise_if_cancelled()?;
            let value = format!(
                "WEBVTT\n\ntimed_words={}\nmusic_spans={}\nvtt_settings={}\nrender_settings={}\nrender_invocation={}",
                invocation_id_from(&timed_words_value),
                invocation_id_from(&music_spans_value),
                vtt,
                render,
                context.invocation_id,
            );
            outputs
                .get_artifact(&webvtt_transcript())?
                .write(value)
                .await?;
            Ok(())
        })
    }
}

fn executor_factory<E>() -> crate::stage_models::StageExecutorFactory
where
    E: StageExecutor + Default + Send + 'static,
{
    Arc::new(|| Box::new(E::default()))
}

macro_rules! impl_default_executor {
    ($($ty:ty),* $(,)?) => {$(
        impl Default for $ty { fn default() -> Self { Self } }
    )*};
}
impl_default_executor!(
    PrepareAudioExecutor,
    TranscribeExecutor,
    DiarizeExecutor,
    DetectMusicExecutor,
    AlignExecutor,
    RenderVttExecutor
);

pub fn prepare_audio_stage() -> StageRegistration {
    let source = source_audio();
    let audio_settings_decl = audio_settings();
    let prepared = prepared_audio();
    let output = ArtifactCapability {
        artifact_id: prepared.artifact_id().clone(),
    };
    StageRegistration {
        stage_id: "audio.prepare".to_owned(),
        display_name: "Prepare audio".to_owned(),
        registration_version: "1".to_owned(),
        behavior_version: "1".to_owned(),
        executor_factory: executor_factory::<PrepareAudioExecutor>(),
        owned_artifacts: vec![prepared.erase()],
        input_capabilities: vec![
            ArtifactCapability {
                artifact_id: source.artifact_id().clone(),
            },
            ArtifactCapability {
                artifact_id: audio_settings_decl.artifact_id().clone(),
            },
        ],
        output_capabilities: vec![output.clone()],
        resolve_requirements: Arc::new(move |_context| {
            Ok(ResolvedStageRequirements {
                inputs: vec![required(source.artifact_id())],
            })
        }),
        resolve_output_capabilities: fixed_outputs(vec![output]),
        resolve_resource_policy: cpu_resources(),
        runtime_settings: vec![],
        concurrency_policy: None,
    }
}

pub fn build_transcribe_stage() -> StageRegistration {
    let prepared = prepared_audio();
    let speech_audio = speech_audio_settings();
    let asr = asr_settings();
    let language = language_settings();
    let token = token_text();
    let output = ArtifactCapability {
        artifact_id: token.artifact_id().clone(),
    };
    StageRegistration {
        stage_id: TRANSCRIBE_STAGE_ID.to_owned(),
        display_name: "Transcribe".to_owned(),
        registration_version: "1".to_owned(),
        behavior_version: "1".to_owned(),
        executor_factory: executor_factory::<TranscribeExecutor>(),
        owned_artifacts: vec![asr.erase(), language.erase(), token.erase()],
        input_capabilities: vec![
            ArtifactCapability {
                artifact_id: prepared.artifact_id().clone(),
            },
            ArtifactCapability {
                artifact_id: asr_settings().artifact_id().clone(),
            },
            ArtifactCapability {
                artifact_id: language_settings().artifact_id().clone(),
            },
        ],
        output_capabilities: vec![output.clone()],
        resolve_requirements: Arc::new(move |_context| {
            Ok(ResolvedStageRequirements {
                inputs: vec![
                    required_with_pushed_setting(
                        prepared.artifact_id(),
                        speech_audio.artifact_id(),
                    ),
                    required(asr_settings().artifact_id()),
                    required(language_settings().artifact_id()),
                ],
            })
        }),
        resolve_output_capabilities: fixed_outputs(vec![output]),
        resolve_resource_policy: cpu_resources(),
        runtime_settings: transcribe_runtime_settings(),
        concurrency_policy: None,
    }
}

pub fn transcribe_stage() -> StageRegistration {
    build_transcribe_stage()
}

pub fn diarize_stage() -> StageRegistration {
    let prepared = prepared_audio();
    let speech_audio = speech_audio_settings();
    let diarization = diarization_settings();
    let segments = speaker_segments();
    let output = ArtifactCapability {
        artifact_id: segments.artifact_id().clone(),
    };
    StageRegistration {
        stage_id: "speaker.diarize".to_owned(),
        display_name: "Diarize".to_owned(),
        registration_version: "1".to_owned(),
        behavior_version: "1".to_owned(),
        executor_factory: executor_factory::<DiarizeExecutor>(),
        owned_artifacts: vec![diarization.erase(), segments.erase()],
        input_capabilities: vec![
            ArtifactCapability {
                artifact_id: prepared.artifact_id().clone(),
            },
            ArtifactCapability {
                artifact_id: diarization_settings().artifact_id().clone(),
            },
        ],
        output_capabilities: vec![output.clone()],
        resolve_requirements: Arc::new(move |_context| {
            Ok(ResolvedStageRequirements {
                inputs: vec![
                    required_with_pushed_setting(
                        prepared.artifact_id(),
                        speech_audio.artifact_id(),
                    ),
                    required(diarization_settings().artifact_id()),
                ],
            })
        }),
        resolve_output_capabilities: fixed_outputs(vec![output]),
        resolve_resource_policy: cpu_resources(),
        runtime_settings: vec![],
        concurrency_policy: None,
    }
}

pub fn detect_music_stage() -> StageRegistration {
    let prepared = prepared_audio();
    let music_audio = music_audio_settings();
    let music = music_spans();
    let output = ArtifactCapability {
        artifact_id: music.artifact_id().clone(),
    };
    StageRegistration {
        stage_id: "audio.detect_music".to_owned(),
        display_name: "Detect music".to_owned(),
        registration_version: "1".to_owned(),
        behavior_version: "1".to_owned(),
        executor_factory: executor_factory::<DetectMusicExecutor>(),
        owned_artifacts: vec![music.erase()],
        input_capabilities: vec![ArtifactCapability {
            artifact_id: prepared.artifact_id().clone(),
        }],
        output_capabilities: vec![output.clone()],
        resolve_requirements: Arc::new(move |_context| {
            Ok(ResolvedStageRequirements {
                inputs: vec![required_with_pushed_setting(
                    prepared.artifact_id(),
                    music_audio.artifact_id(),
                )],
            })
        }),
        resolve_output_capabilities: fixed_outputs(vec![output]),
        resolve_resource_policy: cpu_resources(),
        runtime_settings: vec![],
        concurrency_policy: None,
    }
}

pub fn align_stage() -> StageRegistration {
    let token = token_text();
    let segments = speaker_segments();
    let alignment = alignment_settings();
    let timed = timed_words();
    let output = ArtifactCapability {
        artifact_id: timed.artifact_id().clone(),
    };
    StageRegistration {
        stage_id: "transcript.align".to_owned(),
        display_name: "Align".to_owned(),
        registration_version: "1".to_owned(),
        behavior_version: "1".to_owned(),
        executor_factory: executor_factory::<AlignExecutor>(),
        owned_artifacts: vec![alignment.erase(), timed.erase()],
        input_capabilities: vec![
            ArtifactCapability {
                artifact_id: token_text().artifact_id().clone(),
            },
            ArtifactCapability {
                artifact_id: speaker_segments().artifact_id().clone(),
            },
            ArtifactCapability {
                artifact_id: alignment_settings().artifact_id().clone(),
            },
        ],
        output_capabilities: vec![output.clone()],
        resolve_requirements: Arc::new(move |_context| {
            Ok(ResolvedStageRequirements {
                inputs: vec![
                    required(token.artifact_id()),
                    required(segments.artifact_id()),
                    required(alignment.artifact_id()),
                ],
            })
        }),
        resolve_output_capabilities: fixed_outputs(vec![output]),
        resolve_resource_policy: cpu_resources(),
        runtime_settings: vec![],
        concurrency_policy: None,
    }
}

pub fn render_vtt_stage() -> StageRegistration {
    let timed = timed_words();
    let music = music_spans();
    let vtt = vtt_settings();
    let render = render_settings();
    let transcript = webvtt_transcript();
    let output = ArtifactCapability {
        artifact_id: transcript.artifact_id().clone(),
    };
    StageRegistration {
        stage_id: "render.webvtt".to_owned(),
        display_name: "Render WebVTT".to_owned(),
        registration_version: "1".to_owned(),
        behavior_version: "1".to_owned(),
        executor_factory: executor_factory::<RenderVttExecutor>(),
        owned_artifacts: vec![vtt.erase(), render.erase(), transcript.erase()],
        input_capabilities: vec![
            ArtifactCapability {
                artifact_id: timed_words().artifact_id().clone(),
            },
            ArtifactCapability {
                artifact_id: music_spans().artifact_id().clone(),
            },
            ArtifactCapability {
                artifact_id: vtt_settings().artifact_id().clone(),
            },
            ArtifactCapability {
                artifact_id: render_settings().artifact_id().clone(),
            },
        ],
        output_capabilities: vec![output.clone()],
        resolve_requirements: Arc::new(move |_context| {
            Ok(ResolvedStageRequirements {
                inputs: vec![
                    required(timed.artifact_id()),
                    required(music.artifact_id()),
                    required(vtt.artifact_id()),
                    required(render.artifact_id()),
                ],
            })
        }),
        resolve_output_capabilities: fixed_outputs(vec![output]),
        resolve_resource_policy: unbound_resources(),
        runtime_settings: vec![],
        concurrency_policy: None,
    }
}

#[derive(Clone, Default)]
pub struct WebVttTopologyExampleFeature {
    provider_name: Option<String>,
    api_key: Option<String>,
    concurrency_limit: Option<i64>,
}

impl WebVttTopologyExampleFeature {
    pub fn new(
        provider_name: Option<String>,
        api_key: Option<String>,
        concurrency_limit: Option<i64>,
    ) -> Self {
        Self {
            provider_name,
            api_key,
            concurrency_limit,
        }
    }
}

impl PipelineFeature for WebVttTopologyExampleFeature {
    fn feature_id(&self) -> &str {
        "example.webvtt-topology"
    }
    fn configuration_fingerprint(&self) -> &str {
        "webvtt-topology-example-v1"
    }
    fn install(
        &self,
        builder: &mut dyn StageManifestBuilderAccess,
        service_locator: &mut PipelineRuntimeServicesBuilder,
        env_settings: &mut PipelineRuntimeSettingsBuilder,
    ) -> Result<()> {
        let _ = service_locator;
        if let Some(provider_name) = &self.provider_name {
            env_settings.set(ASR_PROVIDER_NAME_SETTING, provider_name.clone())?;
        }
        if let Some(api_key) = &self.api_key {
            env_settings.set(ASR_API_KEY_SETTING, api_key.clone())?;
        }
        if let Some(concurrency_limit) = self.concurrency_limit {
            env_settings.set(ASR_CONCURRENCY_LIMIT_SETTING, concurrency_limit)?;
        }
        builder.add_artifact_boxed(source_audio().erase())?;
        builder.add_artifact_boxed(audio_settings().erase())?;
        builder.add_artifact_boxed(speech_audio_settings().erase())?;
        builder.add_artifact_boxed(music_audio_settings().erase())?;
        builder.add_stage_boxed(prepare_audio_stage())?;
        builder.add_stage_boxed(build_transcribe_stage())?;
        builder.add_stage_boxed(diarize_stage())?;
        builder.add_stage_boxed(detect_music_stage())?;
        builder.add_stage_boxed(align_stage())?;
        builder.add_stage_boxed(render_vtt_stage())?;
        Ok(())
    }
}

pub type SummaryExampleFeature = WebVttTopologyExampleFeature;

pub fn install_summary_example(builder: &mut StageManifestBuilder) -> Result<()> {
    let feature = WebVttTopologyExampleFeature::default();
    let mut runtime_services = PipelineRuntimeServicesBuilder::new();
    let mut env_settings = PipelineRuntimeSettingsBuilder::new();
    feature.install(builder, &mut runtime_services, &mut env_settings)?;
    builder.record_feature(feature.feature_id(), feature.configuration_fingerprint())?;
    Ok(())
}
