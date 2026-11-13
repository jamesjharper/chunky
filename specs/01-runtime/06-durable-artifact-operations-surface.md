# Durable Content Operations — Open Surface

## Status

This specification owns no public lookup endpoint in MVP. Focused and
collection task-output lookup are owned by
[Durable Artifact Download](05-durable-artifact-download-surface.md).

Any future administrative surface must keep these boundaries separate:

- raw content metadata, retention, and reads are keyed only by `ContentId`;
- requested-output observation is `TaskArtifactBinding` keyed by task ID plus
  canonical `ScopedArtifactRequest`;
- requested-output retention is a task-binding concern and does not implicitly
  extend supplied-input protection; and
- task lifecycle operations are keyed by task ID.

The MVP adds no public provenance lookup, exact schema/content interpretation
operation, cache locator, or canonical task-artifact locator. Querying retained
content metadata, explicitly releasing or renewing a caller retention claim,
and administrative deletion remain open surface decisions rather than implied
external contracts.
