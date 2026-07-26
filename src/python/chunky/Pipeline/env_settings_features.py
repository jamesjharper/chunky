from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from ..Stages.stage_manifest import StageManifestBuilder

if TYPE_CHECKING:
    from .service_features import PipelineRuntimeServicesBuilder

_MISSING = object()


@dataclass(frozen=True)
# TODO(runtime-naming): Rename PipelineRuntimeSettings and its builder to
# RuntimeSettings and RuntimeSettingsBuilder to match specs/01-runtime.
class PipelineRuntimeSettings:
    values: Mapping[str, object]
    _declared_values: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    _specs: Mapping[str, "RuntimeSettingSpec"] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    _active_scopes: Mapping[str, tuple["_RuntimeSettingScope", ...]] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    _required_scopes: Mapping[str, tuple["_RuntimeSettingScope", ...]] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )
    _sources: tuple["RuntimeSettingSource", ...] = field(default_factory=tuple, repr=False)

    def has(self, key: str) -> bool:
        return key in self.values

    def get(self, key: str, default: object | None = None) -> object | None:
        return self.values.get(_normalize_runtime_setting_key(key), default)

    def get_int(
        self,
        key: str,
        *,
        default: int | None = None,
    ) -> int:
        normalized = _normalize_runtime_setting_key(key)
        value = self.values.get(normalized, default)
        if value is None:
            raise RuntimeError(f"missing required runtime setting: {normalized}")
        return _coerce_runtime_setting_int(value, key=normalized)

    def get_float(
        self,
        key: str,
        *,
        default: float | None = None,
    ) -> float:
        normalized = _normalize_runtime_setting_key(key)
        value = self.values.get(normalized, default)
        if value is None:
            raise RuntimeError(f"missing required runtime setting: {normalized}")
        return _coerce_runtime_setting_float(value, key=normalized)

    def get_bool(self, key: str) -> bool:
        normalized = _normalize_runtime_setting_key(key)
        value = self.values.get(normalized, _MISSING)
        if value is _MISSING:
            raise RuntimeError(f"missing required runtime setting: {normalized}")
        return _coerce_runtime_setting_bool(value, normalized)

    def get_string(self, key: str) -> str:
        normalized = _normalize_runtime_setting_key(key)
        value = self.values.get(normalized, _MISSING)
        if value is _MISSING:
            raise RuntimeError(f"missing required runtime setting: {normalized}")
        return _coerce_runtime_setting_string(value, normalized)

    def resolve_for_stages(
        self,
        stage_ids: Iterable[str],
    ) -> "PipelineRuntimeSettings":
        active_stage_ids = _normalize_stage_ids(stage_ids) or frozenset()
        return self._build_settings(
            active_stage_ids=active_stage_ids,
            include_sources=True,
            enforce_required=True,
        )

    def describe_env_var(self, key: str) -> str | None:
        spec = self._specs.get(_normalize_runtime_setting_key(key))
        if spec is None:
            return None
        for source in self._sources:
            if isinstance(source, EnvironmentVariableRuntimeSettingSource):
                return source.env_name_for(spec)
        return spec.env_var

    def _build_settings(
        self,
        *,
        active_stage_ids: frozenset[str] | None,
        include_sources: bool,
        enforce_required: bool,
    ) -> "PipelineRuntimeSettings":
        values = dict(self._declared_values)
        active_specs = self._active_specs(active_stage_ids)

        for key, spec in active_specs.items():
            if spec.default is not _MISSING:
                values.setdefault(key, spec.parse_value(spec.default))

        if include_sources:
            for key, spec in active_specs.items():
                if key in values:
                    continue
                for source in self._sources:
                    resolved = source.resolve(spec)
                    if resolved is _MISSING:
                        continue
                    values[key] = spec.parse_value(resolved)
                    break

        for key, spec in active_specs.items():
            if key in values:
                values[key] = spec.parse_value(values[key])

        if enforce_required:
            missing: list[str] = []
            for key in active_specs:
                if key in values or not self._is_required(key, active_stage_ids):
                    continue
                env_name = self.describe_env_var(key)
                missing.append(key if env_name is None else f"{key} (env: {env_name})")
            if missing:
                raise RuntimeError(
                    "missing required runtime settings: " + ", ".join(sorted(missing))
                )

        return PipelineRuntimeSettings(
            values=MappingProxyType(values),
            _declared_values=self._declared_values,
            _specs=self._specs,
            _active_scopes=self._active_scopes,
            _required_scopes=self._required_scopes,
            _sources=self._sources,
        )

    def _active_specs(
        self,
        active_stage_ids: frozenset[str] | None,
    ) -> dict[str, "RuntimeSettingSpec"]:
        active: dict[str, RuntimeSettingSpec] = {}
        for key, spec in self._specs.items():
            scopes = self._active_scopes.get(key, ())
            if not scopes or any(scope.matches(active_stage_ids) for scope in scopes):
                active[key] = spec
        return active

    def _is_required(
        self,
        key: str,
        active_stage_ids: frozenset[str] | None,
    ) -> bool:
        return any(
            scope.matches(active_stage_ids)
            for scope in self._required_scopes.get(key, ())
        )

