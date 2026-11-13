# Candidate Built-In Runtime Stages

## Artifact Naming and Processing Order

Artifact paths identify semantic artifact families and never encode processing
order. Examples:

```text
transcript/token_text
transcript/token_boundaries
utterance_detections/vad_stream
```

An exact manifest schema adds its immutable version through `ArtifactSchemaId`,
for example `transcript/token_text@1`.

When several stages can produce successive forms of the same artifact family,
their output capabilities carry sparse `processing_order` metadata. A consumer
that must run before some later processing step uses `max_processing_order`; an
unbounded dependency requests the terminal/highest applicable producer.
Processing order is never serialized into `ArtifactSchemaId`,
`ScopedArtifactRequest`, `ContentId`, or task-output identity.

Successive same-family production uses declared semantic binding scopes. For
example, an ASR token binding consumed and republished by punctuation repair may
appear as `punctuation_repair/asr_transcribe/transcript/token_text`. The later
stage publishes a new immutable binding; it never overwrites the earlier
binding or its content.

## Declaration ownership and codecs
Each built-in stage emits the declarations it owns: its unique settings, targets, diagnostics, and any intermediate artifacts it produces. A stage that only consumes an intermediate artifact references the producer-owned declaration through an input requirement.

Several stages may produce the same artifact family at different `processing_order` values. They share the same artifact schema family rather than declaring phase-numbered artifact IDs. The planner resolves the selected producer and exact `ArtifactSchemaId`; terminal producers do not need to know that they are terminal.

All declarations identify a codec. A codec includes MIME type, preferred extension, and serde behavior. For example, JSON streams use `.json`, CSV-like streams may use `.csv`, WebVTT uses `.vtt`, and binary audio may use `.wav` or `.bin` depending on the concrete codec.


## Output Renderers
### Speech Document
Produces terminal “speech document”, an all-inclusive speech format proprietary to chunky.
Includes the composed token stream, which can include optional non-text tokens such as speaker role changes, frame, and chunk markers.

_Inputs:_
- `transcript/token_stream`
- `render/speech_document_settings`.

_Outputs:_
- `render/speech_document` : Protobuf formatted document containing complete sets of selected data 

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
- `transcript/token_stream`
- `render/webvtt_settings`

_Outputs:_
- `render/webvtt_transcript`

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
- `transcript/token_text`,
- `transcript/inverse_normalization_token_edit_stream`if available
- `transcript/speaker_role_change_stream` if available
- `utterance_detections/diarization_stream`if available
- `transcript/token_boundaries`
- `transcript/token_confidence`
- `transcript/composition_settings`

_Outputs:_
- `transcript/token_stream`: Render-ready transcript stream keyed by `{token_seq}`, {token_type}, {start}, {end}, {value} _(processing_order=1)_

_Optional diagnostic artifacts:_
- `diagnostics/transcript_composition_token_stream_report`

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
- `transcript/token_boundaries`
- `utterance_detections/diarization_stream`
- `speech_detection/diarization_detector_profiles`
- `speaker_role_change/settings`

_Outputs:_
- `transcript/speaker_role_change_stream`: Speaker role change tokens keyed by `{speaker_role_change_seq}: {role} | {speaker_id} | {asr_token_range_start}`, where the ASR token range endpoint uses `{frame_seq, chunk_seq, asr_token_seq}`. _(processing_order=1)_

_Optional diagnostic artifacts:_
- `diagnostics/speaker_role_change_stream_alignment_report`

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
- `transcript/token_text`
- `transcript/inverse_text_normalisation_settings`

_Outputs:_
- `transcript/inverse_normalization_token_edit_stream`: Inverse-normalized token edits keyed by `{inverse_normalization_edit_seq}: {asr_token_range_start} | {asr_token_range_end} | {edit_value}`, where ASR token range endpoints use `{frame_seq, chunk_seq, asr_token_seq}`. _(processing_order=1)_

_Optional diagnostic artifacts:_
- `diagnostics/inverse_text_normalisation_changes`

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
- `chunk_plan/chunk_utterance_segments`
- `transcript/token_text` (must be pre inverse normalization)  _(max_processing_order=200)_
- `transcript/token_boundaries` _(max_processing_order=200)_
- `alignment/settings`
- `audio/prepared` satisfying `alignment/audio_settings`.
- `alignment/audio_settings`

_Outputs:_
- `transcript/token_boundaries`: Aligned token boundaries keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {start} | {end}`. _(processing_order=201)_

_Optional diagnostic artifacts:_
- `diagnostics/alignment_alignment_report`

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
- `chunk_plan/chunk_segments`
- `transcript/token_text` _(max_processing_order=100)_
- `transcript/token_boundaries` _(max_processing_order=100)_
- `transcript/token_confidence` _(max_processing_order=100)_
- `asr/repair_settings`.
- `audio/prepared` satisfying `asr/repair_audio_settings`, when repair performs audio-aware re-decoding.
- `asr/repair_audio_settings`

_Outputs:_
- `transcript/token_text`: Repaired token text keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {token}`. _(processing_order=101)_
- `transcript/token_boundaries`: Repaired token boundaries keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {start} | {end}`. _(processing_order=101)_
- `transcript/token_confidence`: Repaired token confidence keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {confidence}`. _(processing_order=101)_

