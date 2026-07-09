use anyhow::{bail, Result};
use serde_json::Value;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::str::FromStr;
use std::sync::Arc;

use crate::artifact_binding_models::{InputArtifacts, OutputArtifacts};
use crate::artifact_codecs_example::JsonCodec;
use crate::artifact_handles::ArtifactTargetHandle;
use crate::artifact_handles_examples::AtomicFileTargetHandle;
use crate::artifact_models::{
    ArtifactCapability, ArtifactDeclaration, ArtifactKey, ArtifactRequirement, ArtifactRole,
};
use crate::cache_providers::FileCacheProvider;
use crate::demo_support::{build_inputs, DemoFixture};
use crate::example_features::{
    music_spans, prepared_audio, speaker_segments, timed_words, token_text, webvtt_transcript,
    SummaryExampleFeature,
};
use crate::pipeline::{ChunkyPipeline, RunRequest};
use crate::stage_models::{
    BoxFuture, PipelineFeature, ResolvedStageRequirements, ResourcePolicy, StageExecutionContext,
    StageExecutor, StagePlanningContext, StageRegistration,
};


fn legacy_key(value: &str) -> ArtifactKey {
    let (path, phase) = match value.rsplit_once('#') {
        Some((path, phase)) => (path, Some(phase.parse::<u32>().expect("valid phase"))),
        None => (value, None),
    };
    let parts = path.split("//").collect::<Vec<_>>();
    match parts.as_slice() {
        [domain, content_kind] => ArtifactKey::new(*domain, *content_kind, phase, None)
            .expect("valid benchmark artifact key"),
        [projection, domain, content_kind] => ArtifactKey::new(*domain, *content_kind, phase, None)
            .and_then(|key| key.with_override_domain(*projection))
            .expect("valid benchmark projected artifact key"),
        _ => panic!("invalid legacy artifact key: {value}"),
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BenchmarkScenario {
    FullPipeline,
    PrepareAudio,
    Transcribe,
    Diarize,
    DetectMusic,
    Align,
}

impl BenchmarkScenario {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::FullPipeline => "full_pipeline",
            Self::PrepareAudio => "prepare_audio",
            Self::Transcribe => "transcribe",
            Self::Diarize => "diarize",
            Self::DetectMusic => "detect_music",
            Self::Align => "align",
        }
    }

    pub const fn all() -> &'static [&'static str] {
        &[
            "full_pipeline",
            "prepare_audio",
            "transcribe",
            "diarize",
            "detect_music",
            "align",
        ]
    }
}

impl FromStr for BenchmarkScenario {
    type Err = anyhow::Error;

    fn from_str(value: &str) -> Result<Self> {
        match value {
            "full_pipeline" => Ok(Self::FullPipeline),
            "prepare_audio" => Ok(Self::PrepareAudio),
            "transcribe" => Ok(Self::Transcribe),
            "diarize" => Ok(Self::Diarize),
            "detect_music" => Ok(Self::DetectMusic),
            "align" => Ok(Self::Align),
            _ => bail!("unknown benchmark scenario: {value}"),
        }
    }
}

#[derive(Clone)]
struct SnapshotValueExecutor {
    input: ArtifactDeclaration<Value>,
    output: ArtifactDeclaration<Value>,
}

impl StageExecutor for SnapshotValueExecutor {
    fn execute<'a>(
        &'a mut self,
        inputs: &'a InputArtifacts,
        outputs: &'a OutputArtifacts,
        context: &'a StageExecutionContext,
    ) -> BoxFuture<'a, Result<()>> {
        Box::pin(async move {
            let value = inputs.get_artifact(&self.input)?.read().await?;
            context.cancellation.raise_if_cancelled()?;
            outputs
                .get_artifact(&self.output)?
                .write((*value).clone())
                .await?;
            Ok(())
        })
    }
}

fn json_target(id: &str) -> ArtifactDeclaration<Value> {
    ArtifactDeclaration::new(
        legacy_key(id),
        ArtifactRole::Target,
        1,
        JsonCodec::<Value>::default(),
        false,
    )
    .expect("valid benchmark target declaration")
}