RuntimeSettingParser = Callable[[object, str], object]

@dataclass(frozen=True)
class RuntimeSettingSpec:
    key: str
    parser: RuntimeSettingParser | None = None
    default: object = _MISSING
    env_var: str | None = None
    description: str = ""

    def normalized_key(self) -> str:
        return _normalize_runtime_setting_key(self.key)

    def parse_value(self, value: object) -> object:
        normalized = self.normalized_key()
        if self.parser is None:
            return value
        return self.parser(value, normalized)

    def has_default(self) -> bool:
        return self.default is not _MISSING

    @classmethod
    def string(
        cls,
        key: str,
        *,
        default: object = _MISSING,
        env_var: str | None = None,
        description: str = "",
    ) -> "RuntimeSettingSpec":
        return cls(
            key=key,
            parser=_coerce_runtime_setting_string,
            default=default,
            env_var=env_var,
            description=description,
        )

    @classmethod
    def integer(
        cls,
        key: str,
        *,
        default: object = _MISSING,
        env_var: str | None = None,
        description: str = "",
    ) -> "RuntimeSettingSpec":
        return cls(
            key=key,
            parser=lambda value, normalized: _coerce_runtime_setting_int(
                value,
                key=normalized,
            ),
            default=default,
            env_var=env_var,
            description=description,
        )

    @classmethod
    def floating(
        cls,
        key: str,
        *,
        default: object = _MISSING,
        env_var: str | None = None,
        description: str = "",
    ) -> "RuntimeSettingSpec":
        return cls(
            key=key,
            parser=lambda value, normalized: _coerce_runtime_setting_float(
                value,
                key=normalized,
            ),
            default=default,
            env_var=env_var,
            description=description,
        )

    @classmethod
    def boolean(
        cls,
        key: str,
        *,
        default: object = _MISSING,
        env_var: str | None = None,
        description: str = "",
    ) -> "RuntimeSettingSpec":
        return cls(
            key=key,
            parser=_coerce_runtime_setting_bool,
            default=default,
            env_var=env_var,
            description=description,
        )


@dataclass(frozen=True)
class RuntimeStageSetting:
    spec: RuntimeSettingSpec
    required: bool = False


class RuntimeSettingSource(Protocol):
    def resolve(self, spec: RuntimeSettingSpec) -> object: ...

class MappingRuntimeSettingSource:
    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = {
            _normalize_runtime_setting_key(key): value for key, value in values.items()
        }

    def resolve(self, spec: RuntimeSettingSpec) -> object:
        return self._values.get(spec.normalized_key(), _MISSING)

class EnvironmentVariableRuntimeSettingSource:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        env_name_overrides: Mapping[str, str] | None = None,
        dotenv_files: Iterable[str | Path] | None = None,
    ) -> None:
        normalized_dotenv_files = tuple(dotenv_files or ())
        self._environ = dict(_load_dotenv_files(normalized_dotenv_files))
        self._environ.update(dict(os.environ if environ is None else environ))
        self._env_name_overrides = {
            _normalize_runtime_setting_key(key): value
            for key, value in (env_name_overrides or {}).items()
        }

    def resolve(self, spec: RuntimeSettingSpec) -> object:
        env_name = self.env_name_for(spec)
        if env_name is None:
            return _MISSING
        return self._environ.get(env_name, _MISSING)

    def env_name_for(self, spec: RuntimeSettingSpec) -> str | None:
        normalized = spec.normalized_key()
        override = self._env_name_overrides.get(normalized)
        if override is not None:
            return override
        if spec.env_var is not None:
            return spec.env_var
        return normalized.upper().replace(".", "_")

