from __future__ import annotations

import json
import unittest
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - keeps the suite importable in tiny envs.
    yaml = None

from src.python.chunky.Artifacts.artifact_models import (
    ArtifactCapability,
    ArtifactDeclaration,
    ArtifactKey,
    ArtifactRequirement,
    ArtifactRole,
    PhaseMatchMode,
)
from src.python.chunky.Artifacts.binding_models import ArtifactHandle, ArtifactInstance
from src.python.chunky.Planning.TaskRequestGraph import (
    AmbiguousProducerSelection,
    ArtifactRequirementResolver,
    ArtifactNeed,
    ArtifactResolution,
    InvocationDraft,
    PlannedInvocation,
    PlannerRequestSettings,
    GraphBuildState,
    PlannerSettingsResolver,
    ProducerPathUnavailable,
    ProducerSelector,
    RequestedOutputBinding,
    SpeculativeGraphBranch,
    TaskRequestContext,
    StageManifestGraphWalker,
    TaskRequestGraphBuilder,
    settings_fingerprint,
)
from src.python.chunky.Planning.execution_planner import TaskRunPlanner
from src.python.chunky.Planning.manifest_graph import StageManifestGraphBuilder
from src.python.chunky import EvaluatedPlan
from src.python.chunky.Stages.stage_manifest import StageConsumer, StageManifest, StageManifestBuilder, StageProducer
from src.python.chunky.Stages.stage_models import (
    PlanningRunPolicy,
    ResolvedSettingsView,
    ResolvedStageRequirements,
    ResourcePolicy,
    StageManifestError,
    StagePlanningContext,
    StagePlanningError,
    StageRegistration,
)


class JsonishCodec:
    codec_id = "test-jsonish"
    codec_version = 1
    mime_type = "application/json"
    extension = ".json"

    async def serialize(self, value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")

    async def deserialize(self, raw: bytes) -> object:
        return json.loads(raw.decode("utf-8"))


class MemorySource:
    kind = "memory"

    def __init__(self, value: object) -> None:
        self.value = value
        self.closed = False

    async def get_value(self, _codec: JsonishCodec) -> object:
        if self.closed:
            raise RuntimeError("source is closed")
        return self.value

    async def get_content_fingerprint(self, codec: JsonishCodec) -> str:
        return (await codec.serialize(self.value)).hex()

    async def close(self) -> None:
        self.closed = True


async def _noop_executor(*_args: object, **_kwargs: object) -> None:
    return None


def _dummy_executor_factory() -> Callable[..., Any]:
    return _noop_executor


def _source_instance(
    declaration: ArtifactDeclaration[Any],
    *,
    value: object,
) -> ArtifactInstance[Any]:
    return ArtifactInstance(
        ArtifactHandle(
            declaration=declaration,
            fingerprint=f"supplied::{declaration.artifact_id}",
            source=MemorySource(value),
        )
    )


def _artifact_key(name: str, *, role: ArtifactRole = ArtifactRole.INTERMEDIATE) -> ArtifactKey:
    if name.startswith("setting_"):
        return _setting_key(name)
    return ArtifactKey.parse(f"graph//{_canonical_node_id(name)}")


def _setting_key(name: str) -> ArtifactKey:
    """Map spec setting node ids to the ArtifactKey override model.

    The graph-case spec names base settings as e.g. ``setting_a`` and edge
    overrides as e.g. ``setting_a_override_stage_b``.  Planner code represents
    the latter as an override-domain artifact whose ``without_override()`` is the
    base setting artifact.
    """

    name = _canonical_node_id(name)
    if name.endswith("_user_or_defaults"):
        base = name.removesuffix("_user_or_defaults")
        return ArtifactKey.parse(f"settings//{base}")

    marker = "_override_stage_"
    if marker in name:
        base, target_suffix = name.split(marker, 1)
        return ArtifactKey.parse(f"stage_{target_suffix}//settings//{base}")

    return ArtifactKey.parse(f"settings//{name}")


def _canonical_node_id(name: str) -> str:
    name = name.replace("-", "_")
    if name.endswith("_optional"):
        return name.removesuffix("_optional")
    return name


def _decl(
    name_or_key: str | ArtifactKey,
    role: ArtifactRole,
) -> ArtifactDeclaration[Any]:
    key = name_or_key if isinstance(name_or_key, ArtifactKey) else _artifact_key(name_or_key, role=role)
    return ArtifactDeclaration(
        artifact_id=key,
        role=role,
        version=1,
        codec=JsonishCodec(),
        cacheable=role is not ArtifactRole.SOURCE,
    )


def _stage_registration(
    *,
    stage_id: str,
    input_ids: Iterable[ArtifactKey],
    output_ids: Iterable[ArtifactKey],
    requirements: Callable[[StagePlanningContext], tuple[ArtifactRequirement, ...]],
    output_resolver: Callable[[StagePlanningContext], tuple[ArtifactCapability, ...]] | None = None,
    resource_resolver: Callable[[StagePlanningContext], ResourcePolicy] | None = None,
) -> StageRegistration:
    outputs = tuple(ArtifactCapability(key) for key in output_ids)
    return StageRegistration(
        stage_id=stage_id,
        display_name=stage_id,
        registration_version="test",
        behavior_version="test",
        executor_factory=_dummy_executor_factory,
        owned_artifacts=(),
        input_capabilities=tuple(ArtifactCapability(key) for key in input_ids),
        output_capabilities=outputs,
        resolve_requirements=lambda context: ResolvedStageRequirements(requirements(context)),
        resolve_output_capabilities=output_resolver or (lambda _context: outputs),
        resolve_resource_policy=resource_resolver or (lambda _context: ResourcePolicy()),
    )


def _manifest(
    *,
    artifacts: Mapping[ArtifactKey, ArtifactDeclaration[Any]],
    stages: Mapping[str, StageRegistration],
    default_producers: Mapping[ArtifactKey, str] | None = None,
) -> StageManifest:
    producers: dict[ArtifactKey, list[StageProducer]] = defaultdict(list)
    consumers: dict[ArtifactKey, list[StageConsumer]] = defaultdict(list)
    for stage in stages.values():
        for output in stage.output_capabilities:
            producers[output.artifact_id].append(
                StageProducer(stage.stage_id, output.artifact_id)
            )
        for input_ in stage.input_capabilities:
            consumers[input_.artifact_id].append(StageConsumer(stage.stage_id))

    return StageManifest(
        manifest_id="test-manifest",
        features={},
        stages=dict(stages),
        artifacts=dict(artifacts),
        producers={key: tuple(value) for key, value in producers.items()},
        consumers={key: tuple(value) for key, value in consumers.items()},
        default_producers=dict(default_producers or {}),
        description={},
    )


async def _evaluate(
    manifest: StageManifest,
    *,
    supplied: Mapping[ArtifactKey, ArtifactInstance[Any]],
    requested: Iterable[ArtifactKey],
    user_supplied_settings: Iterable[ArtifactKey] = (),
    run_policy: PlanningRunPolicy | None = None,
) -> EvaluatedPlan:
    requested_outputs = set(requested)
    TaskRunPlanner(manifest)._validate_requested_outputs(requested_outputs)
    resolver = PlannerSettingsResolver(manifest)
    manifest_graph = StageManifestGraphBuilder(manifest).build()
    request_settings = await PlannerRequestSettings.read_from(
        manifest,
        dict(supplied),
        user_supplied_settings=frozenset(user_supplied_settings),
    )
    context = TaskRequestContext(
        manifest=manifest,
        manifest_graph=manifest_graph,
        requested_output_ids=tuple(sorted(requested_outputs, key=str)),
        supplied_instances=dict(supplied),
        policy=run_policy or PlanningRunPolicy(),
        request_settings=request_settings,
    )
    return await TaskRequestGraphBuilder(
        manifest,
        resolver,
        ProducerSelector(manifest),
    ).build(
        context=context,
    )


@dataclass(frozen=True)
class CanonicalCaseHarness:
    case_id: str
    case: Mapping[str, Any]
    manifest: StageManifest
    supplied: dict[ArtifactKey, ArtifactInstance[Any]]
    user_supplied_settings: frozenset[ArtifactKey]
    requested_outputs: frozenset[ArtifactKey]
    fingerprint_suffixes: dict[str, str]

    async def evaluate(self) -> EvaluatedPlan:
        return await _evaluate(
            self.manifest,
            supplied=self.supplied,
            requested=self.requested_outputs,
            user_supplied_settings=self.user_supplied_settings,
        )

    @property
    def expected_edges(self) -> set[tuple[str, str]]:
        return {
            (_canonical_node_id(left), _canonical_node_id(right))
            for left, right in self.case["plans"]["evaluated"].get("edges", ())
        }

    def actual_edges(self, plan: EvaluatedPlan) -> set[tuple[str, str]]:
        produced = {
            output.instance_id
            for invocation in plan.invocations
            for output in invocation.requested_output_bindings
        }
        supplied_instance_ids = {
            str(key) for key in self.supplied
        }
        edges: set[tuple[str, str]] = set()
        for invocation in plan.invocations:
            stage_node = self._stage_node(invocation)
            for binding in invocation.requested_output_bindings:
                edges.add(
                    (
                        stage_node,
                        self._artifact_node(
                            binding.resolved_artifact_id,
                            binding.instance_id,
                            default_suffix=self._settings_suffix(invocation),
                        ),
                    )
                )
            for binding in invocation.input_bindings:
                is_known_available = (
                    binding.required
                    or binding.resolved_instance_id in produced
                    or binding.resolved_instance_id in supplied_instance_ids
                    or binding.resolved_artifact_id in self.supplied
                )
                if not is_known_available:
                    # Missing optional inputs should be omitted from the evaluated graph.
                    continue
                edges.add(
                    (
                        self._artifact_node(
                            binding.resolved_artifact_id,
                            binding.resolved_instance_id,
                        ),
                        stage_node,
                    )
                )
        return edges

    def settings_mismatches(self, plan: EvaluatedPlan) -> list[str]:
        expected = self._expected_stage_settings()
        if not expected:
            return []

        invocations_by_node = {
            self._stage_node(invocation): invocation
            for invocation in plan.invocations
        }
        mismatches: list[str] = []
        for stage_node, settings in sorted(expected.items()):
            invocation = invocations_by_node.get(stage_node)
            if invocation is None:
                mismatches.append(f"{stage_node}: expected settings but stage was not planned")
                continue
            for setting_id, expected_source_id in sorted(settings.items()):
                setting_key = _artifact_key(setting_id, role=ArtifactRole.SETTINGS)
                expected_key = _artifact_key(expected_source_id, role=ArtifactRole.SETTINGS)
                expected_instance = self.supplied.get(expected_key)
                if expected_instance is None:
                    mismatches.append(
                        f"{stage_node}.{setting_id}: expected source {expected_source_id} is not supplied"
                    )
                    continue
                expected_value = expected_instance.handle.source.value  # type: ignore[union-attr]
                try:
                    actual_value = invocation.resolved_settings.require(setting_key)
                except KeyError:
                    mismatches.append(f"{stage_node}.{setting_id}: setting is absent")
                    continue
                if actual_value != expected_value:
                    mismatches.append(
                        f"{stage_node}.{setting_id}: expected {expected_source_id}, "
                        f"actual={actual_value!r}"
                    )
        return mismatches

    def _expected_stage_settings(self) -> dict[str, dict[str, str]]:
        expected: dict[str, dict[str, str]] = {}
        for node in self.case["plans"]["evaluated"].get("nodes", ()):
            if node.get("kind") != "stage":
                continue
            resolved_settings = node.get("resolved_settings")
            if not resolved_settings:
                continue
            expected[_canonical_node_id(node["id"])] = {
                _canonical_node_id(setting_id): _canonical_node_id(source_id)
                for setting_id, source_id in resolved_settings.items()
            }
        return expected

    def _stage_node(self, invocation: PlannedInvocation) -> str:
        suffix = self._settings_suffix(invocation)
        if not suffix:
            return invocation.registration.stage_id
        # The canonical diagrams name duplicated upstream stage invocations by the
        # setting context that caused the split.
        return f"{invocation.registration.stage_id}_setting_a_override_{suffix}"

    def _artifact_node(
        self,
        artifact_id: ArtifactKey,
        instance_id: str,
        *,
        default_suffix: str = "",
    ) -> str:
        if artifact_id.domain == "settings":
            value = self.supplied.get(artifact_id)
            if value is not None:
                raw = value.handle.source.value  # type: ignore[union-attr]
                semantic_id = raw.get("semantic_id") if isinstance(raw, dict) else None
                if semantic_id:
                    return str(semantic_id)
            return f"{artifact_id.content_kind}_user_or_defaults"

        if artifact_id.override_domain is not None and artifact_id.domain == "settings":
            return f"{artifact_id.content_kind}_override_{artifact_id.override_domain}"

        base = artifact_id.content_kind
        suffix = self._suffix_from_instance_id(instance_id) or default_suffix
        if suffix:
            return f"{base}_override_{suffix}"
        return base

    def _settings_suffix(self, invocation: PlannedInvocation) -> str:
        suffix = self._suffix_from_instance_id(invocation.effective_settings_fingerprint)
        if suffix:
            return suffix
        for _key, value in invocation.resolved_settings.items():
            if isinstance(value, dict):
                suffix = value.get("target_suffix")
                if isinstance(suffix, str) and suffix:
                    return suffix
        return ""

    def _suffix_from_instance_id(self, value: str) -> str:
        if not value:
            return ""
        if "@" in value:
            value = value.rsplit("@", 1)[-1]
        if len(value) == 16:
            for fingerprint, suffix in self.fingerprint_suffixes.items():
                if fingerprint.startswith(value):
                    return suffix
        return self.fingerprint_suffixes.get(value, "")


def _load_graph_cases() -> dict[str, Any]:
    if yaml is None:
        raise unittest.SkipTest("PyYAML is required to load canonical graph cases")
    fixture_path = Path(__file__).with_name("fixtures") / "graph-cases.canonical.yaml"
    with fixture_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _canonical_harness(case_id: str, case: Mapping[str, Any]) -> CanonicalCaseHarness:
    plans = case["plans"]
    proposed = plans["proposed"]
    task = plans["task"]
    nodes: dict[str, dict[str, Any]] = {}
    for plan_name in ("task", "proposed", "evaluated"):
        for node in plans.get(plan_name, {}).get("nodes", ()):  # evaluated nodes help with labels only.
            nodes.setdefault(_canonical_node_id(node["id"]), dict(node))

    settings_in_task = {_canonical_node_id(item) for item in task.get("settings", ())}
    input_ids = {_canonical_node_id(item) for item in task.get("inputs", ())}
    output_ids = {_canonical_node_id(item) for item in task.get("outputs", ())}

    incoming: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[str]] = defaultdict(list)
    for left, right in proposed.get("edges", ()):
        left_id = _canonical_node_id(left)
        right_id = _canonical_node_id(right)
        outgoing[left_id].append(right_id)
        incoming[right_id].append(left_id)

    stage_ids = sorted(
        node_id for node_id, node in nodes.items() if node.get("kind") == "stage"
    )
    artifact_roles: dict[str, ArtifactRole] = {}
    for node_id, node in nodes.items():
        kind = node.get("kind")
        role = node.get("role")
        if kind == "stage":
            continue
        if kind == "setting" or node_id.startswith("setting_"):
            artifact_roles[node_id] = ArtifactRole.SETTINGS
        elif role == "input" or node_id in input_ids:
            artifact_roles[node_id] = ArtifactRole.SOURCE
        elif role == "output" or node_id in output_ids:
            artifact_roles[node_id] = ArtifactRole.TARGET
        else:
            artifact_roles[node_id] = ArtifactRole.INTERMEDIATE

    # Base settings are defaulted roots whenever a stage consumes them.  Override
    # settings are user-supplied only when the task requests them.
    for node_id in list(artifact_roles):
        if node_id.endswith("_user_or_defaults"):
            artifact_roles[node_id.removesuffix("_user_or_defaults")] = ArtifactRole.SETTINGS
    for node_id in settings_in_task:
        artifact_roles[node_id] = ArtifactRole.SETTINGS
        if "_override_stage_" in node_id:
            artifact_roles[node_id.split("_override_stage_", 1)[0]] = ArtifactRole.SETTINGS

    declarations = {
        _artifact_key(node_id, role=role): _decl(node_id, role)
        for node_id, role in artifact_roles.items()
    }

    supplied: dict[ArtifactKey, ArtifactInstance[Any]] = {}
    user_supplied_settings: set[ArtifactKey] = set()
    for node_id in sorted(input_ids):
        key = _artifact_key(node_id)
        supplied[key] = _source_instance(declarations[key], value={"semantic_id": node_id})
    for node_id, role in sorted(artifact_roles.items()):
        if role is not ArtifactRole.SETTINGS:
            continue
        if node_id.endswith("_user_or_defaults"):
            # The canonical diagrams use this as a display node for the base
            # default/user setting. It maps to the same ArtifactKey as the base
            # setting and must not overwrite the root value with a doubled label.
            continue
        key = _artifact_key(node_id, role=role)
        if node_id in settings_in_task:
            value = _setting_value(node_id)
            user_supplied_settings.add(key)
        elif "_override_stage_" in node_id:
            continue
        else:
            value = {"semantic_id": f"{node_id}_user_or_defaults", "target_suffix": ""}
        supplied[key] = _source_instance(declarations[key], value=value)

    override_keys_by_target: dict[str, list[ArtifactKey]] = defaultdict(list)
    for node_id in settings_in_task:
        if "_override_stage_" not in node_id:
            continue
        key = _artifact_key(node_id, role=ArtifactRole.SETTINGS)
        value = supplied[key].handle.source.value  # type: ignore[union-attr]
        targets = value.get("override_targets", ()) if isinstance(value, dict) else ()
        for target in targets:
            override_keys_by_target[target].append(key)

    stage_inputs: dict[str, list[str]] = {
        stage_id: list(incoming.get(stage_id, ())) for stage_id in stage_ids
    }
    stage_outputs: dict[str, list[str]] = {
        stage_id: list(outgoing.get(stage_id, ())) for stage_id in stage_ids
    }

    def path_has_setting_consumer(start_artifact: str, base_setting: str) -> bool:
        # Walk from an input artifact toward its upstream producers.  If any
        # upstream stage consumes the base setting, the downstream requirement is
        # responsible for pushing the override along that edge.
        queue: deque[str] = deque([start_artifact])
        seen: set[str] = set()
        while queue:
            artifact = queue.popleft()
            if artifact in seen:
                continue
            seen.add(artifact)
            for producer_stage in incoming.get(artifact, ()):
                if base_setting in stage_inputs.get(producer_stage, ()):
                    return True
                for upstream in stage_inputs.get(producer_stage, ()):
                    if artifact_roles.get(upstream) is not ArtifactRole.SETTINGS:
                        queue.append(upstream)
        return False

    stages: dict[str, StageRegistration] = {}
    for stage_id in stage_ids:
        input_keys = [_artifact_key(item, role=artifact_roles[item]) for item in stage_inputs[stage_id]]
        output_keys = [_artifact_key(item, role=artifact_roles[item]) for item in stage_outputs[stage_id]]

        def make_requirements(stage_id: str = stage_id) -> Callable[[StagePlanningContext], tuple[ArtifactRequirement, ...]]:
            def requirements(_context: StagePlanningContext) -> tuple[ArtifactRequirement, ...]:
                resolved: list[ArtifactRequirement] = []
                for input_node_id in stage_inputs[stage_id]:
                    role = artifact_roles[input_node_id]
                    key = _artifact_key(input_node_id, role=role)
                    pushed: list[ArtifactKey] = []
                    if role is not ArtifactRole.SETTINGS:
                        for override_key in override_keys_by_target.get(stage_id, ()):  # direct target only
                            base_setting = override_key.content_kind
                            if path_has_setting_consumer(input_node_id, base_setting):
                                pushed.append(override_key)
                    required = not nodes.get(input_node_id, {}).get("optional", False)
                    resolved.append(
                        ArtifactRequirement(
                            key,
                            required=required,
                            pushed_settings=tuple(sorted(pushed, key=str)),
                        )
                    )
                return tuple(resolved)
            return requirements

        stages[stage_id] = _stage_registration(
            stage_id=stage_id,
            input_ids=input_keys,
            output_ids=output_keys,
            requirements=make_requirements(stage_id),
        )

    manifest = _manifest(artifacts=declarations, stages=stages)
    fingerprint_suffixes = _fingerprint_suffixes(supplied, settings_in_task)
    return CanonicalCaseHarness(
        case_id=case_id,
        case=case,
        manifest=manifest,
        supplied=supplied,
        user_supplied_settings=frozenset(user_supplied_settings),
        requested_outputs=frozenset(_artifact_key(item) for item in output_ids),
        fingerprint_suffixes=fingerprint_suffixes,
    )


