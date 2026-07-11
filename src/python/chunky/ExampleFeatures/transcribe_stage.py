from __future__ import annotations

from collections.abc import Callable

from ..Bindings.binding_models import InputArtifacts, OutputArtifacts
from ..Artifacts.artifact_models import ArtifactCapability, ArtifactRequirement
from ..Pipeline.runtime import PipelineRuntimeSettings, RuntimeSettingSpec, RuntimeStageSetting
from ..Stages.stage_models import (
    ResolvedStageRequirements,
    StageExecutionContext,
    StagePlanningContext,
    StageRegistration,
)
from .common import (
    ASR_SETTINGS,
    LANGUAGE_SETTINGS,
    PREPARED_AUDIO,
    SPEECH_AUDIO_SETTINGS,
    TOKEN_TEXT,
    cpu_resources,
    fixed_outputs,
    topology_payload,
)

TRANSCRIBE_STAGE_ID = "asr.transcribe"
ASR_PROVIDER_NAME_SETTING = "example.asr.provider_name"
ASR_API_KEY_SETTING = "example.asr.api_key"
ASR_CONCURRENCY_LIMIT_SETTING = "example.asr.concurrency_limit"
ASR_PROVIDER_NAME_SPEC = RuntimeSettingSpec.string(
    ASR_PROVIDER_NAME_SETTING,
    default="example-asr",
    env_var="EXAMPLE_ASR_PROVIDER_NAME",
)
ASR_API_KEY_SPEC = RuntimeSettingSpec.string(
    ASR_API_KEY_SETTING,
    env_var="EXAMPLE_ASR_API_KEY",
)
ASR_CONCURRENCY_LIMIT_SPEC = RuntimeSettingSpec.integer(
    ASR_CONCURRENCY_LIMIT_SETTING,
    default=4,
    env_var="EXAMPLE_ASR_CONCURRENCY_LIMIT",
)
TRANSCRIBE_RUNTIME_SETTINGS = (
    RuntimeStageSetting(ASR_PROVIDER_NAME_SPEC),
    RuntimeStageSetting(ASR_API_KEY_SPEC, required=True),
    RuntimeStageSetting(ASR_CONCURRENCY_LIMIT_SPEC),
)


class TranscribeExecutor:
    async def execute(
        self,
        inputs: InputArtifacts,
        outputs: OutputArtifacts,
        context: StageExecutionContext,
        runtime_settings: PipelineRuntimeSettings,
    ) -> None:
        prepared = await inputs.get_artifact(PREPARED_AUDIO).read()
        asr = await inputs.get_artifact(ASR_SETTINGS).read()
        language = await inputs.get_artifact(LANGUAGE_SETTINGS).read()
        payload = topology_payload(
            context,
            stage="transcribe",
            extra={
                "prepared_audio_invocation": prepared["invocation_id"],
                "asr": dict(asr),
                "language": dict(language),
                "provider_name": runtime_settings.get_string(ASR_PROVIDER_NAME_SETTING),
                "concurrency_limit": runtime_settings.get_int(ASR_CONCURRENCY_LIMIT_SETTING),
                "api_key_configured": bool(runtime_settings.get_string(ASR_API_KEY_SETTING)),
            },
        )
        context.cancellation.raise_if_cancelled()
        await outputs.get_artifact(TOKEN_TEXT).write(payload)


def transcribe_requirements(
    _context: StagePlanningContext,
) -> ResolvedStageRequirements:
    return ResolvedStageRequirements(
        inputs=(
            ArtifactRequirement(
                PREPARED_AUDIO.artifact_id,
                pushed_settings=(SPEECH_AUDIO_SETTINGS.artifact_id,),
            ),
            ArtifactRequirement(ASR_SETTINGS.artifact_id),
            ArtifactRequirement(LANGUAGE_SETTINGS.artifact_id),
        ),
    )


def build_transcribe_stage(
    *,
    executor_factory: Callable[[], TranscribeExecutor] = TranscribeExecutor,
) -> StageRegistration:
    return StageRegistration(
        stage_id=TRANSCRIBE_STAGE_ID,
        display_name="Transcribe",
        registration_version="1",
        behavior_version="1",
        executor_factory=executor_factory,
        owned_artifacts=(
            ASR_SETTINGS,
            LANGUAGE_SETTINGS,
            TOKEN_TEXT,
        ),
        input_capabilities=(
            ArtifactCapability(PREPARED_AUDIO.artifact_id),
            ArtifactCapability(ASR_SETTINGS.artifact_id),
            ArtifactCapability(LANGUAGE_SETTINGS.artifact_id),
        ),
        output_capabilities=(ArtifactCapability(TOKEN_TEXT.artifact_id),),
        resolve_requirements=transcribe_requirements,
        resolve_output_capabilities=fixed_outputs(
            ArtifactCapability(TOKEN_TEXT.artifact_id),
        ),
        resolve_resource_policy=cpu_resources,
        runtime_settings=TRANSCRIBE_RUNTIME_SETTINGS,
    )


TRANSCRIBE_STAGE = build_transcribe_stage()
