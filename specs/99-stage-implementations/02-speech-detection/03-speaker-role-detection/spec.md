# Purpose


This stage builds speaker-role detection streams from diarization output.

The purpose of this stage is to determine which speaker currently holds each conversational role within the audio file. These roles are later used to inform opportune timings for audio frame chunking, especially when choosing whether a chunk boundary is likely to interrupt a stable speaker turn, a speaker transition, or a short interjection.

This stage consumes diarized speech detections and emits separate role streams for:

- **Primary Speaker**: the speaker who has dominated the recent conversation window.
- **Current Speaker**: the speaker who is most active in the immediate short-term window.
- **Instant Speaker(s)**: the raw speaker or speakers currently detected as speaking by diarization.
- **Interjection Speaker(s)**: instant speakers who are not currently the primary or current speaker.

# 4.1 Speaker Role Detections
## Inputs
This stage requires diarized speech detections. Each input event should contain:

- __`detector_id:`__ The ID of the profile used to make this detection.
- __`detector_type:`__ `diarized`.
- __`confidence:`__ The confidence level for the detector profile.
- __`raw_start:`__ The raw start time of the detection.
- __`raw_end:`__ The raw end time of the detection.
- __`speaker_label:`__ The detected raw speaker label.

This stage also requires the following settings:

- __`intermediate_workspace_path:`__ Path to an intermediate workspace folder. If configured, intermediate output artefacts are written to this folder.
- __`speaker_role_detect_speaker_timeout_duration_ms:`__ Duration of silence after which the current diarization frame is closed.
- __`speaker_role_detect_primary_speaker_window_duration_ms:`__ Rolling lookback window used to detect the primary speaker. A sensible default is around `60000ms`.
- __`speaker_role_detect_current_speaker_window_duration_ms:`__ Rolling lookback window used to detect the current speaker. A sensible default is around `3000ms`.

## Speaker Roles
### Primary Speaker
The Primary Speaker is the individual who holds the dominant role in the recent conversation context.

Primary Speaker is determined using a longer rolling window. Within each diarization frame, the primary speaker is the speaker with the greatest amount of spoken time inside the last `speaker_role_detect_primary_speaker_window_duration_ms`.

The effective start time of a primary-speaker region should be the first time that speaker was seen within the relevant role window, not the later moment when they became dominant.

### Current Speaker
The Current Speaker represents the speaker who is most active in the immediate local context.

Current Speaker is determined using a shorter rolling window. Within each diarization frame, the current speaker is the speaker with the greatest amount of spoken time inside the last `speaker_role_detect_current_speaker_window_duration_ms`.

The effective start time of a current-speaker region should be the first time that speaker was seen within the relevant role window, not the later moment when they became dominant.

### Interjection Speaker(s)

Interjection Speaker(s) are instant speakers who are not currently the Primary Speaker or the Current Speaker.
This stream is useful for detecting short interruptions, backchannels, crosstalk, or secondary speakers who briefly overlap the main speaker.
Interjection output may contain overlapping regions.

# 4.2 Split diarization into per-speaker streams
Take all diarized input detections and split them into one ordered stream per `speaker_label`.

Each per-speaker stream should be ordered by `raw_start`, then by `raw_end`.

Example:

```
Input stream      -> |A| |B| |A| |C| |B| |A|
------------------------------------------------
Speaker A stream  -> |A|     |A|         |A|
Speaker B stream  ->     |B|         |B|
Speaker C stream  ->             |C|
```

# 4.2 Merge duplicate diarization streams

If more than one diarization stream exists for the same speaker label, merge those streams using the same general rules as `# 2.1 Merge VAD detections into non-overlapping word-like events`.

The intent is to produce one canonical non-overlapping diarization stream per raw speaker label.
## Streaming rules.

__Base Rule: Same as word-like event merging__
When diarization detections from different diarization profiles overlap for the same speaker label, cherry-pick the highest-confidence detection first.

