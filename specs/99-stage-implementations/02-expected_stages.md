# Candidate Built-In Pipeline Stages
## Artifact Naming
Some intermediate pipeline artifact families opt into concrete numbered IDs for non-terminal processing phases:

```text
{domain}::{content_kind}::{phase}
```

The number is a sparse processing phase, not a schema version. Numbered input requirements are less-than-or-equal selector constraints: `::100` resolves to the highest available phase for the same artifact family less than or equal to `100`.

For phased families, phase-less intermediate requirements request the completed version of an artifact family:

```text
{domain}::{content_kind}
```

Pipeline stages should not need to know whether they are terminal producers for a phased artifact family. Stages that produce phased intermediate artifacts declare concrete phase-numbered outputs. The planner is responsible for detecting when the selected terminal producer satisfies a phase-less requirement and storing that result under the phase-less final artifact ID. The `::final` selector is not used.

Not all intermediate artifacts use phase selectors. For example, `audio::prepared` is an intermediate artifact whose concrete instances are distinguished by constraints and fingerprints rather than by phase numbers.

## Declaration ownership and codecs
Each built-in stage emits the declarations it owns: its unique settings, targets, diagnostics, and any intermediate artifacts it produces. A stage that only consumes an intermediate artifact references the producer-owned declaration through an input requirement.

For phased intermediate families, the stage producing a concrete phase emits the declaration for that phase. Multiple stages may declare the same phased family only when their phase ownership does not overlap. Phase-less completed-family references are resolved by the planner and do not require terminal producers to know they are terminal.

All declarations identify a codec. A codec includes MIME type, preferred extension, and serde behavior. For example, JSON streams use `.json`, CSV-like streams may use `.csv`, WebVTT uses `.vtt`, and binary audio may use `.wav` or `.bin` depending on the concrete codec.


## Output Renderers
### Speech Document
Produces terminal “speech document”, an all-inclusive speech format proprietary to chunky.
Includes the composed token stream, which can include optional non-text tokens such as speaker role changes, frame, and chunk markers.

_Inputs:_
- `transcript::token_stream`
- `render::speech_document_settings`.

_Outputs:_
- `render::speech_document` : Protobuf formatted document containing complete sets of selected data 

_Speech document settings:_
- `include_speaker_role_changes`: Whether speaker role change tokens should be included.
- `include_speaker_embeddings`: Whether speaker embeddings should be included.
- `include_speaker_role_markers`: Whether speaker role markers should be included in the transcript
- `include_frame_transcript_markers`: Whether frame markers should be included in the transcript.

_Default resources:_
- `unbound`

### WebVTT
Produces terminal transcript artifacts such as WebVTT or speaker-labeled WebVTT

_Inputs:_
- `transcript::token_stream`
- `render::webvtt_settings`

_Outputs:_
- `render::webvtt_transcript`

_WebVTT settings:_
- `include_speakers`: Whether speaker labels should be included.
- `speaker_label_format`: Optional format for speaker labels.
- `cue_grouping_strategy`: Optional rule for grouping words or segments into cues.
- `max_cue_duration_ms`: Optional maximum WebVTT cue duration.
- `max_cue_chars`: Optional maximum cue text length.
- `include_word_timing`: Whether word-level timing should be preserved in metadata when the target format supports it.

_Default resources:_
- `unbound`.

## Composition
### Transcript composition
Composes the narrow transcript content artifacts into one render-ready token stream.

_Inputs:_
- `transcript::token_text`,
- `transcript::inverse_normalization_token_edit_stream`if available
- `transcript::speaker_role_change_stream` if available
- `utterance_detections::diarization_stream`if available
- `transcript::token_boundaries`
- `transcript::token_confidence`
- `transcript::composition_settings`

_Outputs:_
- `transcript::token_stream::001`: Render-ready transcript stream keyed by `{token_seq}`, {token_type}, {start}, {end}, {value}

_Optional diagnostic artifacts:_
- `diagnostics::transcript_composition::token_stream_report`

_Stage settings:_
- `enable`: `{true}|{false}` to disable this stage from running.
- `include_primary_speaker_role_changes`: 
- `include_current_speaker_role_changes`: 
- `include_instant_speaker_role_changes`: 
- `include_frame_markers`: Whether frame markers should be included.
- `include_chunk_markers`: Whether chunk markers should be included.

_Default resources:_
- `cpu_compute_bound`

## Alignment Post Processing
### Speaker role change stream
Produces a clean speaker role change stream from diarization or speaker-role output and aligned ASR tokens.

_Inputs:_
- `transcript::token_boundaries`
- `utterance_detections::diarization_stream`
- `speech_detection::diarization_detector_profiles`
- `speaker_role_change::settings`

_Outputs:_
- `transcript::speaker_role_change_stream::001`: Speaker role change tokens keyed by `{speaker_role_change_seq}: {role} | {speaker_id} | {asr_token_range_start}`, where the ASR token range endpoint uses `{frame_seq, chunk_seq, asr_token_seq}`.

_Optional diagnostic artifacts:_
- `diagnostics::speaker_role_change_stream::alignment_report`