fn fixed_outputs(outputs: Vec<ArtifactCapability>) -> crate::stage_models::StageOutputResolver {
    Arc::new(move |_context: &StagePlanningContext| Ok(outputs.clone()))
}

fn unbound_resources() -> crate::stage_models::StageResourceResolver {
    Arc::new(|_context: &StagePlanningContext| Ok(ResourcePolicy::unbound()))
}

fn snapshot_stage(
    stage_id: &str,
    display_name: &str,
    source: ArtifactDeclaration<Value>,
    target: ArtifactDeclaration<Value>,
) -> StageRegistration {
    let output = ArtifactCapability {
        artifact_id: target.artifact_id().clone(),
    };
    let source_for_requirements = source.clone();
    let source_for_executor = source.clone();
    let target_for_executor = target.clone();
    StageRegistration {
        stage_id: stage_id.to_owned(),
        display_name: display_name.to_owned(),
        registration_version: "1".to_owned(),
        behavior_version: "1".to_owned(),
        executor_factory: Arc::new(move || {
            Box::new(SnapshotValueExecutor {
                input: source_for_executor.clone(),
                output: target_for_executor.clone(),
            })
        }),
        owned_artifacts: vec![target.erase()],
        input_capabilities: vec![ArtifactCapability {
            artifact_id: source.artifact_id().clone(),
        }],
        output_capabilities: vec![output.clone()],
        resolve_requirements: Arc::new(move |_context| {
            Ok(ResolvedStageRequirements {
                inputs: vec![ArtifactRequirement::required(
                    source_for_requirements.artifact_id().clone(),
                )],
            })
        }),
        resolve_output_capabilities: fixed_outputs(vec![output]),
        resolve_resource_policy: unbound_resources(),
        concurrency_policy: None,
    }
}

pub fn prepared_audio_target() -> ArtifactDeclaration<Value> {
    json_target("benchmark//prepared_audio_snapshot")
}

pub fn token_text_target() -> ArtifactDeclaration<Value> {
    json_target("benchmark//token_text_snapshot")
}

pub fn speaker_segments_target() -> ArtifactDeclaration<Value> {
    json_target("benchmark//speaker_segments_snapshot")
}

pub fn music_spans_target() -> ArtifactDeclaration<Value> {
    json_target("benchmark//music_spans_snapshot")
}

pub fn timed_words_target() -> ArtifactDeclaration<Value> {
    json_target("benchmark//timed_words_snapshot")
}

pub fn prepared_audio_snapshot_stage() -> StageRegistration {
    snapshot_stage(
        "benchmark.snapshot.prepare_audio",
        "Snapshot prepared audio",
        prepared_audio(),
        prepared_audio_target(),
    )
}

pub fn token_text_snapshot_stage() -> StageRegistration {
    snapshot_stage(
        "benchmark.snapshot.transcribe",
        "Snapshot token text",
        token_text(),
        token_text_target(),
    )
}

pub fn speaker_segments_snapshot_stage() -> StageRegistration {
    snapshot_stage(
        "benchmark.snapshot.diarize",
        "Snapshot speaker segments",
        speaker_segments(),
        speaker_segments_target(),
    )
}

pub fn music_spans_snapshot_stage() -> StageRegistration {
    snapshot_stage(
        "benchmark.snapshot.detect_music",
        "Snapshot music spans",
        music_spans(),
        music_spans_target(),
    )
}

pub fn timed_words_snapshot_stage() -> StageRegistration {
    snapshot_stage(
        "benchmark.snapshot.align",
        "Snapshot timed words",
        timed_words(),
        timed_words_target(),
    )
}

#[derive(Clone, Default)]
pub struct BenchmarkMaterializationFeature;

impl PipelineFeature for BenchmarkMaterializationFeature {
    fn feature_id(&self) -> &str {
        "example.webvtt-benchmark-materialization"
    }

