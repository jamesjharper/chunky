# Pipeline Builder Inbuilts

## Purpose

`ChunkyPipelineBuilder` should ship with a set of inbuilt feature types that cover the common bootstrap concerns:

- installing a durable file based artifact cache;
- installing runtime-setting sources for reading from environment variables;
- default installing a complete set of core set of core services. 
- installing sets of required stages for common ASR activities 

These are convenience capabilities, not a second plugin system. They exist so a caller can compose a usable pipeline without re-implementing common bootstrap code.

> Note to Agent: Many of these inbuilt capabilities are late stage development activities, expect this document to be become more fleshed out in the future. 

## Built-In Features

### `FileCacheFeature`

`FileCacheFeature` installs the pipeline's artifact-cache runtime service using `FileCacheProvider`. This is the standard way to give a pipeline a durable cache root for artifact reads and writes.

*Conceptual shape:*

```python
class FileCacheFeature:
    def __init__(
        self,
        root: str | Path,
        *,
        config: FileCacheProviderConfig | None = None,
    ) -> None: ...
```

Configuration rules:

- `root` is required and identifies the cache directory on disk;
- `config` is optional and is passed through to `FileCacheProviderConfig`;
- installation claims the `artifact_cache` runtime-service slot;
- if another feature also configures `artifact_cache`, builder installation fails with a hard conflict.

Typical usage:

```python
pipeline = (
    ChunkyPipeline.builder()
    .install(FileCacheFeature("./.chunky-cache"))
    .build()
)
```

### `EnvironmentVariableRuntimeSettingSourceFeature`

`EnvironmentVariableRuntimeSettingSourceFeature` is the default inbuilt source feature for environment-backed runtime settings.

*Conceptual shape:*

```python
class EnvironmentVariableRuntimeSettingSourceFeature:
    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        env_name_overrides: Mapping[str, str] | None = None,
        dotenv_files: Iterable[str | Path] | None = None,
    ) -> None: ...
```

Behavior:

- if `RuntimeSettingSpec.env_var` is set, that name is used;
- otherwise the normalized runtime-setting key is converted to uppercase with `.` mapped to `_`;
- `env_name_overrides` can override the lookup name per runtime-setting key;
- `dotenv_files` allows the source to preload one or more `.env` files;
- process environment values, or values passed via `environ`, override values loaded from `.env` files.

`.env` loading is intentionally opt-in. Installing `EnvironmentVariableRuntimeSettingSourceFeature()` without `dotenv_files` preserves the current environment-only behavior.

Example:

```python
pipeline = (
    ChunkyPipeline.builder()
    .install(
        EnvironmentVariableRuntimeSettingSourceFeature(
            dotenv_files=(".env", ".env.local"),
        )
    )
    .install(FileCacheFeature("./.chunky-cache"))
    .build()
)
```