```
diarization_a  ->  |   SPEAKER_01   |       | SPEAKER_01 |
diarization_b  ->    | SPEAKER_01 |   | SPEAKER_01       |
-------------------------------------------------------------
Output stream  ->  | a:SPEAKER_01 |   | b:SPEAKER_01     |
```

__Rule 2: Capture expanded diarization islands__
If a lower-confidence detection extends beyond the retained higher-confidence detection by more than the selected profile padding, preserve the non-overlapping extension as a separate diarization island.

This mirrors the word-like event stream behaviour: high-confidence detections determine the core region, while lower-confidence detections may still contribute useful boundary extensions.

__Rule 3: Merge only within the same speaker label__
Do not merge events across different `speaker_label` values in this step.
Overlapping speakers are valid diarization output and should remain representable until role detection.

## Streaming output
The result is one canonical diarization stream per `speaker_label`, where each event has:

- __`detector_id:`__ The ID of the profile used to make this detection.
- __`detector_type:`__ `diarized`.
- __`confidence:`__ The confidence level for the detector profile.
- __`raw_start:`__ The raw start time of the detection.
- __`raw_end:`__ The raw end time of the detection.
- __`speaker_label:`__ The detected raw speaker label.

# 4.3 Diarization stream post-processing
Each per-speaker diarization stream should be post-processed to remove noisy detections, apply boundary padding, and merge adjacent or overlapping regions.
## Processing strategy
### 1. Calculate raw duration
```
event_temp[n].raw_duration = diarization[n].raw_end - diarization[n].raw_start
```

### 2. Cull short diarization turns
If a diarization event is shorter than the minimum speech duration for its selected profile, drop it.

```
if event_temp[n].raw_duration < profile[diarization[n].detector_id].min_speech_duration_ms:
    drop diarization[n]
```

This suppresses small diarization fragments that are unlikely to be useful for role detection.
### 3. Apply onset and offset padding
For each retained event, apply the padding configured for the selected detector profile.

```
event_temp[n].start_padded = max(
    diarization[n].raw_start - profile[diarization[n].detector_id].speech_onset_pad_duration_ms,
    0
)

event_temp[n].end_padded = min(
    diarization[n].raw_end + profile[diarization[n].detector_id].speech_offset_pad_duration_ms,
    total_audio_length
)
```

### 4. Merge overlapping or touching regions
After padding, merge regions for the same speaker if they overlap or touch.

```
if current.speaker_label == next.speaker_label and current.end_padded >= next.start_padded:
    merged.start = min(current.start_padded, next.start_padded)
    merged.end = max(current.end_padded, next.end_padded)
    merged.confidence = max(current.confidence, next.confidence)
```

The merged event should retain the same `speaker_label`. If merged regions came from multiple detector profiles, retain enough provenance for diagnostics, such as `source_detector_ids`.

## Streaming output
The result is a post-processed speaker activity stream per `speaker_label`, where each event has:

- __`speaker_turn_seq_num:`__ An incrementing number for addressing each speaker turn.
- __`detector_id:`__ The primary detector ID used for the retained region.
- __`detector_type:`__ `diarized`.
- __`confidence:`__ The confidence level for the retained region.
- __`speaker_label:`__ The raw speaker label.
- __`start:`__ The adjusted start time of the speaker turn.
- __`end:`__ The adjusted end time of the speaker turn.
- __`raw_start:`__ The non-adjusted start time of the speaker turn.
- __`raw_end:`__ The non-adjusted end time of the speaker turn.

# 4.4 Build diarization frames
A diarization frame is a continuous section of audio containing one or more diarization turns.

Each frame starts when any diarized speaker turn begins. The frame remains open while speech continues. If no diarized speaker activity is seen for longer than `speaker_role_detect_speaker_timeout_duration_ms`, the current frame is closed. A new frame is created when the next diarization turn begins.

## Frame construction strategy
### 1. Build a sweep-line event list
After all per-speaker streams have been post-processed, create a global sweep-line event list from all speaker turns.