def _setting_value(node_id: str) -> dict[str, object]:
    node_id = _canonical_node_id(node_id)
    if "_override_stage_" not in node_id:
        return {"semantic_id": node_id, "target_suffix": ""}
    base, target_part = node_id.split("_override_stage_", 1)
    targets = tuple(f"stage_{part}" for part in target_part.split("_") if part)
    suffix = "_".join(part for part in target_part.split("_") if part)
    return {
        "semantic_id": node_id,
        "base_setting": base,
        "override_targets": targets,
        "target_suffix": suffix,
    }


def _fingerprint_suffixes(
    supplied: Mapping[ArtifactKey, ArtifactInstance[Any]],
    setting_ids: set[str],
) -> dict[str, str]:
    overrides: list[tuple[ArtifactKey, object, str]] = []
    for setting_id in setting_ids:
        if "_override_stage_" not in setting_id:
            continue
        key = _artifact_key(setting_id, role=ArtifactRole.SETTINGS)
        instance = supplied[key]
        value = instance.handle.source.value  # type: ignore[union-attr]
        assert isinstance(value, dict)
        suffix = str(value["target_suffix"])
        overrides.append((key, value, suffix))

    result: dict[str, str] = {}
    for count in range(1, len(overrides) + 1):
        for combo in combinations(overrides, count):
            suffix = "_".join(item[2] for item in sorted(combo, key=lambda item: item[2]))
            inherited = ResolvedSettingsView({key: value for key, value, _ in combo})
            result[settings_fingerprint(inherited)] = suffix
            if len(combo) == 1:
                key, value, suffix = combo[0]
                base = key.without_override()
                result[settings_fingerprint(ResolvedSettingsView({base: value}))] = suffix
    return result


class CanonicalGraphCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_graph_cases_match_evaluated_edges(self) -> None:
        """The evaluator should reproduce the machine-readable graph-case spec.

        This intentionally covers happy paths, pruning, optional dependencies,
        and settings-propagation duplication.  Some of these cases document gaps
        in the current implementation and are expected to fail until the planner
        is brought into line with the spec.
        """
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     Loaded from fixtures/graph-cases.canonical.yaml for each subTest.
        #
        #   EvaluatedPlan:
        #     Compared with the evaluated edge list from the same fixture case.
        #
        # Canonical fixture case index:
        #
        #   basic.case_1
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_b -> out_a
        #     EvaluatedPlan: in_a -> stage_a -> int_a -> stage_b -> out_a
        #
        #   basic.case_2
        #     ProposedPlan:  in_a -> stage_a -> {int_a -> stage_b -> int_c,
        #                    int_b -> stage_c -> int_d} -> stage_d -> out_a
        #     EvaluatedPlan: same
        #
        #   multi_output.case_1
        #     ProposedPlan:  in_a -> stage_a -> {int_a -> stage_b -> out_a,
        #                    int_b -> stage_c -> out_b}
        #     EvaluatedPlan: in_a -> stage_a -> int_a -> stage_b -> out_a
        #
        #   multi_output.case_2
        #     ProposedPlan:  in_a -> stage_a -> {int_a -> stage_b -> out_a,
        #                    int_b -> stage_c -> out_b}
        #     EvaluatedPlan: same
        #
        #   multi_input.case_1
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_c -> out_a
        #                    in_b -> stage_b -> int_b_optional -> stage_c
        #     EvaluatedPlan: in_a -> stage_a -> int_a -> stage_c -> out_a
        #
        #   multi_input.case_2
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_c -> out_a
        #                    in_b -> stage_b -> int_b -> stage_c
        #     EvaluatedPlan: same
        #
        #   multi_input_output.case_1
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_c -> out_a
        #                    in_b -> stage_b -> int_b -> {stage_c, stage_d -> out_b}
        #     EvaluatedPlan: in_a -> stage_a -> int_a -> stage_c -> out_a
        #                    in_b -> stage_b -> int_b -> stage_c
        #
        #   multi_input_output.case_2
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_c -> out_a
        #                    in_b -> stage_b -> int_b -> {stage_c, stage_d -> out_b}
        #     EvaluatedPlan: same
        #
        #   multi_input_output.case_3
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_b -> out_a
        #                    in_b -> stage_c -> int_b -> stage_d -> out_b
        #     EvaluatedPlan: same
        #
        #   multi_input_output.case_4
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_b -> out_a
        #                    in_b -> stage_c -> int_b -> stage_d -> out_b
        #     EvaluatedPlan: in_a -> stage_a -> int_a -> stage_b -> out_a
        #
        #   optional.case_1
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_b -> out_a
        #                    in_b_optional -> stage_b
        #     EvaluatedPlan: in_a -> stage_a -> int_a -> stage_b -> out_a
        #
        #   optional.case_2
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_b -> out_a
        #                    in_b -> stage_c -> int_b_optional -> stage_b
        #     EvaluatedPlan: in_a -> stage_a -> int_a -> stage_b -> out_a
        #
        #   optional.case_3
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_b -> out_a
        #                    in_b -> stage_c -> int_b -> stage_d -> int_c_optional -> stage_b
        #     EvaluatedPlan: in_a -> stage_a -> int_a -> stage_b -> out_a
        #
        #   optional.case_4
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_b -> out_a
        #                    in_b -> stage_c -> int_b -> stage_d -> int_c_optional -> stage_b
        #     EvaluatedPlan: in_a -> stage_a -> int_a -> stage_b -> out_a
        #                    in_b -> stage_c -> int_b -> stage_d -> int_c -> stage_b
        #
        #   settings_propagation.case_1
        #     ProposedPlan:  in_a -> stage_a -> int_a -> stage_b -> out_a
        #                    in_b -> stage_c -> int_b -> stage_d -> int_c -> stage_b
        #                    setting_a -> stage_d
        #     EvaluatedPlan: stage_d is replaced by stage_d_setting_a_override_b,
        #                    producing int_c_override_b for stage_b.
        #
        #   settings_propagation.case_2
        #     ProposedPlan:  same shape as case_1, plus int_c -> stage_e -> out_b
        #     EvaluatedPlan: stage_d is split into default and override_b paths.
        #
        #   settings_propagation.case_3
        #     ProposedPlan:  same shape as case_2
        #     EvaluatedPlan: stage_d is split into override_b and override_e paths.
        #
        #   settings_propagation.case_4
        #     ProposedPlan:  same shape as case_2
        #     EvaluatedPlan: one shared override_b_e path feeds both requested outputs.
        #
        #   settings_propagation.case_5
        #     ProposedPlan:  setting_a -> stage_c -> int_b -> stage_d -> int_c
        #                    int_c feeds stage_b -> out_a and stage_e -> out_b.
        #     EvaluatedPlan: stage_c and stage_d split into override_b and override_e paths.
        #
        #   settings_propagation.case_6
        #     ProposedPlan:  like case_5, with setting_a -> stage_c and setting_b -> stage_d
        #     EvaluatedPlan: stage_c and stage_d split into override_b and override_e paths,
        #                    carrying both setting_a and setting_b overrides.

        graph_cases = _load_graph_cases()
        failures: list[str] = []
        for group_id, group in graph_cases["groups"].items():
            for case_key, case in group["cases"].items():
                case_id = f"{group_id}.{case_key}"
                with self.subTest(case_id=case_id):
                    harness = _canonical_harness(case_id, case)
                    try:
                        plan = await harness.evaluate()
                        actual = harness.actual_edges(plan)
                    except Exception as exc:  # noqa: BLE001 - test reports all cases.
                        failures.append(f"{case_id}: raised {type(exc).__name__}: {exc}")
                        continue
                    expected = harness.expected_edges
                    if actual != expected:
                        missing = sorted(expected - actual)
                        unexpected = sorted(actual - expected)
                        failures.append(
                            f"{case_id}: edge mismatch\n"
                            f"  missing={missing}\n"
                            f"  unexpected={unexpected}"
                        )
                    settings_mismatches = harness.settings_mismatches(plan)
                    if settings_mismatches:
                        failures.append(
                            f"{case_id}: resolved settings mismatch\n"
                            + "\n".join(f"  {item}" for item in settings_mismatches)
                        )
        if failures:
            self.fail("\n\n".join(failures))

    async def test_optional_upstream_chain_without_supplied_source_is_pruned_not_failed(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> | stage_a | -> int_a ----------------------------------> | stage_b | -> out_a
        #     in_b -> | stage_c | -> int_b -> | stage_d | -> int_c_optional -> |         |
        #
        #   EvaluatedPlan:
        #     in_a -> | stage_a | -> int_a -> | stage_b | -> out_a
        #
        graph_cases = _load_graph_cases()
        case = graph_cases["groups"]["optional"]["cases"]["case_3"]
        harness = _canonical_harness("optional.case_3", case)
        plan = await harness.evaluate()
        self.assertEqual(harness.expected_edges, harness.actual_edges(plan))

    async def test_reachable_optional_producer_path_remains_optional_on_consumer(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> | stage_a | -> int_a ----------------------------------> | stage_b | -> out_a
        #     in_b -> | stage_c | -> int_b -> | stage_d | -> int_c_optional -> |         |
        #
        #   EvaluatedPlan:
        #     in_a -> | stage_a | -> int_a -------------------------> | stage_b | -> out_a
        #     in_b -> | stage_c | -> int_b -> | stage_d | -> int_c -> |         |
        #
        graph_cases = _load_graph_cases()
        case = graph_cases["groups"]["optional"]["cases"]["case_4"]
        harness = _canonical_harness("optional.case_4", case)

        plan = await harness.evaluate()

        stage_b = next(
            invocation
            for invocation in plan.invocations
            if invocation.registration.stage_id == "stage_b"
        )
        self.assertEqual((ArtifactKey.parse("graph//int_a"),), stage_b.required_input_ids)
        self.assertEqual((ArtifactKey.parse("graph//int_c"),), stage_b.optional_input_ids)


class PlannerEvaluationOptionalPruningTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_optional_source_input_is_omitted_without_producer_probe(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a ----------> |stage_b| -> out_a
        #     in_b_optional -> |       |
        #
        #   EvaluatedPlan:
        #     in_a -> |stage_b| -> out_a
        #
        in_a = _artifact_key("in_a")
        in_b = _artifact_key("in_b")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            in_b: _decl(in_b, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        stage = _stage_registration(
            stage_id="stage_b",
            input_ids=[in_a, in_b],
            output_ids=[out_a],
            requirements=lambda _ctx: (
                ArtifactRequirement(in_a),
                ArtifactRequirement(in_b, required=False),
            ),
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})
        supplied = {in_a: _source_instance(artifacts[in_a], value="source-a")}

        plan = await _evaluate(manifest, supplied=supplied, requested={out_a})

        [invocation] = plan.invocations
        self.assertEqual("stage_b", invocation.registration.stage_id)
        self.assertEqual((in_a,), invocation.required_input_ids)
        self.assertEqual((), invocation.optional_input_ids)

    async def test_two_stage_optional_path_is_pruned_when_required_source_is_missing(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> |stage_required_source| -> int_a --------------------------------------------> |stage_consumer| -> out_a
        #     in_b -> |stage_optional_head| -> int_b -> |stage_optional_tail| -> int_c_optional ---> |              |
        #
        #   EvaluatedPlan:
        #     in_a -> |stage_required_source |-> int_a -> |stage_consumer| -> out_a
        #
        # The optional chain is pruned because its required source, in_b, is missing.
        in_a = _artifact_key("in_a")
        in_b = _artifact_key("in_b")
        int_a = _artifact_key("int_a")
        int_b = _artifact_key("int_b")
        int_c = _artifact_key("int_c")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            in_b: _decl(in_b, ArtifactRole.SOURCE),
            int_a: _decl(int_a, ArtifactRole.INTERMEDIATE),
            int_b: _decl(int_b, ArtifactRole.INTERMEDIATE),
            int_c: _decl(int_c, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        required_source = _stage_registration(
            stage_id="stage_required_source",
            input_ids=[in_a],
            output_ids=[int_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        optional_head = _stage_registration(
            stage_id="stage_optional_head",
            input_ids=[in_b],
            output_ids=[int_b],
            requirements=lambda _ctx: (ArtifactRequirement(in_b),),
        )
        optional_tail = _stage_registration(
            stage_id="stage_optional_tail",
            input_ids=[int_a, int_b],
            output_ids=[int_c],
            requirements=lambda _ctx: (
                ArtifactRequirement(int_a),
                ArtifactRequirement(int_b),
            ),
        )
        consumer = _stage_registration(
            stage_id="stage_consumer",
            input_ids=[int_a, int_c],
            output_ids=[out_a],
            requirements=lambda _ctx: (
                ArtifactRequirement(int_a),
                ArtifactRequirement(int_c, required=False),
            ),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                "stage_required_source": required_source,
                "stage_optional_head": optional_head,
                "stage_optional_tail": optional_tail,
                "stage_consumer": consumer,
            },
        )
        supplied = {in_a: _source_instance(artifacts[in_a], value="source-a")}

        plan = await _evaluate(manifest, supplied=supplied, requested={out_a})

        invocations_by_stage = {
            invocation.registration.stage_id: invocation
            for invocation in plan.invocations
        }
        self.assertEqual(
            {"stage_required_source", "stage_consumer"},
            set(invocations_by_stage),
        )
        self.assertEqual(
            (int_a,),
            invocations_by_stage["stage_consumer"].required_input_ids,
        )
        self.assertEqual((), invocations_by_stage["stage_consumer"].optional_input_ids)

    async def test_optional_ambiguous_producer_path_is_pruned_when_no_candidates_are_valid(self) -> None:
        in_a = _artifact_key("in_a")
        in_b = _artifact_key("in_b")
        int_optional = _artifact_key("int_optional")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            in_b: _decl(in_b, ArtifactRole.SOURCE),
            int_optional: _decl(int_optional, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        optional_a = _stage_registration(
            stage_id="stage_optional_a",
            input_ids=[in_a],
            output_ids=[int_optional],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        optional_b = _stage_registration(
            stage_id="stage_optional_b",
            input_ids=[in_b],
            output_ids=[int_optional],
            requirements=lambda _ctx: (ArtifactRequirement(in_b),),
        )
        consumer = _stage_registration(
            stage_id="stage_consumer",
            input_ids=[int_optional],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(int_optional, required=False),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                "stage_optional_a": optional_a,
                "stage_optional_b": optional_b,
                "stage_consumer": consumer,
            },
        )

        plan = await _evaluate(manifest, supplied={}, requested={out_a})

        [invocation] = plan.invocations
        self.assertEqual("stage_consumer", invocation.registration.stage_id)
        self.assertEqual((), invocation.optional_input_ids)

    async def test_optional_ambiguous_producer_path_raises_when_multiple_candidates_are_valid(self) -> None:
        in_a = _artifact_key("in_a")
        in_b = _artifact_key("in_b")
        int_optional = _artifact_key("int_optional")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            in_b: _decl(in_b, ArtifactRole.SOURCE),
            int_optional: _decl(int_optional, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        optional_a = _stage_registration(
            stage_id="stage_optional_a",
            input_ids=[in_a],
            output_ids=[int_optional],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        optional_b = _stage_registration(
            stage_id="stage_optional_b",
            input_ids=[in_b],
            output_ids=[int_optional],
            requirements=lambda _ctx: (ArtifactRequirement(in_b),),
        )
        consumer = _stage_registration(
            stage_id="stage_consumer",
            input_ids=[int_optional],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(int_optional, required=False),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                "stage_optional_a": optional_a,
                "stage_optional_b": optional_b,
                "stage_consumer": consumer,
            },
        )
        supplied = {
            in_a: _source_instance(artifacts[in_a], value="source-a"),
            in_b: _source_instance(artifacts[in_b], value="source-b"),
        }

        with self.assertRaisesRegex(AmbiguousProducerSelection, "ambiguous producers") as caught:
            await _evaluate(manifest, supplied=supplied, requested={out_a})
        self.assertEqual(
            ("stage_optional_a", "stage_optional_b"),
            tuple(item.candidate.stage_id for item in caught.exception.valid_candidates),
        )
        self.assertEqual((), caught.exception.invalid_candidates)


class PlannerEvaluationSuppliedIntermediateTests(unittest.IsolatedAsyncioTestCase):
    async def test_supplied_intermediate_stops_backward_walk_and_wins_over_producer_path(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> |stage_a| -> int_a -> |stage_b| -> out_a
        #
        #   EvaluatedPlan:
        #     int_a_supplied -> |stage_b| -> out_a
        #
        in_a = _artifact_key("in_a")
        int_a = _artifact_key("int_a")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            int_a: _decl(int_a, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        stage_a = _stage_registration(
            stage_id="stage_a",
            input_ids=[in_a],
            output_ids=[int_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        stage_b = _stage_registration(
            stage_id="stage_b",
            input_ids=[int_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(int_a),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_a": stage_a, "stage_b": stage_b},
        )
        supplied = {
            in_a: _source_instance(artifacts[in_a], value="source-a"),
            int_a: _source_instance(artifacts[int_a], value="caller-supplied-int-a"),
        }

        plan = await _evaluate(manifest, supplied=supplied, requested={out_a})

        self.assertEqual(["stage_b"], [inv.registration.stage_id for inv in plan.invocations])
        [stage_b_invocation] = plan.invocations
        self.assertEqual((int_a,), stage_b_invocation.required_input_ids)
        self.assertEqual(
            f"{int_a}",
            stage_b_invocation.input_bindings[0].resolved_instance_id,
        )

    async def test_supplied_intermediate_does_not_prune_shared_producer_needed_for_other_output(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> | stage_ab | -> int_a -> | stage_a | -> out_a
        #                         -> int_b -> | stage_b | -> out_b
        #
        #   EvaluatedPlan:
        #     int_a_supplied -------------> | stage_a | -> out_a
        #     in_a -> | stage_ab | -> int_b -> | stage_b | -> out_b
        #
        in_a = _artifact_key("in_a")
        int_a = _artifact_key("int_a")
        int_b = _artifact_key("int_b")
        out_a = _artifact_key("out_a")
        out_b = _artifact_key("out_b")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            int_a: _decl(int_a, ArtifactRole.INTERMEDIATE),
            int_b: _decl(int_b, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
            out_b: _decl(out_b, ArtifactRole.TARGET),
        }
        producer = _stage_registration(
            stage_id="stage_ab",
            input_ids=[in_a],
            output_ids=[int_a, int_b],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        consumer_a = _stage_registration(
            stage_id="stage_a",
            input_ids=[int_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(int_a),),
        )
        consumer_b = _stage_registration(
            stage_id="stage_b",
            input_ids=[int_b],
            output_ids=[out_b],
            requirements=lambda _ctx: (ArtifactRequirement(int_b),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                "stage_ab": producer,
                "stage_a": consumer_a,
                "stage_b": consumer_b,
            },
        )
        supplied = {
            in_a: _source_instance(artifacts[in_a], value="source-a"),
            int_a: _source_instance(artifacts[int_a], value="caller-supplied-int-a"),
        }

        plan = await _evaluate(manifest, supplied=supplied, requested={out_a, out_b})

        invocations_by_stage = {
            invocation.registration.stage_id: invocation
            for invocation in plan.invocations
        }
        self.assertEqual({"stage_ab", "stage_a", "stage_b"}, set(invocations_by_stage))
        self.assertEqual((int_b,), invocations_by_stage["stage_ab"].requested_output_ids)
        self.assertEqual(
            f"{int_a}",
            invocations_by_stage["stage_a"].input_bindings[0].resolved_instance_id,
        )
        self.assertEqual(
            invocations_by_stage["stage_ab"].requested_output_bindings[0].instance_id,
            invocations_by_stage["stage_b"].input_bindings[0].resolved_instance_id,
        )

    async def test_supplied_completed_intermediate_satisfies_phase_less_completed_requirement(self) -> None:
        
        # Graph diagrams:
        #
        #   Key:
        #     transcript//token_text#201 is a concrete terminal phase.
        #     transcript//token_text is the phase-less completed-family selector.
        #
        #   Manifest:
        #     stage_terminal can produce the terminal phase:
        #       |stage_terminal| -> transcript//token_text#201
        #
        #     stage_render requires the completed-family selector:
        #       transcript//token_text -> |stage_render| -> out_a
        #
        #   Supplied roots:
        #     transcript//token_text is supplied directly by the caller.
        #
        #   ProposedPlan:
        #     |stage_terminal| -> transcript//token_text#201 >> transcript//token_text -> |stage_render| -> out_a
        #
        #   EvaluatedPlan:
        #     transcript//token_text_supplied -> |stage_render| -> out_a
        #

        completed = ArtifactKey.parse("transcript//token_text")
        terminal_phase = ArtifactKey.parse("transcript//token_text#201")
        out_a = _artifact_key("out_a")
        artifacts = {
            terminal_phase: _decl(terminal_phase, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        terminal_producer = _stage_registration(
            stage_id="stage_terminal",
            input_ids=[],
            output_ids=[terminal_phase],
            requirements=lambda _ctx: (),
        )
        renderer = _stage_registration(
            stage_id="stage_render",
            input_ids=[completed],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(completed),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_terminal": terminal_producer, "stage_render": renderer},
        )
        supplied_completed = _source_instance(
            _decl(completed, ArtifactRole.INTERMEDIATE),
            value="completed-token-text",
        )

        plan = await _evaluate(
            manifest,
            supplied={completed: supplied_completed},
            requested={out_a},
        )

        self.assertEqual(["stage_render"], [inv.registration.stage_id for inv in plan.invocations])
        [render_invocation] = plan.invocations
        self.assertEqual((completed,), render_invocation.required_input_ids)

    async def test_supplied_concrete_phase_satisfies_bounded_requirement_but_not_completed_family(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     transcript//token_text#100_supplied
        #     stage_terminal -> transcript//token_text#201
        #     transcript//token_text#200 -> stage_bounded_consumer -> out_bounded
        #     transcript//token_text -> stage_completed_consumer -> out_completed
        #
        #   EvaluatedPlan, bounded request:
        #     transcript//token_text#100_supplied -> stage_bounded_consumer -> out_bounded
        #
        #   EvaluatedPlan, completed request:
        #     stage_terminal -> transcript//token_text -> stage_completed_consumer -> out_completed
        #
        phase_100 = ArtifactKey.parse("transcript//token_text#100")
        phase_201 = ArtifactKey.parse("transcript//token_text#201")
        completed = ArtifactKey.parse("transcript//token_text")
        out_bounded = _artifact_key("out_bounded")
        out_completed = _artifact_key("out_completed")
        artifacts = {
            phase_100: _decl(phase_100, ArtifactRole.INTERMEDIATE),
            phase_201: _decl(phase_201, ArtifactRole.INTERMEDIATE),
            out_bounded: _decl(out_bounded, ArtifactRole.TARGET),
            out_completed: _decl(out_completed, ArtifactRole.TARGET),
        }
        phase_201_producer = _stage_registration(
            stage_id="stage_terminal",
            input_ids=[],
            output_ids=[phase_201],
            requirements=lambda _ctx: (),
        )
        bounded_consumer = _stage_registration(
            stage_id="stage_bounded_consumer",
            input_ids=[ArtifactKey.parse("transcript//token_text#200")],
            output_ids=[out_bounded],
            requirements=lambda _ctx: (
                ArtifactRequirement(ArtifactKey.parse("transcript//token_text#200")),
            ),
        )
        completed_consumer = _stage_registration(
            stage_id="stage_completed_consumer",
            input_ids=[completed],
            output_ids=[out_completed],
            requirements=lambda _ctx: (ArtifactRequirement(completed),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                "stage_terminal": phase_201_producer,
                "stage_bounded_consumer": bounded_consumer,
                "stage_completed_consumer": completed_consumer,
            },
        )
        supplied = {phase_100: _source_instance(artifacts[phase_100], value="phase-100")}

        bounded_plan = await _evaluate(manifest, supplied=supplied, requested={out_bounded})
        self.assertEqual(
            ["stage_bounded_consumer"],
            [inv.registration.stage_id for inv in bounded_plan.invocations],
        )
        self.assertEqual((phase_100,), bounded_plan.invocations[0].required_input_ids)

        completed_plan = await _evaluate(manifest, supplied=supplied, requested={out_completed})
        self.assertEqual(
            ["stage_terminal", "stage_completed_consumer"],
            [inv.registration.stage_id for inv in completed_plan.invocations],
        )
        completed_input = completed_plan.invocations[1].input_bindings[0]
        self.assertEqual(completed, completed_input.resolved_artifact_id)
        self.assertNotEqual(phase_100, completed_input.resolved_artifact_id)


class PlannerEvaluationErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_required_input_raises_stage_planning_error(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> stage_a -> out_a
        #
        #   EvaluatedPlan:
        #     StagePlanningError; in_a is required but not supplied or produced.
        #
        in_a = _artifact_key("in_a")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        stage = _stage_registration(
            stage_id="stage_a",
            input_ids=[in_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})

        with self.assertRaisesRegex(StagePlanningError, "no supplied stage can satisfy"):
            await _evaluate(manifest, supplied={}, requested={out_a})

    async def test_ambiguous_producers_raise_stage_planning_error(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_a -> out_a
        #     stage_b -> out_a
        #
        #   EvaluatedPlan:
        #     StagePlanningError; out_a has no unique producer.
        #
        out_a = _artifact_key("out_a")
        artifacts = {out_a: _decl(out_a, ArtifactRole.TARGET)}
        stage_a = _stage_registration(
            stage_id="stage_a",
            input_ids=[],
            output_ids=[out_a],
            requirements=lambda _ctx: (),
        )
        stage_b = _stage_registration(
            stage_id="stage_b",
            input_ids=[],
            output_ids=[out_a],
            requirements=lambda _ctx: (),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_a": stage_a, "stage_b": stage_b},
        )

        with self.assertRaisesRegex(AmbiguousProducerSelection, "ambiguous producers") as caught:
            await _evaluate(manifest, supplied={}, requested={out_a})
        self.assertEqual(
            ("stage_a", "stage_b"),
            tuple(item.candidate.stage_id for item in caught.exception.valid_candidates),
        )
        self.assertEqual((), caught.exception.invalid_candidates)

    async def test_default_producer_resolves_ambiguous_producer_choice(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_a -> out_a
        #     stage_b -> out_a
        #
        #   EvaluatedPlan:
        #     stage_b -> out_a
        #
        out_a = _artifact_key("out_a")
        artifacts = {out_a: _decl(out_a, ArtifactRole.TARGET)}
        stage_a = _stage_registration(
            stage_id="stage_a",
            input_ids=[],
            output_ids=[out_a],
            requirements=lambda _ctx: (),
        )
        stage_b = _stage_registration(
            stage_id="stage_b",
            input_ids=[],
            output_ids=[out_a],
            requirements=lambda _ctx: (),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_a": stage_a, "stage_b": stage_b},
            default_producers={out_a: "stage_b"},
        )

        plan = await _evaluate(manifest, supplied={}, requested={out_a})
        self.assertEqual(["stage_b"], [inv.registration.stage_id for inv in plan.invocations])

    async def test_output_resolver_must_expose_claimed_output(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_a -> out_a
        #
        #   EvaluatedPlan:
        #     StagePlanningError; stage_a resolves to no exposed outputs.
        #
        out_a = _artifact_key("out_a")
        artifacts = {out_a: _decl(out_a, ArtifactRole.TARGET)}
        stage = _stage_registration(
            stage_id="stage_a",
            input_ids=[],
            output_ids=[out_a],
            requirements=lambda _ctx: (),
            output_resolver=lambda _ctx: (),
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})

        with self.assertRaisesRegex(StagePlanningError, "cannot expose requested outputs"):
            await _evaluate(manifest, supplied={}, requested={out_a})

    async def test_dynamic_requirement_outside_static_input_capability_raises(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> stage_a -> out_a
        #
        #   EvaluatedPlan:
        #     StageManifestError; stage_a dynamically requires undeclared in_b.
        #
        declared_input = _artifact_key("in_a")
        undeclared_input = _artifact_key("in_b")
        out_a = _artifact_key("out_a")
        artifacts = {
            declared_input: _decl(declared_input, ArtifactRole.SOURCE),
            undeclared_input: _decl(undeclared_input, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        stage = _stage_registration(
            stage_id="stage_a",
            input_ids=[declared_input],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(undeclared_input),),
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})

        with self.assertRaisesRegex(StageManifestError, "outside its static capability"):
            await _evaluate(manifest, supplied={}, requested={out_a})

    async def test_request_specific_cycle_raises_stage_planning_error(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     graph//loop#2 -> stage_a -> graph//loop#1 -> stage_b -> graph//loop#2
        #
        #   EvaluatedPlan:
        #     StagePlanningError; the request-specific dependency graph is cyclic.
        #
        int_a = ArtifactKey.parse("graph//loop#1")
        int_b = ArtifactKey.parse("graph//loop#2")
        artifacts = {
            int_a: _decl(int_a, ArtifactRole.INTERMEDIATE),
            int_b: _decl(int_b, ArtifactRole.INTERMEDIATE),
        }
        stage_a = _stage_registration(
            stage_id="stage_a",
            input_ids=[int_b],
            output_ids=[int_a],
            requirements=lambda _ctx: (ArtifactRequirement(int_b, phase_match=PhaseMatchMode.EXACT),),
        )
        stage_b = _stage_registration(
            stage_id="stage_b",
            input_ids=[int_a],
            output_ids=[int_b],
            requirements=lambda _ctx: (ArtifactRequirement(int_a, phase_match=PhaseMatchMode.EXACT),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_a": stage_a, "stage_b": stage_b},
        )

        with self.assertRaisesRegex(StagePlanningError, "cyclic or unresolved"):
            await _evaluate(manifest, supplied={}, requested={int_a})


class PlannerEvaluationPhaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_phase_less_intermediate_request_is_terminal_remapped_to_family_id(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_asr -> transcript//token_text#200
        #
        #   EvaluatedPlan:
        #     stage_asr -> transcript//token_text
        #
        requested = ArtifactKey.parse("transcript//token_text")
        produced = ArtifactKey.parse("transcript//token_text#200")
        artifacts = {
            produced: _decl(produced, ArtifactRole.INTERMEDIATE),
        }
        stage = _stage_registration(
            stage_id="stage_asr",
            input_ids=[],
            output_ids=[produced],
            requirements=lambda _ctx: (),
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})

        plan = await _evaluate(manifest, supplied={}, requested={requested})
        [invocation] = plan.invocations
        [binding] = invocation.requested_output_bindings
        self.assertEqual(produced, binding.declared_artifact_id)
        self.assertEqual(requested, binding.resolved_artifact_id)
        self.assertTrue(binding.terminal_remapped)

    async def test_bounded_phase_requirement_selects_highest_producer_not_exceeding_bound(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_100 -> transcript//token_text#100
        #     stage_200 -> transcript//token_text#200
        #
        #   EvaluatedPlan:
        #     stage_100 -> transcript//token_text#100
        #
        selector = ArtifactKey.parse("transcript//token_text#150")
        phase_100 = ArtifactKey.parse("transcript//token_text#100")
        phase_200 = ArtifactKey.parse("transcript//token_text#200")
        artifacts = {
            phase_100: _decl(phase_100, ArtifactRole.INTERMEDIATE),
            phase_200: _decl(phase_200, ArtifactRole.INTERMEDIATE),
        }
        stage_100 = _stage_registration(
            stage_id="stage_100",
            input_ids=[],
            output_ids=[phase_100],
            requirements=lambda _ctx: (),
        )
        stage_200 = _stage_registration(
            stage_id="stage_200",
            input_ids=[],
            output_ids=[phase_200],
            requirements=lambda _ctx: (),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_100": stage_100, "stage_200": stage_200},
        )

        plan = await _evaluate(manifest, supplied={}, requested={selector})
        self.assertEqual(["stage_100"], [inv.registration.stage_id for inv in plan.invocations])
        self.assertEqual((phase_100,), plan.invocations[0].requested_output_ids)

    async def test_phase_less_consumer_requirement_uses_terminal_phase_and_remaps_input_to_family_id(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     | stage_100 | -> transcript//token_text#100 -> | stage_201 | -> transcript//token_text#201 >> transcript//token_text -> | stage_render | -> out_a
        #
        #   EvaluatedPlan:
        #     | stage_100 | -> transcript//token_text#100 -> | stage_201 | -> transcript//token_text -> | stage_render | -> out_a
        #
        completed = ArtifactKey.parse("transcript//token_text")
        phase_100 = ArtifactKey.parse("transcript//token_text#100")
        phase_201 = ArtifactKey.parse("transcript//token_text#201")
        out_a = _artifact_key("out_a")
        artifacts = {
            phase_100: _decl(phase_100, ArtifactRole.INTERMEDIATE),
            phase_201: _decl(phase_201, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        stage_100 = _stage_registration(
            stage_id="stage_100",
            input_ids=[],
            output_ids=[phase_100],
            requirements=lambda _ctx: (),
        )
        stage_201 = _stage_registration(
            stage_id="stage_201",
            input_ids=[phase_100],
            output_ids=[phase_201],
            requirements=lambda _ctx: (ArtifactRequirement(phase_100, phase_match=PhaseMatchMode.EXACT),),
        )
        renderer = _stage_registration(
            stage_id="stage_render",
            input_ids=[completed],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(completed),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_100": stage_100, "stage_201": stage_201, "stage_render": renderer},
        )

        plan = await _evaluate(manifest, supplied={}, requested={out_a})

        self.assertEqual(
            ["stage_100", "stage_201", "stage_render"],
            [inv.registration.stage_id for inv in plan.invocations],
        )
        terminal_binding = plan.invocations[1].requested_output_bindings[0]
        self.assertEqual(phase_201, terminal_binding.declared_artifact_id)
        self.assertEqual(completed, terminal_binding.resolved_artifact_id)
        self.assertTrue(terminal_binding.terminal_remapped)
        render_input = plan.invocations[2].input_bindings[0]
        self.assertEqual(completed, render_input.resolved_artifact_id)

    async def test_bounded_phase_input_requirement_selects_highest_available_phase_not_exceeding_bound(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_100 -> transcript//token_text#100
        #     stage_150 -> transcript//token_text#150
        #     stage_201 -> transcript//token_text#201
        #     transcript//token_text#200 -> stage_consumer -> out_a
        #
        #   EvaluatedPlan:
        #     stage_150 -> transcript//token_text#150 -> stage_consumer -> out_a
        #
        selector = ArtifactKey.parse("transcript//token_text#200")
        phase_100 = ArtifactKey.parse("transcript//token_text#100")
        phase_150 = ArtifactKey.parse("transcript//token_text#150")
        phase_201 = ArtifactKey.parse("transcript//token_text#201")
        out_a = _artifact_key("out_a")
        artifacts = {
            phase_100: _decl(phase_100, ArtifactRole.INTERMEDIATE),
            phase_150: _decl(phase_150, ArtifactRole.INTERMEDIATE),
            phase_201: _decl(phase_201, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        stage_100 = _stage_registration(
            stage_id="stage_100",
            input_ids=[],
            output_ids=[phase_100],
            requirements=lambda _ctx: (),
        )
        stage_150 = _stage_registration(
            stage_id="stage_150",
            input_ids=[],
            output_ids=[phase_150],
            requirements=lambda _ctx: (),
        )
        stage_201 = _stage_registration(
            stage_id="stage_201",
            input_ids=[],
            output_ids=[phase_201],
            requirements=lambda _ctx: (),
        )
        consumer = _stage_registration(
            stage_id="stage_consumer",
            input_ids=[selector],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(selector),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                "stage_100": stage_100,
                "stage_150": stage_150,
                "stage_201": stage_201,
                "stage_consumer": consumer,
            },
        )

        plan = await _evaluate(manifest, supplied={}, requested={out_a})

        self.assertEqual(
            ["stage_150", "stage_consumer"],
            [inv.registration.stage_id for inv in plan.invocations],
        )
        self.assertEqual((phase_150,), plan.invocations[1].required_input_ids)

    async def test_exact_phase_request_does_not_fall_back_to_lower_phase(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_100 -> transcript//token_text#100
        #
        #   EvaluatedPlan:
        #     StagePlanningError; exact transcript//token_text#150 is unavailable.
        #
        requested = ArtifactKey.parse("transcript//token_text#150")
        phase_100 = ArtifactKey.parse("transcript//token_text#100")
        artifacts = {phase_100: _decl(phase_100, ArtifactRole.INTERMEDIATE)}
        stage = _stage_registration(
            stage_id="stage_100",
            input_ids=[],
            output_ids=[phase_100],
            requirements=lambda _ctx: (),
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})

        # Top-level concrete phase requests are exact. A lower available phase is
        # valid for normal bounded requirements but not for an exact request.
        with self.assertRaisesRegex(StagePlanningError, "Configured stages do not produce"):
            await _evaluate(manifest, supplied={}, requested={requested})


class PlannerEvaluationOutputSubsetTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_stage_output_subset_expands_and_requirements_are_re_resolved(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> | stage_ab | -> out_a
        #     in_b -> |          | -> out_b
        #
        #   EvaluatedPlan:
        #     in_a -> | stage_ab | -> out_a
        #     in_b -> |          | -> out_b
        #
        in_a = _artifact_key("in_a")
        in_b = _artifact_key("in_b")
        out_a = _artifact_key("out_a")
        out_b = _artifact_key("out_b")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            in_b: _decl(in_b, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
            out_b: _decl(out_b, ArtifactRole.TARGET),
        }

        def requirements(context: StagePlanningContext) -> tuple[ArtifactRequirement, ...]:
            result: list[ArtifactRequirement] = []
            if out_a in context.requested_outputs:
                result.append(ArtifactRequirement(in_a))
            if out_b in context.requested_outputs:
                result.append(ArtifactRequirement(in_b))
            return tuple(result)

        stage = _stage_registration(
            stage_id="stage_ab",
            input_ids=[in_a, in_b],
            output_ids=[out_a, out_b],
            requirements=requirements,
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})
        supplied = {
            in_a: _source_instance(artifacts[in_a], value="a"),
            in_b: _source_instance(artifacts[in_b], value="b"),
        }

        plan = await _evaluate(manifest, supplied=supplied, requested={out_a, out_b})
        self.assertEqual(1, len(plan.invocations))
        invocation = plan.invocations[0]
        self.assertEqual({out_a, out_b}, set(invocation.requested_output_ids))
        self.assertEqual({in_a, in_b}, set(invocation.required_input_ids))

    async def test_supplied_target_does_not_prune_shared_stage_needed_for_other_target(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> | stage_ab | -> out_a
        #                         -> out_b
        #
        #   EvaluatedPlan:
        #     out_a_supplied
        #     in_a -> | stage_ab | -> out_b
        #
        in_a = _artifact_key("in_a")
        out_a = _artifact_key("out_a")
        out_b = _artifact_key("out_b")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
            out_b: _decl(out_b, ArtifactRole.TARGET),
        }
        stage = _stage_registration(
            stage_id="stage_ab",
            input_ids=[in_a],
            output_ids=[out_a, out_b],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})
        supplied = {
            in_a: _source_instance(artifacts[in_a], value="source-a"),
            out_a: _source_instance(artifacts[out_a], value="caller-supplied-out-a"),
        }

        plan = await _evaluate(manifest, supplied=supplied, requested={out_a, out_b})

        self.assertEqual(1, len(plan.invocations))
        [invocation] = plan.invocations
        self.assertEqual("stage_ab", invocation.registration.stage_id)
        self.assertEqual((out_b,), invocation.requested_output_ids)
        self.assertEqual(
            {
                RequestedOutputBinding(out_a, f"{out_a}"),
                RequestedOutputBinding(out_b, invocation.requested_output_bindings[0].instance_id),
            },
            set(plan.requested_output_bindings),
        )

    async def test_shared_stage_output_subset_expansion_keeps_latest_resource_policy(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_ab -> out_a
        #     stage_ab -> out_b
        #
        #   EvaluatedPlan:
        #     stage_ab -> out_a
        #     stage_ab -> out_b
        #
        out_a = _artifact_key("out_a")
        out_b = _artifact_key("out_b")
        artifacts = {
            out_a: _decl(out_a, ArtifactRole.TARGET),
            out_b: _decl(out_b, ArtifactRole.TARGET),
        }

        def resource_policy(context: StagePlanningContext) -> ResourcePolicy:
            if out_b in context.requested_outputs:
                return ResourcePolicy(frozenset({"gpu"}))
            return ResourcePolicy(frozenset({"cpu"}))

        stage = _stage_registration(
            stage_id="stage_ab",
            input_ids=[],
            output_ids=[out_a, out_b],
            requirements=lambda _ctx: (),
            resource_resolver=resource_policy,
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})

        plan = await _evaluate(manifest, supplied={}, requested={out_a, out_b})

        self.assertEqual(1, len(plan.invocations))
        invocation = plan.invocations[0]
        self.assertEqual({out_a, out_b}, set(invocation.requested_output_ids))
        self.assertEqual(ResourcePolicy(frozenset({"gpu"})), invocation.resolved_resource_policy)


class PlannerEvaluationProducerSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_producer_choice_with_no_valid_paths_raises_unavailable(self) -> None:
        in_a = _artifact_key("in_a")
        in_b = _artifact_key("in_b")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            in_b: _decl(in_b, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        stage_a = _stage_registration(
            stage_id="stage_a",
            input_ids=[in_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        stage_b = _stage_registration(
            stage_id="stage_b",
            input_ids=[in_b],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_b),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_a": stage_a, "stage_b": stage_b},
        )

        with self.assertRaisesRegex(ProducerPathUnavailable, "no valid producer path"):
            await _evaluate(manifest, supplied={}, requested={out_a})

    async def test_resolves_only_valid_ambiguous_producer_choice(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> stage_a -> out_a
        #     in_b -> stage_b -> out_a
        #
        #   EvaluatedPlan:
        #     in_a -> stage_a -> out_a
        #
        # in_b is not supplied, so stage_b is not a valid producer path. The
        # planner should fall back to the other producer candidate whose required
        # input path can be satisfied.
        in_a = _artifact_key("in_a")
        in_b = _artifact_key("in_b")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            in_b: _decl(in_b, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        stage_a = _stage_registration(
            stage_id="stage_a",
            input_ids=[in_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        stage_b = _stage_registration(
            stage_id="stage_b",
            input_ids=[in_b],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_b),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_a": stage_a, "stage_b": stage_b},
        )
        supplied = {in_a: _source_instance(artifacts[in_a], value="source-a")}

        plan = await _evaluate(manifest, supplied=supplied, requested={out_a})

        self.assertEqual(["stage_a"], [inv.registration.stage_id for inv in plan.invocations])
        [stage_a_invocation] = plan.invocations
        self.assertEqual((in_a,), stage_a_invocation.required_input_ids)

    async def test_explicit_default_producer_preference_selects_one_of_multiple_candidates(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     cheap_stage -> out_a
        #     preferred_stage -> out_a
        #
        #   EvaluatedPlan:
        #     preferred_stage -> out_a
        #
        out_a = _artifact_key("out_a")
        artifacts = {out_a: _decl(out_a, ArtifactRole.TARGET)}
        cheap = _stage_registration(
            stage_id="cheap_stage",
            input_ids=[],
            output_ids=[out_a],
            requirements=lambda _ctx: (),
        )
        preferred = _stage_registration(
            stage_id="preferred_stage",
            input_ids=[],
            output_ids=[out_a],
            requirements=lambda _ctx: (),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"cheap_stage": cheap, "preferred_stage": preferred},
            default_producers={out_a: "preferred_stage"},
        )

        plan = await _evaluate(manifest, supplied={}, requested={out_a})
        self.assertEqual(["preferred_stage"], [inv.registration.stage_id for inv in plan.invocations])


class SpeculativeGraphBranchTests(unittest.TestCase):
    def _build_probe_fixture(
        self,
        *,
        supplied: Mapping[ArtifactKey, ArtifactInstance[Any]],
    ) -> tuple[
        TaskRequestContext,
        PlannerSettingsResolver,
        ProducerSelector,
        GraphBuildState,
        ArtifactNeed,
        ArtifactKey,
    ]:
        source_id = _artifact_key("in_a")
        optional_id = _artifact_key("int_optional")
        output_id = _artifact_key("out_a")
        artifacts = {
            source_id: _decl(source_id, ArtifactRole.SOURCE),
            optional_id: _decl(optional_id, ArtifactRole.INTERMEDIATE),
            output_id: _decl(output_id, ArtifactRole.TARGET),
        }
        optional_stage = _stage_registration(
            stage_id="stage_optional",
            input_ids=[source_id],
            output_ids=[optional_id],
            requirements=lambda _ctx: (ArtifactRequirement(source_id),),
        )
        consumer = _stage_registration(
            stage_id="stage_consumer",
            input_ids=[optional_id],
            output_ids=[output_id],
            requirements=lambda _ctx: (ArtifactRequirement(optional_id, required=False),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                optional_stage.stage_id: optional_stage,
                consumer.stage_id: consumer,
            },
        )
        context = TaskRequestContext(
            manifest=manifest,
            manifest_graph=StageManifestGraphBuilder(manifest).build(),
            requested_output_ids=(),
            supplied_instances=dict(supplied),
            policy=PlanningRunPolicy(),
            request_settings=PlannerRequestSettings(
                ResolvedSettingsView(),
                ResolvedSettingsView(),
            ),
        )
        state = GraphBuildState()
        state.invocations_by_key["stage_consumer@1"] = InvocationDraft(
            key="stage_consumer@1",
            registration=manifest.find_stage("stage_consumer"),
            inherited_settings=(),
            effective_settings_fingerprint="",
        )
        need = ArtifactNeed(
            requirement=ArtifactRequirement(optional_id, required=False),
            consumer_invocation_key="stage_consumer@1",
        )
        return (
            context,
            PlannerSettingsResolver(manifest),
            ProducerSelector(manifest),
            state,
            need,
            source_id,
        )

    def test_unresolved_optional_probe_discard_does_not_mutate_parent_state(self) -> None:
        context, resolver, selector, state, need, _source_id = self._build_probe_fixture(
            supplied={}
        )
        probe = self._branch(context, resolver, selector, state)

        with self.assertRaisesRegex(ProducerPathUnavailable, "no supplied stage can satisfy"):
            probe.expand_from_requirement(need, force_required=True)

        probe.discard()
        self.assertEqual({"stage_consumer@1"}, set(state.invocations_by_key))
        self.assertIsNone(state.input_binding_for(need))

    def test_resolved_optional_probe_commits_and_records_binding(self) -> None:
        source_id = _artifact_key("in_a")
        (
            context,
            resolver,
            selector,
            state,
            need,
            source_id,
        ) = self._build_probe_fixture(
            supplied={
                source_id: _source_instance(
                    _decl(source_id, ArtifactRole.SOURCE),
                    value="source-a",
                )
            }
        )
        probe = self._branch(context, resolver, selector, state)

        probe.expand_from_requirement(need, force_required=True)
        binding = probe.resolved_input_binding(need)

        self.assertTrue(binding.is_resolved)
        self.assertEqual(need.requirement.artifact_id, binding.resolved_artifact_id)
        self.assertIsNotNone(binding.producer_invocation_key)
        self.assertIsNone(state.input_binding_for(need))
        self.assertIsNone(probe.commit())

        committed = state.input_binding_for(need)
        self.assertIsNotNone(committed)
        assert committed is not None
        self.assertEqual(binding, ArtifactResolution.from_input_binding(committed))
        self.assertEqual(
            {"stage_consumer@1", "stage_optional@1"},
            set(state.invocations_by_key),
        )

    def test_closed_probe_raises_on_reuse(self) -> None:
        source_id = _artifact_key("in_a")
        (
            context,
            resolver,
            selector,
            state,
            need,
            source_id,
        ) = self._build_probe_fixture(
            supplied={
                source_id: _source_instance(
                    _decl(source_id, ArtifactRole.SOURCE),
                    value="source-a",
                )
            }
        )
        probe = self._branch(context, resolver, selector, state)
        probe.expand_from_requirement(need, force_required=True)
        probe.commit()

        for action in (
            lambda: probe.commit(),
            lambda: probe.discard(),
            lambda: probe.resolved_input_binding(need),
            lambda: probe.expand_from_requirement(need, force_required=True),
            lambda: probe.expand_from_binding(
                ArtifactResolution.produced(
                    need.requirement.artifact_id,
                    "graph//int_optional",
                    "stage_optional@1",
                )
            ),
            lambda: probe.expand_from_candidate(
                need,
                StageProducer("stage_optional", need.requirement.artifact_id),
            ),
        ):
            with self.assertRaisesRegex(StagePlanningError, "speculative planning branch has already been closed"):
                action()

    def test_discarded_probe_raises_on_reuse(self) -> None:
        context, resolver, selector, state, need, _source_id = self._build_probe_fixture(
            supplied={}
        )
        probe = self._branch(context, resolver, selector, state)
        probe.discard()

        for action in (
            lambda: probe.commit(),
            lambda: probe.discard(),
            lambda: probe.resolved_input_binding(need),
            lambda: probe.expand_from_requirement(need, force_required=True),
            lambda: probe.expand_from_candidate(
                need,
                StageProducer("stage_optional", need.requirement.artifact_id),
            ),
        ):
            with self.assertRaisesRegex(StagePlanningError, "speculative planning branch has already been closed"):
                action()

    def _branch(
        self,
        context: TaskRequestContext,
        resolver: PlannerSettingsResolver,
        selector: ProducerSelector,
        state: GraphBuildState,
    ) -> SpeculativeGraphBranch:
        return SpeculativeGraphBranch(
            context,
            resolver,
            selector,
            state,
            lambda work_queue, initial_state: StageManifestGraphWalker(
                context,
                resolver,
                selector,
                work_queue,
                initial_state=initial_state,
            ).expand(),
        )


class ArtifactRequirementResolverSpeculativeBranchTests(unittest.TestCase):
    def test_single_valid_ambiguous_candidate_commits_without_leaking_rejected_branch(self) -> None:
        in_a = _artifact_key("in_a")
        in_b = _artifact_key("in_b")
        int_a = _artifact_key("int_a")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            in_b: _decl(in_b, ArtifactRole.SOURCE),
            int_a: _decl(int_a, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        valid = _stage_registration(
            stage_id="stage_valid",
            input_ids=[in_a],
            output_ids=[int_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        invalid = _stage_registration(
            stage_id="stage_invalid",
            input_ids=[in_b],
            output_ids=[int_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_b),),
        )
        consumer = _stage_registration(
            stage_id="stage_consumer",
            input_ids=[int_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(int_a),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                valid.stage_id: valid,
                invalid.stage_id: invalid,
                consumer.stage_id: consumer,
            },
        )
        context = TaskRequestContext(
            manifest=manifest,
            manifest_graph=StageManifestGraphBuilder(manifest).build(),
            requested_output_ids=(),
            supplied_instances={
                in_a: _source_instance(artifacts[in_a], value="source-a"),
            },
            policy=PlanningRunPolicy(),
            request_settings=PlannerRequestSettings(
                ResolvedSettingsView(),
                ResolvedSettingsView(),
            ),
        )
        state = GraphBuildState()
        state.invocations_by_key["stage_consumer@1"] = InvocationDraft(
            key="stage_consumer@1",
            registration=manifest.find_stage("stage_consumer"),
            inherited_settings=(),
            effective_settings_fingerprint="",
        )
        need = ArtifactNeed(
            requirement=ArtifactRequirement(int_a),
            consumer_invocation_key="stage_consumer@1",
        )
        resolver = ArtifactRequirementResolver(
            context,
            PlannerSettingsResolver(manifest),
            ProducerSelector(manifest),
            state,
        )

        binding = resolver.resolve(need)

        self.assertEqual("stage_valid@1", binding.producer_invocation_key)
        self.assertEqual({"stage_consumer@1", "stage_valid@1"}, set(state.invocations_by_key))
        self.assertNotIn("stage_invalid@1", state.invocations_by_key)
        self.assertIsNone(state.input_binding_for(need))

    def test_multiple_valid_ambiguous_candidates_discard_all_candidate_mutations(self) -> None:
        in_a = _artifact_key("in_a")
        in_b = _artifact_key("in_b")
        int_a = _artifact_key("int_a")
        out_a = _artifact_key("out_a")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            in_b: _decl(in_b, ArtifactRole.SOURCE),
            int_a: _decl(int_a, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
        }
        stage_a = _stage_registration(
            stage_id="stage_a",
            input_ids=[in_a],
            output_ids=[int_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        stage_b = _stage_registration(
            stage_id="stage_b",
            input_ids=[in_b],
            output_ids=[int_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_b),),
        )
        consumer = _stage_registration(
            stage_id="stage_consumer",
            input_ids=[int_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(int_a),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                stage_a.stage_id: stage_a,
                stage_b.stage_id: stage_b,
                consumer.stage_id: consumer,
            },
        )
        context = TaskRequestContext(
            manifest=manifest,
            manifest_graph=StageManifestGraphBuilder(manifest).build(),
            requested_output_ids=(),
            supplied_instances={
                in_a: _source_instance(artifacts[in_a], value="source-a"),
                in_b: _source_instance(artifacts[in_b], value="source-b"),
            },
            policy=PlanningRunPolicy(),
            request_settings=PlannerRequestSettings(
                ResolvedSettingsView(),
                ResolvedSettingsView(),
            ),
        )
        state = GraphBuildState()
        state.invocations_by_key["stage_consumer@1"] = InvocationDraft(
            key="stage_consumer@1",
            registration=manifest.find_stage("stage_consumer"),
            inherited_settings=(),
            effective_settings_fingerprint="",
        )
        need = ArtifactNeed(
            requirement=ArtifactRequirement(int_a),
            consumer_invocation_key="stage_consumer@1",
        )
        resolver = ArtifactRequirementResolver(
            context,
            PlannerSettingsResolver(manifest),
            ProducerSelector(manifest),
            state,
        )

        with self.assertRaisesRegex(AmbiguousProducerSelection, "ambiguous producers") as caught:
            resolver.resolve(need)

        self.assertEqual(
            ("stage_a", "stage_b"),
            tuple(item.candidate.stage_id for item in caught.exception.valid_candidates),
        )
        self.assertEqual((), caught.exception.invalid_candidates)
        self.assertEqual({"stage_consumer@1"}, set(state.invocations_by_key))
        self.assertIsNone(state.input_binding_for(need))

class PlannerEvaluationDynamicOutputSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_outputs_can_narrow_advertised_outputs_to_requested_subset(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_multi -> out_a
        #     stage_multi -> out_b
        #
        #   EvaluatedPlan:
        #     stage_multi -> out_a
        #
        out_a = _artifact_key("out_a")
        out_b = _artifact_key("out_b")
        artifacts = {
            out_a: _decl(out_a, ArtifactRole.TARGET),
            out_b: _decl(out_b, ArtifactRole.TARGET),
        }

        def output_resolver(context: StagePlanningContext) -> tuple[ArtifactCapability, ...]:
            return tuple(ArtifactCapability(output) for output in sorted(context.requested_outputs, key=str))

        stage = _stage_registration(
            stage_id="stage_multi",
            input_ids=[],
            output_ids=[out_a, out_b],
            requirements=lambda _ctx: (),
            output_resolver=output_resolver,
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})

        plan = await _evaluate(manifest, supplied={}, requested={out_a})

        [invocation] = plan.invocations
        self.assertEqual((out_a,), invocation.requested_output_ids)
        self.assertNotIn(out_b, invocation.requested_output_ids)

    async def test_incidental_output_requirement_is_not_forced_when_incidental_output_is_not_requested(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -------------> | stage_with_incidental_output | -> out_a
        #     diagnostic_input -> |                              | -> diagnostic_out
        #
        #   EvaluatedPlan:
        #     in_a -> | stage_with_incidental_output | -> out_a
        #
        in_a = _artifact_key("in_a")
        diagnostic_input = _artifact_key("diagnostic_input")
        out_a = _artifact_key("out_a")
        diagnostic_out = _artifact_key("diagnostic_out")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            diagnostic_input: _decl(diagnostic_input, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
            diagnostic_out: _decl(diagnostic_out, ArtifactRole.DIAGNOSTIC),
        }

        def requirements(context: StagePlanningContext) -> tuple[ArtifactRequirement, ...]:
            inputs = [ArtifactRequirement(in_a)]
            if diagnostic_out in context.requested_outputs:
                inputs.append(ArtifactRequirement(diagnostic_input))
            return tuple(inputs)

        stage = _stage_registration(
            stage_id="stage_with_incidental_output",
            input_ids=[in_a, diagnostic_input],
            output_ids=[out_a, diagnostic_out],
            requirements=requirements,
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})
        supplied = {in_a: _source_instance(artifacts[in_a], value="source-a")}

        plan = await _evaluate(manifest, supplied=supplied, requested={out_a})

        [invocation] = plan.invocations
        self.assertEqual((out_a,), invocation.requested_output_ids)
        self.assertEqual((in_a,), invocation.required_input_ids)


class PlannerEvaluationSettingsPrecedenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_configured_base_setting_resolves_through_supplied_artifact_path(self) -> None:
        in_a = _artifact_key("in_a")
        out_a = _artifact_key("out_a")
        base_setting = ArtifactKey.parse("settings//mode")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
            base_setting: _decl(base_setting, ArtifactRole.SETTINGS),
        }
        stage = _stage_registration(
            stage_id="stage_consumer",
            input_ids=[in_a, base_setting],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a), ArtifactRequirement(base_setting)),
        )
        manifest = _manifest(artifacts=artifacts, stages={stage.stage_id: stage})
        supplied = {
            in_a: _source_instance(artifacts[in_a], value="source"),
            base_setting: _source_instance(artifacts[base_setting], value={"mode": "user"}),
        }

        plan = await _evaluate(
            manifest,
            supplied=supplied,
            requested={out_a},
            user_supplied_settings={base_setting},
        )

        [invocation] = plan.invocations
        self.assertEqual({in_a, base_setting}, set(invocation.required_input_ids))
        self.assertEqual({"mode": "user"}, invocation.resolved_settings.require(base_setting))

    async def test_inherited_setting_wins_over_user_configured_base_setting(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #               in_a -> | stage_producer | -> int_a -> | stage_consumer | -> out_a
        #     settings//mode -> |                |

        #     downstream//settings//mode is pushed from stage_consumer to the producer path.
        #
        #   EvaluatedPlan:
        #                     in_a -> | stage_producer | -> int_a -> | stage_consumer | -> out_a
        #     settings//mode(user) -> |                |
        #
        # The inherited downstream override wins over the user-configured base
        # setting for the upstream producer path.

        in_a = _artifact_key("in_a")
        int_a = _artifact_key("int_a")
        out_a = _artifact_key("out_a")
        base_setting = ArtifactKey.parse("settings//mode")
        inherited_override = ArtifactKey.parse("downstream//settings//mode")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            int_a: _decl(int_a, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
            base_setting: _decl(base_setting, ArtifactRole.SETTINGS),
            inherited_override: _decl(inherited_override, ArtifactRole.SETTINGS),
        }
        producer = _stage_registration(
            stage_id="stage_producer",
            input_ids=[in_a, base_setting],
            output_ids=[int_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a), ArtifactRequirement(base_setting)),
        )
        consumer = _stage_registration(
            stage_id="stage_consumer",
            input_ids=[int_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(int_a, pushed_settings=(inherited_override,)),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"stage_producer": producer, "stage_consumer": consumer},
        )
        supplied = {
            in_a: _source_instance(artifacts[in_a], value="source"),
            base_setting: _source_instance(artifacts[base_setting], value={"mode": "user"}),
            inherited_override: _source_instance(artifacts[inherited_override], value={"mode": "inherited"}),
        }

        plan = await _evaluate(
            manifest,
            supplied=supplied,
            requested={out_a},
            user_supplied_settings={base_setting, inherited_override},
        )

        producer_invocations = [inv for inv in plan.invocations if inv.registration.stage_id == "stage_producer"]
        self.assertEqual(1, len(producer_invocations))
        self.assertEqual({"mode": "inherited"}, producer_invocations[0].resolved_settings.require(base_setting))

    async def test_pass_through_settings_do_not_get_dropped_when_current_stage_effective_settings_match(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a + settings//audio -> stage_prepare -> prepared -> | stage_route |
        #                                                            |             | -> route_a -> stage_out_a -> out_a
        #                                                            |             | -> route_b -> stage_out_b -> out_b
        #
        #   EvaluatedPlan:
        #     asr//settings//audio path:
        #       in_a -> | stage_prepare(asr) | -> prepared(asr) -> | stage_route(asr) | -> route_a -> | stage_out_a | -> out_a
        #
        #     diarization//settings//audio path:
        #       in_a -> | stage_prepare(diarization) | -> prepared(diarization) -> | stage_route(diarization) | -> route_b -> stage_out_b -> out_b
        #
        # The route stage does not consume the audio setting itself, but it must
        # pass the inherited setting upstream to stage_prepare.
        source = _artifact_key("in_a")
        prepared = _artifact_key("prepared")
        route_a = _artifact_key("route_a")
        route_b = _artifact_key("route_b")
        out_a = _artifact_key("out_a")
        out_b = _artifact_key("out_b")
        audio_settings = ArtifactKey.parse("settings//audio")
        asr_override = ArtifactKey.parse("asr//settings//audio")
        diar_override = ArtifactKey.parse("diarization//settings//audio")
        artifacts = {
            source: _decl(source, ArtifactRole.SOURCE),
            prepared: _decl(prepared, ArtifactRole.INTERMEDIATE),
            route_a: _decl(route_a, ArtifactRole.INTERMEDIATE),
            route_b: _decl(route_b, ArtifactRole.INTERMEDIATE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
            out_b: _decl(out_b, ArtifactRole.TARGET),
            audio_settings: _decl(audio_settings, ArtifactRole.SETTINGS),
            asr_override: _decl(asr_override, ArtifactRole.SETTINGS),
            diar_override: _decl(diar_override, ArtifactRole.SETTINGS),
        }
        prepare = _stage_registration(
            stage_id="stage_prepare",
            input_ids=[source, audio_settings],
            output_ids=[prepared],
            requirements=lambda _ctx: (ArtifactRequirement(source), ArtifactRequirement(audio_settings)),
        )

        def route_requirements(_context: StagePlanningContext) -> tuple[ArtifactRequirement, ...]:
            # The route stage deliberately does not consume audio settings. Any
            # inherited setting reaching it must pass through to its prepared-audio
            # requirement so the upstream producer is planned under the right context.
            return (ArtifactRequirement(prepared),)

        route = _stage_registration(
            stage_id="stage_route",
            input_ids=[prepared],
            output_ids=[route_a, route_b],
            requirements=route_requirements,
        )
        out_a_stage = _stage_registration(
            stage_id="stage_out_a",
            input_ids=[route_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(route_a, pushed_settings=(asr_override,)),),
        )
        out_b_stage = _stage_registration(
            stage_id="stage_out_b",
            input_ids=[route_b],
            output_ids=[out_b],
            requirements=lambda _ctx: (ArtifactRequirement(route_b, pushed_settings=(diar_override,)),),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={
                "stage_prepare": prepare,
                "stage_route": route,
                "stage_out_a": out_a_stage,
                "stage_out_b": out_b_stage,
            },
        )
        supplied = {
            source: _source_instance(artifacts[source], value="source-audio"),
            audio_settings: _source_instance(artifacts[audio_settings], value={"profile": "default"}),
            asr_override: _source_instance(artifacts[asr_override], value={"profile": "asr"}),
            diar_override: _source_instance(artifacts[diar_override], value={"profile": "diarization"}),
        }

        plan = await _evaluate(
            manifest,
            supplied=supplied,
            requested={out_a, out_b},
            user_supplied_settings={asr_override, diar_override},
        )

        stage_counts = {
            stage_id: sum(1 for inv in plan.invocations if inv.registration.stage_id == stage_id)
            for stage_id in {"stage_prepare", "stage_route", "stage_out_a", "stage_out_b"}
        }
        self.assertEqual(
            {"stage_prepare": 2, "stage_route": 2, "stage_out_a": 1, "stage_out_b": 1},
            stage_counts,
        )
        prepare_settings = {
            tuple(inv.resolved_settings.require(audio_settings).items())
            for inv in plan.invocations
            if inv.registration.stage_id == "stage_prepare"
        }
        self.assertEqual(
            {("profile", "asr"), ("profile", "diarization")},
            prepare_settings,
        )

class PlannerEvaluationDisabledStagePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_output_supplied_as_completed_intermediate_is_valid_no_op(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     disabled_stage -x-> int_a
        #     int_a_supplied
        #
        #   EvaluatedPlan:
        #     int_a_supplied; no stage invocations are needed.
        #
        int_a = _artifact_key("int_a")
        artifacts = {int_a: _decl(int_a, ArtifactRole.INTERMEDIATE)}
        disabled_stage = _stage_registration(
            stage_id="disabled_stage",
            input_ids=[],
            output_ids=[int_a],
            requirements=lambda _ctx: (),
            output_resolver=lambda _ctx: (),
        )
        manifest = _manifest(artifacts=artifacts, stages={disabled_stage.stage_id: disabled_stage})
        supplied = {int_a: _source_instance(artifacts[int_a], value="already-available")}

        plan = await _evaluate(manifest, supplied=supplied, requested={int_a})

        self.assertEqual((), plan.invocations)
        self.assertEqual((int_a,), plan.requested_output_ids)

    async def test_disabled_required_output_unavailable_fails_planning(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     disabled_stage -x-> out_a
        #
        #   EvaluatedPlan:
        #     StagePlanningError; the disabled stage cannot expose out_a.
        #
        out_a = _artifact_key("out_a")
        artifacts = {out_a: _decl(out_a, ArtifactRole.TARGET)}
        disabled_stage = _stage_registration(
            stage_id="disabled_stage",
            input_ids=[],
            output_ids=[out_a],
            requirements=lambda _ctx: (),
            output_resolver=lambda _ctx: (),
        )
        manifest = _manifest(artifacts=artifacts, stages={disabled_stage.stage_id: disabled_stage})

        with self.assertRaisesRegex(StagePlanningError, "cannot expose requested outputs"):
            await _evaluate(manifest, supplied={}, requested={out_a})

    async def test_disabled_irrelevant_branch_is_pruned(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> live_stage -> out_a
        #     disabled_stage -x-> out_disabled
        #
        #   EvaluatedPlan:
        #     in_a -> live_stage -> out_a
        #
        in_a = _artifact_key("in_a")
        out_a = _artifact_key("out_a")
        out_disabled = _artifact_key("out_disabled")
        artifacts = {
            in_a: _decl(in_a, ArtifactRole.SOURCE),
            out_a: _decl(out_a, ArtifactRole.TARGET),
            out_disabled: _decl(out_disabled, ArtifactRole.TARGET),
        }
        live = _stage_registration(
            stage_id="live_stage",
            input_ids=[in_a],
            output_ids=[out_a],
            requirements=lambda _ctx: (ArtifactRequirement(in_a),),
        )
        disabled = _stage_registration(
            stage_id="disabled_stage",
            input_ids=[],
            output_ids=[out_disabled],
            requirements=lambda _ctx: (),
            output_resolver=lambda _ctx: (),
        )
        manifest = _manifest(
            artifacts=artifacts,
            stages={"live_stage": live, "disabled_stage": disabled},
        )
        supplied = {in_a: _source_instance(artifacts[in_a], value="source")}

        plan = await _evaluate(manifest, supplied=supplied, requested={out_a})

        self.assertEqual(["live_stage"], [inv.registration.stage_id for inv in plan.invocations])


class PlannerEvaluationManifestValidationTests(unittest.TestCase):
    def test_manifest_validation_rejects_static_candidate_graph_cycle(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     int_b -> stage_a -> int_a -> stage_b -> int_b
        #
        #   EvaluatedPlan:
        #     No evaluated graph is built; manifest validation rejects the cycle.
        #
        int_a = _artifact_key("int_a")
        int_b = _artifact_key("int_b")
        stage_a = _stage_registration(
            stage_id="stage_a",
            input_ids=[int_b],
            output_ids=[int_a],
            requirements=lambda _ctx: (ArtifactRequirement(int_b),),
        )
        stage_b = _stage_registration(
            stage_id="stage_b",
            input_ids=[int_a],
            output_ids=[int_b],
            requirements=lambda _ctx: (ArtifactRequirement(int_a),),
        )

        with self.assertRaisesRegex(StageManifestError, "static candidate graph contains a cycle"):
            (
                StageManifestBuilder()
                .add_artifact(_decl(int_a, ArtifactRole.INTERMEDIATE))
                .add_artifact(_decl(int_b, ArtifactRole.INTERMEDIATE))
                .add_stage(stage_a)
                .add_stage(stage_b)
                .validate()
            )

    def test_manifest_validation_rejects_duplicate_stage_id(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_a -> out_a
        #     stage_a -> out_a
        #
        #   EvaluatedPlan:
        #     No evaluated graph is built; manifest validation rejects the duplicate stage id.
        #
        out_a = _artifact_key("out_a")
        stage_1 = _stage_registration(stage_id="stage_a", input_ids=[], output_ids=[out_a], requirements=lambda _ctx: ())
        stage_2 = _stage_registration(stage_id="stage_a", input_ids=[], output_ids=[out_a], requirements=lambda _ctx: ())
        builder = StageManifestBuilder().add_artifact(_decl(out_a, ArtifactRole.TARGET)).add_stage(stage_1)

        with self.assertRaisesRegex(StageManifestError, "duplicate stage id"):
            builder.add_stage(stage_2)

    def test_manifest_validation_rejects_duplicate_conflicting_artifact_declaration(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     out_a is declared twice with conflicting codecs.
        #
        #   EvaluatedPlan:
        #     No evaluated graph is built; manifest validation rejects the conflict.
        #
        class OtherCodec(JsonishCodec):
            codec_id = "other-jsonish"

        out_a = _artifact_key("out_a")
        first = _decl(out_a, ArtifactRole.TARGET)
        conflicting = ArtifactDeclaration(
            artifact_id=out_a,
            role=ArtifactRole.TARGET,
            version=1,
            codec=OtherCodec(),
        )
        builder = StageManifestBuilder().add_artifact(first)

        with self.assertRaisesRegex(StageManifestError, "conflicting declarations"):
            builder.add_artifact(conflicting)

    def test_manifest_validation_rejects_stage_consuming_undeclared_artifact(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     in_a -> stage_a -> out_a
        #
        #   EvaluatedPlan:
        #     No evaluated graph is built; manifest validation rejects undeclared in_a.
        #
        in_a = _artifact_key("in_a")
        out_a = _artifact_key("out_a")
        stage = _stage_registration(stage_id="stage_a", input_ids=[in_a], output_ids=[out_a], requirements=lambda _ctx: ())

        with self.assertRaisesRegex(StageManifestError, "input capability references unknown artifact selector"):
            StageManifestBuilder().add_artifact(_decl(out_a, ArtifactRole.TARGET)).add_stage(stage).validate()

    def test_manifest_validation_rejects_stage_producing_undeclared_artifact(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     stage_a -> out_a
        #
        #   EvaluatedPlan:
        #     No evaluated graph is built; manifest validation rejects undeclared out_a.
        #
        out_a = _artifact_key("out_a")
        stage = _stage_registration(stage_id="stage_a", input_ids=[], output_ids=[out_a], requirements=lambda _ctx: ())

        with self.assertRaisesRegex(StageManifestError, "output capability references unknown concrete artifact"):
            StageManifestBuilder().add_stage(stage).validate()

    def test_manifest_validation_rejects_phase_on_non_intermediate_artifact(self) -> None:
        # Graph diagrams:
        #
        #   ProposedPlan:
        #     settings//mode#1 declaration
        #
        #   EvaluatedPlan:
        #     No evaluated graph is built; phased settings declarations are invalid.
        #
        with self.assertRaisesRegex(ValueError, "only intermediate artifact declarations may use phases"):
            _decl(ArtifactKey.parse("settings//mode#1"), ArtifactRole.SETTINGS)


if __name__ == "__main__":
    unittest.main()
