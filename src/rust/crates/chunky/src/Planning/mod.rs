//! Planning modules for request graph construction and execution planning.
//!
//! The modules here build request graphs over a frozen manifest and turn them
//! into execution-ready plans using the crate's artifact and stage layers.

pub mod execution_planner;
pub mod manifest_graph;
pub mod task_request_graph;

pub use execution_planner::ExecutionPlanner;
pub use manifest_graph::{ManifestConsumerEdge, ManifestProducerEdge, StageManifestGraph, StageManifestGraphBuilder};
pub use task_request_graph::*;