Each speaker turn contributes two sweep events:
```
turn_start = {
    time: speaker_turn.start,
    event_type: "start",
    speaker_label: speaker_turn.speaker_label
}

turn_end = {
    time: speaker_turn.end,
    event_type: "end",
    speaker_label: speaker_turn.speaker_label
}
```

Sort sweep events by:
1. `time` ascending.
2. `event_type`, with `end` before `start` at the same timestamp.
3. `speaker_label` Ascending for deterministic ordering.
   
### 2. Open and close frames
```
frame_open = false
active_speakers = set()
last_activity_end = null

for event in sweep_events:
    if not frame_open:
        open new frame at event.time
        frame_open = true

    if last_activity_end is not null:
        silence_duration = event.time - last_activity_end
        if active_speakers is empty and silence_duration >= speaker_role_detect_speaker_timeout_duration_ms:
            close current frame at last_activity_end
            open new frame at event.time

    apply event to active_speakers

    if active_speakers is empty:
        last_activity_end = event.time
```

### 3. Store ordered speaker turns in each frame
Each frame should contain an ordered sequence of all speaker turns that overlap the frame.
Speaker turns should be clipped to the frame boundary for frame-local calculations, but the original absolute times should be preserved.

```
frame_turn.start = max(speaker_turn.start, frame.start)
frame_turn.end = min(speaker_turn.end, frame.end)
frame_turn.absolute_start = speaker_turn.start
frame_turn.absolute_end = speaker_turn.end
```

## Frame output
Each diarization frame should contain:

- __`frame_seq_num:`__ An incrementing number for addressing each diarization frame.
- __`start:`__ The frame start time.
- __`end:`__ The frame end time.
- __`duration:`__ The frame duration.
- __`speaker_labels:`__ Ordered list of speaker labels seen within the frame.
- __`speaker_turns:`__ Ordered sequence of all speaker turns that overlap the frame.

# 4.5 Detect speaker roles within each frame
For each diarization frame, detect Primary Speaker, Current Speaker, Instant Speaker(s), and Interjection Speaker(s).

Role detection is calculated independently for each frame. When a frame ends, all rolling windows are reset. A speaker role from one frame should not carry into the next frame.

If a frame ends before a role detection window is full, use only the duration available within that frame.
## Common value calculations
### 1. Spoken duration inside a window.

For a given speaker and a given role window:
```
window_start = max(frame.start, t - window_duration)
window_end = t

speaker_spoken_duration = SUM(
    overlap_duration(turn.start, turn.end, window_start, window_end)
    for turn in frame.speaker_turns
    if turn.speaker_label == speaker_label
)
```

Where:
```
overlap_duration(a_start, a_end, b_start, b_end) = max(0, min(a_end, b_end) - max(a_start, b_start))
```

### 2. First seen time inside a window
For a given speaker and role window, the speaker's effective role start is the first time the speaker was seen inside that window.

```
speaker_first_seen = MIN(
    max(turn.start, window_start)
    for turn in frame.speaker_turns
    if turn.speaker_label == speaker_label
    and overlap_duration(turn.start, turn.end, window_start, window_end) > 0
)
```

This means the emitted role start time is not the moment the speaker became dominant. It is the first time the speaker contributed speech inside the relevant role window.

## Detecting Primary Speaker
Primary Speaker is the speaker with the greatest amount of spoken time inside the last `speaker_role_detect_primary_speaker_window_duration_ms`.

```
primary_window_start = max(
    frame.start,
    t - speaker_role_detect_primary_speaker_window_duration_ms
)

primary_speaker = speaker_with_max_spoken_duration(
    primary_window_start,
    t
)
```

If there is no previous primary speaker in the frame, the first speaker detected in the frame is assigned immediately.

If two speakers have equal spoken duration, resolve the tie deterministically using the following order:

1. Keep the existing Primary Speaker if one of the tied speakers is already primary.
2. Prefer the speaker with the earliest `speaker_first_seen` time in the window.
3. Prefer the speaker with the longest single continuous turn in the window.
4. Prefer lexical order of `speaker_label` as a final deterministic fallback.

## Detecting Current Speaker

Current Speaker is the speaker with the greatest amount of spoken time inside the last `speaker_role_detect_current_speaker_window_duration_ms`.

```
current_window_start = max(
    frame.start,
    t - speaker_role_detect_current_speaker_window_duration_ms
)

current_speaker = speaker_with_max_spoken_duration(
    current_window_start,
    t
)
```

If there is no previous current speaker in the frame, the first speaker detected in the frame is assigned immediately.

If two speakers have equal spoken duration, resolve the tie deterministically using the following order:

1. Keep the existing Current Speaker if one of the tied speakers is already current.
2. Prefer the speaker who is currently active at time `t`.
3. Prefer the speaker with the earliest `speaker_first_seen` time in the window.
4. Prefer the speaker with the longest single continuous turn in the window.
5. Prefer lexical order of `speaker_label` as a final deterministic fallback.

## Detecting Instant Speaker(s)
Instant Speaker(s) are the raw speakers active at time `t` according to the post-processed diarization streams.

```
instant_speakers = {
    turn.speaker_label
    for turn in frame.speaker_turns
    if turn.start <= t and turn.end > t
}
```

Instant Speaker output may include zero, one, or multiple speaker labels.

## Detecting Interjection Speaker(s)
Interjection Speaker(s) are instant speakers who are not the Primary Speaker or the Current Speaker.

```
interjection_speakers = instant_speakers - {primary_speaker, current_speaker}
```

This means that a speaker can only be emitted as an interjection speaker when they are actively detected by diarization at that moment, and they are not already occupying one of the dominant role streams.

# 4.6 Emit speaker-role streams
Emit each role into its own stream.

Primary Speaker and Current Speaker streams should not contain overlapping sections within their own stream. Interjection Speaker streams may contain overlapping sections, because multiple interjection speakers may be active at the same time.

## Stream compaction rules

__Rule 1: Merge adjacent regions with the same role speaker__

If two adjacent role regions have the same `speaker_label`, and there is no gap between them, merge them.

```
if current.role_type == next.role_type
and current.speaker_label == next.speaker_label
and current.end >= next.start:
    current.end = max(current.end, next.end)
```

__Rule 2: Close role regions at frame boundaries__

When a diarization frame ends, all open Primary Speaker and Current Speaker regions should close at the frame end.

The next frame must start role detection from an empty state.

__Rule 3: Do not force interjection stream exclusivity__
Interjection regions are allowed to overlap each other.
This is intentional because two non-primary, non-current speakers may be detected during the same instant.

## Primary Speaker stream output

Each Primary Speaker event should contain:

- __`role_seq_num:`__ An incrementing number for addressing each role event.
- __`role_type:`__ `primary_speaker`.
- __`speaker_label:`__ The raw speaker label assigned to the role.
- __`start:`__ The effective role start time.
- __`end:`__ The effective role end time.
- __`frame_seq_num:`__ The diarization frame that produced this role event.
- __`window_duration_ms:`__ The window duration used to calculate the role.
- __`spoken_duration_ms:`__ Amount of spoken time by this speaker within the role window at the point of role assignment.

## Current Speaker stream output

Each Current Speaker event should contain:

- __`role_seq_num:`__ An incrementing number for addressing each role event.
- __`role_type:`__ `current_speaker`.
- __`speaker_label:`__ The raw speaker label assigned to the role.
- __`start:`__ The effective role start time.
- __`end:`__ The effective role end time.
- __`frame_seq_num:`__ The diarization frame that produced this role event.
- __`window_duration_ms:`__ The window duration used to calculate the role.
- __`spoken_duration_ms:`__ Amount of spoken time by this speaker within the role window at the point of role assignment.

## Instant Speaker stream output

