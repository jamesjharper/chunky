//! Crate entry point for the Chunky Rust example implementation.
//!
//! The implementation folders group the artifact, planning, scheduling, cache,
//! and example-feature layers. The flat re-exports below keep the integration
//! tests ergonomic.

#[path = "Bindings/binding_models.rs"]
pub mod artifact_binding_models;
#[path = "Bindings/bindings.rs"]
pub mod artifact_bindings;
#[path = "Artifacts/artifact_codecs_example.rs"]
pub mod artifact_codecs_example;
#[path = "Artifacts/artifact_handles.rs"]
pub mod artifact_handles;
#[path = "Artifacts/artifact_handles_examples.rs"]
pub mod artifact_handles_examples;
#[path = "Artifacts/artifact_models.rs"]
pub mod artifact_models;
#[path = "Artifacts/fingerprints.rs"]
pub mod fingerprints;
#[path = "Artifacts/phase_catalog.rs"]
pub mod phase_catalog;

#[path = "Bindings/cache_models.rs"]
pub mod cache_models;
#[path = "Bindings/file_cache_provider/mod.rs"]
pub mod file_cache_provider;

#[path = "Stages/stage_manifest.rs"]
pub mod stage_manifest;
#[path = "Stages/stage_models.rs"]
pub mod stage_models;

#[path = "Planning/execution_planner.rs"]
pub mod execution_planner;
#[path = "Planning/manifest_graph.rs"]
pub mod manifest_graph;
#[path = "Planning/task_request_graph/mod.rs"]
pub mod task_request_graph;

#[path = "Scheduling/progress_models.rs"]
pub mod progress_models;
#[path = "Scheduling/task_control.rs"]
pub mod task_control;
#[path = "Scheduling/task_runner.rs"]
pub mod task_runner;

pub mod artifacts {
    pub use crate::artifact_binding_models::*;
    pub use crate::artifact_bindings::*;
    pub use crate::artifact_codecs_example::*;
    pub use crate::artifact_handles::*;
    pub use crate::artifact_handles_examples::*;
    pub use crate::artifact_models::*;
    pub use crate::fingerprints::*;
    pub use crate::phase_catalog::*;
}

pub mod cache_providers {
    pub use crate::cache_models::*;
    pub use crate::file_cache_provider::*;
}

pub mod stages {
    pub use crate::stage_manifest::*;
    pub use crate::stage_models::*;
}

pub mod planning {
    pub use crate::execution_planner::*;
    pub use crate::manifest_graph::*;
    pub use crate::task_request_graph::*;

    pub mod manifest_graph {
        pub use crate::manifest_graph::*;
    }

    pub mod execution_planner {
        pub use crate::execution_planner::*;
    }

    pub mod task_request_graph {
        pub use crate::task_request_graph::*;
    }
}

pub mod scheduling {
    pub use crate::progress_models::*;
    pub use crate::task_control::*;
    pub use crate::task_runner::*;
}

pub mod benchmark_support;
pub mod demo_support;
pub mod example_features;
pub mod pipeline;

pub use pipeline::{
    chunky_run, chunky_run_away, ArtifactCachePolicy, ChunkyPipeline, ChunkyPipelineBuilder,
    RunRequest,
};