_Stage settings:_
- `enable`: `{true}|{false}` to disable this stage from running.
- `assignment_strategy`: Speaker role change assignment strategy, such as `max_overlap`, `midpoint`,
- `min_overlap_ratio`: Minimum ASR-token/speaker overlap required before assigning a speaker role change.
- `unknown_speaker_label`: Label used when no speaker can be assigned.

_Default resources:_
- `cpu_compute_bound`

### Inverse Text Normalisation
Converts normalized transcript text into a preferred spoken or written form before downstream rendering or alignment, when requested.

_Inputs:_
- `transcript::token_text`
- `transcript::inverse_text_normalisation_settings`

_Outputs:_
- `transcript::inverse_normalization_token_edit_stream::001`: Inverse-normalized token edits keyed by `{inverse_normalization_edit_seq}: {asr_token_range_start} | {asr_token_range_end} | {edit_value}`, where ASR token range endpoints use `{frame_seq, chunk_seq, asr_token_seq}`.

_Optional diagnostic artifacts:_
- `diagnostics::inverse_text_normalisation::changes`

_Stage settings:_
- `enable`: `{true}|{false}` to disable this stage from running.

_Inverse text normalisation settings:_
- `normalisation_model`: Optional model or rule-set identifier.
- `language`: Optional language hint.
- `normalise_numbers`: Whether numeric expressions should be converted.
- `normalise_dates`: Whether date expressions should be converted.
- `normalise_symbols`: Whether symbols and abbreviations should be converted.
### Word alignment
Aligns ASR text output back to chunk audio and produces word- or segment-level timing artefacts.

_Inputs:_
- `chunk_plan::chunk_utterance_segments`
- `transcript::token_text::200` // must be pre inverse normalization 
- `transcript::token_boundaries::200`
- `alignment::settings`
- `audio::prepared` satisfying `alignment::audio_settings`.
- `alignment::audio_settings`

_Outputs:_
- `transcript::token_boundaries::201`: Aligned token boundaries keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {start} | {end}`.

_Optional diagnostic artifacts:_
- `diagnostics::alignment::alignment_report`

_Stage settings:_
- `enable`: `{true}|{false}` to disable this stage from running.

_Alignment settings:_
- `alignment_model`: Alignment engine or model identifier.
- `min_word_confidence`: Optional confidence threshold.
- `allow_segment_alignment_fallback`: Whether segment-level alignment may be emitted when word alignment fails.

_Audio settings:_
- `audio_source_selector`: Logical selector for the audio instance used for alignment.
- `sample_rate_hz`: Required sample rate for the alignment engine.
- `channels`: Required channel count for the alignment engine.
- `timing_preserved`: Whether preprocessing must preserve source timing exactly.

_Default resources:_
- `gpu_compute_bound` when using a GPU aligner, otherwise `cpu_compute_bound`.

## Speech-Recognition
### ASR repair
Repairs, normalises, or re-decodes initial transcript output before alignment.

_Inputs:_
- `chunk_plan::chunk_segments`
- `transcript::token_text::100`
- `transcript::token_boundaries::100`
- `transcript::token_confidence::100`
- `asr::repair_settings`.
- `audio::prepared` satisfying `asr::repair_audio_settings`, when repair performs audio-aware re-decoding.
- `asr::repair_audio_settings`

_Outputs:_
- `transcript::token_text::101`: Repaired token text keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {token}`.
- `transcript::token_boundaries::101`: Repaired token boundaries keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {start} | {end}`.
- `transcript::token_confidence::101`: Repaired token confidence keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {confidence}`.

_Optional diagnostic artifacts:_
- `diagnostics::asr_repair::changes`

_Stage settings:_
- `enable`: `{true}|{false}` to disable this stage from running.

_ASR repair settings:_
- `repair_strategy`: Strategy such as text normalization, hallucination removal, punctuation repair, or model-assisted repair.
- `repair_model`: Optional model identifier.

_Audio settings:_
- `audio_source_selector`: Logical selector for the audio instance used for repair.
- `sample_rate_hz`: Required sample rate when repair performs audio-aware re-decoding.
- `channels`: Required channel count when repair performs audio-aware re-decoding.
- `timing_preserved`: Whether preprocessing must preserve source timing exactly.

_Default resources:_
- `gpu_compute_bound` for local-model-assisted repair, otherwise `cpu_compute_bound`.

### ASR
Runs transcription over planned chunks. Frames may be processed in parallel when resources allow. Within a frame, chunk ordering may still matter if previous chunk output is used as context.

_Inputs:_
- `chunk_plan::chunk_segments`
- `asr::settings`.
- `audio::prepared` satisfying `asr::audio_settings`.
- `asr::audio_settings`.

