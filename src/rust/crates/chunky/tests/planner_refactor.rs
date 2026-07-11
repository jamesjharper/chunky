mod support;

use anyhow::{anyhow, Result};
use chunky_example::artifact_handles::ArtifactSourceHandle;
use chunky_example::artifact_models::{
    ArtifactDeclaration, ArtifactRequirement, ArtifactRole, DefaultValueSourceProvider,
    PhaseMatchMode,
};
use pretty_assertions::assert_eq;
use serde_json::json;
use std::collections::BTreeMap;
use std::sync::Arc;
use support::*;

#[derive(Clone)]
struct FixedAudioSettingsDefaultValueSourceProvider {
    sample_rate_hz: i64,
    channels: i64,
}

#[async_trait::async_trait]
impl DefaultValueSourceProvider<serde_json::Value>
    for FixedAudioSettingsDefaultValueSourceProvider
{
    async fn resolve_source(
        &self,
        supplied_source: Option<Arc<dyn ArtifactSourceHandle>>,
        declaration: &ArtifactDeclaration<serde_json::Value>,
    ) -> Result<Arc<dyn ArtifactSourceHandle>> {
        let mut resolved = serde_json::Map::new();
        resolved.insert("sample_rate_hz".to_owned(), json!(self.sample_rate_hz));
        resolved.insert("channels".to_owned(), json!(self.channels));

        if let Some(source) = supplied_source {
            let value = source.get_value(declaration.erase().codec.as_ref()).await?;
            match value.as_ref().downcast_ref::<serde_json::Value>() {
                Some(serde_json::Value::Object(supplied_values)) => {
                    resolved.extend(supplied_values.clone());
                }
                Some(other) => {
                    return Err(anyhow!("audio settings must be mapping-like, got {other}"))
                }
                None => {
                    return Err(anyhow!(
                        "audio settings source returned the wrong value type"
                    ))
                }
            }
        }

        let source: Arc<dyn ArtifactSourceHandle> =
            MemorySource::new(serde_json::Value::Object(resolved));
        Ok(source)
    }
}

#[tokio::test]
async fn override_domain_can_replace_default_value_source_provider() -> Result<()> {
    let base = ArtifactDeclaration::new(
        legacy_key("audio_preprocessing//audio_settings")?,
        ArtifactRole::Settings,
        1,
        JsonValueCodec,
        true,
    )?
    .with_default_value_source_provider(Arc::new(
        FixedAudioSettingsDefaultValueSourceProvider {
            sample_rate_hz: 44100,
            channels: 1,
        },
    ));

    let inherited = base.with_override_domain("speech_processing")?;
    let inherited_erased = inherited.erase();
    let inherited_source = inherited_erased
        .default_value_source_provider
        .as_ref()
        .expect("inherited provider")
        .resolve_source_any(None, &inherited_erased)
        .await?;
    let inherited_value = inherited_source
        .get_value(inherited.erase().codec.as_ref())
        .await?;
    assert_eq!(
        &json!({"sample_rate_hz": 44100, "channels": 1}),
        inherited_value
            .as_ref()
            .downcast_ref::<serde_json::Value>()
            .unwrap(),
    );

    let overridden = base.with_override_domain_and_default_value_source_provider(
        "music_processing",
        Some(Arc::new(FixedAudioSettingsDefaultValueSourceProvider {
            sample_rate_hz: 8000,
            channels: 1,
        })),
    )?;
    let overridden_erased = overridden.erase();
    let supplied_source: Arc<dyn ArtifactSourceHandle> = MemorySource::new(json!({"channels": 2}));
    let overridden_source = overridden_erased
        .default_value_source_provider
        .as_ref()
        .expect("override provider")
        .resolve_source_any(Some(supplied_source), &overridden_erased)
        .await?;
    let overridden_value = overridden_source
        .get_value(overridden.erase().codec.as_ref())
        .await?;
    assert_eq!(
        &json!({"sample_rate_hz": 8000, "channels": 2}),
        overridden_value
            .as_ref()
            .downcast_ref::<serde_json::Value>()
            .unwrap(),
    );

    Ok(())
}

#[tokio::test]
async fn distinct_pushed_audio_settings_create_distinct_prepare_invocations() -> Result<()> {
    let harness = AudioSettingsHarness::new(false);
    let plan = harness.plan().await?;

    let prepares: Vec<_> = plan
        .invocations
        .iter()
        .filter(|invocation| invocation.registration.stage_id() == "audio.prepare")
        .collect();

    assert_eq!(2, prepares.len());
    let mut sample_rates: Vec<i64> = prepares
        .iter()
        .map(|invocation| {
            invocation
                .resolved_settings
                .require(&harness.base_audio_settings_key)
                .unwrap()["sample_rate_hz"]
                .as_i64()
                .unwrap()
        })
        .collect();
    sample_rates.sort();
    assert_eq!(vec![16000, 48000], sample_rates);
    assert!(prepares.iter().any(|invocation| {
        invocation
            .resolved_settings
            .require(&harness.base_audio_settings_key)
            .unwrap()
            == &json!({"sample_rate_hz": 48000, "channels": 2})
    }));
    Ok(())
}

#[tokio::test]
async fn inherited_edge_settings_override_user_supplied_base_setting() -> Result<()> {
    let harness = AudioSettingsHarness::new(true);
    let plan = harness.plan().await?;

    let prepares: Vec<_> = plan
        .invocations
        .iter()
        .filter(|invocation| invocation.registration.stage_id() == "audio.prepare")
        .collect();

    assert_eq!(2, prepares.len());
    let mut sample_rates: Vec<i64> = prepares
        .iter()
        .map(|invocation| {
            invocation
                .resolved_settings
                .require(&harness.base_audio_settings_key)
                .unwrap()["sample_rate_hz"]
                .as_i64()
                .unwrap()
        })
        .collect();
    sample_rates.sort();
    assert_eq!(vec![16000, 48000], sample_rates);
    Ok(())
}