@dataclass(frozen=True)
class _RuntimeSettingScope:
    stage_ids: frozenset[str] | None

    def matches(self, active_stage_ids: frozenset[str] | None) -> bool:
        if self.stage_ids is None or active_stage_ids is None:
            return True
        return bool(self.stage_ids.intersection(active_stage_ids))

class PipelineRuntimeSettingsBuilder:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self._specs: dict[str, RuntimeSettingSpec] = {}
        self._active_scopes: dict[str, list[_RuntimeSettingScope]] = {}
        self._required_scopes: dict[str, list[_RuntimeSettingScope]] = {}
        self._sources: list[RuntimeSettingSource] = []
        self._sealed = False

    def set(self, key: str, value: object) -> "PipelineRuntimeSettingsBuilder":
        self._assert_not_sealed()
        normalized = _normalize_runtime_setting_key(key)
        if normalized in self._values:
            raise RuntimeError(f"duplicate runtime setting value: {normalized}")
        self._values[normalized] = value
        return self

    def install_source(
        self,
        source: RuntimeSettingSource,
    ) -> "PipelineRuntimeSettingsBuilder":
        self._assert_not_sealed()
        self._sources.append(source)
        return self

    def declare(
        self,
        spec: RuntimeSettingSpec,
        *,
        stage_ids: Iterable[str] | None = None,
    ) -> "PipelineRuntimeSettingsBuilder":
        self._assert_not_sealed()
        normalized = spec.normalized_key()
        normalized_spec = RuntimeSettingSpec(
            key=normalized,
            parser=spec.parser,
            default=spec.default,
            env_var=spec.env_var,
            description=spec.description,
        )
        existing = self._specs.get(normalized)
        if existing is not None and existing != normalized_spec:
            raise RuntimeError(f"conflicting runtime setting spec: {normalized}")
        self._specs.setdefault(normalized, normalized_spec)
        self._active_scopes.setdefault(normalized, []).append(
            _RuntimeSettingScope(_normalize_stage_ids(stage_ids))
        )
        return self

    def require(
        self,
        spec: RuntimeSettingSpec,
        *,
        stage_ids: Iterable[str] | None = None,
    ) -> "PipelineRuntimeSettingsBuilder":
        self.declare(spec, stage_ids=stage_ids)
        normalized = spec.normalized_key()
        self._required_scopes.setdefault(normalized, []).append(
            _RuntimeSettingScope(_normalize_stage_ids(stage_ids))
        )
        return self

    def install_stage_settings(
        self,
        stage_id: str,
        settings: Iterable[RuntimeStageSetting],
    ) -> "PipelineRuntimeSettingsBuilder":
        for setting in settings:
            if setting.required:
                self.require(setting.spec, stage_ids=(stage_id,))
            else:
                self.declare(setting.spec, stage_ids=(stage_id,))
        return self

    def freeze(self) -> PipelineRuntimeSettings:
        self._sealed = True
        return self._build_settings(
            active_stage_ids=None,
            include_sources=False,
            enforce_required=False,
        )

    def _assert_not_sealed(self) -> None:
        if self._sealed:
            raise RuntimeError("runtime settings builder is already frozen")

    def _build_settings(
        self,
        *,
        active_stage_ids: frozenset[str] | None,
        include_sources: bool,
        enforce_required: bool,
    ) -> PipelineRuntimeSettings:
        settings = PipelineRuntimeSettings(
            values=MappingProxyType({}),
            _declared_values=MappingProxyType(dict(self._values)),
            _specs=MappingProxyType(dict(self._specs)),
            _active_scopes=MappingProxyType(
                {
                    key: tuple(scopes)
                    for key, scopes in self._active_scopes.items()
                }
            ),
            _required_scopes=MappingProxyType(
                {
                    key: tuple(scopes)
                    for key, scopes in self._required_scopes.items()
                }
            ),
            _sources=tuple(self._sources),
        )
        return settings._build_settings(
            active_stage_ids=active_stage_ids,
            include_sources=include_sources,
            enforce_required=enforce_required,
        )

