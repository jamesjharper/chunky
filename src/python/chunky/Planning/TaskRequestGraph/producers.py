from __future__ import annotations

"""Producer and root selection for evaluated graph expansion."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...Artifacts.artifact_models import ArtifactKey, PhaseMatchMode
from .request_models import TaskRequestContext
from .shared_models import ProducerSelection, instance_id
from ...Stages.stage_manifest import StageManifest, StageProducer
from ...Stages.stage_models import StagePlanningError

if TYPE_CHECKING:
    from .binding_models import ArtifactResolution
    from .graph_walker import SpeculativeGraphBranch


class ProducerPathUnavailable(StagePlanningError):
    """No producer path exists for the requested artifact requirement."""


@dataclass(frozen=True)
class ValidProducerCandidate:
    candidate: StageProducer
    branch: SpeculativeGraphBranch
    binding: ArtifactResolution


@dataclass(frozen=True)
class InvalidProducerCandidate:
    candidate: StageProducer
    error: ProducerPathUnavailable


@dataclass(frozen=True)
class ProducerCandidateProbeResult:
    valid_candidates: tuple[ValidProducerCandidate, ...] = ()
    invalid_candidates: tuple[InvalidProducerCandidate, ...] = ()

class AmbiguousProducerSelection(StagePlanningError):
    """Multiple producers are equally preferred before path validation."""

    def __init__(
        self,
        message: str,
        *,
        selector: ArtifactKey,
        highest_phase: int,
        candidates: tuple[StageProducer, ...],
        probe_result: ProducerCandidateProbeResult | None = None,
    ) -> None:
        super().__init__(message)
        self.selector = selector
        self.highest_phase = highest_phase
        self.candidates = candidates
        resolved_probe_result = probe_result or ProducerCandidateProbeResult()
        self.valid_candidates = resolved_probe_result.valid_candidates
        self.invalid_candidates = resolved_probe_result.invalid_candidates

    def with_probe_result(
        self,
        probe_result: ProducerCandidateProbeResult,
    ) -> "AmbiguousProducerSelection":
        return AmbiguousProducerSelection(
            str(self),
            selector=self.selector,
            highest_phase=self.highest_phase,
            candidates=self.candidates,
            probe_result=probe_result,
        )


class ProducerSelector:
    def __init__(
        self,
        manifest: StageManifest,
    ) -> None:
        self._manifest = manifest

    def select_supplied(
        self,
        state: TaskRequestContext,
        selector: ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> tuple[ArtifactKey, str] | None:
        selected = self._manifest.phase_catalog.highest_matching_key(
            state.supplied_instances,
            selector,
            phase_match,
        )
        if selected is None:
            return None
        return selected, instance_id(selected, "")

    def select_producer(
        self,
        selector: ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> ProducerSelection:
        ranked, highest_phase = self._ranked_candidates(selector, phase_match)

        default_stage_id = (
            self._manifest.default_producers.get(selector)
            or self._manifest.default_producers.get(ranked[0].declared_output_id)
        )
        if default_stage_id is not None:
            for candidate in ranked:
                if candidate.stage_id == default_stage_id:
                    return self.selection_from_candidate(selector, candidate)

        if len(ranked) > 1:
            ids = ", ".join(sorted(item.stage_id for item in ranked))
            raise AmbiguousProducerSelection(
                f"ambiguous producers for {selector} at phase {highest_phase}: {ids}",
                selector=selector,
                highest_phase=highest_phase,
                candidates=tuple(ranked),
            )
        return self.selection_from_candidate(selector, ranked[0])

    def selection_from_candidate(
        self,
        selector: ArtifactKey,
        candidate: StageProducer,
    ) -> ProducerSelection:
        resolved_output_id = self._manifest.phase_catalog.resolve_output_id(
            selector=selector,
            declared_output_id=candidate.declared_output_id,
        )
        return ProducerSelection(
            registration=self._manifest.find_stage(candidate.stage_id),
            declared_output_id=candidate.declared_output_id,
            resolved_output_id=resolved_output_id,
        )

    def _ranked_candidates(
        self,
        selector: ArtifactKey,
        phase_match: PhaseMatchMode,
    ) -> tuple[list[StageProducer], int]:
        candidates = self._manifest.find_producers_for_requirement(
            selector,
            phase_match=phase_match,
        )
        if not candidates:
            raise ProducerPathUnavailable(f"no supplied stage can satisfy {selector}")

        highest_phase = max(candidate.declared_output_id.phase or -1 for candidate in candidates)
        phase_valid = [
            candidate
            for candidate in candidates
            if (candidate.declared_output_id.phase or -1) == highest_phase
        ]

        default_stage_id = (
            self._manifest.default_producers.get(selector)
            or self._manifest.default_producers.get(phase_valid[0].declared_output_id)
        )
        if default_stage_id is not None:
            for candidate in phase_valid:
                if candidate.stage_id == default_stage_id:
                    return [candidate], highest_phase

        ranked = sorted(phase_valid, key=lambda item: item.stage_id)
        return ranked, highest_phase