struct AudioSettingsHarness {
    include_user_base_audio_settings: bool,
    base_audio_settings_key: chunky_example::artifact_models::ArtifactKey,
}

impl AudioSettingsHarness {
    fn new(include_user_base_audio_settings: bool) -> Self {
        Self {
            include_user_base_audio_settings,
            base_audio_settings_key: legacy_key("settings//audio_settings").unwrap(),
        }
    }

    async fn plan(&self) -> Result<chunky_example::task_request_graph::TaskRequestGraph<'static>> {
        let source_audio = decl("source_audio", ArtifactRole::Source);
        let base_audio_settings =
            decl(self.base_audio_settings_key.clone(), ArtifactRole::Settings);
        let speech_audio_settings = decl(
            legacy_key("speech_processing//settings//audio_settings")?,
            ArtifactRole::Settings,
        );
        let music_audio_settings = decl(
            legacy_key("music_processing//settings//audio_settings")?,
            ArtifactRole::Settings,
        );
        let prepared_audio = decl("prepared_audio", ArtifactRole::Intermediate);
        let speech_tokens = decl("speech_tokens", ArtifactRole::Intermediate);
        let music_spans = decl("music_spans", ArtifactRole::Intermediate);
        let webvtt = decl("webvtt", ArtifactRole::Target);

        let prepare = stage_registration(
            "audio.prepare",
            vec![
                source_audio.artifact_id().clone(),
                base_audio_settings.artifact_id().clone(),
            ],
            vec![prepared_audio.artifact_id().clone()],
            {
                let source_audio = source_audio.artifact_id().clone();
                let base_audio_settings = base_audio_settings.artifact_id().clone();
                move |_| {
                    vec![
                        requirement(source_audio.clone()),
                        requirement(base_audio_settings.clone()),
                    ]
                }
            },
        );
        let transcribe = stage_registration(
            "asr.transcribe",
            vec![
                prepared_audio.artifact_id().clone(),
                speech_audio_settings.artifact_id().clone(),
            ],
            vec![speech_tokens.artifact_id().clone()],
            {
                let prepared_audio = prepared_audio.artifact_id().clone();
                let speech_audio_settings = speech_audio_settings.artifact_id().clone();
                move |_| {
                    vec![ArtifactRequirement {
                        artifact_id: prepared_audio.clone(),
                        required: true,
                        phase_match: PhaseMatchMode::AtMost,
                        pushed_settings: vec![speech_audio_settings.clone()],
                    }]
                }
            },
        );
        let detect_music = stage_registration(
            "music.detect",
            vec![
                prepared_audio.artifact_id().clone(),
                music_audio_settings.artifact_id().clone(),
            ],
            vec![music_spans.artifact_id().clone()],
            {
                let prepared_audio = prepared_audio.artifact_id().clone();
                let music_audio_settings = music_audio_settings.artifact_id().clone();
                move |_| {
                    vec![ArtifactRequirement {
                        artifact_id: prepared_audio.clone(),
                        required: true,
                        phase_match: PhaseMatchMode::AtMost,
                        pushed_settings: vec![music_audio_settings.clone()],
                    }]
                }
            },
        );
        let render = stage_registration(
            "render.webvtt",
            vec![
                speech_tokens.artifact_id().clone(),
                music_spans.artifact_id().clone(),
            ],
            vec![webvtt.artifact_id().clone()],
            {
                let speech_tokens = speech_tokens.artifact_id().clone();
                let music_spans = music_spans.artifact_id().clone();
                move |_| {
                    vec![
                        requirement(speech_tokens.clone()),
                        requirement(music_spans.clone()),
                    ]
                }
            },
        );
        let manifest = Box::leak(Box::new(manifest(
            vec![
                source_audio.clone(),
                base_audio_settings.clone(),
                speech_audio_settings.clone(),
                music_audio_settings.clone(),
                prepared_audio,
                speech_tokens,
                music_spans,
                webvtt.clone(),
            ],
            vec![prepare, transcribe, detect_music, render],
            BTreeMap::new(),
        )));

        let mut supplied = BTreeMap::new();
        supplied.insert(
            source_audio.artifact_id().clone(),
            source_instance(&source_audio, json!({"audio": true})),
        );
        supplied.insert(
            speech_audio_settings.artifact_id().clone(),
            source_instance(&speech_audio_settings, json!({"sample_rate_hz": 16000})),
        );
        supplied.insert(
            music_audio_settings.artifact_id().clone(),
            source_instance(
                &music_audio_settings,
                json!({"sample_rate_hz": 48000, "channels": 2}),
            ),
        );
        if self.include_user_base_audio_settings {
            supplied.insert(
                base_audio_settings.artifact_id().clone(),
                source_instance(
                    &base_audio_settings,
                    json!({"sample_rate_hz": 22050, "channels": 1}),
                ),
            );
        } else {
            supplied.insert(
                base_audio_settings.artifact_id().clone(),
                source_instance(
                    &base_audio_settings,
                    json!({"sample_rate_hz": 44100, "channels": 1}),
                ),
            );
        }

        evaluate(
            manifest,
            supplied,
            set([webvtt.artifact_id().clone()]),
            set([
                speech_audio_settings.artifact_id().clone(),
                music_audio_settings.artifact_id().clone(),
                base_audio_settings.artifact_id().clone(),
            ]),
            None,
        )
        .await
    }
}