class RuntimeSettingSourceFeature:
    feature_id = "chunky.runtime.setting-source"

    def __init__(
        self,
        *,
        source: RuntimeSettingSource,
        feature_id: str | None = None,
        configuration_fingerprint: str = "runtime-setting-source",
    ) -> None:
        self._source = source
        if feature_id is not None:
            self.feature_id = feature_id
        self.configuration_fingerprint = configuration_fingerprint

    def install(
        self,
        manifest_builder: StageManifestBuilder,
        service_locator: PipelineRuntimeServicesBuilder,
        env_settings: PipelineRuntimeSettingsBuilder,
    ) -> None:
        del manifest_builder, service_locator
        env_settings.install_source(self._source)


class EnvironmentVariableRuntimeSettingSourceFeature(RuntimeSettingSourceFeature):
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        env_name_overrides: Mapping[str, str] | None = None,
        dotenv_files: Iterable[str | Path] | None = None,
    ) -> None:
        normalized_dotenv_files = tuple(dotenv_files or ())
        fingerprint_parts = ["runtime-setting-source:environment-variable"]
        if env_name_overrides:
            overrides = ",".join(
                f"{_normalize_runtime_setting_key(key)}={value}"
                for key, value in sorted(env_name_overrides.items())
            )
            fingerprint_parts.append(f"overrides:{overrides}")
        if normalized_dotenv_files:
            dotenv_fingerprint = ",".join(
                str(Path(path).expanduser().resolve()) for path in normalized_dotenv_files
            )
            fingerprint_parts.append(f"dotenv:{dotenv_fingerprint}")
        super().__init__(
            source=EnvironmentVariableRuntimeSettingSource(
                environ=environ,
                env_name_overrides=env_name_overrides,
                dotenv_files=normalized_dotenv_files,
            ),
            configuration_fingerprint="|".join(fingerprint_parts),
        )


class ExplicitRuntimeSettingsFeature:
    feature_id = "chunky.runtime.explicit-settings"

    def __init__(self, values: Mapping[str, object]) -> None:
        self._values = dict(values)
        items = ",".join(
            f"{key}={value!r}" for key, value in sorted(self._values.items())
        )
        self.configuration_fingerprint = f"explicit-runtime-settings:{items}"

    def install(
        self,
        manifest_builder: StageManifestBuilder,
        service_locator: PipelineRuntimeServicesBuilder,
        env_settings: PipelineRuntimeSettingsBuilder,
    ) -> None:
        del manifest_builder, service_locator
        for key, value in self._values.items():
            env_settings.set(key, value)


def _normalize_stage_ids(
    stage_ids: Iterable[str] | None,
) -> frozenset[str] | None:
    if stage_ids is None:
        return None
    return frozenset(stage_id.strip() for stage_id in stage_ids if stage_id.strip())


def _coerce_runtime_setting_string(value: object, key: str) -> str:
    if isinstance(value, str):
        return value
    raise RuntimeError(
        f"runtime setting {key} must be a string, got {type(value).__name__}"
    )


def _coerce_runtime_setting_bool(value: object, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise RuntimeError(
        f"runtime setting {key} must be a bool, got {type(value).__name__}"
    )


def _normalize_runtime_setting_key(key: str) -> str:
    normalized = key.strip()
    if not normalized:
        raise ValueError("runtime setting key must be non-empty")
    return normalized


def _load_dotenv_files(
    dotenv_files: Iterable[str | Path] | None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for dotenv_file in dotenv_files or ():
        path = Path(dotenv_file).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"dotenv file not found: {path}")
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                raise RuntimeError(f"invalid dotenv line in {path}: {raw_line}")
            key, value = line.split("=", 1)
            normalized_key = key.strip()
            if not normalized_key:
                raise RuntimeError(f"invalid dotenv key in {path}: {raw_line}")
            values[normalized_key] = _strip_optional_quotes(value.strip())
    return values


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _coerce_runtime_setting_int(value: object, *, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"runtime setting {key!r} must be an int, got {type(value).__name__}"
        )
    return value


def _coerce_runtime_setting_float(value: object, *, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"runtime setting {key!r} must be a float, got {type(value).__name__}"
        )
    return float(value)
