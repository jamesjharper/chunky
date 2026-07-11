from __future__ import annotations

from ..Bindings.binding_models import InputArtifacts, OutputArtifacts
from ..Artifacts.artifact_models import ArtifactCapability, ArtifactRequirement
from ..Pipeline.runtime import PipelineRuntimeSettings
from ..Stages.stage_models import (
    ResolvedStageRequirements,
    StageExecutionContext,
    StagePlanningContext,
    StageRegistration,
)
from .common import (
    ALIGNMENT_SETTINGS,
    SPEAKER_SEGMENTS,
    TIMED_WORDS,
    TOKEN_TEXT,
    cpu_resources,
    fixed_outputs,
    topology_payload,
)


class AlignExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
        runtime_settings: PipelineRuntimeSettings,
    ) -> None:
        del runtime_settings
        token_text = await inputs.get_artifact(TOKEN_TEXT).read()
        speaker_segments = await inputs.get_artifact(SPEAKER_SEGMENTS).read()
        alignment = await inputs.get_artifact(ALIGNMENT_SETTINGS).read()
        payload = topology_payload(
            context,
            stage="align",
            extra={
                "token_text_invocation": token_text["invocation_id"],
                "speaker_segments_invocation": speaker_segments["invocation_id"],
                "alignment": alignment,
            },
        )
        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(TIMED_WORDS).write(payload)


def align_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(
            ArtifactRequirement(TOKEN_TEXT.artifact_id),
            ArtifactRequirement(SPEAKER_SEGMENTS.artifact_id),
            ArtifactRequirement(ALIGNMENT_SETTINGS.artifact_id),
        ),
    )


ALIGN_STAGE = StageRegistration(
    stage_id="transcript.align",
    display_name="Align",
    registration_version="1",
    behavior_version="1",
    executor_factory=AlignExecutor,
    owned_artifacts=(
        ALIGNMENT_SETTINGS,
        TIMED_WORDS,
    ),
    input_capabilities=(
        ArtifactCapability(TOKEN_TEXT.artifact_id),
        ArtifactCapability(SPEAKER_SEGMENTS.artifact_id),
        ArtifactCapability(ALIGNMENT_SETTINGS.artifact_id),
    ),
    output_capabilities=(ArtifactCapability(TIMED_WORDS.artifact_id),),
    resolve_requirements=align_requirements,
    resolve_output_capabilities=fixed_outputs(
        ArtifactCapability(TIMED_WORDS.artifact_id),
    ),
    resolve_resource_policy=cpu_resources,
)