_Outputs:_
- `transcript::token_text::001`: Raw token text keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {token}`.
- `transcript::token_boundaries::001`: Raw token boundaries keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {start} | {end}`.
- `transcript::token_confidence::001`: Raw token confidence keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {confidence}`.

_Optional diagnostic artifacts:_
- `diagnostics::asr::chunk_results`
- `diagnostics::asr::decode_stats`.

_ASR settings:_
- `asr_model`: ASR model identifier.
- `language`: Optional language hint.
- `frame_parallelism`: Maximum frame decode parallelism.
- `context_strategy`: Whether prior chunk text is provided as context.
- `temperature`: Optional decode temperature.
- `beam_size`: Optional beam size.
- `initial_prompt`: Optional prompt text.
- `hot_words`: Optional list of words the model should bias towards.

_Audio settings:_
- `audio_source_selector`: Logical selector for the audio instance used for transcription.
- `sample_rate_hz`: Required sample rate for the ASR engine.
- `channels`: Required channel count for the ASR engine.
- `timing_preserved`: Whether preprocessing must preserve source timing exactly.

_Default resources:_
- `gpu_compute_bound` for local-model-processing, unbound for api-based-processing
- `gpu_memory_bound` for local-model-processing, unbound for api-based-processing

## Speaker Embeddings
### Speech embeddings
Extracts reusable speaker or speech embeddings. Embeddings may be requested as terminal outputs or consumed by diarization, role detection, speaker role change stream generation, or downstream voice-document generation.

_Inputs:_
- `audio::prepared` satisfying `speaker_embedding::audio_settings`.
- `speaker_detection::speaker_role_token_stream`. `{role},{speaker_role_turn_seq_num},{speaker_label},{start},{end},{raw_start},{raw_end}`
- `speaker_embedding::settings`.
- `speaker_embedding::audio_settings`.

_Outputs:_
- `speaker_detection::speaker_embeddings::101` as `{speaker_label} | {version} | {vector}`

_Optional diagnostic artifacts:_
- `diagnostics::speaker_embedding::embedding_stats`.

_Stage settings:_
- `enable`: `{true}|{false}` to disable this stage from running.

_Embedding settings:_
- `embedding_model`: Speaker embedding model identifier.
- `embedding_scope`: `whole_audio`, `speaker`, `speaker_turn`, or `frame`.
- `min_turn_duration_ms`: Minimum speech duration used for an embedding sample.
- `max_samples_per_speaker`: Optional cap on samples per speaker.
- `normalize_embeddings`: Whether output vectors should be normalized.

_Audio settings:_
- `audio_source_selector`: Logical selector for the audio instance used for embeddings.
- `sample_rate_hz`: Required sample rate for the embedding model.
- `channels`: Required channel count for the embedding model.
- `timing_preserved`: Whether preprocessing must preserve source timing exactly.

_Default resources:_
- `gpu_compute_bound` when using a GPU embedding model, otherwise `cpu_compute_bound`.

## Chunk Planning
### Chunk plan builder
Builds ASR chunk plans from the repaired utterance event stream and optional speaker-role streams.

_Inputs:_
- `utterance_detections::utterance_stream`.
- `speaker_detection::speaker_role_token_stream`, when speaker-aware chunk planning is enabled.
- `chunk_plan::settings`.

_Outputs:_
- `chunk_plan::chunk_segments::001`: Planned chunk-level source-audio intervals for ASR keyed by `{frame_seq} + {chunk_seq}: {start} | {end} | {boundary_confidence} | {avg_utterance_confidence}`.
- `chunk_plan::chunk_utterance_segments::001`: Planned utterance-level source-audio intervals within chunks keyed by `{frame_seq} + {chunk_seq} + {utterance_seq}: {start} | {end} | {confidence}` | {token_type}.

_Optional diagnostic artifacts:_
- `diagnostics::chunk_plan::audio_frames`
- `diagnostics::chunk_plan::boundary_report`

_Chunk plan settings:_
- `use_primary_speaker_roles_for_frame_boundaries`: If disabled, speaker roles changes will not trigger a frame boundary
- `use_speech_island_for_frame_boundaries`: If disabled, speech islands will not trigger a frame boundary. 
- `chunk_plan_include_empty_frames`: Whether empty frames should be included.
- `chunk_plan_avoid_interjection_threshold_ms`: Avoid selecting chunk boundaries near interjections.
- `chunk_plan_max_duration_ms`: Maximum allowed chunk-core duration.
- `chunk_plan_allowed_extra_chunks`: Number of additional chunks allowed beyond the minimum viable chunk count.

*Note: Disabling both `use_primary_speaker_roles_for_frame_boundaries` and `use_speech_island_for_frame_boundaries` will produce a frame equal to the duration of the recording. If the recording is long, this may produce significant performance issues while building the chuck plan, and will result in the ASR process running chucks synchronously.*

_Default resources:_
- `cpu_compute_bound`

### Utterance stream repair
Detects overlong utterance events and attempts to split them using energy dips, speaker changes, and fallback cuts. This stage can be a no-op when the initial utterance stream contains no repairable overlong events.

_Inputs:_
- `utterance_detections::utterance_stream::200`.
- `audio::prepared` satisfying `utterance_detections::utterance_stream_repair_audio_settings`, when energy-dip or acoustic repair is enabled.
- `utterance_detections::diarization_stream` - if available 
- `utterance_detections::utterance_stream_repair_settings`.
- `utterance_detections::utterance_stream_repair_audio_settings`, when energy-dip or acoustic repair is enabled.

_Outputs:_
- `utterance_detections::utterance_stream::201`.

_Optional diagnostic artifacts:_
- `diagnostics::utterance_stream_repair::split_report`.

_Stage settings:_
- `enable`: `{true}|{false}` to disable this stage from running.

_Repair settings:_
- `detect_long_utterance_threshold_ms`: Duration above which an event is considered overlong.
- `split_strategy`: `energy_dip`, `speaker_change`, `fallback_cut`, or ordered combinations.
- `min_split_gap_ms`: Minimum gap allowed between split points.
- `fallback_cut_max_duration_ms`: Maximum event duration when fallback cutting is allowed.

_Audio settings:_
- `audio_source_selector`: Logical selector for the audio instance used for acoustic split detection.
- `sample_rate_hz`: Required sample rate for acoustic split detection.
- `channels`: Required channel count for acoustic split detection.
- `timing_preserved`: Whether preprocessing must preserve source timing exactly.

_Default resources:_
- `cpu_compute_bound`.

### Utterance event stream
Combines raw VAD and diarization signals into a single ordered, non-overlapping utterance event stream. 

_Inputs:_
- `utterance_detections::vad_stream` 
- `utterance_detections::diarization_stream` 
- `speech_detection::diarization_detector_profiles`
- `speech_detection::vad_detector_profiles`
- `utterance_detections::stream_merge_settings`.

_Outputs:_
- `utterance_detections::utterance_stream::001`.

_Optional diagnostic artifacts:_
- `diagnostics::utterance_event_stream::merge_report`.

_utterance detections stream merge settings:_
- `treat_speaker_turns_as_utterances`: If disabled, speaker turns will not generate_utterances, disable this only if speaker turn quality is causing non audio intervals being included in the chunk plan, otherwise disabling can reduce recall on poor quality audio tracks.
- `detect_utterance_pause_threshold_ms`: Pause threshold for utterance-stream breaks.
- `detect_long_utterance_threshold_ms`: Threshold for flagging likely multi-utterance events.
- `detect_suppress_phantom_utterances_threshold_ms`: Threshold for suppressing isolated phantom events.
- `detect_boundary_extension_merge_threshold_ms`: Threshold for merging meaningful uncovered boundary extensions.

_Shared detector profile settings:_
`speech_detection::vad_detector_profiles` and `speech_detection::diarization_detector_profiles` are separate settings artifacts. Each artifact contains a list of detector profile objects, not one profile and not one combined VAD/diarization list. The detector kind is implied by the owning settings artifact. The shared fields on each profile object are:
- `detector_id`.
- `confidence`.
- `speech_onset_pad_duration_ms`.
- `speech_offset_pad_duration_ms`.
- `min_speech_duration_ms`.

_Default resources:_
- `cpu_compute_bound`.

## Speaker Detection
### Speaker role detection
Detects speaker roles or speaker classes used by downstream planning and embedding stages. The current implementation maps raw diarization events into canonical turns, diarization frames, and primary/current/instant/interjection speaker streams.

_Inputs:_
- `utterance_detections::diarization_stream`.
- `speech_detection::diarization_detector_profiles`.
- `speaker_detection::role_detection_settings`.

_Outputs:_
- `speaker_detection::speaker_role_token_stream::001`. `{role},{speaker_role_turn_seq_num},{speaker_label},{start},{end},{raw_start},{raw_end}`

_Optional diagnostic artifacts:_
- `diagnostics::speaker_role_detection::diarization_frames`
- `diagnostics::speaker_role_detection::role_streams`

_Stage settings:_
- `enable`: `{true}|{false}` to disable this stage from running.

_Role detection settings:_
- `speaker_role_detect_speaker_timeout_duration_ms`: Gap after which a speaker frame closes.
- `speaker_role_detect_primary_speaker_window_duration_ms`: Window used to infer the primary speaker.
- `speaker_role_detect_current_speaker_window_duration_ms`: Window used to infer the current speaker.
- `speaker_role_detect_primary_speaker_hysteresis_ms`: Hysteresis for primary speaker changes.
- `speaker_role_detect_primary_speaker_island_max_duration_ms`: Max primary-speaker island duration.
- `speaker_role_detect_current_speaker_hysteresis_ms`: Hysteresis for current speaker changes.

_Shared detector profile settings:_
`speech_detection::diarization_detector_profiles` contains a list of diarization profile objects. The speaker role detector borrows this shared profile collection instead of defining its own copy of the schema. The shared fields on each profile object are:
- `detector_id`.
- `confidence`.
- `speech_onset_pad_duration_ms`.
- `speech_offset_pad_duration_ms`.
- `min_speech_duration_ms`.

_Default resources:_
- `cpu_compute_bound`.

### Diarized speech detection
Produces speaker-aware speech detection or diarization artifacts when speaker-labeled outputs, speaker-role planning, speaker role change streams, or speaker embeddings require them.

_Inputs:_
- `audio::prepared` satisfying `speaker_detection::diarization_audio_settings`.
- `speaker_detection::diarization_settings`.
- `speaker_detection::diarization_pynnote_settings` - needed only if `diarization_settings.diarization_implementation == "pynnote"`
- `speaker_detection::diarization_audio_settings`.
- `speech_detection::diarization_detector_profiles`

_Outputs:_
- `utterance_detections::diarization_stream::001` as `{detector_id} | {confidence} | {raw_start} | {raw_end} | {speaker_label}`
- `speaker_detection::speaker_embeddings::001` as `{speaker_label} | {version} | {vector}`

_Optional diagnostic artifacts:_
- `diagnostics::diarization::raw_segments`.

_Stage settings:_
- `enable`: `{true}|{false}` to disable this stage from running.

_Diarization settings:_
- `diarization_implementation`: Diarization implementation identifier. In initial version only `pynnote` is supported

_Pynnote settings:_
- `detect_min_speakers`: Optional minimum speaker count.
- `detect_max_speakers`: Optional maximum speaker count.
- `diarization_device`: `cpu` or `cuda`.
- `segmentation_batch_size`: Optional segmentation batch size.
- `embedding_batch_size`: Optional embedding batch size.
- `hugging_face_token`: Optional secret token, excluded from semantic fingerprints.

_Audio settings:_
- `audio_source_selector`: Logical selector for the audio instance used by diarization.
- `sample_rate_hz`: Required sample rate for diarization.
- `channels`: Required channel count for diarization.
- `timing_preserved`: Whether preprocessing must preserve source timing exactly.

_Diarization Detector profile settings:_
`speech_detection::diarization_detector_profiles` contains a list of diarization profile objects. Each profile may represent a different diarization model, confidence weighting, or boundary-shaping trade-off. Each profile object contains:
- `detector_id`.
- `detector_model`.
- `confidence`.
- `enabled`.
- `speech_onset_pad_duration_ms`.
- `speech_offset_pad_duration_ms`.
- `min_speech_duration_ms`.

_Default resources:_
- `gpu_compute_bound` when `diarization_device` is `cuda`, otherwise `cpu_compute_bound`.
- `gpu_memory_bound` when `diarization_device` is `cuda`.

### VAD speech detection
Runs one or more VAD profiles and produces raw speech detection events. The current implementation maps this into the combined `RawDetectionEvents` model with VAD and diarization rows.

_Inputs:_
- `audio::prepared` satisfying `speech_detection::vad_audio_settings`.
- `speech_detection::vad_settings`.
- `speech_detection::vad_audio_settings`.
- `speech_detection::vad_detector_profiles`

_Outputs:_
- `utterance_detections::vad_stream::001` as `{detector_id} | {confidence} | {raw_start} | {raw_end}`

_Optional diagnostic artifacts:_
- `diagnostics::vad::profile_events`
- `diagnostics::vad::merged_events`

_VAD settings:_
- `detection_batch_size`: Maximum detector jobs that may run concurrently.

_Audio settings:_
- `audio_source_selector`: Logical selector for the audio instance used by VAD.
- `sample_rate_hz`: Required sample rate for VAD.
- `channels`: Required channel count for VAD.
- `timing_preserved`: Whether preprocessing must preserve source timing exactly.

_VAD Detector profile settings:_
`speech_detection::vad_detector_profiles` contains a list of VAD profile objects. Each profile may represent a different sensitivity or confidence trade-off, and enabled profiles run independently over the selected audio. Each profile object contains:
- `detector_id`.
- `detector_model`.
- `confidence`.
- `enabled`.
- `speech_onset_pad_duration_ms`.
- `speech_offset_pad_duration_ms`.
- `min_speech_duration_ms`.
- `vad_threshold`.
- `vad_neg_threshold`.
- `vad_max_speech_duration_s`.
- `vad_min_silence_at_max_speech_ms`.
- `vad_min_silence_duration_ms`.


_Default resources:_
- `gpu_compute_bound` if the selected VAD model uses GPU, otherwise `cpu_compute_bound`.

## Audio Preparation
### Audio preparation
Converts source audio into concrete audio instances required by downstream stages. May also apply configured normalization, loudness adjustment, compression, limiting, high-pass filtering, noise suppression, or speech enhancement.

Source audio is immutable. This stage produces prepared audio instances that satisfy exact downstream constraints. Multiple concrete instances share the same logical artifact ID, and equivalent requirements should resolve to the same concrete prepared audio fingerprint.

_Inputs:_
- `source::audio`.
- One or more canonical `audio::prepared` logical requirements emitted by downstream stages during planning.

_Outputs:_
- `audio::prepared`: Prepared audio instance satisfying a canonical logical audio requirement. The planner may bind `source::audio` directly instead when the immutable source already satisfies the same requirement.

_Optional diagnostic artifacts:_
- `diagnostics::audio_preparation::metrics`
- `diagnostics::audio_preparation::warnings`

_Prepared audio settings:_
- `profile`: `off`, `diarization_safe`, `minimal`, `balanced`, `strong`, `aggressive`, `asr_repair`, or `dual_default`.
- `target_sample_rate_hz`.
- `target_channels`.
- `bit_depth`.
- `channel_mixdown_strategy`.

_Filter settings:_
- `high_pass_filter_enabled`.
- `high_pass_cutoff_hz`.
- `normalization`.
- `target_loudness_lufs`.
- `true_peak_limit_dbfs`.
- `loudness_range_target_lu`.

_Dynamics settings:_
- `compression`.
- `compressor_threshold_db`.
- `compressor_ratio`.
- `compressor_attack_ms`.
- `compressor_release_ms`.
- `limiter_enabled`.
- `limiter_threshold_dbfs`.
- `limiter_release_ms`.

_Enhancement settings:_
- `noise_suppression`.
- `noise_suppression_engine`.
- `noise_reduction_db`.
- `stationary_noise_bias`.
- `speech_enhancement_enabled`.
- `speech_enhancement_engine`.
- `speech_enhancement_strength`.

_Logical audio constraints:_
- `sample_rate_hz`.
- `channels`.
- `container`.
- `codec`.
- `bit_depth`.
- `timing_preserved`.
- Optional preprocessing profile or transform fingerprint.

_Default resources:_
- `cpu_compute_bound`.

---

## Interview Notes
### 2026-06-05

1. Numeric phase selectors such as `{domain}::{content_kind}::{phase}` apply only to intermediate artifacts. Source, settings, target, and diagnostic artifacts should not support this artifact-family phase selector model.
2. Final intermediate artifacts encode completeness by omitting the phase segment from the artifact ID, for example `transcript::token_text`. Numeric phases remain concrete intermediate processing phases, while the phase-less family ID represents the complete/final artifact for cache lookup and materialization.
3. Bounded numeric requirements such as `transcript::token_text::200` are planning-time selectors, not necessarily exact persisted artifact IDs. The planner resolves them to the highest available concrete numeric phase in the same intermediate artifact family less than or equal to the requested bound.
4. Phase numbers do not use globally meaningful bands across artifact families. Numeric phase values are meaningful only within a single intermediate artifact family and its producer chain.
5. The `::final` selector should be removed from the artifact model. Final references should use the phase-less intermediate artifact family ID, such as `transcript::token_text`, rather than `transcript::token_text::final`.
6. Pipeline stages should not need to know whether they are terminal producers for an artifact family. Stages that produce phased intermediate artifacts should always declare concrete phase-numbered outputs. Consumers that need the completed version should declare a phase-less requirement, and the planner is responsible for detecting the terminal selected producer in the task graph and assigning the correct phase-less final artifact ID for cache lookup and downstream consumption.
7. When a planned terminal producer satisfies a phase-less final intermediate requirement, store only the phase-less final artifact ID. Do not also store or alias the same produced content under the concrete phase-numbered artifact ID.
8. Final intermediate identity is contextual to a phase-less requirement in the planned graph. A non-terminal output should remain stored under its concrete phase-numbered ID even if it is currently the highest known phase in its artifact family.
9. Input declarations that include a phase number express a less-than-or-equal upper bound for that artifact family. For example, an input requirement of `transcript::token_text::200` means "needs the highest available `transcript::token_text` phase less than or equal to 200." Output declarations with phase numbers remain exact concrete phases, such as outputting `transcript::token_text::201`.
10. A phase-less intermediate input requirement means the consumer needs the completed version of that artifact family. This finality is per artifact family, not necessarily finality for the entire pipeline.
11. Source, settings, target, and diagnostic artifacts may use semantic namespaced IDs with arbitrary namespace depth, but they do not receive numeric phase selector semantics. The planner should apply phase semantics based on artifact role/declaration, not solely from the textual number of `::` segments.
12. Existing intermediate input references using `::final` should be rewritten as phase-less family requirements.
13. Terminal remapping from a concrete phase-numbered output to a phase-less final artifact ID happens per output artifact family, not per whole stage invocation. A multi-output stage may have one output stored as phase-less final while another output from the same invocation remains stored under its concrete phase-numbered ID.
14. Direct user requests for a phase-less intermediate artifact family should follow the same terminal remapping rule as downstream phase-less input requirements. The selected terminal producer for that family stores the result only under the phase-less final artifact ID.
15. Concrete numeric intermediate phases may be requested directly when a user or stage needs an exact processing point, but exactness should be represented as a requirement-mode flag in the planner/declaration model rather than by changing artifact ID syntax. By default, phase-numbered input declarations are less-than-or-equal bounds; an exact request would use the same artifact ID with an explicit exact phase match mode.
16. Ordinary stage input declarations default to less-than-or-equal phase matching. Exact phase matching is reserved for cases where a consumer is semantically incompatible with later phases in the same artifact family.
17. Disabled optional stages are excluded from producer selection and phase selector resolution. The planner should resolve phase-less final and bounded numeric requirements over the enabled, valid graph only, without creating placeholder phases for disabled stages.
18. Enabled no-op stages should still produce their declared output phase. A no-op result records that the stage evaluated the inputs under its settings and behavior version, even when the emitted artifact content is unchanged.
19. Phase-less final intermediate artifact fingerprints should still include the actual producing stage invocation, stage behavior version, resolved settings, and input fingerprints. The phase-less artifact ID communicates completed-family identity, while the fingerprint preserves cache correctness and provenance.
20. The run manifest should record the concrete declared output phase that produced a phase-less final intermediate artifact, such as `produced_from_declared_output: transcript::token_text::101`. This is manifest provenance only and should not create an additional cache alias.
21. The section order in this document is descriptive only and should not imply runtime order. Actual execution order is derived from the planned artifact dependency DAG.
22. Stage definitions should depend on artifact requirements, not directly on other stage IDs. Stage IDs are used for provenance, behavior versioning, scheduling metadata, and explicit producer preferences, but ordinary dependencies should remain artifact-based.
23. Every stage's effective settings should be represented as settings artifact inputs, including settings synthesized from defaults. Some stages may explicitly consume shared settings artifacts owned by another stage or subsystem rather than duplicating the same setting schema; VAD and diarization detector profiles are key examples and should be defined once in the pipeline.
24. Shared settings artifacts should have one authoritative declaration owned by the subsystem they conceptually belong to. Other stages may reference that declaration as a borrowed settings input instead of redefining the schema.
25. Borrowed/shared settings artifacts are included in the consuming stage invocation fingerprint like any other input. Settings ownership affects declaration authority, not dependency or cache invalidation behavior.
26. Stages should resolve borrowed/shared settings as required inputs only when the current request and settings actually require them. `possible_inputs` can remain broad for graph construction, while `resolve_required_inputs(context)` should be precise.
27. Open concern: audio preparation needs more design work. The current approach of declaring one prepared output per required/ready input, such as `audio::prepared` or `audio::prepared`, may make audio preparation a special case. The desired direction is that each requiring stage calculates its own audio-related hash from its own audio settings, allowing multiple audio preparation invocations with different settings, without overcomplicating the general artifact architecture.
28. Prepared audio should be modeled as a generic intermediate artifact family such as `audio::prepared`, with concrete instances distinguished by constraints, resolved audio settings, source fingerprint, and provider versions rather than by consumer-specific artifact IDs like `audio::prepared` or `audio::prepared`.
29. Stages that need audio should declare a logical requirement for `audio::prepared` plus constraints derived from their own audio settings artifact, rather than directly depending on `audio::source` matching those settings. The planner can then bind the immutable source audio directly when it satisfies the constraints or insert audio preparation when it does not.
30. When `source::audio` already satisfies an `audio::prepared` logical requirement, the planner should bind the source directly rather than creating a trivial cached `audio::prepared` artifact. The graph and manifest should still record the resolved availability edge so dependent stages wait for the bound audio resource to be available before running.
31. Identical canonical `audio::prepared` requirements should share one audio preparation invocation or one direct source binding. The planner should canonicalize logical audio constraints and resolved audio settings so multiple consumers with the same requirement depend on the same prepared audio node.
32. The `audio::prepared` fingerprint should include only audio-preparation-relevant inputs: source audio fingerprint, canonical audio constraints, resolved audio transform settings, audio preparation implementation version, and provider versions. Full consuming-stage settings remain part of the consuming stage fingerprint, but unrelated settings such as ASR beam size should not change the prepared audio cache key.
33. Audio-consuming stages should keep their own audio settings artifacts, such as `asr::audio_settings` or `alignment::audio_settings`, when those settings are semantically part of the stage's behavior. The planner extracts and canonicalizes the audio-preparation-relevant subset from those settings to resolve generic `audio::prepared` requirements.
34. Main stage input lists should express audio-consuming dependencies as `audio::prepared` satisfying the stage-owned audio settings artifact, leaving direct source binding versus inserted audio preparation to the planner.
35. `audio::prepared` is an intermediate artifact but should not use the numeric phase/final-family model. Phase selector semantics apply only to intermediate artifact families that explicitly opt into phase numbering; `audio::prepared` multiplicity is based on concrete constraints and fingerprints instead.
36. The Artifact Naming section should describe phased IDs as an opt-in pattern for applicable intermediate artifact families, not as a blanket rule for every intermediate artifact.
37. Phased artifact families should explicitly declare their phase behavior in artifact metadata, such as `phase_mode: numbered` or equivalent. The planner should use declarations, not textual ID parsing alone, to decide whether a numeric suffix is a phase bound.
38. Phase-less final behavior applies only to declared phased intermediate families. For non-phased intermediates such as `audio::prepared`, a phase-less ID is just the normal artifact ID, not a completed-family selector.
39. Speaker embedding artifacts should use a neutral shared family such as `speaker_detection::speaker_embeddings`, because embeddings may be produced or consumed outside diarization-specific workflows.
40. Avoid a separate opportunistic-output model. A stage advertises the set of outputs it can create, and for each planned invocation the upstream graph requirements determine which of those outputs the stage is asked to produce.
41. A stage should attempt to produce every advertised output that the planned graph explicitly asks it to produce. Advertised outputs that are not requested by the graph should not be produced just because the stage could create them as incidental outputs.
42. Speaker embeddings should follow normal phased producer selection. If the completed `speaker_detection::speaker_embeddings` family is requested and Speech embeddings is enabled and selected as the terminal producer, the graph should ask Speech embeddings to provide the phase-less final artifact. If Speech embeddings is disabled or unavailable, the planner may resolve the request to an earlier enabled producer such as Diarized speech detection producing `speaker_detection::speaker_embeddings::001`.
43. A specific stage invocation should be planned for an explicit subset of the stage's advertised outputs. The stage must produce the requested output subset for the invocation, but may optionally emit additional unused artifacts as incidental outputs. Those incidental outputs may be recorded, but graph correctness must not depend on them unless they were part of the planned requested output subset.
44. Incidental output artifacts should be eligible for cache storage only when they are declared in the stage's advertised outputs and pass artifact validation. Undeclared files or scratch data may exist internally but should not become cacheable semantic artifacts.
45. The manifest does not need to distinguish planned/requested outputs from incidental outputs. Graph correctness is determined by the planned invocation output subset; incidental outputs can be recorded as normal produced artifacts once validated.
46. Cache-hit skipping for a stage invocation should consider only the planned requested output subset for that invocation. Missing incidental outputs should not force a rerun, and present incidental outputs should not affect skip behavior unless requested by the current graph.
47. Use the term "incidental outputs" for declared artifacts a stage emits during execution that were not part of the planned requested output subset.
48. Diagnostics and logs should be modeled as ordinary declared artifacts with diagnostic role and role-specific retention/materialization policy, not as a separate planning mechanism. If requested, they are part of the planned output subset; if emitted unrequested, they are incidental outputs.
49. Render outputs such as `render::webvtt_transcript` and `render::speech_document` should be target artifacts. They are terminal semantic outputs that may be materialized to files, memory, streams, or sinks separately from cache identity, and they should not use phase semantics.
50. Renderer settings such as `render::webvtt_settings` and `render::speech_document_settings` should remain normal settings artifacts, not embedded inside target artifact declarations.
51. During task graph construction, each stage should have an opportunity to resolve concrete input requirements and output capabilities from context. Context must include the stage's resolved settings artifacts and may include selected aspects of the already built graph if that can be done deterministically and without making planning messy.
52. Transcript composition should use dynamic requirement resolution: enrichment streams such as speaker role changes, diarization, or inverse normalization should be required only when `transcript::composition_settings` requests composition behavior that needs them.
53. Dynamic output capability resolution may narrow a stage's broad advertised output set for the current context. This resolution must be deterministic and side-effect free.
54. Any graph context exposed to stage requirement or capability resolution should be read-only.
55. Dynamic requirement and output capability resolution must not depend on cache state. Cache availability may affect execution skipping, but not graph validity or graph shape.
56. A caller-supplied phase-less intermediate artifact should satisfy the completed-family requirement directly and prevent the planner from producing that artifact family by default. The supplied artifact is validated, fingerprinted as a supplied input, and used as a graph root.
57. A caller-supplied concrete phased intermediate artifact should be eligible to satisfy bounded numeric input requirements for that artifact family. It should not automatically satisfy phase-less completed-family requirements unless supplied as the phase-less artifact ID or explicitly requested as the final substitute.
58. Supplied artifacts should win over producer paths by default, but an explicit recompute/force-producer request may override this behavior and cause the planner to build a producer path instead.
59. Unused supplied artifacts or settings should produce warnings after graph planning.
60. Use `utterance_detections::utterance_stream` and "utterance event stream" terminology for pre-ASR VAD/diarization-derived events. Reserve "word" or "token" terminology for ASR transcript outputs and alignment where actual transcript words/tokens are involved.
61. Chunk plan interval artifacts should be named by granularity: `chunk_plan::chunk_segments` for chunk-level source-audio intervals and `chunk_plan::chunk_utterance_segments` for utterance-level source-audio intervals within chunks.
62. Base ASR should consume `chunk_plan::chunk_segments`. Word alignment should consume `chunk_plan::chunk_utterance_segments`. ASR implementations that need utterance metadata may dynamically require `chunk_plan::chunk_utterance_segments`, but it should not be part of the minimal ASR contract.
63. Token identity needs two layers. Each contributing token family/type should have stable IDs within its own kind so artifacts can make statements about other token kinds, such as a normalization token covering ASR token coordinates A through B. The final composed token stream also needs a stable unified token sequence; transcript composition may translate kind-local token IDs into final token sequence numbers.
64. ASR token identity should remain the meaningful compound coordinate `{frame_seq} + {chunk_seq} + {asr_token_seq}` rather than an opaque `asr_token_id`. Rename generic `token_seq` references in ASR-derived artifacts to `asr_token_seq`. This allows repair to replace a frame without resequencing the whole stream and supports per-frame word alignment.
65. Token families such as inverse normalization, speaker role change streams, and speaker roles should be frame/chunk agnostic where possible, while still being able to reference ASR token coordinate ranges when needed.
66. Inverse normalization edit streams should produce their own simple incremental sequence of edit tokens, reference the ASR token range they edit, and include the edit value. Their own sequence should not be frame/chunk scoped.
67. The clean speaker stream should be modeled as speaker role change tokens, not as complex overlapping speaker spans. It should emit a token whenever a speaker role changes and identify the role and speaker, such as instant/current/primary speaker changes. Transcript composition can then insert those role-change tokens into the final composed token stream alongside ASR text tokens.
68. Rename `transcript::speaker_role_change_stream` to `transcript::speaker_role_change_stream` because the clean stream is event/change-token based rather than span/turn based.
69. Consolidation note: the open audio-preparation concern in note 27 is resolved by notes 28-38 and the main body now uses generic `audio::prepared` instead of consumer-specific ready artifacts.
70. Consolidation note: the clean speaker stream stage now uses `speaker_role_change::settings`.


71. Stage-owned declarations are the manifest source of truth. A stage emits declarations for unique source, settings, target, and diagnostic artifacts it owns, and for intermediate artifacts it produces. Consumers reference declarations through requirements instead of redeclaring consumed intermediates.
72. Codecs include a preferred file extension in addition to MIME type. The extension guides cache file naming and target materialization but does not force stages to require file paths.
