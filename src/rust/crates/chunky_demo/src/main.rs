use anyhow::{bail, Result};
use chunky_example::cache_providers::FileCacheProviderGarbageCollectionPolicy;
use chunky_example::demo_support::{build_request, create_demo_fixture, DEFAULT_WORK_DIR};
use std::env;
use std::path::PathBuf;

#[tokio::main]
async fn main() -> Result<()> {
    let work_dir = env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_WORK_DIR));

    let fixture = create_demo_fixture(work_dir, "demo", true).await?;

    println!("work dir: {}", fixture.work_dir.display());
    println!("manifest: {}", fixture.pipeline.stage_manifest.manifest_id);
    println!("manifest description: {}", fixture.manifest_path.display());
    println!("sqlite cache: {}", fixture.cache.database_path().display());

    println!("\n--- first run ---");
    let handle = fixture
        .pipeline
        .run_away(build_request(&fixture.audio_path, &fixture.output_path));
    let first_result = handle.await_to_completion().await;
    let Some(first) = first_result.output else {
        bail!("demo run failed: {:?}", first_result.errors);
    };
    if !first.events.is_empty() {
        println!("{}", first.events.join("\n"));
    }
    println!("{}", std::fs::read_to_string(&fixture.output_path)?);
    println!(
        "progress terminal state: {}",
        handle.get_progress().task_state.as_str()
    );

    println!("\n--- second run ---");
    let second = fixture
        .pipeline
        .run(build_request(&fixture.audio_path, &fixture.output_path))
        .await?;
    if !second.events.is_empty() {
        println!("{}", second.events.join("\n"));
    }

    let stats = fixture.cache.statistics().await?;
    println!("cache entries: {}", stats.current_entry_count);
    let gc = fixture
        .cache
        .garbage_collect(Some(FileCacheProviderGarbageCollectionPolicy {
            max_age_seconds: Some(7.0 * 24.0 * 60.0 * 60.0),
            ..Default::default()
        }))
        .await?;
    println!(
        "garbage collection: entries={}, vacuum={}",
        gc.entries_deleted, gc.vacuum_performed
    );

    Ok(())
}
