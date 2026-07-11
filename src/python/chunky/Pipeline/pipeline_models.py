from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..Artifacts.artifact_handles import ArtifactSourceHandle, ArtifactTargetHandle
from ..Artifacts.artifact_models import (
    ArtifactInstanceLocator,
    ArtifactKey,
    ArtifactRefLocator,
)
from ..Stages.stage_manifest import StageManifest
from ..Stages.stage_models import PlanningRunPolicy


@dataclass(frozen=True)
class ArtifactCachePolicy:
    ignore_existing_cache: bool = False
    do_not_cache: bool = False
    desired_ttl: timedelta | int | float | None = None


@dataclass(frozen=True)
class RequestValidationError(ValueError):
    errors: tuple[str, ...]

    def __str__(self) -> str:
        return "; ".join(self.errors)


@dataclass(frozen=True)
class RunRequest:
    inputs: Mapping[
        ArtifactKey | str,
        ArtifactSourceHandle[Any]
        | ArtifactInstanceLocator
        | Mapping[str, ArtifactSourceHandle[Any] | ArtifactInstanceLocator],
    ]
    outputs: Mapping[
        ArtifactKey | str,
        ArtifactTargetHandle[Any]
        | Sequence[ArtifactTargetHandle[Any]]
        | Mapping[str, ArtifactTargetHandle[Any]],
    ]
    run_policy: PlanningRunPolicy | None = None
    cache_policy: Mapping[
        ArtifactKey | str,
        ArtifactCachePolicy | Mapping[str, ArtifactCachePolicy],
    ] | None = None

    def normalize(
        self,
        stage_manifest: StageManifest,
    ) -> "NormalizedRunRequest":
        errors: list[str] = []
        input_sources = self._collect_flattened_request_artifacts(
            self.inputs,
            errors,
            "inputs",
        )
        output_targets = self._collect_flattened_request_outputs(self.outputs, errors)
        cache_policy = self._collect_flattened_cache_policy(
            self.cache_policy or {},
            errors,
        )

        declarations = {}
        for artifact_id in (*input_sources.keys(), *output_targets.keys(), *cache_policy.keys()):
            try:
                declarations[artifact_id] = stage_manifest.find_artifact(artifact_id)
            except Exception:
                errors.append(f"unknown request artifact: {artifact_id}")

        for artifact_id, source_or_locator in input_sources.items():
            if (
                isinstance(source_or_locator, ArtifactInstanceLocator)
                and source_or_locator.artifact_id != artifact_id.without_projection()
            ):
                errors.append(
                    "inputs: artifact instance locator artifact_id must match its request key: "
                    f"{source_or_locator.artifact_id} != {artifact_id}"
                )

        for artifact_id, targets in output_targets.items():
            if targets:
                continue
            declaration = declarations.get(artifact_id)
            if declaration is None:
                continue
            if not declaration.cacheable:
                errors.append(
                    f"target-less output {artifact_id} must be cacheable for durable retrieval"
                )
            if cache_policy.get(artifact_id, ArtifactCachePolicy()).do_not_cache:
                errors.append(
                    f"target-less output {artifact_id} conflicts with cache_policy.do_not_cache"
                )

        if not output_targets:
            errors.append("run request must contain at least one output")

        if errors:
            raise RequestValidationError(tuple(errors))

        return NormalizedRunRequest(
            input_sources=input_sources,
            output_targets=output_targets,
            cache_policy=cache_policy,
        )

    @classmethod
    def _collect_flattened_request_artifacts(
        cls,
        artifacts: Mapping[ArtifactKey | str, Any],
        errors: list[str],
        label: str,
    ) -> dict[ArtifactKey, Any]:
        flattened: dict[ArtifactKey, Any] = {}
        for domain_or_key, value in artifacts.items():
            cls._flatten_request_value(flattened, (), domain_or_key, value, errors, label)
        return flattened

    @classmethod
    def _collect_flattened_request_outputs(
        cls,
        artifacts: Mapping[ArtifactKey | str, Any],
        errors: list[str],
    ) -> dict[ArtifactKey, tuple[ArtifactTargetHandle[Any], ...]]:
        raw = cls._collect_flattened_request_artifacts(artifacts, errors, "outputs")
        outputs: dict[ArtifactKey, tuple[ArtifactTargetHandle[Any], ...]] = {}
        for artifact_id, value in raw.items():
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                targets = tuple(value)
            else:
                targets = (value,)
            outputs[artifact_id] = targets
        return outputs

    @classmethod
    def _collect_flattened_cache_policy(
        cls,
        cache_policy: Mapping[ArtifactKey | str, Any],
        errors: list[str],
    ) -> dict[ArtifactKey, ArtifactCachePolicy]:
        raw = cls._collect_flattened_request_artifacts(cache_policy, errors, "cache_policy")
        normalized: dict[ArtifactKey, ArtifactCachePolicy] = {}
        for artifact_id, value in raw.items():
            if isinstance(value, ArtifactCachePolicy):
                normalized[artifact_id] = value
            else:
                errors.append(
                    f"cache_policy for {artifact_id} must be an ArtifactCachePolicy"
                )
        return normalized

    @classmethod
    def _flatten_request_value(
        cls,
        flattened: dict[ArtifactKey, Any],
        path: tuple[str, ...],
        domain_or_key: ArtifactKey | str,
        value: Any,
        errors: list[str],
        label: str,
    ) -> None:
        if isinstance(domain_or_key, ArtifactKey):
            if path:
                errors.append(
                    f"{label}: nested request mappings must use string path components"
                )
                return
            if isinstance(value, Mapping):
                errors.append(f"{label}: artifact-key entries must map directly to a handle")
                return
            cls._add_request_artifact(flattened, domain_or_key, value, errors, label)
            return

        next_path = (*path, domain_or_key)
        if isinstance(value, Mapping):
            for child_key, child_value in value.items():
                if not isinstance(child_key, str):
                    errors.append(f"{label}: nested request mapping keys must be strings")
                    continue
                cls._flatten_request_value(
                    flattened,
                    next_path,
                    child_key,
                    child_value,
                    errors,
                    label,
                )
            return

        try:
            if 1 <= len(next_path) <= 4:
                locator = ArtifactRefLocator.parse("/".join(next_path))
                artifact_id = locator.artifact_key
                if locator.projection_context != "global":
                    artifact_id = artifact_id.with_projection(locator.projection_context)
            else:
                errors.append(
                    f"{label}: request artifact mappings must resolve to an artifact reference, "
                    "DOMAIN/KIND, DOMAIN/KIND/PHASE, PROJECTION/DOMAIN/KIND, "
                    "or PROJECTION/DOMAIN/KIND/PHASE path"
                )
                return
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
            return
        cls._add_request_artifact(flattened, artifact_id, value, errors, label)

    @staticmethod
    def _add_request_artifact(
        artifacts: dict[ArtifactKey, Any],
        artifact_id: ArtifactKey,
        handle: Any,
        errors: list[str],
        label: str,
    ) -> None:
        if artifact_id in artifacts:
            errors.append(f"{label}: duplicate request artifact: {artifact_id}")
            return
        artifacts[artifact_id] = handle


@dataclass(frozen=True)
class NormalizedRunRequest:
    input_sources: dict[ArtifactKey, ArtifactSourceHandle[Any] | ArtifactInstanceLocator]
    output_targets: dict[ArtifactKey, tuple[ArtifactTargetHandle[Any], ...]]
    cache_policy: dict[ArtifactKey, ArtifactCachePolicy]
