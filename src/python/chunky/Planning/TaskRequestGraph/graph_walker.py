from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

from ...Artifacts.artifact_models import (
    ArtifactKey,
    ArtifactRequirement,
    ArtifactRole,
)
from ...Stages.stage_manifest import StageProducer
from ...Stages.stage_models import (
    ResolvedSettingsView,
    StagePlanningError,
)
from .binding_models import (
    ArtifactResolution,
    ArtifactRequirementResult,
    MissingOptionalArtifact,
)
from .graph_state_models import GraphBuildState
from .invocation_models import InvocationDraft
from .requirement_models import ArtifactNeed, ProducedArtifact
from .request_models import TaskRequestContext
from .shared_models import ProducerSelection
from .producers import (
    AmbiguousProducerSelection,
    InvalidProducerCandidate,
    ProducerCandidateProbeResult,
    ProducerPathUnavailable,
    ProducerSelector,
    ValidProducerCandidate,
)
from .settings import PlannerSettingsResolver


class StageManifestGraphWalker:
    """Fixed-point expansion for one mutable graph-build state."""

    def __init__(
        self,
        context: TaskRequestContext,
        settings_resolver: PlannerSettingsResolver,
        producer_selector: ProducerSelector,
        work_queue: GraphWorkQueue,
        *,
        initial_state: GraphBuildState | None = None,
    ) -> None:
        self._context = context
        self._settings_resolver = settings_resolver
        self._producer_selector = producer_selector
        self._state = initial_state.clone() if initial_state is not None else GraphBuildState()
        self._work = work_queue
        self._artifact_requirement_resolver = ArtifactRequirementResolver(
            context,
            settings_resolver,
            producer_selector,
            self._state,
        )
        self._invocation_requirement_resolver = InvocationRequirementResolver(
            context,
            settings_resolver,
            self._state,
        )

    def expand(self) -> GraphBuildState:

        # This process if fairly logic heavy, so additional comments are provided for context

        # Using breath first, walk backwards though the "proposed_plan" graph.
        # 1: For each expected output find a producer for that artifact Either its
        # - a: already supplied (base condition)
        # - b: it requires a stage to run
        # 2: For each stage which need to run, find all its required inputs. Add this to expected output list.
        # 3: Iterate until all output requirements are met, or fail trying
        while self._work.has_pending_work:
            while self._work.has_unmet_artifact_requirements:
                work_item = self._work.pop_unmet_artifact_requirement()
                self._resolve_artifact_requirement(work_item)

            while self._work.has_unresolved_invocation and not self._work.has_unmet_artifact_requirements:
                candidate_key = self._work.pop_unresolved_invocation()
                requirements = self._invocation_requirement_resolver.resolve(candidate_key)

                # A stage will almost always have its own stet of required inputs, these need to be added to
                # the unresolved_artifacts list
                self._work.add_requirements_to_plan(requirements)
        return self._state

    def _resolve_artifact_requirement(self, work_item: GraphWorkItem) -> None:
        need = work_item.need
        result = self._artifact_requirement_resolver.resolve(
            need,
            force_required=work_item.force_required,
        )
        if isinstance(result, MissingOptionalArtifact):
            return

        # Emit the resolved requirement as a concrete binding
        if need.is_invocation_input:
            self._state.record_invocation_input_binding(need, result)

        elif need.is_requested_output:
            assert need.requested_output_id is not None
            self._state.record_requested_output_binding(need.requested_output_id, result)

        # Push to work list to process in the next cycle
        if result.is_produced_by_invocation:
            self._work.queue_invocation(result.producer_invocation_key)