Each Instant Speaker event should contain:

- __`role_seq_num:`__ An incrementing number for addressing each role event.
- __`role_type:`__ `instant_speaker`.
- __`speaker_label:`__ The raw speaker label detected as active.
- __`start:`__ The start time of the instant speaker region.
- __`end:`__ The end time of the instant speaker region.
- __`frame_seq_num:`__ The diarization frame that produced this role event.
- __`source_speaker_turn_seq_num:`__ The speaker turn that produced the instant speaker event.

## Interjection Speaker stream output

Each Interjection Speaker event should contain:

- __`role_seq_num:`__ An incrementing number for addressing each role event.
- __`role_type:`__ `interjection_speaker`.
- __`speaker_label:`__ The raw speaker label detected as an interjection.
- __`start:`__ The start time of the interjection region.
- __`end:`__ The end time of the interjection region.
- __`frame_seq_num:`__ The diarization frame that produced this role event.
- __`primary_speaker_label:`__ The Primary Speaker at the time of the interjection.
- __`current_speaker_label:`__ The Current Speaker at the time of the interjection.
- __`source_speaker_turn_seq_num:`__ The speaker turn that produced the interjection event.

## Intermediate Output
Completing this process should result in separate output documents for the post-processed diarization turns, diarization frames, and emitted speaker-role streams.

If configured, each stage can output its own intermediate file. Intermediate files should be automatically detected and reused provided no profile data or settings have changed between runs.

`diarization_frames.csv`
> The diarization frames produced from the global post-processed speaker activity stream.

`primary_speaker_stream.csv`
> The emitted Primary Speaker role stream. _This stream should not contain overlapping sections._

`current_speaker_stream.csv`
> The emitted Current Speaker role stream. _This stream should not contain overlapping sections._

`instant_speaker_stream.csv`
> The emitted Instant Speaker role stream. _This stream may contain overlapping sections._

`interjection_speaker_stream.csv`
> The emitted Interjection Speaker role stream. _This stream may contain overlapping sections._

`speaker_role_detection_settings.yaml`
> Output of the settings used for this run.
> - __`speaker_role_detect_speaker_timeout_duration_ms:`__ Duration of silence after which the current diarization frame is closed.
> - __`speaker_role_detect_primary_speaker_window_duration_ms:`__ Rolling lookback window used to detect the Primary Speaker.
> - __`speaker_role_detect_current_speaker_window_duration_ms:`__ Rolling lookback window used to detect the Current Speaker.
> - __`profile[n].min_speech_duration_ms:`__ Minimum speech duration required for a diarization turn from profile `n` to be retained.
> - __`profile[n].speech_onset_pad_duration_ms:`__ Padding added to the start of retained diarization turns from profile `n`.
> - __`profile[n].speech_offset_pad_duration_ms:`__ Padding added to the end of retained diarization turns from profile `n`.

---

## Learned Facts To Actualize Later

These facts were learned while consolidating `../02-expected_stages.md` and should be reconciled in this document during its own design pass.

1. Replace references to "word-like events" with "utterance event stream" terminology for pre-ASR VAD/diarization-derived events.
2. Keep this stage focused on deriving speaker-role evidence and role streams from diarization. The clean transcript insertion stream is now `transcript::speaker_role_change_stream` and is produced by a separate speaker role change stream stage after ASR token boundaries are available.
3. The clean `transcript::speaker_role_change_stream` is change-token based, not span/turn based. It emits `{speaker_role_change_seq}: {role} | {speaker_id} | {asr_token_range_start}` and is consumed by transcript composition.
4. Speaker-role detection may still use speaker turns, frames, primary/current/instant/interjection calculations internally and may emit diagnostic role streams.
5. Shared diarization detector profiles should be declared once by the speech detection subsystem and borrowed by this stage as normal fingerprinted settings inputs.
6. If this stage emits intermediate files or diagnostics, those should be declared artifacts or diagnostics; unrequested declared outputs are incidental outputs.
