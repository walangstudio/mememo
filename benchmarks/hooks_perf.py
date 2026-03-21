#!/usr/bin/env python3
"""
Real-world performance benchmark: mememo inject hook vs naive file-read baseline.

Measures token savings when using mememo's inject hook compared to reading
the top-K most relevant source files directly.

Usage:
    python benchmarks/hooks_perf.py [options]

Options:
    --repo URL         Git repo to clone (default: https://github.com/ntancardoso/gb)
    --tmp-dir PATH     Scratch dir (default: /tmp/mememo-perf)
    --output PATH      Report output (default: benchmark_report.md)
    --baseline-k N     Top-K files for baseline (default: 5)
    --patterns GLOB+   File patterns to index
    --skip-setup       Skip clone+index, reuse existing data in --tmp-dir
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# Ensure project root is on sys.path when invoked directly
# ──────────────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ──────────────────────────────────────────────────────────────────────────────
# Test prompts
# ──────────────────────────────────────────────────────────────────────────────

PROMPTS = [
    "add rate limiting middleware to the API",
    "implement pagination for list endpoints",
    "add input validation and error handling",
    "refactor the database connection layer",
    "add unit tests for the core business logic",
    "implement caching for expensive operations",
    "add authentication and authorization",
    "fix the error handling in the request pipeline",
]

# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class BaselineResult:
    files: list[str]
    file_tokens: list[int]
    total_tokens: int
    top_similarity: float


@dataclass
class InjectResult:
    system_message: str | None
    inject_tokens: int
    candidates_found: int
    memories_injected: int


@dataclass
class BenchResult:
    prompt: str
    baseline: BaselineResult
    inject: InjectResult
    savings: int
    efficiency_pct: float


@dataclass
class SetupInfo:
    repo_url: str
    repo_path: str
    files_indexed: int
    chunks_created: int
    persistent_memories_seeded: int
    setup_duration_seconds: float
    storage_dir: str


# ──────────────────────────────────────────────────────────────────────────────
# Static seed memories (one per prompt topic, fully deterministic)
# ──────────────────────────────────────────────────────────────────────────────

_STATIC_MEMORIES = [
    {
        "content": (
            "Rate limiting: token bucket algorithm at API middleware layer. "
            "Redis stores bucket state per client IP and API key. Limits: 100 req/min "
            "standard, 1000 req/min premium. Middleware short-circuits with 429 and "
            "Retry-After header before reaching handlers."
        ),
        "type": "decision",
        "tags": ["rate-limiting", "middleware", "redis"],
    },
    {
        "content": (
            "Pagination: cursor-based (not offset) for all list endpoints. "
            "Default page size 20, max 100. Cursor is an opaque base64-encoded "
            "timestamp+id tuple. Response includes next_cursor and has_more fields."
        ),
        "type": "decision",
        "tags": ["pagination", "api", "cursor"],
    },
    {
        "content": (
            "Input validation: schema validation at controller layer before business logic. "
            "Invalid payloads return 422 with structured error body: "
            "{field, message, code}. No validation in service layer — controllers own it."
        ),
        "type": "context",
        "tags": ["validation", "error-handling", "api"],
    },
    {
        "content": (
            "Database connection: singleton pool pattern, env-var configured. "
            "DSN from DATABASE_URL env var. Pool: min 2, max 10 connections. "
            "Health check query on borrow. Reconnect on transient errors with exponential backoff."
        ),
        "type": "analysis",
        "tags": ["database", "connection-pool", "configuration"],
    },
    {
        "content": (
            "Unit tests: pytest with conftest.py fixtures. Integration tests in tests/integration/, "
            "excluded from default `pytest` run (require -m integration). "
            "Core business logic tests mock external dependencies via pytest-mock. "
            "Coverage target: 80% for core/, 60% overall."
        ),
        "type": "context",
        "tags": ["testing", "pytest", "unit-tests"],
    },
    {
        "content": (
            "Caching strategy: two-layer — L1 in-process lru_cache (TTL 60s, max 256 entries) "
            "for hot read paths; L2 Redis (TTL 300s) for shared state across instances. "
            "Invalidation is event-driven via domain events, not time-only. "
            "Cache keys: {entity}:{id}:{version}."
        ),
        "type": "decision",
        "tags": ["caching", "redis", "performance"],
    },
    {
        "content": (
            "Authentication: JWT with short-lived access tokens (15 min) and refresh tokens (7 days). "
            "RBAC via roles claim in JWT. Middleware validates JWT on every protected route before "
            "handler. Role enforcement in middleware, not handlers. No session storage — stateless."
        ),
        "type": "decision",
        "tags": ["authentication", "authorization", "jwt", "rbac"],
    },
    {
        "content": (
            "Error handling: domain exceptions (e.g. NotFoundError, ValidationError) mapped to "
            "HTTP status codes in a global exception handler. No raw stack traces in API responses — "
            "structured {error, code, request_id} only. Errors logged with full context server-side."
        ),
        "type": "analysis",
        "tags": ["error-handling", "exceptions", "api"],
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# Setup helpers
# ──────────────────────────────────────────────────────────────────────────────


async def _seed_memories(memory_manager, repo_path: Path) -> int:
    from mememo.types.memory import CreateMemoryParams, MemoryRelationships

    count = 0

    for m in _STATIC_MEMORIES:
        params = CreateMemoryParams(
            content=m["content"],
            type=m["type"],
            tags=m["tags"],
            relationships=MemoryRelationships(),
        )
        await memory_manager.create_memory(params)
        count += 1

    # Also seed up to 3 key repo files as context memories
    candidates = [
        repo_path / "README.md",
        repo_path / "go.mod",
        repo_path / "package.json",
        repo_path / "pyproject.toml",
        repo_path / "main.go",
        repo_path / "main.py",
        repo_path / "index.js",
        repo_path / "index.ts",
    ]

    seeded_files = 0
    for fp in candidates:
        if seeded_files >= 3:
            break
        if not fp.exists():
            continue
        try:
            text = fp.read_text(errors="replace")[:500]
            params = CreateMemoryParams(
                content=f"{fp.name}:\n{text}",
                type="context",
                file_path=str(fp.relative_to(repo_path)),
                tags=["repo-meta"],
                relationships=MemoryRelationships(),
            )
            await memory_manager.create_memory(params)
            count += 1
            seeded_files += 1
        except Exception:
            pass

    return count


async def setup(
    repo_url: str,
    tmp_dir: str,
    file_patterns: list[str],
) -> tuple[SetupInfo, str]:
    import mememo.server as _server
    from mememo.tools.index_repository import index_repository
    from mememo.tools.schemas import IndexRepositoryParams

    start = time.time()
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)

    repo_name = repo_url.rstrip("/").split("/")[-1]
    repo_path = tmp / repo_name

    if not repo_path.exists():
        print(f"Cloning {repo_url} -> {repo_path} ...")
        subprocess.run(
            ["git", "clone", "--depth=1", repo_url, str(repo_path)],
            check=True,
        )
    else:
        print(f"Repo already at {repo_path}, skipping clone.")

    storage_dir = str(tmp / "data")
    os.environ["MEMEMO_STORAGE_DIR"] = storage_dir

    await _server.initialize_mememo()
    memory_manager = _server.memory_manager
    config = _server.config  # noqa: F841 — kept for clarity; callers read from _server

    print("Indexing repository ...")
    idx_params = IndexRepositoryParams(
        repo_path=str(repo_path),
        file_patterns=file_patterns,
        incremental=False,
    )
    result = await index_repository(idx_params, memory_manager)

    if result.files_indexed == 0:
        raise RuntimeError(f"No files indexed — try --patterns. Message: {result.message}")

    print(f"Indexed {result.files_indexed} files, {result.chunks_created} chunks.")

    print("Seeding persistent memories ...")
    seeded = await _seed_memories(memory_manager, repo_path)
    print(f"Seeded {seeded} persistent memories.")

    duration = time.time() - start
    info = SetupInfo(
        repo_url=repo_url,
        repo_path=str(repo_path),
        files_indexed=result.files_indexed,
        chunks_created=result.chunks_created,
        persistent_memories_seeded=seeded,
        setup_duration_seconds=duration,
        storage_dir=storage_dir,
    )
    return info, str(repo_path)


# ──────────────────────────────────────────────────────────────────────────────
# Measurement
# ──────────────────────────────────────────────────────────────────────────────


async def measure_baseline(
    memory_manager,
    prompt: str,
    repo_path: Path,
    top_k: int = 5,
) -> BaselineResult:
    from mememo.types.memory import SearchParams
    from mememo.utils.token_counter import count_tokens

    params = SearchParams(
        query=prompt,
        top_k=50,
        type="code_snippet",
        min_similarity=0.0,
    )
    results = await memory_manager.search_similar(params)

    # Deduplicate by file_path, keeping best similarity per file
    best: dict[str, float] = {}
    for r in results:
        fp = r.memory.content.file_path or ""
        if fp not in best or r.similarity > best[fp]:
            best[fp] = r.similarity

    # Sort by similarity descending, take top_k
    sorted_files = sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:top_k]

    repo = Path(repo_path)
    files: list[str] = []
    file_tokens: list[int] = []
    top_sim = 0.0

    for fp_rel, sim in sorted_files:
        if sim > top_sim:
            top_sim = sim
        full = repo / fp_rel
        try:
            text = full.read_text(errors="replace")
            tok = count_tokens(text)
        except FileNotFoundError:
            continue
        files.append(fp_rel)
        file_tokens.append(tok)

    return BaselineResult(
        files=files,
        file_tokens=file_tokens,
        total_tokens=sum(file_tokens),
        top_similarity=top_sim,
    )


async def measure_inject(memory_manager, config, prompt: str) -> InjectResult:
    from mememo.cli import _build_context_block
    from mememo.types.memory import SearchParams
    from mememo.utils.token_counter import count_tokens

    hook_cfg = config.hook
    params = SearchParams(
        query=prompt,
        top_k=20,
        min_similarity=hook_cfg.inject_search_floor,
    )
    results = await memory_manager.search_similar(params)

    block = _build_context_block(
        results,
        budget=hook_cfg.inject_token_budget,
        min_similarity=hook_cfg.inject_min_similarity,
    )

    if block:
        system_message = f"Relevant memories from previous sessions:\n{block}"
        inject_tokens = count_tokens(system_message)
        memories_injected = block.count("- [")
    else:
        system_message = None
        inject_tokens = 0
        memories_injected = 0

    return InjectResult(
        system_message=system_message,
        inject_tokens=inject_tokens,
        candidates_found=len(results),
        memories_injected=memories_injected,
    )


async def run_all(
    memory_manager,
    config,
    repo_path: str,
    prompts: list[str],
    baseline_k: int,
) -> list[BenchResult]:
    results: list[BenchResult] = []
    for i, prompt in enumerate(prompts, 1):
        print(f"  [{i}/{len(prompts)}] {prompt[:70]}")
        baseline = await measure_baseline(memory_manager, prompt, Path(repo_path), top_k=baseline_k)
        inject = await measure_inject(memory_manager, config, prompt)
        savings = baseline.total_tokens - inject.inject_tokens
        efficiency_pct = (
            inject.inject_tokens / baseline.total_tokens * 100 if baseline.total_tokens > 0 else 0.0
        )
        results.append(
            BenchResult(
                prompt=prompt,
                baseline=baseline,
                inject=inject,
                savings=savings,
                efficiency_pct=efficiency_pct,
            )
        )
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────────


def render_report(
    results: list[BenchResult],
    setup_info: SetupInfo,
    output_path: str,
) -> None:
    lines: list[str] = []

    lines.append("# mememo inject hook — token savings benchmark\n")

    # Setup table
    lines.append("## Setup\n")
    lines.append("| Key | Value |")
    lines.append("|-----|-------|")
    lines.append(f"| Repo | {setup_info.repo_url} |")
    lines.append(f"| Repo path | `{setup_info.repo_path}` |")
    lines.append(f"| Files indexed | {setup_info.files_indexed} |")
    lines.append(f"| Chunks created | {setup_info.chunks_created} |")
    lines.append(f"| Persistent memories seeded | {setup_info.persistent_memories_seeded} |")
    lines.append(f"| Setup duration | {setup_info.setup_duration_seconds:.1f}s |")
    lines.append(f"| Storage dir | `{setup_info.storage_dir}` |")
    lines.append("")

    # Results table
    lines.append("## Results\n")
    lines.append(
        "| Prompt | Baseline tokens | Inject tokens | Savings | Ratio% | Memories injected |"
    )
    lines.append("|--------|----------------|--------------|---------|--------|------------------|")
    for r in results:
        if r.inject.inject_tokens > 0:
            inj_col = str(r.inject.inject_tokens)
            sav_col = str(r.savings)
            ratio_col = f"{r.efficiency_pct:.1f}%"
            mem_col = str(r.inject.memories_injected)
        else:
            inj_col = "—"
            sav_col = "—"
            ratio_col = "—"
            mem_col = "0"

        prompt_short = r.prompt[:55] + "…" if len(r.prompt) > 55 else r.prompt
        lines.append(
            f"| {prompt_short} | {r.baseline.total_tokens} | {inj_col} | {sav_col} | {ratio_col} | {mem_col} |"
        )

    lines.append("")

    # Summary
    injected = [r for r in results if r.inject.inject_tokens > 0]
    lines.append("## Summary\n")
    lines.append(f"- Prompts with injection: **{len(injected)}/{len(results)}**")
    if injected:
        mean_savings = sum(r.savings for r in injected) / len(injected)
        total_savings = sum(r.savings for r in injected)
        mean_ratio = sum(r.efficiency_pct for r in injected) / len(injected)
        lines.append(f"- Mean savings (injected prompts): **{mean_savings:.0f} tokens**")
        lines.append(f"- Total savings: **{total_savings} tokens**")
        lines.append(f"- Mean inject/baseline ratio: **{mean_ratio:.1f}%**")
    else:
        lines.append("- No injections — check similarity thresholds and seed memories.")
    lines.append("")

    if injected:
        # Best injection
        best = max(injected, key=lambda r: r.savings)
        lines.append("## Best injection\n")
        lines.append(f"**Prompt:** {best.prompt}  ")
        lines.append(
            f"**Savings:** {best.savings} tokens ({best.efficiency_pct:.1f}% of baseline)\n"
        )
        lines.append("```")
        lines.append(best.inject.system_message or "")
        lines.append("```")
        lines.append("")

        # Worst injection
        worst = min(injected, key=lambda r: r.savings)
        lines.append("## Worst injection\n")
        lines.append(f"**Prompt:** {worst.prompt}  ")
        lines.append(
            f"**Ratio:** {worst.efficiency_pct:.1f}% of baseline "
            f"({worst.inject.inject_tokens} inject vs {worst.baseline.total_tokens} baseline tokens)"
        )
        lines.append("")

    # Raw JSON
    lines.append("## Raw JSON\n")
    raw = {
        "setup": dataclasses.asdict(setup_info),
        "results": [dataclasses.asdict(r) for r in results],
    }
    lines.append("```json")
    lines.append(json.dumps(raw, indent=2))
    lines.append("```")
    lines.append("")

    report = "\n".join(lines)
    Path(output_path).write_text(report, encoding="utf-8")
    print(f"\nReport written to: {output_path}")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="mememo inject hook — token savings benchmark")
    p.add_argument(
        "--repo",
        default="https://github.com/ntancardoso/gb",
        help="Git repo to clone (default: https://github.com/ntancardoso/gb)",
    )
    p.add_argument(
        "--tmp-dir",
        default="/tmp/mememo-perf",
        help="Scratch directory (default: /tmp/mememo-perf)",
    )
    p.add_argument(
        "--output",
        default="benchmark_report.md",
        help="Report output path (default: benchmark_report.md)",
    )
    p.add_argument(
        "--baseline-k",
        type=int,
        default=5,
        help="Top-K files for baseline (default: 5)",
    )
    p.add_argument(
        "--patterns",
        nargs="+",
        default=["**/*.py", "**/*.ts", "**/*.js", "**/*.go", "**/*.java", "**/*.rb"],
        help="File patterns to index",
    )
    p.add_argument(
        "--skip-setup",
        action="store_true",
        help="Skip clone+index, reuse existing data in --tmp-dir",
    )
    return p.parse_args()


async def main() -> None:
    args = _parse_args()
    import mememo.server as _server

    if args.skip_setup:
        storage_dir = str(Path(args.tmp_dir) / "data")
        os.environ["MEMEMO_STORAGE_DIR"] = storage_dir
        print(f"--skip-setup: loading existing data from {storage_dir}")
        await _server.initialize_mememo()

        repo_name = args.repo.rstrip("/").split("/")[-1]
        repo_path = str(Path(args.tmp_dir) / repo_name)
        setup_info = SetupInfo(
            repo_url=args.repo,
            repo_path=repo_path,
            files_indexed=-1,
            chunks_created=-1,
            persistent_memories_seeded=-1,
            setup_duration_seconds=0.0,
            storage_dir=storage_dir,
        )
    else:
        setup_info, repo_path = await setup(args.repo, args.tmp_dir, args.patterns)

    memory_manager = _server.memory_manager
    config = _server.config

    print("\nRunning benchmarks ...")
    results = await run_all(memory_manager, config, repo_path, PROMPTS, args.baseline_k)

    render_report(results, setup_info, args.output)


if __name__ == "__main__":
    asyncio.run(main())
