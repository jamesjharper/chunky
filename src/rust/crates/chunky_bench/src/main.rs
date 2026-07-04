use anyhow::{bail, Result};
use chunky_example::benchmark_support::{
    build_benchmark_request, create_benchmark_fixture, BenchmarkScenario,
};
use chunky_example::demo_support::DEFAULT_WORK_DIR;
use serde::Serialize;
use std::env;
use std::path::PathBuf;
use std::time::Instant;

#[derive(Serialize)]
struct SampleSummary {
    samples_ms: Vec<f64>,
    min_ms: f64,
    max_ms: f64,
    mean_ms: f64,
    median_ms: f64,
    p95_ms: f64,
}

#[derive(Serialize)]
struct ScenarioResult {
    runtime: &'static str,
    scenario: &'static str,
    iterations: usize,
    warmups: usize,
    cold_run: SampleSummary,
    hot_run: SampleSummary,
}

fn parse_args() -> Result<(usize, usize, PathBuf, BenchmarkScenario, bool, bool, bool)> {
    let mut iterations = 10usize;
    let mut warmups = 2usize;
    let mut work_dir = PathBuf::from(DEFAULT_WORK_DIR);
    let mut scenario = BenchmarkScenario::FullPipeline;
    let mut emit_json = false;
    let mut json_only = false;
    let mut keep_work_dir = false;

    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--iterations" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("missing value for --iterations"))?;
                iterations = value.parse()?;
            }
            "--warmups" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("missing value for --warmups"))?;
                warmups = value.parse()?;
            }
            "--work-dir" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("missing value for --work-dir"))?;
                work_dir = PathBuf::from(value);
            }
            "--scenario" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("missing value for --scenario"))?;
                scenario = value.parse()?;
            }
            "--json" => emit_json = true,
            "--json-only" => json_only = true,
            "--keep-work-dir" => keep_work_dir = true,
            other => bail!("unknown argument: {other}"),
        }
    }

    if iterations == 0 {
        bail!("--iterations must be positive");
    }
    Ok((
        iterations,
        warmups,
        work_dir,
        scenario,
        emit_json || json_only,
        json_only,
        keep_work_dir,
    ))
}

async fn measure_run<F>(future: F) -> Result<f64>
where
    F: std::future::Future<Output = Result<chunky_example::task_runner::TaskRunResult>>,
{
    let started = Instant::now();
    future.await?;
    Ok(started.elapsed().as_secs_f64() * 1000.0)
}

async fn run_pipeline_iteration(
    work_dir: PathBuf,
    iteration_id: &str,
    scenario: BenchmarkScenario,
) -> Result<(f64, f64)> {
    let fixture = create_benchmark_fixture(work_dir, iteration_id, scenario, true).await?;
    let cold_ms = measure_run(fixture.pipeline.run(build_benchmark_request(
        &fixture.audio_path,
        &fixture.output_path,
        scenario,
    )))
    .await?;
    let hot_ms = measure_run(fixture.pipeline.run(build_benchmark_request(
        &fixture.audio_path,
        &fixture.output_path,
        scenario,
    )))
    .await?;
    Ok((cold_ms, hot_ms))
}

fn summarize(samples_ms: Vec<f64>) -> SampleSummary {
    let mut ordered = samples_ms.clone();
    ordered.sort_by(|left, right| left.partial_cmp(right).unwrap());
    let p95_index = ((ordered.len() - 1) as f64 * 0.95).round() as usize;
    let sum = samples_ms.iter().sum::<f64>();
    let mean_ms = sum / samples_ms.len() as f64;
    let median_ms = if ordered.len() % 2 == 0 {
        let right = ordered.len() / 2;
        (ordered[right - 1] + ordered[right]) / 2.0
    } else {
        ordered[ordered.len() / 2]
    };

    SampleSummary {
        min_ms: *ordered.first().unwrap(),
        max_ms: *ordered.last().unwrap(),
        mean_ms,
        median_ms,
        p95_ms: ordered[p95_index],
        samples_ms,
    }
}

fn print_summary(result: &ScenarioResult) {
    println!("runtime: {}", result.runtime);
    println!("scenario: {}", result.scenario);
    println!("iterations: {}", result.iterations);
    println!("warmups: {}", result.warmups);
    println!(
        "cold run ms: mean={:.3}, median={:.3}, p95={:.3}, min={:.3}, max={:.3}",
        result.cold_run.mean_ms,
        result.cold_run.median_ms,
        result.cold_run.p95_ms,
        result.cold_run.min_ms,
        result.cold_run.max_ms,
    );
    println!(
        "hot run ms: mean={:.3}, median={:.3}, p95={:.3}, min={:.3}, max={:.3}",
        result.hot_run.mean_ms,
        result.hot_run.median_ms,
        result.hot_run.p95_ms,
        result.hot_run.min_ms,
        result.hot_run.max_ms,
    );
}

#[tokio::main]
async fn main() -> Result<()> {
    let (iterations, warmups, work_dir, scenario, emit_json, json_only, keep_work_dir) =
        parse_args()?;
    let bench_root = work_dir.join(format!("{}_benchmark", scenario.as_str()));

    for warmup in 0..warmups {
        run_pipeline_iteration(
            bench_root.join(format!("warmup-{warmup}")),
            &format!("rust-benchmark-warmup-{warmup}"),
            scenario,
        )
        .await?;
    }

    let mut cold_samples = Vec::with_capacity(iterations);
    let mut hot_samples = Vec::with_capacity(iterations);
    for iteration in 0..iterations {
        let (cold_ms, hot_ms) = run_pipeline_iteration(
            bench_root.join(format!("iteration-{iteration}")),
            &format!("rust-benchmark-{iteration}"),
            scenario,
        )
        .await?;
        cold_samples.push(cold_ms);
        hot_samples.push(hot_ms);
    }

    if !keep_work_dir && bench_root.exists() {
        std::fs::remove_dir_all(&bench_root)?;
    }

    let result = ScenarioResult {
        runtime: "rust",
        scenario: scenario.as_str(),
        iterations,
        warmups,
        cold_run: summarize(cold_samples),
        hot_run: summarize(hot_samples),
    };
    if !json_only {
        print_summary(&result);
    }
    if emit_json {
        println!("{}", serde_json::to_string_pretty(&result)?);
    }

    Ok(())
}