_Optional diagnostic artifacts:_
- `diagnostics/asr_repair_changes`

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
- `chunk_plan/chunk_segments`
- `asr/settings`.
- `audio/prepared` satisfying `asr/audio_settings`.
- `asr/audio_settings`.

_Outputs:_
- `transcript/token_text`: Raw token text keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {token}`. _(processing_order=1)_
- `transcript/token_boundaries`: Raw token boundaries keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {start} | {end}`. _(processing_order=1)_
- `transcript/token_confidence`: Raw token confidence keyed by `{frame_seq} + {chunk_seq} + {asr_token_seq}: {confidence}`. _(processing_order=1)_

_Optional diagnostic artifacts:_
- `diagnostics/asr_chunk_results`
- `diagnostics/asr_decode_stats`.

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
- `audio/prepared` satisfying `speaker_embedding/audio_settings`.
- `speaker_detection/speaker_role_token_stream`. `{role},{speaker_role_turn_seq_num},{speaker_label},{start},{end},{raw_start},{raw_end}`
- `speaker_embedding/settings`.
- `speaker_embedding/audio_settings`.

_Outputs:_
- `speaker_detection/speaker_embeddings` as `{speaker_label} | {version} | {vector}` _(processing_order=101)_

_Optional diagnostic artifacts:_
- `diagnostics/speaker_embedding_embedding_stats`.

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
- `utterance_detections/utterance_stream`.
- `speaker_detection/speaker_role_token_stream`, when speaker-aware chunk planning is enabled.
- `chunk_plan/settings`.

_Outputs:_
- `chunk_plan/chunk_segments`: Planned chunk-level source-audio intervals for ASR keyed by `{frame_seq} + {chunk_seq}: {start} | {end} | {boundary_confidence} | {avg_utterance_confidence}`. _(processing_order=1)_
- `chunk_plan/chunk_utterance_segments`: Planned utterance-level source-audio intervals within chunks keyed by `{frame_seq} + {chunk_seq} + {utterance_seq}: {start} | {end} | {confidence}` | {token_type}. _(processing_order=1)_

_Optional diagnostic artifacts:_
- `diagnostics/chunk_plan_audio_frames`
- `diagnostics/chunk_plan_boundary_report`

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
- `utterance_detections/utterance_stream`. _(max_processing_order=200)_
- `audio/prepared` satisfying `utterance_detections/utterance_stream_repair_audio_settings`, when energy-dip or acoustic repair is enabled.
- `utterance_detections/diarization_stream` - if available 
- `utterance_detections/utterance_stream_repair_settings`.
- `utterance_detections/utterance_stream_repair_audio_settings`, when energy-dip or acoustic repair is enabled.

_Outputs:_
- `utterance_detections/utterance_stream`. _(processing_order=201)_

_Optional diagnostic artifacts:_
- `diagnostics/utterance_stream_repair_split_report`.

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
- `utterance_detections/vad_stream` 
- `utterance_detections/diarization_stream` 
- `speech_detection/diarization_detector_profiles`
- `speech_detection/vad_detector_profiles`
- `utterance_detections/stream_merge_settings`.

_Outputs:_
- `utterance_detections/utterance_stream`. _(processing_order=1)_

_Optional diagnostic artifacts:_
- `diagnostics/utterance_event_stream_merge_report`.

_utterance detections stream merge settings:_
- `treat_speaker_turns_as_utterances`: If disabled, speaker turns will not generate_utterances, disable this only if speaker turn quality is causing non audio intervals being included in the chunk plan, otherwise disabling can reduce recall on poor quality audio tracks.
- `detect_utterance_pause_threshold_ms`: Pause threshold for utterance-stream breaks.
- `detect_long_utterance_threshold_ms`: Threshold for flagging likely multi-utterance events.
- `detect_suppress_phantom_utterances_threshold_ms`: Threshold for suppressing isolated phantom events.
- `detect_boundary_extension_merge_threshold_ms`: Threshold for merging meaningful uncovered boundary extensions.

_Shared detector profile settings:_
`speech_detection/vad_detector_profiles` and `speech_detection/diarization_detector_profiles` are separate settings artifacts. Each artifact contains a list of detector profile objects, not one profile and not one combined VAD/diarization list. The detector kind is implied by the owning settings artifact. The shared fields on each profile object are:
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
- `utterance_detections/diarization_stream`.
- `speech_detection/diarization_detector_profiles`.
- `speaker_detection/role_detection_settings`.

_Outputs:_
- `speaker_detection/speaker_role_token_stream`. `{role},{speaker_role_turn_seq_num},{speaker_label},{start},{end},{raw_start},{raw_end}` _(processing_order=1)_

