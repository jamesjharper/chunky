use anyhow::Result;
use serde_json::json;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::artifact_handles::{ArtifactSourceHandle, ArtifactTargetHandle};
use crate::artifact_handles_examples::{AtomicFileTargetHandle, FileSource, InMemorySource};
use crate::cache_providers::FileCacheProvider;
use crate::example_features::{
    alignment_settings, asr_settings, audio_settings, diarization_settings, language_settings,
    music_audio_settings, render_settings, source_audio, speech_audio_settings,
    vtt_settings, webvtt_transcript, SummaryExampleFeature,
};
use crate::pipeline::{ChunkyPipeline, RunRequest};

pub const DEFAULT_WORK_DIR: &str = "/tmp/chunky-complete-example-rust";

pub struct DemoFixture {
    pub work_dir: PathBuf,
    pub audio_path: PathBuf,
    pub output_path: PathBuf,
    pub manifest_path: PathBuf,
    pub cache: FileCacheProvider,
    pub pipeline: ChunkyPipeline,
}

pub fn build_inputs(audio_path: &Path) -> BTreeMap<crate::artifact_models::ArtifactKey, Arc<dyn ArtifactSourceHandle>> {
    let mut inputs: BTreeMap<_, Arc<dyn ArtifactSourceHandle>> = BTreeMap::new();
    inputs.insert(source_audio().artifact_id().clone(), FileSource::new(audio_path));
    inputs.insert(
        audio_settings().artifact_id().clone(),
        InMemorySource::new(
            "demo-settings",
            json!({"sample_rate_hz": 44100, "channels": 1}),
        ),
    );
    inputs.insert(
        speech_audio_settings().artifact_id().clone(),
        InMemorySource::new("demo-settings", json!({"sample_rate_hz": 16000})),
    );
    inputs.insert(
        music_audio_settings().artifact_id().clone(),
        InMemorySource::new(
            "demo-settings",
            json!({"sample_rate_hz": 48000, "channels": 2}),
        ),
    );
    inputs.insert(
        asr_settings().artifact_id().clone(),
        InMemorySource::new("demo-settings", json!({"model": "placeholder-asr"})),
    );
    inputs.insert(
        language_settings().artifact_id().clone(),
        InMemorySource::new("demo-settings", json!({"language": "en"})),
    );
    inputs.insert(
        diarization_settings().artifact_id().clone(),
        InMemorySource::new(
            "demo-settings",
            json!({"model": "placeholder-diarizer"}),
        ),
    );
    inputs.insert(
        alignment_settings().artifact_id().clone(),
        InMemorySource::new("demo-settings", json!({"mode": "word"})),
    );
    inputs.insert(
        vtt_settings().artifact_id().clone(),
        InMemorySource::new("demo-settings", json!({"include_speakers": true})),
    );
    inputs.insert(
        render_settings().artifact_id().clone(),
        InMemorySource::new("demo-settings", json!({"style": "caption-rich"})),
    );
    inputs
}

pub fn build_request(audio_path: &Path, output_path: &Path) -> RunRequest {
    let inputs = build_inputs(audio_path);
    let mut outputs: BTreeMap<_, Arc<dyn ArtifactTargetHandle>> = BTreeMap::new();
    outputs.insert(
        webvtt_transcript().artifact_id().clone(),
        AtomicFileTargetHandle::new(output_path),
    );

    RunRequest::new(inputs, outputs)
}

pub async fn create_demo_fixture(
    work_dir: impl Into<PathBuf>,
    owner_task_id: &str,
    clean: bool,
) -> Result<DemoFixture> {
    let work_dir = work_dir.into();
    if clean && work_dir.exists() {
        std::fs::remove_dir_all(&work_dir)?;
    }
    std::fs::create_dir_all(&work_dir)?;

    let audio_path = work_dir.join("input-audio.bin");
    let output_path = work_dir.join("output.vtt");
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

    let feature = SummaryExampleFeature::default();
    let pipeline = ChunkyPipeline::builder(Arc::new(cache.clone()))
        .install(&feature)?
        .build()?;
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