class SpeculativeGraphBranch:
    """Clone/expand/commit wrapper used to validate candidate producer paths."""

    def __init__(
        self,
        context: TaskRequestContext,
        settings_resolver: PlannerSettingsResolver,
        producer_selector: ProducerSelector,
        parent_state: GraphBuildState,
        expand_session: Callable[[GraphWorkQueue, GraphBuildState], GraphBuildState],
    ) -> None:
        self._context = context
        self._settings_resolver = settings_resolver
        self._producer_selector = producer_selector
        self._parent_state = parent_state
        self._expand_session = expand_session
        self._state = parent_state.clone()
        self._active = True

    def _ensure_active(self) -> None:
        if not self._active:
            raise StagePlanningError("speculative planning branch has already been closed")

    def _expand_queue(self, work_queue: GraphWorkQueue) -> None:
        self._ensure_active()
        self._state = self._expand_session(work_queue, self._state)

    def commit(self) -> None:
        self._ensure_active()
        self._parent_state.replace_with(self._state)
        self._active = False

    def discard(self) -> None:
        self._ensure_active()
        self._active = False

    def resolved_input_binding(
        self,
        need: ArtifactNeed,
    ) -> ArtifactResolution:
        self._ensure_active()

        binding = self._state.input_binding_for(need)
        if binding is None:
            raise StagePlanningError(
                f"optional probe for {need.requirement.artifact_id} succeeded "
                "without recording a consumer binding"
            )
        return ArtifactResolution.from_input_binding(binding)

    def expand_from_requirement(
        self,
        need: ArtifactNeed,
        *,
        force_required: bool = False,
    ) -> None:
        self._expand_queue(GraphWorkQueue.for_requirement(need, force_required=force_required))

    def expand_from_requirement_producer(
        self,
        need: ArtifactNeed,
        selection: ProducerSelection,
    ) -> ArtifactResolution:
        self._ensure_active()
        producer_context = self._settings_resolver.build_stage_context(
            self._context,
            selection.registration,
            {selection.declared_output_id},
            need.inherited_settings,
        )
        producer_artifact = ProducedArtifact.for_selection(
            producer_context,
            selection,
        )

        existing_producer = self._state.find_invocation_key_by_instance(
            producer_artifact.resolved_instance_id,
        )
        if existing_producer is not None:
            return ArtifactResolution.produced(
                producer_artifact.resolved_artifact_id,
                producer_artifact.resolved_instance_id,
                existing_producer,
            )

        invocation_key = self._state.find_or_create_invocation(
            producer_artifact,
            need.inherited_settings,
        ).key

        return ArtifactResolution.produced(
            producer_artifact.resolved_artifact_id,
            producer_artifact.resolved_instance_id,
            invocation_key,
        )

    def expand_from_candidate(
        self,
        need: ArtifactNeed,
        candidate: StageProducer,
    ) -> ArtifactResolution:
        self._ensure_active()
        selection = self._producer_selector.selection_from_candidate(
            need.requirement.artifact_id,
            candidate,
        )
        resolution = self.expand_from_requirement_producer(
            need,
            selection,
        )
        self.expand_from_resolution(resolution)
        return resolution

    def expand_from_binding(
        self,
        resolution: ArtifactResolution,
    ) -> None:
        """Backward-compatible alias for expanding from a resolved binding.

        Older planner tests and examples used ``expand_from_binding`` for the
        operation now spelled ``expand_from_resolution``.  The behavior is
        identical: if the binding is produced by another invocation, queue that
        invocation for speculative expansion.
        """
        self.expand_from_resolution(resolution)

    def expand_from_resolution(
        self,
        resolution: ArtifactResolution,
    ) -> None:
        self._ensure_active()
        if not resolution.is_produced_by_invocation:
            return

        assert resolution.producer_invocation_key is not None
        self._expand_queue(
            GraphWorkQueue(
                unmet_artifact_requirements=deque(),
                unresolved_stages=deque((resolution.producer_invocation_key,)),
                unresolved_stage_keys={resolution.producer_invocation_key},
            )
        )


@dataclass(frozen=True)
class GraphWorkItem:
    need: ArtifactNeed
    force_required: bool = False


@dataclass
class GraphWorkQueue:
    unmet_artifact_requirements: deque[GraphWorkItem]
    unresolved_stages: deque[str]
    unresolved_stage_keys: set[str]

    @staticmethod
    def from_requested_outputs(
        requested_outputs: tuple[ArtifactKey, ...],
        requirement_factory,
    ) -> "GraphWorkQueue":
        return GraphWorkQueue(
            unmet_artifact_requirements=deque(
                GraphWorkItem(
                    ArtifactNeed(
                        requirement=requirement_factory(output_id),
                        requested_output_id=output_id,
                    )
                )
                for output_id in requested_outputs
            ),
            unresolved_stages=deque(),
            unresolved_stage_keys=set(),
        )

    @staticmethod
    def for_requirement(
        requirement: ArtifactNeed,
        *,
        force_required: bool = False,
    ) -> "GraphWorkQueue":
        return GraphWorkQueue(
            unmet_artifact_requirements=deque((GraphWorkItem(requirement, force_required),)),
            unresolved_stages=deque(),
            unresolved_stage_keys=set(),
        )

    @property
    def has_pending_work(self) -> bool:
        return bool(self.unmet_artifact_requirements or self.unresolved_stages)

    @property
    def has_unmet_artifact_requirements(self) -> bool:
        return bool(self.unmet_artifact_requirements)

    @property
    def has_unresolved_invocation(self) -> bool:
        return bool(self.unresolved_stages)

    def pop_unmet_artifact_requirement(self) -> GraphWorkItem:
        return self.unmet_artifact_requirements.popleft()

    def queue_invocation(self, candidate_key: str | None) -> None:
        if candidate_key is None:
            return
        if candidate_key in self.unresolved_stage_keys:
            return
        self.unresolved_stage_keys.add(candidate_key)
        self.unresolved_stages.append(candidate_key)

    def pop_unresolved_invocation(self) -> str:
        candidate_key = self.unresolved_stages.popleft()
        self.unresolved_stage_keys.remove(candidate_key)
        return candidate_key

    def add_requirements_to_plan(self, requirements: Iterable[ArtifactNeed]) -> None:
        self.unmet_artifact_requirements.extend(
            GraphWorkItem(requirement) for requirement in requirements
        )


