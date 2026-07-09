//! Task control handles and result models.
//!
//! `RunHandle` exposes cancellation, progress snapshots, subscriptions, and
//! terminal results. Domain failures are represented by `RunResult`, not by
//! panicking the awaiting caller.

use std::future::IntoFuture;
use std::pin::Pin;
use std::sync::Arc;

use tokio::sync::watch;

use crate::stage_models::CancellationSource;

use crate::progress_models::{
    ProgressDispatcher, ProgressSubscription, RunProgressHandler, RunProgressSnapshot,
    RunProgressTracker, TaskState,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RunStatus {
    Completed,
    Failed,
    Cancelled,
}

impl RunStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunError {
    pub code: String,
    pub message: String,
    pub stage_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RunResult<T> {
    pub task_id: String,
    pub status: RunStatus,
    pub output: Option<T>,
    pub errors: Vec<RunError>,
}

impl<T> RunResult<T> {
    pub fn completed(task_id: impl Into<String>, output: T) -> Self {
        Self {
            task_id: task_id.into(),
            status: RunStatus::Completed,
            output: Some(output),
            errors: Vec::new(),
        }
    }

    pub fn cancelled(task_id: impl Into<String>) -> Self {
        Self {
            task_id: task_id.into(),
            status: RunStatus::Cancelled,
            output: None,
            errors: Vec::new(),
        }
    }

    pub fn failed(task_id: impl Into<String>, errors: Vec<RunError>) -> Self {
        Self {
            task_id: task_id.into(),
            status: RunStatus::Failed,
            output: None,
            errors,
        }
    }

    pub fn failed_with_output(
        task_id: impl Into<String>,
        output: T,
        errors: Vec<RunError>,
    ) -> Self {
        Self {
            task_id: task_id.into(),
            status: RunStatus::Failed,
            output: Some(output),
            errors,
        }
    }
}

pub struct RunHandle<T> {
    pub task_id: String,
    cancellation: CancellationSource,
    tracker: RunProgressTracker,
    dispatcher: ProgressDispatcher,
    completion: watch::Receiver<Option<Arc<RunResult<T>>>>,
}

impl<T> RunHandle<T>
where
    T: Clone + Send + Sync + 'static,
{
    pub fn new(
        task_id: impl Into<String>,
        cancellation: CancellationSource,
        tracker: RunProgressTracker,
        dispatcher: ProgressDispatcher,
        completion: watch::Receiver<Option<Arc<RunResult<T>>>>,
    ) -> Self {
        Self {
            task_id: task_id.into(),
            cancellation,
            tracker,
            dispatcher,
            completion,
        }
    }

    pub fn cancel(&self, reason: Option<String>) -> bool {
        let task_state = self.tracker.get_snapshot().task_state;
        if self.completion.borrow().is_some() || task_state.is_terminal() {
            return false;
        }

        let changed = self.cancellation.cancel(reason);
        if changed {
            self.tracker.set_task_state(TaskState::Cancelling);
        }
        changed
    }

    pub fn get_progress(&self) -> RunProgressSnapshot {
        self.tracker.get_snapshot()
    }

    pub fn get_result(&self) -> Option<RunResult<T>> {
        self.completion
            .borrow()
            .as_ref()
            .map(|result| result.as_ref().clone())
    }

    pub fn subscribe_progress_events<F>(&self, handler: F) -> ProgressSubscription
    where
        F: Fn(RunProgressSnapshot) + Send + Sync + 'static,
    {
        let handler: RunProgressHandler = Arc::new(handler);
        let subscription = self.dispatcher.subscribe_arc(handler.clone());
        // A new subscriber receives the complete current snapshot immediately.
        self.dispatcher
            .publish_to(handler, self.tracker.get_snapshot());
        subscription
    }

    pub async fn await_to_completion(&self) -> RunResult<T> {
        let mut completion = self.completion.clone();
        loop {
            if let Some(result) = completion.borrow().clone() {
                return result.as_ref().clone();
            }
            completion
                .changed()
                .await
                .expect("task completion channel closed without a result");
        }
    }
}

impl<T> IntoFuture for RunHandle<T>
where
    T: Clone + Send + Sync + 'static,
{
    type Output = RunResult<T>;
    type IntoFuture = Pin<Box<dyn std::future::Future<Output = Self::Output> + Send>>;

    fn into_future(self) -> Self::IntoFuture {
        Box::pin(async move { self.await_to_completion().await })
    }
}