_Optional diagnostic artifacts:_
- `diagnostics/speaker_role_detection_diarization_frames`
- `diagnostics/speaker_role_detection_role_streams`

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
`speech_detection/diarization_detector_profiles` contains a list of diarization profile objects. The speaker role detector borrows this shared profile collection instead of defining its own copy of the schema. The shared fields on each profile object are:
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
- `audio/prepared` satisfying `speaker_detection/diarization_audio_settings`.
- `speaker_detection/diarization_settings`.
- `speaker_detection/diarization_pynnote_settings` - needed only if `diarization_settings.diarization_implementation == "pynnote"`
- `speaker_detection/diarization_audio_settings`.
- `speech_detection/diarization_detector_profiles`

_Outputs:_
- `utterance_detections/diarization_stream` as `{detector_id} | {confidence} | {raw_start} | {raw_end} | {speaker_label}` _(processing_order=1)_
- `speaker_detection/speaker_embeddings` as `{speaker_label} | {version} | {vector}` _(processing_order=1)_

_Optional diagnostic artifacts:_
- `diagnostics/diarization_raw_segments`.

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
- `hugging_face_token`: Optional secret token, excluded from semantic binding provenance.

_Audio settings:_
- `audio_source_selector`: Logical selector for the audio instance used by diarization.
- `sample_rate_hz`: Required sample rate for diarization.
- `channels`: Required channel count for diarization.
- `timing_preserved`: Whether preprocessing must preserve source timing exactly.

_Diarization Detector profile settings:_
`speech_detection/diarization_detector_profiles` contains a list of diarization profile objects. Each profile may represent a different diarization model, confidence weighting, or boundary-shaping trade-off. Each profile object contains:
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
- `audio/prepared` satisfying `speech_detection/vad_audio_settings`.
- `speech_detection/vad_settings`.
- `speech_detection/vad_audio_settings`.
- `speech_detection/vad_detector_profiles`

_Outputs:_
- `utterance_detections/vad_stream` as `{detector_id} | {confidence} | {raw_start} | {raw_end}` _(processing_order=1)_

_Optional diagnostic artifacts:_
- `diagnostics/vad_profile_events`
- `diagnostics/vad_merged_events`

_VAD settings:_
- `detection_batch_size`: Maximum detector jobs that may run concurrently.

_Audio settings:_
- `audio_source_selector`: Logical selector for the audio instance used by VAD.
- `sample_rate_hz`: Required sample rate for VAD.
- `channels`: Required channel count for VAD.
- `timing_preserved`: Whether preprocessing must preserve source timing exactly.

_VAD Detector profile settings:_
`speech_detection/vad_detector_profiles` contains a list of VAD profile objects. Each profile may represent a different sensitivity or confidence trade-off, and enabled profiles run independently over the selected audio. Each profile object contains:
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

Source audio is immutable. This stage produces prepared audio instances that satisfy exact downstream constraints. Multiple concrete instances share the same logical artifact ID, and equivalent requirements should resolve to the same concrete prepared-audio binding provenance.

_Inputs:_
- `source/audio`.
- One or more canonical `audio/prepared` logical requirements emitted by downstream stages during planning.

_Outputs:_
- `audio/prepared`: Prepared audio instance satisfying a canonical logical audio requirement. The planner may bind `source/audio` directly instead when the immutable source already satisfies the same requirement.

_Optional diagnostic artifacts:_
- `diagnostics/audio_preparation_metrics`
- `diagnostics/audio_preparation_warnings`

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
- Optional preprocessing profile or transform semantic setting.

_Default resources:_
- `cpu_compute_bound`.

---

---

## Artifact Binding Redesign Notes (2026-08-13)

1. Artifact family paths never contain phase/processing-order suffixes.
2. Successive producers of one artifact family declare `processing_order` on their output capabilities.
3. Stage input requirements may use `max_processing_order`; ordinary unbounded requirements select the terminal/highest applicable producer. There is no general exact-processing-order dependency mode.
4. Stage dependencies remain artifact-based. Stable scope segments identify
   binding occurrences; `producer_id` is internal planning/provenance identity,
   not caller request syntax or ordinary inter-stage coupling.
5. Each stage may expose at most one output capability for one artifact schema.
6. Stage inputs and outputs use ordered bindings with `ONE` or `ONE_OR_MORE` cardinality and optionality.
7. A multi-file source such as split recorder WAVs is one ordered `source/audio` binding whose members remain independently valid content objects.
8. Incremental publication is declared per output capability and is independent of cardinality. Published members are final.
9. Content identity is exact-byte `ContentId`; execution reuse is binding-level provenance. Stage-produced outputs do not use predictable content IDs.
10. Settings remain planner-resolved semantic inputs; the final effective settings set contributes separately to binding provenance.
11. The complete resolved output set contributes to stage production provenance. Cache partial hits must not remove hit outputs from the executor invocation.
12. Incidental outputs remain valid only when declared by the stage, but graph correctness depends only on the planned resolved output set.
13. `audio/prepared` remains a generic artifact family; concrete variants are distinguished by production provenance and exact content, not by consumer-specific artifact IDs.
14. Renderer outputs remain target artifacts and do not require special locator semantics.