class ArtifactRequirementResolver:
    def __init__(
        self,
        context: TaskRequestContext,
        settings_resolver: PlannerSettingsResolver,
        producer_selector: ProducerSelector,
        state: GraphBuildState,
    ) -> None:
        self._context = context
        self._settings_resolver = settings_resolver
        self._producer_selector = producer_selector
        self._state = state

    def _expand_session(
        self,
        work_queue: GraphWorkQueue,
        initial_state: GraphBuildState,
    ) -> GraphBuildState:
        return StageManifestGraphWalker(
            self._context,
            self._settings_resolver,
            self._producer_selector,
            work_queue,
            initial_state=initial_state,
        ).expand()

    def resolve(
        self,
        need: ArtifactNeed,
        *,
        force_required: bool = False,
    ) -> ArtifactRequirementResult:

        # Check if artifact comes from inherited path-specific settings
        setting_resolution = self._resolve_inherited_setting_artifact(need)
        if setting_resolution is not None:
            return setting_resolution

        # Check if the artifact has been supplied to the task
        supplied_resolution = self._resolve_supplied_artifact(need)
        if supplied_resolution is not None:
            return supplied_resolution

        # At this point, there was no pre-existing available artifact,
        # So ether the artifact is missing, or something needs to go "produce" it

        # Check if the artifact is an optional source.
        # As a source there isn't a need to traverse the sub graph as they are terminal nodes
        if self._is_optional_source(need):
            return MissingOptionalArtifact(need.requirement.artifact_id)

        # If the artifact is optional explore the subgraph to see if it has a valid resolved value
        # If this fails then the whole optional path is omitted.
        if not need.is_required and not force_required:
            return self._try_expand_and_resolve_optional_requirement(need)

        # Find a stage which and produce this artifact
        return self._resolve_artifact_producer(need)

    def _resolve_inherited_setting_artifact(
        self,
        need: ArtifactNeed,
    ) -> ArtifactResolution | None:
        # Ignore non setting artifacts
        selector = need.requirement.artifact_id
        declaration = self._context.manifest.artifacts.get(selector)
        if declaration is None or declaration.role is not ArtifactRole.SETTINGS:
            return None

        # Check to see if the requirement contains contains the artifact we are
        # looking for within the inherited_settings object
        inherited_matches = [
            key
            for key, _value in need.inherited_settings
            if key.without_override() == selector.without_override()
        ]

        # No matches found
        if not inherited_matches:
            return None

        # For now, there is no support of queuing settings.
        # I cant think of any rational reason why this would be needed but in theory it could be supported
        if len(inherited_matches) > 1:
            rendered = ", ".join(str(key) for key in sorted(inherited_matches, key=str))
            raise StagePlanningError(f"multiple inherited settings can satisfy {selector}: {rendered}")

        inherited_key = inherited_matches[0]
        if inherited_key not in self._context.supplied_instances:
            raise StagePlanningError(
                f"inherited setting {inherited_key} was selected for {selector} "
                "but has no supplied/default artifact instance"
            )

        return ArtifactResolution.supplied_setting(inherited_key)

    def _resolve_supplied_artifact(
        self,
        need: ArtifactNeed,
    ) -> ArtifactResolution | None:
        requirement = need.requirement
        supplied = self._producer_selector.select_supplied(
            self._context,
            requirement.artifact_id,
            requirement.phase_match,
        )
        if supplied is None:
            return None

        supplied_id, supplied_instance_id = supplied
        return ArtifactResolution.supplied(supplied_id, supplied_instance_id)

    def _resolve_producer_artifact(
        self,
        need: ArtifactNeed,
    ) -> ProducedArtifact:
        selection = self._producer_selector.select_producer(
            need.requirement.artifact_id,
            need.requirement.phase_match,
        )
        producer_context = self._settings_resolver.build_stage_context(
            self._context,
            selection.registration,
            {selection.declared_output_id},
            need.inherited_settings,
        )
        return ProducedArtifact.for_selection(
            producer_context,
            selection,
        )

    def _resolve_artifact_producer(
        self,
        need: ArtifactNeed,
    ) -> ArtifactResolution:

        try:
            producer_artifact = self._resolve_producer_artifact(need)
        except AmbiguousProducerSelection as exc:
            # Try to disambiguate each candidate by culling any options which dont produce a valid graph.
            # If its still ambiguous at the end AmbiguousProducerSelection should be rethrown
            return self._try_expand_and_resolve_ambiguous_producer(need, exc)

        # This exact artifact may have already have a producer resolved. Ie we already know which stage
        # with what settings will produce this exact artifact. In this case there is the need to traverse further
        # as the need has already been meet
        existing_producer = self._state.find_invocation_key_by_instance(
            producer_artifact.resolved_instance_id
        )
        if existing_producer is not None:
            return ArtifactResolution.produced(
                producer_artifact.resolved_artifact_id,
                producer_artifact.resolved_instance_id,
                existing_producer,
            )

        # Create a producer for this artifact
        invocation_key = self._state.find_or_create_invocation(
            producer_artifact,
            need.inherited_settings,
        ).key

        return ArtifactResolution.produced(
            producer_artifact.resolved_artifact_id,
            producer_artifact.resolved_instance_id,
            invocation_key,
        )

    def _try_expand_and_resolve_optional_requirement(
        self,
        need: ArtifactNeed,
    ) -> ArtifactRequirementResult:
        branch = SpeculativeGraphBranch(
            self._context,
            self._settings_resolver,
            self._producer_selector,
            self._state,
            self._expand_session,
        )
        try:
            branch.expand_from_requirement(need, force_required=True)
        except ProducerPathUnavailable:
            branch.discard()
            return MissingOptionalArtifact(need.requirement.artifact_id)

        resolution = branch.resolved_input_binding(need)
        branch.commit()
        return resolution

    def _try_expand_and_resolve_ambiguous_producer(
        self,
        need: ArtifactNeed,
        ambiguity: AmbiguousProducerSelection,
    ) -> ArtifactResolution:
        probe_result = self._probe_producer_candidates(need, ambiguity.candidates)

        if len(probe_result.valid_candidates) == 1:
            winner = probe_result.valid_candidates[0]
            # Updates the plan with speculated path
            winner.branch.commit()
            return winner.binding

        for valid_candidate in probe_result.valid_candidates:
            valid_candidate.branch.discard()

        if not probe_result.valid_candidates:
            invalid_stage_ids = ", ".join(
                sorted(
                    candidate.candidate.stage_id
                    for candidate in probe_result.invalid_candidates
                )
            )
            raise ProducerPathUnavailable(
                f"no valid producer path can satisfy {need.requirement.artifact_id}: "
                f"{invalid_stage_ids}"
            )

        raise ambiguity.with_probe_result(probe_result)

    def _probe_producer_candidates(
        self,
        need: ArtifactNeed,
        candidates: tuple[StageProducer, ...],
    ) -> ProducerCandidateProbeResult:
        valid_candidates: list[ValidProducerCandidate] = []
        invalid_candidates: list[InvalidProducerCandidate] = []

        for candidate in candidates:
            branch = SpeculativeGraphBranch(
                self._context,
                self._settings_resolver,
                self._producer_selector,
                self._state,
                self._expand_session,
            )
            try:
                resolution = branch.expand_from_candidate(need, candidate)
            except ProducerPathUnavailable as error:
                branch.discard()
                invalid_candidates.append(
                    InvalidProducerCandidate(candidate=candidate, error=error)
                )
                continue

            valid_candidates.append(
                ValidProducerCandidate(
                    candidate=candidate,
                    branch=branch,
                    binding=resolution,
                )
            )

        return ProducerCandidateProbeResult(
            valid_candidates=tuple(valid_candidates),
            invalid_candidates=tuple(invalid_candidates),
        )

    def _is_optional_source(
        self,
        need: ArtifactNeed,
    ) -> bool:
        declaration = self._context.manifest.artifacts.get(
            need.requirement.artifact_id
        )
        return (
            declaration is not None
            and declaration.role is ArtifactRole.SOURCE
            and not need.is_required
        )


