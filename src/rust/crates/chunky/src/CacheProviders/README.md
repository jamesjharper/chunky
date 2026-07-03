# Cache providers Rust port

This folder ports the files present in `spec/05-cache-providers/impl-py` into Rust source-only modules.

The port mirrors the Python structure:

- `cache_models.rs` ports the cache resolution result contracts.
- `file_cache_provider/` ports the SQLite-coordinated filesystem cache provider.
- `file_cache_provider/sqlite_file_cache.rs` is the compatibility facade matching the Python facade.

The snippets assume the existing Rust artifact modules from `spec/01-artifacts/impl-rs` are available at the crate root. A real crate using this source would need dependencies equivalent to:

```toml
anyhow = "1"
async-trait = "0.1"
blake3 = "1"
dunce = "1"
hostname = "0.4"
nix = { version = "0.29", features = ["signal", "process"] }
rusqlite = { version = "0.32", features = ["bundled"] }
thiserror = "1"
tokio = { version = "1", features = ["fs", "rt", "sync", "time"] }
uuid = { version = "1", features = ["v4"] }
walkdir = "2"
```

This is intentionally not wrapped in a new full Cargo package because the surrounding archive uses loose Rust source files for the spec examples.
