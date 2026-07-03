from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.python.chunky_bench.benchmark_support import (
        build_benchmark_request,
        create_benchmark_fixture,
        scenario_names,
    )
    from src.python.chunky_demo.demo_support import DEFAULT_WORK_DIR
else:
    from .benchmark_support import (
        build_benchmark_request,
        create_benchmark_fixture,
        scenario_names,
    )
    from ..chunky_demo.demo_support import DEFAULT_WORK_DIR


@dataclass(frozen=True)
class SampleSummary:
    samples_ms: list[float]
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p95_ms: float


@dataclass(frozen=True)
class ScenarioResult:
    runtime: str
    scenario: str
    iterations: int
    warmups: int
    cold_run: SampleSummary
    hot_run: SampleSummary


def summarize(samples_ms: list[float]) -> SampleSummary:
    ordered = sorted(samples_ms)
    p95_index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * 0.95)))
    return SampleSummary(
        samples_ms=samples_ms,
        min_ms=min(samples_ms),
        max_ms=max(samples_ms),
        mean_ms=statistics.fmean(samples_ms),
        median_ms=statistics.median(samples_ms),
        p95_ms=ordered[p95_index],
    )


async def measure_run(coro) -> float:
    started = time.perf_counter_ns()
    await coro
    finished = time.perf_counter_ns()
    return (finished - started) / 1_000_000.0


async def run_pipeline_iteration(
    work_dir: Path,
    *,
    iteration_id: str,
    scenario: str,
) -> tuple[float, float]:
    fixture = await create_benchmark_fixture(
        work_dir,
        owner_task_id=iteration_id,
        scenario=scenario,
        clean=True,
    )
    cold_ms = await measure_run(
        fixture.pipeline.run(
            build_benchmark_request(
                fixture.audio_path,
                fixture.output_path,
                scenario,
            )
        )
    )
    hot_ms = await measure_run(
        fixture.pipeline.run(
            build_benchmark_request(
                fixture.audio_path,
                fixture.output_path,
                scenario,
            )
        )
    )
    return cold_ms, hot_ms


async def benchmark_pipeline(
    work_dir: Path,
    *,
    iterations: int,
    warmups: int,
    keep_work_dir: bool,
    scenario: str,
) -> ScenarioResult:
    bench_root = work_dir / f"{scenario}_benchmark"

    for index in range(warmups):
        await run_pipeline_iteration(
            bench_root / f"warmup-{index}",
            iteration_id=f"python-benchmark-warmup-{index}",
            scenario=scenario,
        )

    cold_samples: list[float] = []
    hot_samples: list[float] = []
    for index in range(iterations):
        cold_ms, hot_ms = await run_pipeline_iteration(
            bench_root / f"iteration-{index}",
            iteration_id=f"python-benchmark-{index}",
            scenario=scenario,
        )
        cold_samples.append(cold_ms)
        hot_samples.append(hot_ms)

    if not keep_work_dir and bench_root.exists():
        shutil.rmtree(bench_root)

    return ScenarioResult(
        runtime="python",
        scenario=scenario,
        iterations=iterations,
        warmups=warmups,
        cold_run=summarize(cold_samples),
        hot_run=summarize(hot_samples),
    )


def print_summary(result: ScenarioResult) -> None:
    print(f"runtime: {result.runtime}")
    print(f"scenario: {result.scenario}")
    print(f"iterations: {result.iterations}")
    print(f"warmups: {result.warmups}")
    print(
        "cold run ms: "
        f"mean={result.cold_run.mean_ms:.3f}, "
        f"median={result.cold_run.median_ms:.3f}, "
        f"p95={result.cold_run.p95_ms:.3f}, "
        f"min={result.cold_run.min_ms:.3f}, "
        f"max={result.cold_run.max_ms:.3f}"
    )
    print(
        "hot run ms: "
        f"mean={result.hot_run.mean_ms:.3f}, "
        f"median={result.hot_run.median_ms:.3f}, "
        f"p95={result.hot_run.p95_ms:.3f}, "
        f"min={result.hot_run.min_ms:.3f}, "
        f"max={result.hot_run.max_ms:.3f}"
    )


async def async_main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Benchmark the Chunky Python pipeline.")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--scenario", choices=scenario_names(), default="full_pipeline")
    parser.add_argument("--json", action="store_true", dest="emit_json")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--keep-work-dir", action="store_true")
    args = parser.parse_args(argv)

    if args.iterations <= 0:
        raise SystemExit("--iterations must be positive")
    if args.warmups < 0:
        raise SystemExit("--warmups must be non-negative")

    result = await benchmark_pipeline(
        args.work_dir,
        iterations=args.iterations,
        warmups=args.warmups,
        keep_work_dir=args.keep_work_dir,
        scenario=args.scenario,
    )
    if not args.json_only:
        print_summary(result)
    if args.emit_json or args.json_only:
        print(json.dumps(asdict(result), indent=2))


def main(argv: list[str] | None = None) -> None:
    asyncio.run(async_main(argv))


if __name__ == "__main__":
    main()
