from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from chunky.Bindings.file_cache_provider import (
        FileCacheProviderGarbageCollectionPolicy,
    )
    from src.python.chunky_demo.demo_support import (
        DEFAULT_WORK_DIR,
        build_request,
        create_demo_fixture,
    )
else:
    from ..chunky.Bindings.file_cache_provider import (
        FileCacheProviderGarbageCollectionPolicy,
    )
    from .demo_support import DEFAULT_WORK_DIR, build_request, create_demo_fixture


async def demo(work_dir: Path | None = None) -> None:
    work_dir = work_dir or DEFAULT_WORK_DIR
    fixture = await create_demo_fixture(work_dir, owner_task_id="demo")

    print(f"work dir: {fixture.work_dir}")
    print(f"manifest: {fixture.pipeline.stage_manifest.manifest_id}")
    print(f"manifest description: {fixture.manifest_path}")
    print(f"sqlite cache: {fixture.cache.database_path}")

    print("\n--- first run ---")
    handle = fixture.pipeline.run_away(
        build_request(fixture.audio_path, fixture.output_path)
    )
    first_result = await handle
    if first_result.output is None:
        raise RuntimeError(first_result.errors)

    first = first_result.output
    print("\n".join(first.events))
    print(fixture.output_path.read_text("utf-8"))
    print(f"progress terminal state: {handle.get_progress().task_state.value}")

    print("\n--- second run ---")
    second = await fixture.pipeline.run(
        build_request(fixture.audio_path, fixture.output_path)
    )
    print("\n".join(second.events))

    stats = await fixture.cache.statistics()
    print(f"cache entries: {stats.current_entry_count}")
    gc = await fixture.cache.garbage_collect(
        FileCacheProviderGarbageCollectionPolicy(max_age_seconds=7 * 24 * 60 * 60)
    )
    print(
        "garbage collection: "
        f"entries={gc.entries_deleted}, vacuum={gc.vacuum_performed}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Chunky Python demo.")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help=f"Directory for demo artifacts and cache. Default: {DEFAULT_WORK_DIR}",
    )
    args = parser.parse_args(argv)
    asyncio.run(demo(args.work_dir))


if __name__ == "__main__":
    main()
