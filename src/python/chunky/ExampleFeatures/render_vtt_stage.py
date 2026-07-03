from __future__ import annotations

from ..Artifacts.binding_models import InputArtifacts, OutputArtifacts
from ..Artifacts.artifact_models import ArtifactCapability, ArtifactRequirement
from ..Stages.stage_models import (
    ResolvedStageRequirements,
    StageExecutionContext,
    StagePlanningContext,
    StageRegistration,
)
from .common import (
    MUSIC_SPANS,
    RENDER_SETTINGS,
    TIMED_WORDS,
    VTT_SETTINGS,
    WEBVTT_TRANSCRIPT,
    fixed_outputs,
    unbound_resources,
)


class RenderVttExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
    ) -> None:
        timed_words = await inputs.get_artifact(TIMED_WORDS).read()
        music_spans = await inputs.get_artifact(MUSIC_SPANS).read()
        vtt = await inputs.get_artifact(VTT_SETTINGS).read()
        render = await inputs.get_artifact(RENDER_SETTINGS).read()
        value = "\n".join(
            [
                "WEBVTT",
                "",
                f"timed_words={timed_words['invocation_id']}",
                f"music_spans={music_spans['invocation_id']}",
                f"vtt_settings={vtt}",
                f"render_settings={render}",
                f"render_invocation={context.invocation_id}",
            ]
        )
        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(WEBVTT_TRANSCRIPT).write(value)


def render_vtt_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(
            ArtifactRequirement(TIMED_WORDS.artifact_id),
            ArtifactRequirement(MUSIC_SPANS.artifact_id),
            ArtifactRequirement(VTT_SETTINGS.artifact_id),
            ArtifactRequirement(RENDER_SETTINGS.artifact_id),
        ),
    )


RENDER_VTT_STAGE = StageRegistration(
    stage_id="render.webvtt",
    display_name="Render WebVTT",
    registration_version="1",
    behavior_version="1",
    executor_factory=RenderVttExecutor,
    owned_artifacts=(
        VTT_SETTINGS,
        RENDER_SETTINGS,
        WEBVTT_TRANSCRIPT,
    ),
    input_capabilities=(
        ArtifactCapability(TIMED_WORDS.artifact_id),
        ArtifactCapability(MUSIC_SPANS.artifact_id),
        ArtifactCapability(VTT_SETTINGS.artifact_id),
        ArtifactCapability(RENDER_SETTINGS.artifact_id),
    ),
    output_capabilities=(ArtifactCapability(WEBVTT_TRANSCRIPT.artifact_id),),
    resolve_requirements=render_vtt_requirements,
    resolve_output_capabilities=fixed_outputs(
        ArtifactCapability(WEBVTT_TRANSCRIPT.artifact_id),
    ),
    resolve_resource_policy=unbound_resources,
)