    fn configuration_fingerprint(&self) -> &str {
        "webvtt-benchmark-materialization-v1"
    }

    fn install(
        &self,
        builder: &mut dyn crate::stage_models::StageManifestBuilderAccess,
    ) -> Result<()> {
        builder.add_artifact_boxed(prepared_audio_target().erase())?;
        builder.add_artifact_boxed(token_text_target().erase())?;
        builder.add_artifact_boxed(speaker_segments_target().erase())?;
        builder.add_artifact_boxed(music_spans_target().erase())?;
        builder.add_artifact_boxed(timed_words_target().erase())?;
        builder.add_stage_boxed(prepared_audio_snapshot_stage())?;
        builder.add_stage_boxed(token_text_snapshot_stage())?;
        builder.add_stage_boxed(speaker_segments_snapshot_stage())?;
        builder.add_stage_boxed(music_spans_snapshot_stage())?;
        builder.add_stage_boxed(timed_words_snapshot_stage())?;
        Ok(())
    }
}

fn scenario_output_key(scenario: BenchmarkScenario) -> ArtifactKey {
    match scenario {
        BenchmarkScenario::FullPipeline => webvtt_transcript().artifact_id().clone(),
        BenchmarkScenario::PrepareAudio => prepared_audio_target().artifact_id().clone(),
        BenchmarkScenario::Transcribe => token_text_target().artifact_id().clone(),
        BenchmarkScenario::Diarize => speaker_segments_target().artifact_id().clone(),
        BenchmarkScenario::DetectMusic => music_spans_target().artifact_id().clone(),
        BenchmarkScenario::Align => timed_words_target().artifact_id().clone(),
    }
}

pub fn build_benchmark_request(
    audio_path: &Path,
    output_path: &Path,
    scenario: BenchmarkScenario,
) -> RunRequest {
    let inputs = build_inputs(audio_path);
    let mut outputs: BTreeMap<_, Arc<dyn ArtifactTargetHandle>> = BTreeMap::new();
    outputs.insert(
        scenario_output_key(scenario),
        AtomicFileTargetHandle::new(output_path),
    );
    RunRequest::new(inputs, outputs)
}

pub async fn create_benchmark_fixture(
    work_dir: impl Into<PathBuf>,
    owner_task_id: &str,
    scenario: BenchmarkScenario,
    clean: bool,
) -> Result<DemoFixture> {
    let work_dir = work_dir.into();
    if clean && work_dir.exists() {
        std::fs::remove_dir_all(&work_dir)?;
    }
    std::fs::create_dir_all(&work_dir)?;

    let extension = if scenario == BenchmarkScenario::FullPipeline {
        ".vtt"
    } else {
        ".json"
    };
    let audio_path = work_dir.join("input-audio.bin");
    let output_path = work_dir.join(format!("output{extension}"));
    let manifest_path = work_dir.join("stage-manifest.json");
    std::fs::write(&audio_path, (0u8..=255).collect::<Vec<_>>().repeat(16))?;

    let cache = FileCacheProvider::with_options(
        work_dir.join("cache"),
        Some(owner_task_id.to_owned()),
        None,
        None,
        None,
        None,
    )?;
    cache.recover().await?;

    let pipeline = match scenario {
        BenchmarkScenario::FullPipeline => {
            let feature = SummaryExampleFeature::default();
            ChunkyPipeline::builder(Arc::new(cache.clone()))
                .install(&feature)?
                .build()?
        }
        _ => {
            let feature = SummaryExampleFeature::default();
            let benchmark_feature = BenchmarkMaterializationFeature;
            ChunkyPipeline::builder(Arc::new(cache.clone()))
                .install(&feature)?
                .install(&benchmark_feature)?
                .build()?
        }
    };

    std::fs::write(
        &manifest_path,
        format!("{}\n", pipeline.stage_manifest.describe_json()?),
    )?;

    Ok(DemoFixture {
        work_dir,
        audio_path,
        output_path,
        manifest_path,
        cache,
        pipeline,
    })
}