class InvocationRequirementResolver:
    def __init__(
        self,
        context: TaskRequestContext,
        settings_resolver: PlannerSettingsResolver,
        state: GraphBuildState,
    ) -> None:
        self._context = context
        self._settings_resolver = settings_resolver
        self._state = state

    def resolve(
        self,
        invocation_key: str,
    ) -> tuple[ArtifactNeed, ...]:
        candidate = self._state.find_invocation_by_key(invocation_key)

        # A shared candidate can gain requested outputs after its inputs were
        # first resolved; refresh requirements only when that output set changes.
        if not candidate.has_unresolved_requirements():
            return ()

        declared_outputs = set(candidate.output_bindings)
        context = self._settings_resolver.build_stage_context(
            self._context,
            candidate.registration,
            declared_outputs,
            candidate.inherited_settings,
        )

        # interrogate stage its requirements and capabilities
        stage_output_capabilities = candidate.registration.resolve_validated_output_capabilities(context)
        stage_requirements = candidate.registration.resolve_validated_requirements(context)
        resource_policy = candidate.registration.resolve_validated_resource_policy(context)

        # Check that the current configuration can actually create the outputs which are expected
        # This is nuanced because the stage has a hook to describe what it can produce based on the provided settings.
        # This means it may advertise a output capability via registration.output_capabilities which omitted from
        # resolve_output_capabilities(...)
        self._validate_stage_can_produce_outputs(
            candidate,
            declared_outputs,
            stage_output_capabilities,
        )

        # Ask the stage for its requirements based on the current setting and output context
        pending_requirements = self._input_requirements_for_invocation(
            self._context,
            candidate,
            stage_requirements.inputs,
        )
        candidate.mark_current_requirements_as_resolved()

        candidate.resolved_settings = context.resolved_settings
        candidate.resource_policy = resource_policy

        # The rediscovery process will readd all the input bindings
        # so that have to be cleared at this stage
        candidate.input_bindings.clear()

        return pending_requirements

    def _validate_stage_can_produce_outputs(
        self,
        invocation: InvocationDraft,
        declared_outputs: set[ArtifactKey],
        output_capabilities,
    ) -> None:
        output_capabilities_ids = {capability.artifact_id for capability in output_capabilities}
        missing = declared_outputs - output_capabilities_ids

        if missing:
            rendered = ", ".join(str(item) for item in sorted(missing, key=str))
            raise StagePlanningError(
                f"{invocation.registration.stage_id} cannot expose requested outputs: "
                f"{rendered}"
            )

    def _input_requirements_for_invocation(
        self,
        context: TaskRequestContext,
        invocation: InvocationDraft,
        requirements: tuple[ArtifactRequirement, ...],
    ) -> tuple[ArtifactNeed, ...]:

        inherited_view = ResolvedSettingsView(dict(invocation.inherited_settings))
        settings_used_by_this_stage = invocation.registration.declared_setting_ids()
        pass_through_settings = inherited_view.remove(settings_used_by_this_stage)

        merged_settings = context.request_settings.merge_with_inherited(inherited_view)

        pending_inputs: list[ArtifactNeed] = []
        for requirement in requirements:

            # Figure out what settings should be used while trying to resolve this input
            inherited_settings = self._resolve_inherited_settings_for_input_requirement(
                requirement,
                invocation,
                pass_through_settings,
                merged_settings,
            )
            pending_inputs.append(
                ArtifactNeed(
                    requirement=requirement,
                    inherited_settings=inherited_settings,
                    consumer_invocation_key=invocation.key,
                )
            )
        return tuple(pending_inputs)

    def _resolve_inherited_settings_for_input_requirement(
        self,
        requirement: ArtifactRequirement,
        invocation: InvocationDraft,
        pass_through_settings: ResolvedSettingsView,
        merged_settings: ResolvedSettingsView,
    ) -> tuple[tuple[ArtifactKey, object], ...]:

        if self._is_settings_artifact(requirement.artifact_id):
            # when a stage asks for a setting artifact, the planner may satisfy it directly from
            # inherited overrides instead of planning another producer
            inherited_settings = dict(invocation.inherited_settings)
        else:
            # Propagate all the setting down stream, minus the settings which where consumed by this stage
            inherited_settings = dict(pass_through_settings.items())

        # Resolve the settings pushed by this stage from total merged set
        for pushed in requirement.pushed_settings:
            inherited_settings[pushed] = merged_settings.require(pushed)

        return tuple(sorted(inherited_settings.items(), key=lambda item: str(item[0])))

    def _is_settings_artifact(self, artifact_id: ArtifactKey) -> bool:
        declaration = self._context.manifest.artifacts.get(artifact_id)
        return declaration is not None and declaration.role is ArtifactRole.SETTINGS
