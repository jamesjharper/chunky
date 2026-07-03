//! Progress tracking models for runs and stage invocations.
//!
//! This is intentionally source-only. It assumes the surrounding Rust example
//! already provides the artifact, stage, planning, and cache modules.

use std::collections::{BTreeMap, HashMap};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread::{self, JoinHandle};

use anyhow::{bail, Result};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TaskState {
    Created,
    Running,
    Cancelling,
    Completed,
    Failed,
    Cancelled,
}

impl TaskState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::Running => "running",
            Self::Cancelling => "cancelling",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StageState {
    Pending,
    Running,
    Blocked,
    Finished,
    Failed,
    Cancelled,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StageBlockedReason {
    WaitingForCompute,
    WaitingForOtherTask,
}

impl StageBlockedReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::WaitingForCompute => "waiting_for_compute",
            Self::WaitingForOtherTask => "waiting_for_other_task",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct StageSubtaskProgress {
    pub subtask_id: String,
    pub name: String,
    pub percentage: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct StageProgressSnapshot {
    pub invocation_id: String,
    pub stage_id: String,
    pub display_name: String,
    pub subtasks: Vec<StageSubtaskProgress>,
    pub blocked_reason: Option<StageBlockedReason>,
    pub blocked_description: Option<String>,
    pub blocking_task_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct RunProgressSnapshot {
    pub task_id: String,
    pub task_state: TaskState,
    pub total_stage_count: usize,
    pub finished_stage_count: usize,
    pub pending_stage_count: usize,
    pub running_stage_count: usize,
    pub blocked_stage_count: usize,
    pub pending_stages: Vec<StageProgressSnapshot>,
    pub running_stages: Vec<StageProgressSnapshot>,
    pub blocked_stages: Vec<StageProgressSnapshot>,
}

pub type RunProgressHandler = Arc<dyn Fn(RunProgressSnapshot) + Send + Sync + 'static>;

enum DispatchMessage {
    Call(RunProgressHandler, RunProgressSnapshot),
    Shutdown,
}

#[derive(Clone)]
pub struct ProgressDispatcher {
    handlers: Arc<Mutex<HashMap<u64, RunProgressHandler>>>,
    sender: Arc<Mutex<Option<mpsc::Sender<DispatchMessage>>>>,
    worker: Arc<Mutex<Option<JoinHandle<()>>>>,
    next_key: Arc<AtomicU64>,
}

impl ProgressDispatcher {
    pub fn new() -> Self {
        let handlers = Arc::new(Mutex::new(HashMap::new()));
        let (sender, receiver) = mpsc::channel::<DispatchMessage>();
        let worker = thread::Builder::new()
            .name("chunky-progress-example".to_owned())
            .spawn(move || {
                while let Ok(message) = receiver.recv() {
                    match message {
                        DispatchMessage::Call(handler, snapshot) => {
                            let call = AssertUnwindSafe(|| handler(snapshot));
                            if catch_unwind(call).is_err() {
                                eprintln!("progress handler panicked");
                            }
                        }
                        DispatchMessage::Shutdown => break,
                    }
                }
            })
            .expect("failed to start progress dispatcher thread");

        Self {
            handlers,
            sender: Arc::new(Mutex::new(Some(sender))),
            worker: Arc::new(Mutex::new(Some(worker))),
            next_key: Arc::new(AtomicU64::new(1)),
        }
    }

    pub fn null() -> Self {
        Self {
            handlers: Arc::new(Mutex::new(HashMap::new())),
            sender: Arc::new(Mutex::new(None)),
            worker: Arc::new(Mutex::new(None)),
            next_key: Arc::new(AtomicU64::new(1)),
        }
    }

    pub fn subscribe<F>(&self, handler: F) -> ProgressSubscription
    where
        F: Fn(RunProgressSnapshot) + Send + Sync + 'static,
    {
        self.subscribe_arc(Arc::new(handler))
    }

    pub fn subscribe_arc(&self, handler: RunProgressHandler) -> ProgressSubscription {
        let key = self.next_key.fetch_add(1, Ordering::Relaxed);
        self.handlers
            .lock()
            .expect("progress handler mutex poisoned")
            .insert(key, handler);
        ProgressSubscription {
            handlers: self.handlers.clone(),
            key,
            closed: false,
        }
    }

    pub fn publish(&self, snapshot: RunProgressSnapshot) {
        let handlers = self
            .handlers
            .lock()
            .expect("progress handler mutex poisoned")
            .values()
            .cloned()
            .collect::<Vec<_>>();
        for handler in handlers {
            self.publish_to(handler, snapshot.clone());
        }
    }

    pub fn publish_to(&self, handler: RunProgressHandler, snapshot: RunProgressSnapshot) {
        let sender = self
            .sender
            .lock()
            .expect("progress sender mutex poisoned")
            .clone();
        if let Some(sender) = sender {
            let _ = sender.send(DispatchMessage::Call(handler, snapshot));
        }
    }

    pub fn close(&self) {
        let sender = self
            .sender
            .lock()
            .expect("progress sender mutex poisoned")
            .take();
        if let Some(sender) = sender {
            let _ = sender.send(DispatchMessage::Shutdown);
        }

        let worker = self
            .worker
            .lock()
            .expect("progress worker mutex poisoned")
            .take();
        if let Some(worker) = worker {
            let _ = worker.join();
        }
    }
}

impl Default for ProgressDispatcher {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Clone, Default)]
pub struct NullProgressDispatcher;

impl NullProgressDispatcher {
    pub fn subscribe<F>(&self, _handler: F) -> ProgressSubscription
    where
        F: Fn(RunProgressSnapshot) + Send + Sync + 'static,
    {
        ProgressSubscription::detached()
    }

    pub fn publish(&self, _snapshot: RunProgressSnapshot) {}
    pub fn close(&self) {}
}

pub struct ProgressSubscription {
    handlers: Arc<Mutex<HashMap<u64, RunProgressHandler>>>,
    key: u64,
    closed: bool,
}

impl ProgressSubscription {
    fn detached() -> Self {
        Self {
            handlers: Arc::new(Mutex::new(HashMap::new())),
            key: 0,
            closed: true,
        }
    }

    pub fn close(&mut self) {
        if !self.closed {
            self.closed = true;
            self.handlers
                .lock()
                .expect("progress handler mutex poisoned")
                .remove(&self.key);
        }
    }
}

impl Drop for ProgressSubscription {
    fn drop(&mut self) {
        self.close();
    }
}

#[derive(Clone, Debug)]
struct MutableStageProgress {
    invocation_id: String,
    stage_id: String,
    display_name: String,
    state: StageState,
    subtasks: BTreeMap<String, StageSubtaskProgress>,
    blocked_reason: Option<StageBlockedReason>,
    blocked_description: Option<String>,
    blocking_task_id: Option<String>,
}

impl MutableStageProgress {
    fn new(invocation_id: String, stage_id: String, display_name: String) -> Self {
        Self {
            invocation_id,
            stage_id,
            display_name,
            state: StageState::Pending,
            subtasks: BTreeMap::new(),
            blocked_reason: None,
            blocked_description: None,
            blocking_task_id: None,
        }
    }
}

struct TrackerState {
    task_state: TaskState,
    stages: BTreeMap<String, MutableStageProgress>,
    last_snapshot: Option<RunProgressSnapshot>,
    batch_depth: usize,
    batch_dirty: bool,
}

#[derive(Clone)]
pub struct RunProgressTracker {
    task_id: String,
    dispatcher: ProgressDispatcher,
    state: Arc<Mutex<TrackerState>>,
}

impl RunProgressTracker {
    pub fn new(task_id: impl Into<String>, dispatcher: ProgressDispatcher) -> Self {
        Self {
            task_id: task_id.into(),
            dispatcher,
            state: Arc::new(Mutex::new(TrackerState {
                task_state: TaskState::Created,
                stages: BTreeMap::new(),
                last_snapshot: None,
                batch_depth: 0,
                batch_dirty: false,
            })),
        }
    }

    pub fn batch_update<F>(&self, update: F)
    where
        F: FnOnce(),
    {
        {
            let mut state = self.state.lock().expect("progress tracker mutex poisoned");
            state.batch_depth += 1;
        }

        update();

        let snapshot = {
            let mut state = self.state.lock().expect("progress tracker mutex poisoned");
            state.batch_depth -= 1;
            if state.batch_depth == 0 && state.batch_dirty {
                state.batch_dirty = false;
                Self::snapshot_if_changed(&self.task_id, &mut state)
            } else {
                None
            }
        };
        if let Some(snapshot) = snapshot {
            self.dispatcher.publish(snapshot);
        }
    }

    pub fn register_stage(
        &self,
        invocation_id: impl Into<String>,
        stage_id: impl Into<String>,
        display_name: impl Into<String>,
    ) {
        let invocation_id = invocation_id.into();
        self.mutate(move |state| {
            state.stages.insert(
                invocation_id.clone(),
                MutableStageProgress::new(invocation_id, stage_id.into(), display_name.into()),
            );
        });
    }

    pub fn set_task_state(&self, task_state: TaskState) {
        self.mutate(move |state| state.task_state = task_state);
    }

    pub fn mark_pending(&self, invocation_id: &str) {
        self.set_stage_state(invocation_id, StageState::Pending);
    }

    pub fn mark_running(&self, invocation_id: &str) {
        self.set_stage_state(invocation_id, StageState::Running);
    }

    pub fn mark_finished(&self, invocation_id: &str) {
        self.set_stage_state(invocation_id, StageState::Finished);
    }

    pub fn mark_failed(&self, invocation_id: &str) {
        self.set_stage_state(invocation_id, StageState::Failed);
    }

    pub fn mark_cancelled(&self, invocation_id: &str) {
        self.set_stage_state(invocation_id, StageState::Cancelled);
    }

    pub fn mark_blocked(
        &self,
        invocation_id: &str,
        reason: StageBlockedReason,
        description: impl Into<String>,
        blocking_task_id: Option<String>,
    ) {
        let invocation_id = invocation_id.to_owned();
        let description = description.into();
        self.mutate(move |state| {
            let stage = state
                .stages
                .get_mut(&invocation_id)
                .expect("unknown stage invocation");
            stage.state = StageState::Blocked;
            stage.blocked_reason = Some(reason);
            stage.blocked_description = Some(description);
            stage.blocking_task_id = blocking_task_id;
        });
    }

    pub fn clear_blocked(&self, invocation_id: &str) {
        let invocation_id = invocation_id.to_owned();
        self.mutate(move |state| {
            let stage = state
                .stages
                .get_mut(&invocation_id)
                .expect("unknown stage invocation");
            if stage.state == StageState::Blocked {
                stage.state = StageState::Pending;
            }
            stage.blocked_reason = None;
            stage.blocked_description = None;
            stage.blocking_task_id = None;
        });
    }

    pub fn update_subtask(&self, invocation_id: &str, subtask: StageSubtaskProgress) {
        let invocation_id = invocation_id.to_owned();
        self.mutate(move |state| {
            let stage = state
                .stages
                .get_mut(&invocation_id)
                .expect("unknown stage invocation");
            stage.subtasks.insert(subtask.subtask_id.clone(), subtask);
        });
    }

    pub fn remove_subtask(&self, invocation_id: &str, subtask_id: &str) {
        let invocation_id = invocation_id.to_owned();
        let subtask_id = subtask_id.to_owned();
        self.mutate(move |state| {
            let stage = state
                .stages
                .get_mut(&invocation_id)
                .expect("unknown stage invocation");
            stage.subtasks.remove(&subtask_id);
        });
    }

    pub fn get_snapshot(&self) -> RunProgressSnapshot {
        let state = self.state.lock().expect("progress tracker mutex poisoned");
        Self::build_snapshot(&self.task_id, &state)
    }

    fn set_stage_state(&self, invocation_id: &str, stage_state: StageState) {
        let invocation_id = invocation_id.to_owned();
        self.mutate(move |state| {
            let stage = state
                .stages
                .get_mut(&invocation_id)
                .expect("unknown stage invocation");
            stage.state = stage_state;
            if stage_state != StageState::Blocked {
                stage.blocked_reason = None;
                stage.blocked_description = None;
                stage.blocking_task_id = None;
            }
            if stage_state != StageState::Running {
                stage.subtasks.clear();
            }
        });
    }

    fn mutate<F>(&self, update: F)
    where
        F: FnOnce(&mut TrackerState),
    {
        let snapshot = {
            let mut state = self.state.lock().expect("progress tracker mutex poisoned");
            update(&mut state);
            if state.batch_depth > 0 {
                state.batch_dirty = true;
                None
            } else {
                Self::snapshot_if_changed(&self.task_id, &mut state)
            }
        };
        if let Some(snapshot) = snapshot {
            self.dispatcher.publish(snapshot);
        }
    }

    fn snapshot_if_changed(
        task_id: &str,
        state: &mut TrackerState,
    ) -> Option<RunProgressSnapshot> {
        let snapshot = Self::build_snapshot(task_id, state);
        if state.last_snapshot.as_ref() == Some(&snapshot) {
            return None;
        }
        state.last_snapshot = Some(snapshot.clone());
        Some(snapshot)
    }

    fn build_snapshot(task_id: &str, state: &TrackerState) -> RunProgressSnapshot {
        let mut pending_stages = Vec::new();
        let mut running_stages = Vec::new();
        let mut blocked_stages = Vec::new();
        let mut finished_stage_count = 0;

        for stage in state.stages.values() {
            let snapshot = StageProgressSnapshot {
                invocation_id: stage.invocation_id.clone(),
                stage_id: stage.stage_id.clone(),
                display_name: stage.display_name.clone(),
                subtasks: stage.subtasks.values().cloned().collect(),
                blocked_reason: stage.blocked_reason,
                blocked_description: stage.blocked_description.clone(),
                blocking_task_id: stage.blocking_task_id.clone(),
            };

            match stage.state {
                StageState::Pending => pending_stages.push(snapshot),
                StageState::Running => running_stages.push(snapshot),
                StageState::Blocked => blocked_stages.push(snapshot),
                StageState::Finished | StageState::Failed | StageState::Cancelled => {
                    finished_stage_count += 1
                }
            }
        }

        RunProgressSnapshot {
            task_id: task_id.to_owned(),
            task_state: state.task_state,
            total_stage_count: state.stages.len(),
            finished_stage_count,
            pending_stage_count: pending_stages.len(),
            running_stage_count: running_stages.len(),
            blocked_stage_count: blocked_stages.len(),
            pending_stages,
            running_stages,
            blocked_stages,
        }
    }
}

#[derive(Clone)]
pub struct StageProgressReporter {
    tracker: RunProgressTracker,
    invocation_id: String,
}

impl StageProgressReporter {
    pub fn new(tracker: RunProgressTracker, invocation_id: impl Into<String>) -> Self {
        Self {
            tracker,
            invocation_id: invocation_id.into(),
        }
    }

    pub fn start_subtask(
        &self,
        subtask_id: impl Into<String>,
        name: impl Into<String>,
        percentage: Option<f64>,
    ) -> Result<StageSubtaskReporter> {
        let mut reporter = StageSubtaskReporter {
            parent: self.clone(),
            subtask_id: subtask_id.into(),
            name: name.into(),
            finished: false,
        };
        reporter.update(None, None)?;
        if let Some(percentage) = percentage {
            reporter.update(None, Some(percentage))?;
        }
        Ok(reporter)
    }

    fn update(
        &self,
        subtask_id: &str,
        name: &str,
        percentage: Option<f64>,
    ) -> Result<()> {
        validate_percentage(percentage)?;
        self.tracker.update_subtask(
            &self.invocation_id,
            StageSubtaskProgress {
                subtask_id: subtask_id.to_owned(),
                name: name.to_owned(),
                percentage,
            },
        );
        Ok(())
    }

    fn remove(&self, subtask_id: &str) {
        self.tracker.remove_subtask(&self.invocation_id, subtask_id);
    }
}

pub struct StageSubtaskReporter {
    parent: StageProgressReporter,
    subtask_id: String,
    name: String,
    finished: bool,
}

impl StageSubtaskReporter {
    pub fn update(
        &mut self,
        name: Option<String>,
        percentage: Option<f64>,
    ) -> Result<()> {
        if self.finished {
            return Ok(());
        }
        if let Some(name) = name {
            self.name = name;
        }
        self.parent.update(&self.subtask_id, &self.name, percentage)
    }

    pub fn finish(&mut self) {
        if self.finished {
            return;
        }
        self.finished = true;
        self.parent.remove(&self.subtask_id);
    }
}

impl Drop for StageSubtaskReporter {
    fn drop(&mut self) {
        self.finish();
    }
}

fn validate_percentage(percentage: Option<f64>) -> Result<()> {
    if let Some(value) = percentage {
        if !(0.0..=100.0).contains(&value) {
            bail!("percentage must be between 0 and 100");
        }
    }
    Ok(())
}

pub fn ensure_progress_tracker(
    task_id: impl Into<String>,
    progress_tracker: Option<RunProgressTracker>,
) -> RunProgressTracker {
    progress_tracker.unwrap_or_else(|| RunProgressTracker::new(task_id, ProgressDispatcher::null()))
}
